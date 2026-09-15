import base64
from datetime import datetime

import pytest
from backend.email_filter import POLICY_VERSION
from backend.gmail import GmailAuth, SyncEngine, SYNC_MONTHS
from backend.parser import IST
from backend.store import encode
from .conftest import email, authenticated_headers


class FakeAuth:
    def token(self):
        return "fake-token-for-unit-tests"


def gmail_message(id="a", params=None, **changes):
    e = {**email(id), **changes}
    message = {
        "id": id,
        "internalDate": "1785582000000",
        "labelIds": ["INBOX", "CATEGORY_UPDATES"],
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(e["body"].encode()).decode()},
            "headers": authenticated_headers(e["sender"])
            + [{"name": "Subject", "value": e["subject"]}],
        },
    }
    if params is not None:
        assert params["format"] == "full", "Broad scan should not fetch metadata first"
    return message


def run(engine, id="job"):
    with engine.store.tx() as db:
        db.execute("INSERT INTO jobs(id,state,started_at) VALUES(?,'running','2026-09-05')", (id,))
    engine.lock.acquire()
    engine._run(id)
    return dict(engine.store.db.execute("SELECT * FROM jobs WHERE id=?", (id,)).fetchone())


def test_full_sync_incremental_idempotency(store, monkeypatch):
    store.set_setting("bound_mailbox", "test@example.test")
    engine = SyncEngine(store, FakeAuth())
    calls = []

    def request(path, params=None):
        calls.append((path, params))
        if path == "/profile":
            return {"historyId": "10"}
        if path == "/messages":
            return {"messages": [{"id": "a"}]}
        if path == "/messages/a":
            return gmail_message(params=params)
        if path == "/history":
            return {"historyId": "11", "history": []}
        raise AssertionError(path)

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "complete"
    assert store.get_setting("history_id") == "11"
    assert store.get_setting("backfill") is None
    assert run(engine, "again")["state"] == "complete"
    assert len(store.list_transactions()) == 1
    assert [params["format"] for path, params in calls if path == "/messages/a"] == ["full"]
    queries = [params["q"] for path, params in calls if path == "/messages"]
    assert len(queries) == 1
    assert "subject:" not in queries[0]
    assert "category:" not in queries[0]
    assert "from:" not in queries[0]
    assert any("after:" in str(params) and "before:" in str(params) for _, params in calls)


def test_expired_history_falls_back_to_full(store, monkeypatch):
    store.set_setting("bound_mailbox", "test@example.test")
    store.set_setting("history_id", "old")
    store.set_setting("gmail_scan_policy", POLICY_VERSION)
    engine = SyncEngine(store, FakeAuth())

    def request(path, params=None):
        if path == "/history" and params["startHistoryId"] == "old":
            return None
        if path == "/profile":
            return {"historyId": "fresh"}
        if path == "/messages":
            return {"messages": [{"id": "a"}]}
        if path == "/messages/a":
            return gmail_message(params=params)
        if path == "/history":
            return {"historyId": "finished", "history": []}
        raise AssertionError(path)

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "complete"
    assert store.get_setting("history_id") == "finished"


def test_failed_fetch_does_not_advance_checkpoint(store, monkeypatch):
    store.set_setting("bound_mailbox", "test@example.test")
    store.set_setting("history_id", "10")
    store.set_setting("gmail_scan_policy", POLICY_VERSION)
    engine = SyncEngine(store, FakeAuth())

    def request(path, params=None):
        if path == "/history":
            return {"historyId": "11", "history": [{"messagesAdded": [{"message": {"id": "a"}}]}]}
        if path == "/messages/a":
            raise ValueError("Network interrupted")
        raise AssertionError(path)

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "failed"
    assert store.get_setting("history_id") == "10"
    assert (
        store.db.execute("SELECT status FROM queue WHERE message_id='a'").fetchone()[0] == "pending"
    )
    monkeypatch.setattr(
        engine,
        "request",
        lambda path, params=None: (
            gmail_message(params=params)
            if path == "/messages/a"
            else {"historyId": "11", "history": []}
        ),
    )
    assert run(engine, "retry")["state"] == "complete"
    assert len(store.list_transactions()) == 1


