"""Sync additions reflect ledger changes, independently of email counts."""

import pytest
import json

from backend.app import create_app
from backend.backup import make_backup, restore_backup
from backend.gmail import SyncEngine
from backend.store import Store
from .client import LocalClient
from .conftest import email
from .test_accounting import add
from .test_sync import FakeAuth


def job(store, id):
    with store.tx() as db:
        db.execute("INSERT INTO jobs(id,state,started_at) VALUES(?,'complete','2026-09-15')", (id,))


def ingest_new(store, id="a", job_id="first", reference="123456789012"):
    message = email(id)
    message["body"] = message["body"].replace("123456789012", reference)
    store.ingest(message, "test@example.test", sync_job_id=job_id)
    return next(t for t in store.list_transactions() if t.get("reference") == reference)["id"]


def test_additions_survive_noop_sync_and_restart_without_marking_legacy(store):
    legacy = add(store)
    job(store, "first")
    tid = ingest_new(store)
    assert store.transaction(tid)["is_new"] is True
    assert store.transaction(tid)["sync_job_id"] == "first"
    assert store.transaction(legacy["id"])["is_new"] is False
    assert store.sync_summary("first") == {"added": 1, "unseen": 1}
    # A second email for the same referenced payment does not add another row.
    job(store, "next")
    ingest_new(store, "duplicate", "next")
    assert store.sync_summary("next") == {"added": 0, "unseen": 1}
    reopened = Store(store.path, store.vault)
    try:
        assert reopened.transaction(tid)["is_new"] is True
        assert reopened.transaction(tid)["source_count"] == 2
        assert reopened.sync_summary("next") == {"added": 0, "unseen": 1}
    finally:
        reopened.close()


def test_seen_only_affects_selected_ids_and_preserves_sync_added_count(store):
    job(store, "first")
    tid = ingest_new(store)
    # A later arrival must stay new even if a stale view acknowledges its rows.
    job(store, "next")
    later = ingest_new(store, "later", "next", "123456789099")
    assert store.mark_sync_seen([tid, tid, "unknown"]) == 1
    assert store.mark_sync_seen([tid]) == 0
    assert store.transaction(tid)["is_new"] is False
    assert store.transaction(later)["is_new"] is True
    assert store.sync_summary("first") == {"added": 1, "unseen": 1}
    reopened = Store(store.path, store.vault)
    try:
        assert reopened.transaction(tid)["is_new"] is False
    finally:
        reopened.close()


@pytest.mark.parametrize("split", [False, True])
def test_matching_existing_export_is_not_a_new_transaction(store, split):
    store.import_export(
        {
            "transactions": [
                {
                    "id": "a",
                    "date": "2026-08-01",
                    "kind": "Bank debit",
                    "amount": 500,
                    "detail": "EXAMPLE SHOP",
                    "account": "XX1234",
                }
            ]
        }
    )
    original = store.list_transactions()[0]["id"]
    store.update(original, {"category": "Groceries"})
    allocations = [
        {"type": "personal", "amount_minor": 25000},
        {"type": "reimbursable", "amount_minor": 25000},
    ]
    if split:
        store.update(original, {"allocations": allocations})
    job(store, "first")
    added = ingest_new(store)
    if split:
        with pytest.raises(ValueError, match="Remove splits"):
            store.merge(added, original)
    engine = SyncEngine(store, FakeAuth())
    engine.reconcile_export("a", "test@example.test:a")
    store.reconcile_sync_changes()
    assert store.sync_summary("first") == {"added": 0, "unseen": 0}
    assert len(store.list_transactions()) == 1
    assert store.transaction(original)["category"] == "Groceries"
    assert store.transaction(original)["is_new"] is False
    if split:
        assert store.transaction(original)["allocations"] == allocations
        assert store.transaction(original)["spend_minor"] == 25000


def test_failed_job_keeps_additions_and_retry_does_not_count_them_again(store):
    job(store, "first")
    tid = ingest_new(store)
    with store.tx() as db:
        db.execute("UPDATE jobs SET state='failed' WHERE id='first'")
    job(store, "retry")
    ingest_new(store, job_id="retry")
    assert store.transaction(tid)["is_new"] is True
    assert store.sync_summary("first")["added"] == 1
    assert store.sync_summary("retry") == {"added": 0, "unseen": 1}


