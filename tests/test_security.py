import base64
import json
import sys
import threading
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from fastapi.testclient import TestClient

from backend.app import create_app, make_backup, restore_backup
from backend.local_access import LocalAccess, read_runtime, runtime_path
from backend.parser import decode_message
from backend.store import encode, now
from .client import LocalClient
from .conftest import authenticated_headers, email, transaction


def test_api_requires_private_access_for_reads_writes_and_csrf(store):
    store.ingest(email())
    app = create_app(store, start_scheduler=False)
    with TestClient(app) as stranger:
        assert stranger.get("/api/health").status_code == 200
        for path in (
            "/api/session",
            "/api/sources/local:abc123",
            "/api/export.csv",
            "/api/transactions",
        ):
            assert stranger.get(path).status_code == 401
        assert (
            stranger.get("/api/session", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        assert stranger.post("/api/erase", json={"confirmation": "DELETE"}).status_code == 401
        assert app.state.local_access.token not in stranger.get("/").text
    with LocalClient(app) as owner:
        assert owner.get("/api/sources/local:abc123").json()["body"]
        assert owner.get("/api/export.csv").status_code == 200
        csrf = owner.get("/api/session").json()["csrf"]
        assert (
            owner.post(
                "/api/categories", json={"name": "Test"}, headers={"X-CSRF-Token": csrf}
            ).status_code
            == 200
        )
    other = create_app(store, start_scheduler=False)
    with TestClient(other) as client:
        assert (
            client.get(
                "/api/session", headers={"Authorization": "Bearer " + app.state.local_access.token}
            ).status_code
            == 401
        )


def test_runtime_token_file_is_private_atomic_and_removed_only_by_owner(tmp_path):
    access = LocalAccess(tmp_path, 8765)
    assert not access.authorized("Bearer invalid-☃")
    target = tmp_path / "unrelated"
    target.write_text("keep")
    runtime_path(tmp_path, 8765).symlink_to(target)
    access.publish()
    path = runtime_path(tmp_path, 8765)
    assert not path.is_symlink()
    assert target.read_text() == "keep"
    assert path.stat().st_mode & 0o777 == 0o600
    assert read_runtime(tmp_path, 8765)["token"] == access.token
    newer = LocalAccess(tmp_path, 8765)
    newer.publish()
    access.remove()
    assert read_runtime(tmp_path, 8765)["token"] == newer.token
    path.chmod(0o644)
    with pytest.raises(ValueError):
        read_runtime(tmp_path, 8765)
    path.chmod(0o600)
    newer.remove()
    assert not path.exists()


def test_account_ownership_does_not_coerce_strings(store):
    with LocalClient(create_app(store, start_scheduler=False)) as client:
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        response = client.post(
            "/api/accounts", json={"name": "Synthetic bank", "owned": "false"}, headers=headers
        )
        assert response.status_code == 400
        assert client.get("/api/accounts").json() == []


@pytest.mark.parametrize(
    "changes",
    [
        {"excluded": "false"},
        {"avoidable": 1},
        {"identity_confirmed": "true"},
        {"notes": {}},
        {"counterparty": []},
        {"category": None},
        {"date": "2026-02-30"},
        {"amount_minor": True},
        {"amount_minor": 1.5},
        {"amount_minor": 2**53},
        {"allocations": [{"type": "personal", "amount_minor": True}]},
        {"allocations": [{"type": "personal", "amount_minor": 100000, "category": {}}]},
        {"linked_to": 123},
        {"unknown": "field"},
    ],
)
def test_invalid_edits_never_change_ledger(store, changes):
    row = store.create_manual(transaction())
    before = store.transaction(row["id"])
    with LocalClient(create_app(store, start_scheduler=False)) as client:
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        response = client.patch("/api/transactions/" + row["id"], json=changes, headers=headers)
        assert response.status_code == 400
        assert store.transaction(row["id"]) == before


def rewritten_backup(store, rewrite):
    password = "synthetic-backup-password"
    magic, salt, encrypted = make_backup(store, password).split(b"\n", 2)
    key = base64.urlsafe_b64encode(
        Scrypt(salt=base64.urlsafe_b64decode(salt), length=32, n=2**15, r=8, p=1).derive(
            password.encode()
        )
    )
    payload = json.loads(Fernet(key).decrypt(encrypted))
    rewrite(payload)
    return password, magic + b"\n" + salt + b"\n" + Fernet(key).encrypt(encode(payload).encode())


@pytest.mark.parametrize(
    "corruption", ["missing_name", "bad_boolean", "bad_context", "missing_parent"]
)
def test_malformed_restore_is_atomic(store, corruption):
    store.ingest(email())
    before = store.transaction(store.list_transactions()[0]["id"])

    def rewrite(payload):
        row = payload["tables"]["transactions"][0]
        data = json.loads(row["data"])
        if corruption == "missing_name":
            data.pop("counterparty")
        elif corruption == "bad_boolean":
            data["excluded"] = "false"
        elif corruption == "missing_parent":
            data.update(kind="refund", direction="credit", linked_to="missing")
        else:
            payload["tables"]["settings"].append(
                {"key": "financial_context", "value": encode({"notes": {}})}
            )
        row["data"] = encode(data)

    password, archive = rewritten_backup(store, rewrite)
    with pytest.raises(ValueError):
        restore_backup(store, password, archive)
    assert store.transaction(before["id"]) == before
    assert store.report("2026-08")["totals"]["spend_minor"] == before["spend_minor"]


def test_restore_failure_in_final_validation_preserves_original(store, monkeypatch):
    store.ingest(email())
    backup = make_backup(store, "synthetic-backup-password")
    extra = store.create_manual(transaction())

    def fail(_):
        raise ValueError("Synthetic failed report validation")

    monkeypatch.setattr("backend.backup.validate_ledger", fail)
    with pytest.raises(ValueError):
        restore_backup(store, "synthetic-backup-password", backup)
    assert store.transaction(extra["id"])
    assert len(store.list_transactions()) == 2


def test_restore_reauthorizes_ai_and_discards_ephemeral_previews(store):
    store.ingest(email())
    store.set_setting("ai_advisor_enabled", True)
    store.set_setting("ai_advisor_consent_version", 1)
    store.set_setting("statement_preview:example", "local-key-encrypted-data")
    backup = make_backup(store, "synthetic-backup-password")
    restore_backup(store, "synthetic-backup-password", backup)
    assert store.get_setting("ai_advisor_enabled") is False
    assert store.get_setting("ai_advisor_consent_version") is None
    assert store.get_setting("statement_preview:example") is None
    assert store.source("local:abc123")["body"]


def test_ai_consent_requires_an_authenticated_explicit_boolean(store):
    app = create_app(store, start_scheduler=False)
    with TestClient(app) as stranger:
        assert (
            stranger.put("/api/ai-advisor/preferences", json={"enabled": True}).status_code == 401
        )
    with LocalClient(app) as client:
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        assert (
            client.put(
                "/api/ai-advisor/preferences", json={"enabled": "true"}, headers=headers
            ).status_code
            == 400
        )
        assert not store.get_setting("ai_advisor_enabled", False)
        assert (
            client.put(
                "/api/ai-advisor/preferences",
                json={"enabled": True, "consent_version": 1},
                headers=headers,
            ).status_code
            == 200
        )
        assert client.get("/api/status").json()["ai_advisor_enabled"] is True
        assert (
            client.put(
                "/api/ai-advisor/preferences", json={"enabled": False}, headers=headers
            ).status_code
            == 200
        )
        assert client.get("/api/ai-advisor").json()["state"] == "paused"


def test_existing_pre_authentication_sources_are_flagged_without_changing_accounting(store):
    store.ingest(email())
    before = store.list_transactions()[0]["spend_minor"]
    with store.tx() as db:
        db.execute("UPDATE sources SET parser_version='local-6'")
    store.flag_legacy_source_authentication()
    assert store.list_transactions()[0]["spend_minor"] == before
    assert store.report("2026-08")["totals"]["source_review_count"] == 1


def bank_message(headers):
    body = "INR 500.00 has been spent on your YES BANK Credit Card ending with 1234 at EXAMPLE SHOP on 01-08-2026 at 12:00:00 PM"
    return {
        "id": "synthetic",
        "internalDate": "1785582000000",
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
            "headers": headers + [{"name": "Subject", "value": "Transaction alert"}],
        },
    }


@pytest.mark.parametrize(
    "scenario",
    ["failed", "missing", "forged_second", "wrong_domain", "untrusted_receiver", "comment_pass"],
)
def test_unverified_sender_never_adds_spending(store, scenario):
    headers = authenticated_headers("alerts@yesbank.in")
    if scenario == "missing":
        headers.pop()
    elif scenario == "forged_second":
        headers.append(dict(headers[-1]))
        headers[-2]["value"] = "mx.google.com; dmarc=fail header.from=yesbank.in"
    elif scenario == "wrong_domain":
        headers[-1]["value"] = headers[-1]["value"].replace(
            "header.from=yesbank.in", "header.from=attacker.test"
        )
    elif scenario == "untrusted_receiver":
        headers[-1]["value"] = headers[-1]["value"].replace("mx.google.com", "attacker.test")
    elif scenario == "comment_pass":
        headers[-1]["value"] = (
            "mx.google.com; spf=pass; dmarc=fail (dmarc=pass header.from=yesbank.in)"
        )
    else:
        headers[-1]["value"] = headers[-1]["value"].replace("=pass", "=fail")
    assert store.ingest(decode_message(bank_message(headers))) == "review"
    assert not store.list_transactions()
    assert "authentication" in store.list_sources()[0]["reason"]


def test_authenticated_email_keeps_provenance_through_reparse_and_restore(store):
    decoded = decode_message(bank_message(authenticated_headers("alerts@yesbank.in")))
    assert store.ingest(decoded) == "parsed"
    store.reparse()
    archive = make_backup(store, "synthetic-backup-password")
    restore_backup(store, "synthetic-backup-password", archive)
    store.reparse()
    assert len(store.list_transactions()) == 1
    assert store.source("local:synthetic")["authentication"]["verified"] is True


@pytest.mark.parametrize("operation", ["erase", "restore"])
def test_late_ai_result_cannot_repopulate_replaced_ledger(store, monkeypatch, operation):
    row = store.create_manual({**transaction(), "date": now()[:10]})
    backup = make_backup(store, "synthetic-backup-password")
    store.set_setting("ai_advisor_enabled", True)
    store.set_setting("ai_advisor_consent_version", 1)
    started, finish = threading.Event(), threading.Event()
    processes = []
    result = {
        "summary": "Private expense details",
        "patterns": [
            {
                "title": "Private purchase",
                "detail": "Private financial interpretation",
                "confidence": "high",
                "transaction_ids": [row["id"]],
            }
        ],
        "commitments": [],
        "actions": [],
        "uncertainties": [],
        "target": {"amount_minor": None, "currency": "INR", "reason": ""},
    }

    class LateProcess:
        def __init__(self, cmd, **kwargs):
            self.out = Path(cmd[cmd.index("-o") + 1])
            self.returncode = None
            self.terminated = False
            processes.append(self)

        def communicate(self, prompt=None, timeout=None):
            started.set()
            assert finish.wait(5)
            self.out.write_text(json.dumps(result))
            self.returncode = 0

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

    monkeypatch.setattr("backend.ai_advisor.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("backend.ai_advisor.subprocess.Popen", LateProcess)
    app = create_app(store, start_scheduler=False)
    with LocalClient(app) as client:
        advisor = app.state.advisor
        advisor.start(force=True)
        try:
            assert started.wait(5)
            headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
            body = (
                {"confirmation": "DELETE"}
                if operation == "erase"
                else {
                    "confirmation": "REPLACE",
                    "password": "synthetic-backup-password",
                    "file": base64.b64encode(backup).decode(),
                }
            )
            response = client.post("/api/" + operation, json=body, headers=headers)
            assert response.status_code == 200
            assert processes[0].terminated
        finally:
            finish.set()
            assert advisor.lock.acquire(timeout=5)
            advisor.lock.release()
        assert store.get_setting("ai_advisor") is None
        assert len(store.list_transactions()) == (0 if operation == "erase" else 1)


def test_ai_needs_current_notice_and_preview_stays_local(store, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail(
            "Viewing local data or disabled AI must not start a network client or AI process"
        )

    monkeypatch.setattr("httpx.Client", forbidden)
    monkeypatch.setattr("backend.ai_advisor.subprocess.Popen", forbidden)
    store.create_manual({**transaction(), "date": now()[:10]})
    # Legacy enablement without the current notice must remain paused.
    store.set_setting("ai_advisor_enabled", True)
    app = create_app(store, start_scheduler=False)
    with LocalClient(app) as client:
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        assert client.get("/api/status").json()["ai_advisor_enabled"] is False
        assert app.state.advisor.start(force=True)["state"] == "paused"
        preview = client.get("/api/ai-advisor/preview").json()
        assert preview["sampled_records"] == 1
        assert (
            client.get(
                "/api/ai-advisor/preview", headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        for consent in ({}, {"consent_version": 0}, {"consent_version": True}):
            assert (
                client.put(
                    "/api/ai-advisor/preferences",
                    json={"enabled": True, **consent},
                    headers=headers,
                ).status_code
                == 400
            )
        assert app.state.advisor.start()["state"] == "paused"
        assert not store.get_setting("ai_advisor_consent_version")
