"""The showcase must never touch an existing ledger or any live integration."""

from datetime import date
import json
from pathlib import Path

import pytest

from scripts.demo import MemoryVault, create_demo_app, demo_store, populate
from tests.client import LocalClient


def forbidden(*args, **kwargs):
    raise AssertionError("The demo attempted to access a live resource")


def test_demo_is_disposable_and_ignores_real_data_and_keychain(tmp_path, monkeypatch):
    import backend.app
    import keyring

    original = tmp_path / "real-ledger.sqlite3"
    original.write_bytes(b"DO NOT TOUCH: original private ledger")
    monkeypatch.setenv("MONTHLYCOST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(backend.app, "data_directory", forbidden)
    monkeypatch.setattr(backend.app, "Vault", forbidden)
    for method in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, method, forbidden)
    with demo_store(date(2026, 9, 15)) as store:
        temporary = store.path.parent
        assert temporary.resolve().parent == Path("/tmp").resolve()
        assert isinstance(store.vault, MemoryVault)
        assert store.vault.read("gmail-token") is None
        assert store.vault.read("oauth-client") is None
        with LocalClient(create_demo_app(store, 8875)) as client:
            assert client.get("/api/status").json()["demo"] is True
            assert "DO NOT TOUCH" not in client.get("/api/transactions").text
            source_id = store.list_sources()[0]["id"]
            assert "FICTIONAL DEMO EMAIL" in client.get("/api/sources/" + source_id).json()["body"]
    assert not temporary.exists()
    assert original.read_bytes() == b"DO NOT TOUCH: original private ledger"
    assert list(tmp_path.iterdir()) == [original]


def test_demo_fixture_has_reconciled_rich_synthetic_data():
    with demo_store(date(2026, 9, 15)) as store:
        rows = store.list_transactions()
        assert len(rows) == 299
        assert len({row["date"][:7] for row in rows}) == 7
        assert max(row["date"] for row in rows) == "2026-09-15"
        assert len({row["category"] for row in rows}) >= 18
        assert {row["currency"] for row in rows} == {"INR", "USD"}
        assert all("demo" in row["counterparty"].lower() for row in rows)
        assert all(source["sender"] == "alerts@example.test" for source in store.list_sources())
        assert all(
            row["spend_minor"] == 0
            for row in rows
            if row["kind"]
            in {
                "income",
                "reimbursement",
                "own_transfer",
                "investment",
                "card_repayment",
            }
        )
        for row in rows:
            if row["allocations"]:
                assert (
                    sum(item["amount_minor"] for item in row["allocations"]) == row["amount_minor"]
                )
                assert row["spend_minor"] == 120000
            if row["kind"] == "refund":
                assert row["linked_to"] and row["spend_minor"] == -120000
        report = store.report("2026-09")
        assert report["totals"]["review_count"] == 2
        assert report["totals"]["refund_minor"] == 120000
        assert report["totals"]["spend_minor"] == sum(
            row["spend_minor"]
            for row in rows
            if row["date"].startswith("2026-09") and row["currency"] == "INR"
        )
        assert len(report["recurring"]) >= 5
        assert store.get_setting("financial_context")["monthly_target_minor"] == 5500000
        with pytest.raises(ValueError, match="new, empty ledger"):
            populate(store, date(2026, 9, 15))


@pytest.mark.parametrize("as_of", [date(2026, 1, 1), date(2026, 9, 15)])
def test_demo_fixture_reproducible_and_handles_month_boundaries(as_of):
    with demo_store(as_of) as first:
        before = first.list_transactions()
    with demo_store(as_of) as second:
        assert before == second.list_transactions()
        assert len({row["date"][:7] for row in before}) == 7
        assert max(row["date"] for row in before) == as_of.isoformat()


def test_demo_blocks_live_integrations_and_preserves_authentication(monkeypatch):
    from backend.ai_advisor import Advisor
    from backend.gmail import GmailAuth, SyncEngine
    from backend.merchant_research import MerchantResearch

    for cls, methods in (
        (GmailAuth, ("configure", "start", "complete", "disconnect")),
        (SyncEngine, ("start",)),
        (Advisor, ("start",)),
        (MerchantResearch, ("start",)),
    ):
        for method in methods:
            monkeypatch.setattr(cls, method, forbidden)
    with demo_store(date(2026, 9, 15)) as store:
        app = create_demo_app(store, 8875)
        with LocalClient(app) as client:
            token = client.headers.pop("Authorization")
            for path in ("/api/status", "/api/spending-focus", "/api/ai-advisor"):
                assert client.get(path).status_code == 401
            client.headers["Authorization"] = token
            csrf = client.get("/api/session").json()["csrf"]
            client.headers["X-CSRF-Token"] = csrf
            for path in (
                "/api/auth/configure",
                "/api/auth/start",
                "/api/auth/disconnect",
                "/api/sync",
                "/api/ai-advisor/refresh",
                "/api/import/local",
                "/api/import",
                "/api/restore",
                "/api/erase",
                "/api/reparse",
                "/api/transactions/example/merchant-lookup",
                "/api/future-network-feature",
            ):
                assert client.post(path, json={}).status_code == 403
            assert client.get("/api/auth/callback?code=demo").status_code == 403
            assert (
                client.put("/api/ai-advisor/preferences", json={"enabled": True}).status_code == 403
            )
            status = client.get("/api/status").json()
            assert status["connection"]["state"] == "disconnected"
            assert status["configured"] is False
            assert status["local_export_available"] is False
            assert str(Path.home()) not in json.dumps(status)
            focus = client.get("/api/spending-focus").json()
            assert focus["month"] == "2026-09"
            analysis = client.get("/api/ai-advisor").json()
            assert "No AI service was called" in analysis["message"]
            assert len(analysis["result"]["patterns"]) == 3
            assert (
                client.patch(
                    "/api/financial-context", json={"monthly_target_minor": 6000000}
                ).status_code
                == 200
            )
            assert store.get_setting("financial_context")["monthly_target_minor"] == 6000000