def test_excluded_sync_candidates_are_not_retained(store):
    job(store, "first")
    status = store.ingest(
        email(body="Let's meet for lunch tomorrow", subject="Lunch tomorrow"),
        "test@example.test",
        sync_job_id="first",
    )
    assert status == "excluded"
    assert not store.list_sources()
    assert not store.list_transactions()


def test_unverified_sync_candidate_body_is_not_cached(store):
    job(store, "first")
    message = email()
    message["authentication"] = {"verified": False}
    assert store.ingest(message, "test@example.test", sync_job_id="first") == "excluded"
    assert not store.list_sources()
    assert store.sync_summary("first") == {"added": 0, "unseen": 0}


def test_status_and_seen_api_report_real_additions_with_csrf(store):
    job(store, "first")
    tid = ingest_new(store)
    job(store, "noop")
    with LocalClient(create_app(store, start_scheduler=False)) as client:
        status = client.get("/api/status").json()
        assert status["job"]["id"] == "noop"
        assert status["job"]["added"] == 0
        assert status["new_transaction_count"] == 1
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        assert client.post("/api/sync/seen", json={"transaction_ids": [tid]}).status_code == 403
        for invalid in (None, [], {}, {"transaction_ids": None}, {"transaction_ids": [1]}):
            assert (
                client.post(
                    "/api/sync/seen",
                    content=json.dumps(invalid),
                    headers={**headers, "Content-Type": "application/json"},
                ).status_code
                == 400
            )
        response = client.post("/api/sync/seen", json={"transaction_ids": [tid]}, headers=headers)
        assert response.json() == {"marked": 1}
        assert client.get("/api/status").json()["new_transaction_count"] == 0
        assert client.get("/api/transactions").json()[0]["is_new"] is False


def test_backup_restore_discards_local_sync_markers_atomically(store):
    job(store, "first")
    tid = ingest_new(store)
    store.set_setting("gmail_scan_policy", 2)
    backup = make_backup(store, "a-long-test-password")
    with pytest.raises(ValueError):
        restore_backup(store, "wrong-password", backup)
    assert store.transaction(tid)["is_new"] is True
    restore_backup(store, "a-long-test-password", backup)
    assert store.get_setting("gmail_scan_policy") is None
    assert store.transaction(tid)["is_new"] is False
    assert store.sync_summary() == {"added": 0, "unseen": 0}
    assert not store.db.execute("PRAGMA foreign_key_check").fetchall()


def test_cleanup_removes_only_unlinked_gmail_cache(store):
    mailbox = "test@example.test"
    store.ingest(email("bank"), mailbox)
    bank = store.list_transactions()[0]
    for id in ("discard", "linked"):
        store.ingest(email(id, body="Let's talk about the payment next week."), mailbox)
    store.ingest(email("local", body="Let's talk about the payment next week."))
    with store.tx() as db:
        store.issue(
            db,
            "manual_review",
            "Keep supporting evidence",
            transaction_id=bank["id"],
            source_id=mailbox + ":linked",
        )
    store.discard_unretained_sources(mailbox)
    assert {s["id"] for s in store.list_sources()} == {
        mailbox + ":bank",
        mailbox + ":linked",
        "local:local",
    }
    assert store.transaction(bank["id"])["amount_minor"] == bank["amount_minor"]
    assert store.source(mailbox + ":bank")["body"]
    assert not store.db.execute(
        "SELECT 1 FROM issues WHERE source_id=?", (mailbox + ":discard",)
    ).fetchone()
    assert not store.db.execute("PRAGMA foreign_key_check").fetchall()


def test_erase_removes_sync_markers_with_the_ledger(store, monkeypatch):
    job(store, "first")
    ingest_new(store)
    monkeypatch.setattr(store.vault, "delete", lambda name: None)
    with LocalClient(create_app(store, start_scheduler=False)) as client:
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        response = client.post("/api/erase", json={"confirmation": "DELETE"}, headers=headers)
        assert response.json() == {"deleted": True}
        assert client.get("/api/status").json()["new_transaction_count"] == 0
    assert not store.db.execute("SELECT * FROM sync_additions").fetchall()
    assert not store.db.execute("PRAGMA foreign_key_check").fetchall()
