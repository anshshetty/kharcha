import pytest
from .client import LocalClient as TestClient
from backend.app import create_app, make_backup, restore_backup
from .test_accounting import add
from .conftest import email


def test_csrf_and_origin(store):
    with TestClient(create_app(store, start_scheduler=False)) as c:
        token = c.get("/api/session").json()["csrf"]
        assert c.post("/api/categories", json={"name": "Test"}).status_code == 403
        assert (
            c.post(
                "/api/categories",
                json={"name": "Test"},
                headers={"X-CSRF-Token": token, "Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert c.get("/api/status", headers={"Host": "evil.example"}).status_code == 400
        assert (
            c.post(
                "/api/categories", json={"name": "Test"}, headers={"X-CSRF-Token": token}
            ).status_code
            == 200
        )


def test_backup_restore_and_bad_password_atomic(store):
    store.ingest(email())
    t = store.list_transactions()[0]
    store.update(t["id"], {"category": "Groceries"})
    content = make_backup(store, "a-long-test-password")
    assert b"EXAMPLE SHOP" not in content and b"Groceries" not in content
    add(store)
    with pytest.raises(ValueError):
        restore_backup(store, "wrong password", content)
    assert len(store.list_transactions()) == 2
    restore_backup(store, "a-long-test-password", content)
    assert len(store.list_transactions()) == 1
    assert store.list_transactions()[0]["category"] == "Groceries"
    assert "EXAMPLE SHOP" in store.source("local:abc123")["body"]


def test_csv_formula_escape(store):
    add(store, counterparty='=HYPERLINK("evil")')
    with TestClient(create_app(store, start_scheduler=False)) as c:
        response = c.get("/api/export.csv")
        assert "'=HYPERLINK" in response.text


def test_reports_are_available_without_gmail(store):
    add(store)
    with TestClient(create_app(store, start_scheduler=False)) as c:
        assert c.get("/api/report?month=2026-08").json()["totals"]["spend_minor"] == 100000
        assert c.get("/api/status").json()["connection"]["state"] == "disconnected"


@pytest.mark.parametrize(
    "origin,history,expected",
    [
        (None, "10", {"months": 6, "resumed": True, "policy_update": True}),
        ("earlier", "10", {"months": 6, "resumed": True, "policy_update": True}),
        ("current", "10", {"months": 6, "resumed": False, "policy_update": True}),
        ("current", None, {"months": 6, "resumed": False, "policy_update": False}),
    ],
)
def test_status_explains_one_time_scan_and_saved_progress(store, origin, history, expected):
    app = create_app(store, start_scheduler=False)
    store.set_setting("history_id", history)
    state = {"origin_job_id": origin, "months": 6, "rescan": True}
    store.set_setting("backfill", state)
    with store.tx() as db:
        db.execute("INSERT INTO jobs(id,state,started_at) VALUES('current','running','2026-09-18')")
    with TestClient(app) as c:
        assert c.get("/api/status").json()["job"]["scan"] == expected
        store.set_setting("backfill", {**state, "rescan": False})
        assert c.get("/api/status").json()["job"]["scan"]["policy_update"] is False
        store.set_setting("backfill", None)
        assert c.get("/api/status").json()["job"]["scan"] is None
        store.set_setting("backfill", state)
        with store.tx() as db:
            db.execute("UPDATE jobs SET state='complete' WHERE id='current'")
        assert c.get("/api/status").json()["job"]["scan"] is None


def test_oauth_state_is_checked(store):
    app = create_app(store, start_scheduler=False)
    with TestClient(app) as c:
        r = c.get("/api/auth/callback?state=invalid&code=not-real")
        assert r.status_code == 400


def test_oauth_return_navigation_preserves_api_protection(store, tmp_path, monkeypatch):
    public = tmp_path / "frontend/out"
    public.mkdir(parents=True)
    (public / "index.html").write_text("<h1>Synthetic app shell</h1>")
    monkeypatch.setattr("backend.app.ROOT", tmp_path)
    with TestClient(create_app(store, start_scheduler=False)) as c:
        headers = {
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        }
        shell = c.get("/?connected=1", headers=headers)
        assert shell.status_code == 200
        assert shell.headers["cache-control"] == "no-cache"
        assert c.get("/api/status", headers=headers).status_code == 403
        assert (
            c.get("/?connected=1", headers={**headers, "Sec-Fetch-Mode": "cors"}).status_code == 403
        )
        assert c.post("/api/auth/start", json={}, headers=headers).status_code == 403


def test_invalid_money_does_not_create_transaction(store):
    with TestClient(create_app(store, start_scheduler=False)) as c:
        token = c.get("/api/session").json()["csrf"]
        r = c.post(
            "/api/transactions",
            json={
                "date": "2026-08-01",
                "amount_minor": -100,
                "direction": "debit",
                "kind": "purchase",
                "currency": "INR",
            },
            headers={"X-CSRF-Token": token},
        )
        assert r.status_code == 400
        assert not store.list_transactions()


def test_retired_import_settings_are_hidden_and_not_restored(store):
    store.ingest(email())
    transaction_id = store.list_transactions()[0]["id"]
    store.set_setting("plugin_mailbox", "owner@example.test")
    store.set_setting("plugin_import_status", {"imported_sources": 1})
    with TestClient(create_app(store, start_scheduler=False)) as client:
        assert "plugin_import" not in client.get("/api/status").json()
    archive = make_backup(store, "a-long-test-password")
    restore_backup(store, "a-long-test-password", archive)
    assert store.get_setting("plugin_mailbox") is None
    assert store.get_setting("plugin_import_status") is None
    assert store.list_transactions()[0]["id"] == transaction_id
    assert store.source("local:abc123")["body"]


def test_reparse_preserves_historical_incomplete_evidence(store):
    store.ingest(email())
    with store.tx() as db:
        db.execute("UPDATE sources SET parser_version='incomplete', status='review'")
    assert store.reparse() == 0
    assert store.source("local:abc123")["status"] == "review"


def test_month_selection_reaches_spending_and_ai_routes(store, monkeypatch):
    monkeypatch.setattr("backend.ai_advisor.now", lambda: "2026-09-13T12:00:00+05:30")
    monkeypatch.setattr("backend.spending_focus.now", lambda: "2026-09-13T12:00:00+05:30")
    previous = add(store, date="2026-08-31", amount_minor=500)
    add(store, date="2026-09-01", amount_minor=999)
    app = create_app(store, start_scheduler=False)
    calls = []
    monkeypatch.setattr(
        app.state.advisor, "start", lambda **kw: calls.append(kw) or {"state": "running"}
    )
    with TestClient(app) as client:
        focus = client.get("/api/spending-focus?month=2026-08").json()
        assert focus["other_minor"] == 500
        assert focus["current"] is False
        preview = client.get("/api/ai-advisor/preview?month=2026-08").json()
        assert [t["id"] for t in preview["evidence"]] == [previous["id"]]
        assert client.get("/api/ai-advisor?month=2026-08").json()["state"] == "paused"
        assert calls == []
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        assert (
            client.post(
                "/api/ai-advisor/refresh?month=2026-08&currency=USD", headers=headers, json={}
            ).status_code
            == 200
        )
        assert calls == [{"force": True, "currency": "USD", "month": "2026-08"}]
        for route in ("spending-focus", "ai-advisor", "ai-advisor/preview"):
            assert client.get(f"/api/{route}?month=2026-99").status_code == 400
            assert client.get(f"/api/{route}?month=2026-10").status_code == 400
