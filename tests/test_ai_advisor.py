import time
import pytest
from backend.ai_advisor import snapshot, validate_result, Advisor, fingerprint, REVIEW_VERSION
from .test_accounting import add


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    monkeypatch.setattr("backend.ai_advisor.now", lambda: "2026-09-13T12:00:00+05:30")


def result():
    return {
        "summary": "A single purchase accounts for most other spending.",
        "patterns": [
            {
                "title": "Concentration",
                "detail": "Supported",
                "confidence": "high",
                "transaction_ids": ["known"],
            }
        ],
        "commitments": [],
        "actions": [],
        "uncertainties": [],
        "target": {"amount_minor": None, "currency": "INR", "reason": ""},
    }


def saved(store, **changes):
    store.set_setting("ai_advisor_enabled", True)
    store.set_setting("ai_advisor_consent_version", 1)
    data = snapshot(store)
    state = {
        "state": "complete",
        "review_version": REVIEW_VERSION,
        "focus_month": data["focus_month"],
        "currency": "INR",
        "attempted_at": time.time(),
        "result": result(),
        "digest": fingerprint(data),
        **changes,
    }
    store.set_setting("ai_advisor", state)
    return state


def test_unknown_or_missing_citations_rejected():
    with pytest.raises(ValueError):
        validate_result(result(), {"different"})
    r = result()
    r["patterns"][0]["transaction_ids"] = []
    with pytest.raises(ValueError):
        validate_result(r, {"known"})
    assert validate_result(result(), {"known"})


def test_no_manufactured_target_or_long_report():
    r = result()
    r["target"]["amount_minor"] = 100
    with pytest.raises(ValueError):
        validate_result(r, {"known"})
    r = result()
    r["patterns"] *= 4
    with pytest.raises(ValueError):
        validate_result(r, {"known"})


def test_snapshot_excludes_protected_and_exempt_evidence(store):
    store.set_setting(
        "financial_context",
        {
            "fixed_categories": ["Rent & home", "EMIs"],
            "unavoidable_categories": ["Home construction"],
        },
    )
    with store.tx() as db:
        db.execute("INSERT INTO categories VALUES('Home construction')")
    for category, kind in [
        ("Rent & home", "purchase"),
        ("EMIs", "emi"),
        ("Home construction", "purchase"),
    ]:
        add(store, date="2026-09-01", category=category, kind=kind)
    add(store, date="2026-09-01", amount_minor=90000000, excluded=True)
    other = add(store, date="2026-09-01", category="Shopping")
    data = snapshot(store)
    assert [t["id"] for t in data["evidence"]] == [other["id"]]
    assert data["focus"]["other_minor"] == 100000


def test_retry_cooldown_preserves_previous_result(store):
    saved(store)
    advisor = Advisor(store)
    assert advisor.start(force=True)["state"] == "complete"
    assert not advisor.lock.locked()


def test_analysis_paused_until_authorized(store):
    advisor = Advisor(store)
    assert advisor.start(force=True)["state"] == "paused"
    assert not advisor.lock.locked()


def test_current_month_evidence_and_future_filter(store):
    current = add(store, date="2026-09-01", amount_minor=100)
    add(store, date="2026-09-30", amount_minor=999999)
    add(store, date="2026-08-01", amount_minor=999999)
    data = snapshot(store)
    assert data["focus_month"] == "2026-09"
    assert [t["id"] for t in data["evidence"]] == [current["id"]]
    assert data["focus"]["spending_minor"] == 100


def test_previous_month_and_version_results_are_not_displayed(store):
    saved(store, focus_month="2026-08")
    assert "result" not in Advisor(store).status()
    saved(store, review_version=2)
    assert "result" not in Advisor(store).status()


def test_changed_ledger_hides_old_insights_but_sync_timestamp_does_not(store):
    saved(store)
    store.set_setting("last_sync", "2026-09-13T13:00:00")
    assert "result" in Advisor(store).status()
    add(store, date="2026-09-01", amount_minor=100)
    state = Advisor(store).status()
    assert state["stale"] and "result" not in state


def test_currency_results_do_not_leak_between_views(store):
    saved(store)
    assert "result" not in Advisor(store).status("USD")
    assert snapshot(store, "USD")["currency"] == "USD"


def test_interrupted_stale_review_can_be_refreshed(store):
    saved(store, state="running")
    add(store, date="2026-09-02", amount_minor=100)
    state = Advisor(store).status()
    assert state["state"] == "failed"
    assert state["stale"] and "result" not in state


def test_snapshot_uses_disclosed_fields_and_excludes_raw_private_fields(store):
    import json

    add(
        store,
        date="2026-09-01",
        counterparty="Example Merchant",
        account="PRIVATE_ACCOUNT_SENTINEL",
        reference="PRIVATE_REFERENCE_SENTINEL",
        notes="User entered note",
    )
    store.vault.write("gmail-token", "PRIVATE_CREDENTIAL_SENTINEL")
    data = snapshot(store)
    encoded = json.dumps(data)
    for private in (
        "PRIVATE_ACCOUNT_SENTINEL",
        "PRIVATE_REFERENCE_SENTINEL",
        "PRIVATE_CREDENTIAL_SENTINEL",
    ):
        assert private not in encoded
    assert data["evidence"][0]["notes"] == "User entered note"
    assert set(data["evidence"][0]) == {
        "id",
        "date",
        "currency",
        "merchant",
        "counterparty",
        "other_spend_minor",
        "categories",
        "kind",
        "notes",
        "corrected",
        "warnings",
    }
