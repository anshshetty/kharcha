import pytest

from backend.ai_advisor import Advisor, snapshot
from backend.app import create_app
from backend.backup import make_backup, restore_backup
from backend.insights import brief, validate_context
from backend.spending_focus import spending_focus
from backend.store import Store
from .client import LocalClient
from .test_accounting import add


def test_new_ledger_has_no_personal_cost_assumptions(store):
    for category, kind in [("Rent & home", "purchase"), ("EMIs", "emi")]:
        add(store, date="2026-09-01", category=category, kind=kind, amount_minor=100)
    with store.tx() as db:
        db.execute("INSERT INTO categories VALUES('House construction')")
    add(store, date="2026-09-01", category="House construction", amount_minor=100)
    focus = spending_focus(store, as_of="2026-09-13")
    assert focus["fixed_minor"] == focus["unavoidable_minor"] == 0
    assert focus["other_minor"] == focus["spending_minor"] == 300
    assert focus["policy"] == {"fixed_categories": [], "unavoidable_categories": []}
    assert brief(store, "2026-09", "INR")["project_spending_minor"] == 0


def test_custom_choices_save_and_reclassify_without_changing_ledger(store):
    with LocalClient(create_app(store, start_scheduler=False)) as client:
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        for name in ("Studio, tools & supplies", "Pet care"):
            assert (
                client.post("/api/categories", json={"name": name}, headers=headers).status_code
                == 200
            )
        for name in ("Studio, tools & supplies", "Pet care", "Shopping"):
            add(store, date="2026-09-01", category=name, amount_minor=100)
        before = store.list_transactions()
        context = {
            "notes": "Keep creative work separate",
            "monthly_target_minor": 500,
            "project_categories": ["Studio, tools & supplies"],
        }
        assert (
            client.put("/api/financial-context", json=context, headers=headers).status_code == 200
        )
        choices = {
            "fixed_categories": ["Studio, tools & supplies"],
            "unavoidable_categories": ["Pet care"],
        }
        assert client.patch("/api/financial-context", json=choices).status_code == 403
        response = client.patch("/api/financial-context", json=choices, headers=headers)
        assert response.status_code == 200
        assert response.json() == {**validate_context(context), **choices}
        # Saving notes/targets separately cannot overwrite category choices.
        response = client.patch(
            "/api/financial-context", json={"notes": "Updated note"}, headers=headers
        )
        assert response.json()["fixed_categories"] == choices["fixed_categories"]
        focus = spending_focus(store, as_of="2026-09-13")
        assert (focus["fixed_minor"], focus["unavoidable_minor"], focus["other_minor"]) == (
            100,
            100,
            100,
        )
        assert focus["unavoidable_categories"][0]["name"] == "Pet care"
        plan = brief(store, "2026-09", "INR")
        assert plan["project_spending_minor"] == 100
        assert plan["remaining_minor"] == 300
        assert store.list_transactions() == before
        empty = {"fixed_categories": [], "unavoidable_categories": [], "project_categories": []}
        assert (
            client.patch("/api/financial-context", json=empty, headers=headers).status_code == 200
        )
        assert spending_focus(store, as_of="2026-09-13")["other_minor"] == 300
        assert brief(store, "2026-09", "INR")["project_spending_minor"] == 0
        assert store.list_transactions() == before


@pytest.mark.parametrize(
    "invalid",
    [
        {"fixed_categories": "Shopping"},
        {"fixed_categories": [False]},
        {"unavoidable_categories": [""]},
        {"project_categories": None},
        {"fixed_categories": ["Shopping"], "unavoidable_categories": ["Shopping"]},
        {"fixed_categories": ["Shopping"] * 101},
    ],
)
def test_invalid_category_choices_rejected_atomically(store, invalid):
    original = validate_context({"notes": "Retain this"})
    store.set_setting("financial_context", original)
    with LocalClient(create_app(store, start_scheduler=False)) as client:
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        assert (
            client.patch("/api/financial-context", json=invalid, headers=headers).status_code == 400
        )
    assert store.get_setting("financial_context") == original


def test_custom_preferences_survive_restart_and_backup_restore(store, tmp_path):
    choices = validate_context(
        {
            "fixed_categories": ["Subscriptions"],
            "unavoidable_categories": ["Health"],
            "project_categories": ["Travel"],
            "notes": "Personal choices",
        }
    )
    store.set_setting("financial_context", choices)
    reopened = Store(store.path, store.vault)
    try:
        assert reopened.get_setting("financial_context") == choices
    finally:
        reopened.close()
    restored = Store(tmp_path / "restored.sqlite3", store.vault)
    try:
        restore_backup(
            restored, "long-example-password", make_backup(store, "long-example-password")
        )
        assert restored.get_setting("financial_context") == choices
    finally:
        restored.close()


def test_project_split_refund_uses_original_categories_even_across_months(store):
    store.set_setting("financial_context", validate_context({"project_categories": ["Travel"]}))
    purchase = add(
        store,
        date="2026-08-01",
        amount_minor=1000,
        allocations=[
            {"type": "personal", "category": "Travel", "amount_minor": 600},
            {"type": "personal", "category": "Shopping", "amount_minor": 400},
        ],
    )
    refund = add(
        store,
        date="2026-09-01",
        kind="refund",
        direction="credit",
        amount_minor=100,
        category="Uncategorized",
    )
    store.update(refund["id"], {"linked_to": purchase["id"]})
    plan = brief(store, "2026-09", "INR")
    assert (
        plan["spending_minor"],
        plan["project_spending_minor"],
        plan["everyday_spending_minor"],
    ) == (-100, -60, -40)


def test_ai_follows_custom_choices_and_invalidates_old_result(store, monkeypatch):
    from .test_ai_advisor import saved

    monkeypatch.setattr("backend.ai_advisor.now", lambda: "2026-09-13T12:00:00+05:30")
    regular = add(store, date="2026-09-01", category="Shopping")
    protected = add(store, date="2026-09-01", category="Health")
    # A major project is not automatically protected from review.
    store.set_setting("financial_context", validate_context({"project_categories": ["Shopping"]}))
    saved(store)
    app = create_app(store, start_scheduler=False)
    advisor = app.state.advisor
    generation = advisor.generation
    with LocalClient(app) as client:
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        assert (
            client.patch(
                "/api/financial-context",
                json={"unavoidable_categories": ["Health"]},
                headers=headers,
            ).status_code
            == 200
        )
        assert advisor.generation > generation
        advisor._save_current("INR", generation, {"result": {"summary": "Obsolete result"}})
        status = Advisor(store).status()
        assert status["stale"] and "result" not in status
        data = snapshot(store)
        assert [row["id"] for row in data["evidence"]] == [regular["id"]]
        assert protected["id"] not in [row["id"] for row in data["evidence"]]


def test_category_creation_rejects_wrong_types_without_writing(store):
    with LocalClient(create_app(store, start_scheduler=False)) as client:
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        before = client.get("/api/categories").json()
        for body in (
            {"name": 123},
            {"name": ["Example"]},
            [],
            {"name": " "},
            {"name": "Example", "fixed": True},
        ):
            assert client.post("/api/categories", json=body, headers=headers).status_code == 400
        assert client.get("/api/categories").json() == before