def test_messages_arriving_during_full_import_are_replayed(store, monkeypatch):
    store.set_setting("bound_mailbox", "test@example.test")
    engine = SyncEngine(store, FakeAuth())

    def request(path, params=None):
        if path == "/profile":
            return {"historyId": "10"}
        if path == "/messages":
            return {"messages": [{"id": "a"}]}
        if path.startswith("/messages/"):
            return gmail_message(path.split("/")[-1], params=params)
        if path == "/history":
            return {"historyId": "11", "history": [{"messagesAdded": [{"message": {"id": "new"}}]}]}

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "complete"
    assert len(store.list_sources()) == 2
    assert len(store.list_transactions()) == 1


def test_deleted_gmail_message_keeps_ledger(store, monkeypatch):
    store.set_setting("bound_mailbox", "test@example.test")
    store.ingest(email("a"), "test@example.test")
    store.set_setting("history_id", "10")
    store.set_setting("gmail_scan_policy", POLICY_VERSION)
    engine = SyncEngine(store, FakeAuth())
    monkeypatch.setattr(
        engine,
        "request",
        lambda path, params=None: {
            "historyId": "11",
            "history": [{"messagesDeleted": [{"message": {"id": "a"}}]}],
        },
    )
    assert run(engine)["state"] == "complete"
    assert len(store.list_transactions()) == 1
    assert store.source("test@example.test:a")["missing"] == 1


