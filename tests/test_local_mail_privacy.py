"""Broad local scanning retains completed transaction evidence, not mailbox chatter."""

import json

import pytest

from backend.ai_advisor import enabled, snapshot
from backend.backup import make_backup, restore_backup
from backend.store import Store
from .conftest import email


def sync_job(store, id="privacy-sync"):
    with store.tx() as db:
        db.execute("INSERT INTO jobs(id,state) VALUES(?,'running')", (id,))
    return id


def personal_message(body, subject="A note for you", id="personal"):
    message = email(id, body=body, subject=subject)
    message.update(
        sender="person@gmail.com",
        authentication={"verified": True, "domain": "gmail.com", "receiver": "mx.google.com"},
    )
    return message


@pytest.mark.parametrize(
    "subject,body",
    [
        ("Dinner", "I paid INR 500 for our dinner."),
        ("Dinner", "I spent INR 500 at SHOP on 01/08/2026."),
        ("Dinner", "I have not paid INR 500 to Alex Example for dinner yet."),
        ("Invoice", "Unpaid invoice INR 500 at SHOP. Invoice ID: INV12345"),
        ("Shopping question", "Should I purchase at SHOP for INR 500?"),
        ("My payment", "I paid INR 500 from account XX1234 at SHOP on 01/08/2026."),
        ("My payment", "I paid INR 500 to Alex Example for dinner. UPI Ref: 123456789012"),
        ("My payment", "I've paid INR 500 to Alex Example for dinner. UPI Ref: 123456789012"),
        ("My payment", "We’ve already paid INR 500 from account XX1234 at SHOP."),
        (
            "Payment plans",
            "I will pay INR 500 from account XX1234 at SHOP. UPI Ref: 123456789012",
        ),
        (
            "Payment plans",
            "Payment of INR 500 will be debited from account XX1234 at SHOP tomorrow.",
        ),
        (
            "Payment discussion",
            "INR 500 was not debited from account XX1234 at SHOP. UPI Ref: 123456789012",
        ),
        (
            "Payment discussion",
            "INR 500 wasn't debited from account XX1234 at SHOP. UPI Ref: 123456789012",
        ),
        (
            "Payment discussion",
            "If INR 500 is debited from account XX1234 at SHOP, what should I do?",
        ),
        (
            "INR 500 paid at SHOP. UPI Ref: 123456789012",
            "Here are the family photos from our holiday.",
        ),
        (
            "Payment successful",
            "We discussed INR 500 at dinner. Receipt ID: DISC12345",
        ),
        ("Payment discussion", "Let's talk about the payment next week."),
    ],
)
def test_personal_financial_chatter_leaves_no_sync_records(store, subject, body):
    result = store.ingest(
        personal_message(body, subject),
        "test@example.test",
        sync_job_id=sync_job(store),
    )
    assert result == "excluded"
    assert store.list_sources() == []
    assert store.list_transactions() == []
    assert store.db.execute("SELECT COUNT(*) FROM issues").fetchone()[0] == 0
    assert store.sync_summary("privacy-sync") == {"added": 0, "unseen": 0}


@pytest.mark.parametrize("sender", ["seller@gmail.com", "hello@small-shop.example"])
def test_structured_receipt_is_accepted_without_sender_or_subject_allowlists(store, sender):
    message = email(
        "receipt",
        subject="Documents for your records",
        body=(
            "Receipt ID: ABC12345\nPayment received INR500.00\n"
            "Merchant: Example Shop\nDate: 01/08/2026"
        ),
    )
    message.update(
        sender=sender,
        authentication={
            "verified": True,
            "domain": sender.rsplit("@", 1)[1],
            "receiver": "mx.google.com",
        },
    )
    assert store.ingest(message, "test@example.test", sync_job_id=sync_job(store)) == "parsed"
    assert len(store.list_transactions()) == 1
    assert store.source("test@example.test:receipt")["body"] == message["body"]
    assert store.sync_summary("privacy-sync") == {"added": 1, "unseen": 1}


def test_existing_bank_evidence_survives_fraud_warning_footer(store):
    message = email()
    message["body"] += "\nIf you did not authorize this transaction, contact your bank immediately."
    assert store.ingest(message, "test@example.test", sync_job_id=sync_job(store)) == "parsed"
    assert store.list_transactions()[0]["amount_minor"] == 50000
    assert store.source("test@example.test:abc123")["body"] == message["body"]


def test_unverified_completed_receipt_is_discarded_without_review_cache(store):
    message = email()
    message["authentication"] = {"verified": False}
    assert store.ingest(message, "test@example.test", sync_job_id=sync_job(store)) == "excluded"
    assert store.list_sources() == []
    assert store.list_transactions() == []
    assert store.db.execute("SELECT COUNT(*) FROM issues").fetchone()[0] == 0


def test_rejected_content_cannot_enter_ai_snapshot_or_restored_backup(store, monkeypatch):
    monkeypatch.setattr("backend.ai_advisor.now", lambda: "2026-08-02T12:00:00+05:30")
    job_id = sync_job(store)
    marker = "PRIVATE_FAMILY_DISCUSSION_SENTINEL"
    private = personal_message(
        "I paid INR 500 to " + marker + " for our dinner. UPI Ref: 123456789012",
        "Private payment discussion " + marker,
    )
    assert store.ingest(private, "test@example.test", sync_job_id=job_id) == "excluded"
    # Genuine parsed spending remains available to the user and opted-in AI.
    assert store.ingest(email("bank"), "test@example.test", sync_job_id=job_id) == "parsed"
    data = snapshot(store)
    assert not enabled(store)
    assert len(data["evidence"]) == 1
    assert data["evidence"][0]["other_spend_minor"] == 50000
    assert marker not in json.dumps(data)
    assert email("bank")["body"] not in json.dumps(data)
    backup = make_backup(store, "privacy-test-long-password")
    restored = Store(None, store.vault)
    try:
        restore_backup(restored, "privacy-test-long-password", backup)
        assert [row["id"] for row in restored.list_sources()] == ["test@example.test:bank"]
        assert len(restored.list_transactions()) == 1
        assert marker not in json.dumps(snapshot(restored))
        for name in ("sources", "transactions", "observations", "issues", "audit", "settings"):
            records = [dict(row) for row in restored.db.execute("SELECT * FROM " + name)]
            assert marker not in json.dumps(records, default=str)
    finally:
        restored.close()


def test_explicit_local_ingestion_can_still_retain_items_for_review(store):
    message = personal_message("Let's talk about the payment next week.")
    assert store.ingest(message) == "review"
    assert len(store.list_sources()) == 1
    assert store.source("local:personal")["body"] == message["body"]