def test_oauth_uses_pkce_and_readonly_scope(store, monkeypatch):
    from urllib.parse import urlparse, parse_qs

    monkeypatch.setattr(
        store.vault,
        "read",
        lambda name: (
            encode({"client_id": "example.apps.googleusercontent.com", "client_secret": "test"})
            if name == "oauth-client"
            else None
        ),
    )
    auth = GmailAuth(store, store.vault, "http://127.0.0.1:8765")
    query = parse_qs(urlparse(auth.start()).query)
    assert query["scope"] == ["https://www.googleapis.com/auth/gmail.readonly"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8765/api/auth/callback"]
    assert len(query["state"][0]) >= 32
    with pytest.raises(ValueError):
        auth.complete("wrong", "code")


def test_body_excluded_by_parser_is_not_retained(store, monkeypatch):
    store.set_setting("bound_mailbox", "test@example.test")
    store.set_setting("history_id", "10")
    store.set_setting("gmail_scan_policy", POLICY_VERSION)
    engine = SyncEngine(store, FakeAuth())

    def request(path, params=None):
        if path == "/history":
            return {"historyId": "11", "history": [{"messagesAdded": [{"message": {"id": "a"}}]}]}
        return gmail_message(params=params, body="Your transaction has failed. Please try again.")

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "complete"
    assert store.list_sources() == []
    assert store.list_transactions() == []


def test_full_body_failure_retries_without_advancing_checkpoint(store, monkeypatch):
    store.set_setting("bound_mailbox", "test@example.test")
    store.set_setting("history_id", "10")
    store.set_setting("gmail_scan_policy", POLICY_VERSION)
    engine = SyncEngine(store, FakeAuth())
    formats = []
    failed = False

    def request(path, params=None):
        nonlocal failed
        if path == "/history":
            return {"historyId": "11", "history": [{"messagesAdded": [{"message": {"id": "a"}}]}]}
        formats.append(params["format"])
        if params["format"] == "full" and not failed:
            failed = True
            raise ValueError("Temporary fetch failure")
        return gmail_message(params=params)

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "failed"
    assert store.get_setting("history_id") == "10"
    assert run(engine, "retry")["state"] == "complete"
    assert formats == ["full", "full"]
    assert store.get_setting("history_id") == "11"
    assert len(store.list_transactions()) == 1


def test_cached_ingestion_finishes_export_reconciliation_on_resume(store, monkeypatch):
    store.set_setting("bound_mailbox", "test@example.test")
    store.set_setting("history_id", "10")
    store.set_setting("gmail_scan_policy", POLICY_VERSION)
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
    with store.tx() as db:
        db.execute("INSERT INTO jobs(id,state,started_at) VALUES('old','running','2026-09-01')")
        db.execute("INSERT INTO queue(message_id,job_id) VALUES('a','old')")
    # Simulate a process stopping after the source and addition were committed,
    # before the export merge and done checkpoint.
    store.ingest(email("a"), "test@example.test", sync_job_id="old")
    assert len(store.list_transactions()) == 2
    engine = SyncEngine(store, FakeAuth())

    def request(path, params=None):
        assert path == "/history", "Cached messages must not be downloaded again"
        return {"historyId": "11", "history": []}

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "complete"
    assert len(store.list_transactions()) == 1
    assert store.transaction(original)["category"] == "Groceries"
    assert store.transaction(original)["is_new"] is False
    assert store.sync_summary("old") == {"added": 0, "unseen": 0}
    assert store.sync_summary("job") == {"added": 0, "unseen": 0}


@pytest.mark.parametrize("mode", ["full", "history", "resume"])
def test_all_sync_paths_scan_broadly_and_retain_only_transactions(store, monkeypatch, mode):
    store.set_setting("bound_mailbox", "test@example.test")
    if mode != "full":
        store.set_setting("history_id", "10")
        store.set_setting("gmail_scan_policy", POLICY_VERSION)
    messages = {
        "transaction": gmail_message("transaction"),
        "custom": gmail_message("custom", subject="Your update", sender="shop@custom.test"),
        "reply": gmail_message("reply", subject="Re: Details", sender="owner@gmail.com"),
        "promotion-category": gmail_message("promotion-category", subject="Latest from your store"),
        "social-category": gmail_message("social-category", subject="An update for you"),
        "personal": gmail_message("personal", subject="Family plans", body="Let's meet on Sunday."),
        "unknown": gmail_message(
            "unknown", subject="Payment discussion", body="Let's discuss our payment options."
        ),
        "otp": gmail_message("otp", subject="Your OTP for purchase", body="Your OTP is 123456."),
    }
    transaction_ids = ("transaction", "custom", "reply", "promotion-category", "social-category")
    for index, mid in enumerate(transaction_ids):
        body = email()["body"].replace("123456789012", f"9876543210{index:02d}")
        messages[mid]["payload"]["body"]["data"] = base64.urlsafe_b64encode(body.encode()).decode()
    messages["reply"]["payload"]["headers"].append({"name": "In-Reply-To", "value": "<previous>"})
    messages["promotion-category"]["labelIds"] = ["CATEGORY_PROMOTIONS"]
    messages["social-category"]["labelIds"] = ["CATEGORY_SOCIAL"]
    if mode == "resume":
        with store.tx() as db:
            db.executemany(
                "INSERT INTO queue(message_id,job_id) VALUES(?,'old-job')",
                [(message_id,) for message_id in messages],
            )
    engine = SyncEngine(store, FakeAuth())
    fetched = []

    def request(path, params=None):
        if path == "/profile":
            return {"historyId": "10"}
        if path == "/messages":
            assert "subject:" not in params["q"]
            assert "from:" not in params["q"]
            assert "category:" not in params["q"]
            return {"messages": [{"id": message_id} for message_id in messages]}
        if path == "/history":
            return {
                "historyId": "11",
                "history": [{"messagesAdded": [{"message": {"id": mid}} for mid in messages]}]
                if mode == "history"
                else [],
            }
        assert params["format"] == "full"
        message_id = path.rsplit("/", 1)[1]
        fetched.append(message_id)
        return messages[message_id]

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "complete"
    assert sorted(fetched) == sorted(messages)
    assert {source["id"] for source in store.list_sources()} == {
        "test@example.test:" + mid for mid in transaction_ids
    }
    assert len(store.list_transactions()) == len(transaction_ids)
    assert store.get_setting("history_id") == "11"


@pytest.mark.parametrize(
    "message",
    [
        None,
        {"id": "a", "payload": None},
        {
            "id": "a",
            "payload": {"headers": [{"name": "Subject", "value": 3}]},
        },
        {"id": "a", "payload": {"body": {"data": "a"}, "mimeType": "text/plain"}},
        gmail_message("a", body="INR 0 debited from account XX1234 at SHOP. UPI Ref: 123456789012"),
    ],
)
def test_missing_or_malformed_messages_leave_no_source_or_issue(store, monkeypatch, message):
    store.set_setting("bound_mailbox", "test@example.test")
    store.set_setting("history_id", "10")
    store.set_setting("gmail_scan_policy", POLICY_VERSION)
    engine = SyncEngine(store, FakeAuth())

    def request(path, params=None):
        if path == "/history":
            return {"historyId": "11", "history": [{"messagesAdded": [{"message": {"id": "a"}}]}]}
        assert params["format"] == "full"
        return message

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "complete"
    assert store.list_sources() == []
    assert store.db.execute("SELECT COUNT(*) FROM issues").fetchone()[0] == 0
    assert store.db.execute("SELECT status FROM queue WHERE message_id='a'").fetchone()[0] == "done"
    assert store.get_setting("history_id") == "11"


def test_policy_upgrade_backfills_done_ids_once_and_skips_cached_sources(store, monkeypatch):
    store.set_setting("bound_mailbox", "test@example.test")
    store.set_setting("history_id", "old-history")
    store.ingest(email("cached"), "test@example.test")
    with store.tx() as db:
        db.executemany(
            "INSERT INTO queue(message_id,job_id,status) VALUES(?,'old-job','done')",
            [(mid,) for mid in ("cached", "missed", "out-of-window")],
        )
    engine = SyncEngine(store, FakeAuth())
    calls = []

    def request(path, params=None):
        calls.append((path, params))
        if path == "/profile":
            return {"historyId": "fresh-baseline"}
        if path == "/messages":
            return {"messages": [{"id": "cached"}, {"id": "missed"}]}
        if path == "/messages/missed":
            return gmail_message(
                "missed", params, subject="Information for you", sender="shop@custom.test"
            )
        if path == "/history":
            assert params["startHistoryId"] != "old-history"
            return {"historyId": "finished", "history": []}
        raise AssertionError(path)

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "complete"
    assert store.get_setting("gmail_scan_policy") == POLICY_VERSION
    assert {s["id"] for s in store.list_sources()} == {
        "test@example.test:cached",
        "test@example.test:missed",
    }
    assert (
        store.db.execute("SELECT status FROM queue WHERE message_id='out-of-window'").fetchone()[0]
        == "done"
    )
    assert run(engine, "again")["state"] == "complete"
    assert sum(path == "/messages" for path, _ in calls) == 1
    assert sum(path == "/messages/missed" for path, _ in calls) == 1


def test_policy_upgrade_revisits_old_unlinked_review_sources(store, monkeypatch):
    store.set_setting("bound_mailbox", "test@example.test")
    store.set_setting("history_id", "old-history")
    store.ingest(email("missed", body="Let's discuss our payment options."), "test@example.test")
    assert store.source("test@example.test:missed")["status"] == "review"
    with store.tx() as db:
        db.execute("INSERT INTO queue(message_id,job_id,status) VALUES('missed','old','done')")
    engine = SyncEngine(store, FakeAuth())
    fetched = []

    def request(path, params=None):
        if path == "/profile":
            assert store.list_sources() == []
            return {"historyId": "fresh-baseline"}
        if path == "/messages":
            return {"messages": [{"id": "missed"}]}
        if path == "/history":
            return {"historyId": "finished", "history": []}
        fetched.append(path)
        return gmail_message("missed", params, subject="Your update")

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "complete"
    assert fetched == ["/messages/missed"]
    assert store.source("test@example.test:missed")["status"] == "parsed"
    assert len(store.list_transactions()) == 1


def test_strict_backfill_migration_keeps_window_and_baseline(store, monkeypatch):
    store.set_setting("bound_mailbox", "test@example.test")
    store.set_setting(
        "backfill",
        {
            "baseline": "original-baseline",
            "window": "after:100 before:200",
            "queries": ["after:100 before:200 subject:transaction"],
            "query_index": 0,
            "page": "strict-page",
            "filter_policy": 1,
            "months": SYNC_MONTHS,
        },
    )
    with store.tx() as db:
        db.execute("INSERT INTO queue(message_id,job_id,status) VALUES('missed','old','done')")
    engine = SyncEngine(store, FakeAuth())

    def request(path, params=None):
        if path == "/messages":
            assert params["q"] == "after:100 before:200 -in:spam -in:trash -in:sent -in:drafts"
            assert "pageToken" not in params
            return {"messages": [{"id": "missed"}]}
        if path == "/messages/missed":
            return gmail_message("missed", params, subject="Your update")
        if path == "/history":
            assert params["startHistoryId"] == "original-baseline"
            return {"historyId": "finished", "history": []}
        raise AssertionError(path)

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "complete"
    assert len(store.list_transactions()) == 1
    assert store.get_setting("gmail_scan_policy") == POLICY_VERSION
    assert store.get_setting("backfill") is None


def test_interrupted_policy_rescan_resumes_saved_page(store, monkeypatch):
    store.set_setting("bound_mailbox", "test@example.test")
    store.set_setting("history_id", "old-history")
    with store.tx() as db:
        db.executemany(
            "INSERT INTO queue(message_id,job_id,status) VALUES(?,'old-job','done')",
            [("a",), ("b",)],
        )
    engine = SyncEngine(store, FakeAuth())
    pages, fetched = [], []
    failed = False

    def request(path, params=None):
        nonlocal failed
        if path == "/profile":
            return {"historyId": "fresh-baseline"}
        if path == "/messages":
            page = params.get("pageToken")
            pages.append(page)
            if not page:
                return {"messages": [{"id": "a"}], "nextPageToken": "second"}
            if not failed:
                failed = True
                raise ValueError("Temporary discovery failure")
            return {"messages": [{"id": "b"}]}
        if path.startswith("/messages/"):
            mid = path.rsplit("/", 1)[1]
            fetched.append(mid)
            return gmail_message(mid, params, subject="Your update")
        if path == "/history":
            return {"historyId": "finished", "history": []}
        raise AssertionError(path)

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "failed"
    assert store.get_setting("gmail_scan_policy") is None
    assert store.get_setting("history_id") == "old-history"
    assert store.get_setting("backfill")["page"] == "second"
    assert store.get_setting("backfill")["months"] == SYNC_MONTHS
    assert run(engine, "retry")["state"] == "complete"
    assert pages == [None, "second", "second"]
    assert fetched == ["a", "b"]
    assert store.get_setting("gmail_scan_policy") == POLICY_VERSION


@pytest.mark.parametrize("mode", ["history", "full"])
def test_spam_trash_sent_and_drafts_do_not_enter_ledger(store, monkeypatch, mode):
    store.set_setting("bound_mailbox", "test@example.test")
    store.set_setting("gmail_scan_policy", POLICY_VERSION)
    if mode == "history":
        store.set_setting("history_id", "10")
    engine = SyncEngine(store, FakeAuth())
    labels = ("SPAM", "TRASH", "SENT", "DRAFT")
    fetched = []

    def request(path, params=None):
        if path == "/profile":
            return {"historyId": "10"}
        if path == "/messages":
            return {"messages": [{"id": label} for label in labels]}
        if path == "/history":
            return {
                "historyId": "11",
                "history": [
                    {
                        "messagesAdded": [
                            {"message": {"id": label, "labelIds": [label]}} for label in labels
                        ]
                    }
                ]
                if mode == "history"
                else [],
            }
        mid = path.rsplit("/", 1)[1]
        fetched.append(mid)
        message = gmail_message(mid, params)
        message["labelIds"] = [mid]
        return message

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "complete"
    assert store.list_sources() == []
    assert store.list_transactions() == []
    assert fetched == ([] if mode == "history" else list(labels))


def freeze_gmail_time(monkeypatch, date):
    current = datetime.fromisoformat(date + "T14:35:20").replace(tzinfo=IST)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)

    monkeypatch.setattr("backend.gmail.datetime", FrozenDateTime)
    return current


@pytest.mark.parametrize(
    ("today", "expected_cutoff"),
    [
        ("2026-09-15", "2026-03-15"),
        ("2026-08-31", "2026-02-28"),
        ("2024-08-31", "2024-02-29"),
        ("2026-03-31", "2025-09-30"),
        ("2026-01-31", "2025-07-31"),
    ],
)
def test_full_sync_uses_six_calendar_months(store, monkeypatch, today, expected_cutoff):
    current = freeze_gmail_time(monkeypatch, today)
    cutoff = datetime.fromisoformat(expected_cutoff + "T14:35:20").replace(tzinfo=IST)
    store.set_setting("bound_mailbox", "test@example.test")
    engine = SyncEngine(store, FakeAuth())
    queries = []

    def request(path, params=None):
        if path == "/profile":
            return {"historyId": "baseline"}
        if path == "/messages":
            queries.append(params["q"])
            assert store.get_setting("backfill")["months"] == 6
            return {"messages": []}
        if path == "/history":
            return {"historyId": "finished", "history": []}
        raise AssertionError(path)

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "complete"
    assert queries == [
        f"after:{int(cutoff.timestamp())} before:{int(current.timestamp()) + 1}"
        " -in:spam -in:trash -in:sent -in:drafts"
    ]


@pytest.mark.parametrize("saved_months", [None, 12])
def test_old_backfill_restarts_six_month_window_and_clears_pending_only(
    store, monkeypatch, saved_months
):
    current = freeze_gmail_time(monkeypatch, "2026-09-15")
    cutoff = current.replace(month=3)
    store.set_setting("bound_mailbox", "test@example.test")
    store.set_setting("gmail_scan_policy", POLICY_VERSION)
    store.ingest(email("cached"), "test@example.test")
    original = store.list_transactions()[0]["id"]
    state = {
        "baseline": "original-baseline",
        "window": "after:100 before:200",
        "queries": ["after:100 before:200 -in:spam -in:trash -in:sent -in:drafts"],
        "query_index": 0,
        "page": "stale-twelve-month-page",
        "filter_policy": POLICY_VERSION,
        "rescan": False,
    }
    if saved_months is not None:
        state["months"] = saved_months
    store.set_setting("backfill", state)
    with store.tx() as db:
        db.executemany(
            "INSERT INTO queue(message_id,job_id,status) VALUES(?,'old-job',?)",
            [("old-pending", "pending"), ("cached", "pending"), ("old-done", "done")],
        )
    engine = SyncEngine(store, FakeAuth())
    queries, fetched = [], []

    def request(path, params=None):
        if path == "/messages":
            queries.append(params["q"])
            assert "pageToken" not in params
            assert store.get_setting("backfill")["months"] == 6
            assert store.get_setting("backfill")["baseline"] == "original-baseline"
            assert not store.db.execute("SELECT 1 FROM queue WHERE status='pending'").fetchall()
            assert (
                store.db.execute("SELECT status FROM queue WHERE message_id='old-done'").fetchone()[
                    0
                ]
                == "done"
            )
            assert store.source("test@example.test:cached")["status"] == "parsed"
            return {"messages": [{"id": "fresh"}, {"id": "cached"}]}
        if path == "/messages/fresh":
            fetched.append(path)
            return gmail_message("fresh", params)
        if path == "/history":
            assert params["startHistoryId"] == "original-baseline"
            return {"historyId": "finished", "history": []}
        raise AssertionError(path)

    monkeypatch.setattr(engine, "request", request)
    assert run(engine)["state"] == "complete"
    assert queries == [
        f"after:{int(cutoff.timestamp())} before:{int(current.timestamp()) + 1}"
        " -in:spam -in:trash -in:sent -in:drafts"
    ]
    assert fetched == ["/messages/fresh"]
    assert store.transaction(original)["id"] == original
    assert {source["id"] for source in store.list_sources()} == {
        "test@example.test:cached",
        "test@example.test:fresh",
    }
    assert store.db.execute("SELECT 1 FROM queue WHERE message_id='old-pending'").fetchone() is None
    assert (
        store.db.execute("SELECT status FROM queue WHERE message_id='old-done'").fetchone()[0]
        == "done"
    )
    assert store.get_setting("backfill") is None
