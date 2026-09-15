import base64
import json
from pathlib import Path
import sys
import threading

import pytest

from backend.app import create_app
from backend.backup import make_backup, restore_backup
from backend.categorization import business_identity
from backend.merchant_research import (
    MerchantResearch,
    match_key,
    search_descriptor,
    validate_result,
)
from backend.store import Store
from .client import LocalClient
from .test_accounting import add


def result():
    return {
        "merchant": "Example Cloud",
        "category": "Subscriptions",
        "confidence": "medium",
        "explanation": "The service's company page connects the billing entity to its software product; the shortened descriptor remains uncertain.",
        "sources": [
            {
                "title": "Company information",
                "url": "https://example.com/company",
                "evidence": "The company operates the software service.",
            }
        ],
    }


def completed(store, tx, response=None):
    store.set_setting(
        "merchant_lookup:" + tx["id"],
        {
            "state": "complete",
            "key": match_key(tx),
            "lookup_id": "lookup-synthetic",
            "result": response or result(),
            "message": "Review the match.",
        },
    )
    return MerchantResearch(store)


def accept(research, tx, remember=True):
    return research.accept(tx["id"], {"lookup_id": "lookup-synthetic", "remember": remember})


def test_agione_is_a_sourced_inference_and_keeps_original(store):
    t = add(store, counterparty="AGIONE TE/ICICI Ban", category="Uncategorized")
    assert t["category"] == "Subscriptions"
    assert t["merchant_display"] == "Emergent"
    assert t["counterparty"] == "AGIONE TE/ICICI Ban"
    assert t["category_inference"]["confidence"] == "medium"
    assert not t["identity_confirmed"]
    assert t["category_inference"]["source_url"] == "https://emergent.sh/info"
    assert business_identity("Agione Technologies Private Limited")["confidence"] == "high"
    for descriptor in (
        "AGIONE TEA SHOP",
        "AGIONE TEXTILES",
        "agione.te@icici",
        "Emergent Transport",
    ):
        assert business_identity(descriptor) is None
    research = MerchantResearch(store)
    state = research.start(t["id"])
    assert state["state"] == "complete" and not research.active
    assert state["result"]["sources"]


def test_agione_backfill_preserves_categories_and_accounting(store):
    manual = add(store, counterparty="AGIONE TE/ICICI Ban", category="Education")
    movement = add(
        store, counterparty="AGIONE TE/ICICI Ban", category="Uncategorized", kind="own_transfer"
    )
    person = add(
        store,
        counterparty="AGIONE TE/ICICI Ban",
        category="Uncategorized",
        counterparty_key="person:synthetic",
        identity_confirmed=True,
    )
    t = add(store, counterparty="AGIONE TE/ICICI Ban", category="Uncategorized")
    with store.tx() as db:
        data = json.loads(
            db.execute("SELECT data FROM transactions WHERE id=?", (t["id"],)).fetchone()[0]
        )
        data["category"] = "Uncategorized"
        data.pop("category_inference")
        db.execute("UPDATE transactions SET data=? WHERE id=?", (json.dumps(data), t["id"]))
    store.categorize_existing()
    assert store.transaction(t["id"])["category"] == "Subscriptions"
    assert store.transaction(manual["id"])["category"] == "Education"
    assert store.transaction(movement["id"])["spend_minor"] == 0
    assert store.transaction(person["id"])["category"] == "Uncategorized"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://example.com",
        "https://127.0.0.1/x",
        "https://foo.local/x",
        "https://user:password@example.com",
        "https://example.com\\@localhost/x",
    ],
)
def test_unsafe_source_links_rejected(url):
    r = result()
    r["sources"][0]["url"] = url
    with pytest.raises(ValueError):
        validate_result(r, ["Subscriptions"])


def test_missing_evidence_and_invalid_categories_cannot_be_applied():
    for changes in (
        {"sources": []},
        {"confidence": "low"},
        {"category": "Invented category"},
        {"merchant": None},
    ):
        with pytest.raises(ValueError):
            validate_result({**result(), **changes}, ["Subscriptions"])
    unresolved = {
        **result(),
        "merchant": None,
        "category": None,
        "confidence": "low",
        "sources": [],
    }
    assert validate_result(unresolved)


def test_private_references_removed_from_web_query():
    assert search_descriptor("AGIONE TE/ICICI Ban") == "AGIONE TE"
    assert search_descriptor("UPI/123456789012/EXAMPLE CLOUD/8877665544") == "EXAMPLE CLOUD"
    with pytest.raises(ValueError):
        search_descriptor("123456789012-private.person@bank")
    with pytest.raises(ValueError):
        search_descriptor("Razorpay")


def test_accept_remembers_exact_descriptor_and_preserves_bank_evidence(store):
    t = add(store, counterparty="EXMPL CLOUD/ICICI Ban", category="Uncategorized")
    historical = add(store, counterparty=t["counterparty"], category="Uncategorized")
    research = completed(store, t)
    saved = accept(research, t)
    assert saved["category"] == "Subscriptions"
    assert saved["merchant_display"] == "Example Cloud"
    for field in (
        "counterparty",
        "counterparty_key",
        "identity_confirmed",
        "amount_minor",
        "account",
        "date",
        "kind",
        "spend_minor",
    ):
        assert saved[field] == t[field]
    assert store.transaction(historical["id"])["category"] == "Uncategorized"
    future = add(store, counterparty=t["counterparty"], category="Uncategorized")
    assert future["category"] == "Subscriptions" and future["merchant_display"] == "Example Cloud"
    other = add(store, counterparty="EXMPL CLOUD/AXIS Bank", category="Uncategorized")
    foreign = add(store, counterparty=t["counterparty"], category="Uncategorized", currency="USD")
    assert other["category"] == foreign["category"] == "Uncategorized"
    assert research.status(t["id"])["state"] == "accepted"


def test_manual_categories_saved_rules_and_identity_win(store):
    t = add(store, counterparty="EXMPL CLOUD", category="Education")
    saved = accept(completed(store, t), t)
    assert saved["category"] == "Education"
    t = add(store, counterparty="EXMPL CLOUD TWO", category="Uncategorized")
    store.update(t["id"], {"category": "Uncategorized"})
    saved = accept(completed(store, t), t)
    assert saved["category"] == "Uncategorized"
    person = add(
        store,
        counterparty="EXMPL CLOUD",
        category="Uncategorized",
        identity_confirmed=True,
        counterparty_key="person:synthetic",
    )
    assert person["category"] == "Uncategorized"
    with pytest.raises(ValueError):
        MerchantResearch(store).start(person["id"])
    with store.tx() as db:
        db.execute(
            "INSERT INTO rules VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "rule-synthetic",
                "merchant",
                None,
                "exmpl cloud",
                100000,
                "INR",
                "debit",
                "Education",
                "2026-08-01",
            ),
        )
    future = add(store, counterparty="EXMPL CLOUD", category="Uncategorized")
    assert future["category"] == "Education" and future["category_source"] == "rule"


def test_no_remember_and_forget_preserve_later_manual_corrections(store):
    t = add(store, counterparty="EXMPL CLOUD", category="Uncategorized")
    research = completed(store, t)
    accept(research, t, remember=False)
    assert (
        add(store, counterparty=t["counterparty"], category="Uncategorized")["category"]
        == "Uncategorized"
    )
    assert research.forget(t["id"])["category"] == "Uncategorized"
    research = completed(store, store.transaction(t["id"]))
    accept(research, t)
    store.update(t["id"], {"category": "Education"})
    saved = research.forget(t["id"])
    assert saved["category"] == "Education" and "merchant_identification" not in saved
    assert store.get_setting(match_key(t)) is None


def test_stale_or_unresolved_result_cannot_be_applied(store):
    t = add(store, counterparty="EXMPL CLOUD", category="Uncategorized")
    research = completed(store, t)
    store.update(t["id"], {"counterparty": "Different business"})
    with pytest.raises(ValueError):
        accept(research, t)
    assert research.status(t["id"])["state"] == "idle"
    t = store.transaction(t["id"])
    unresolved = {
        **result(),
        "merchant": None,
        "category": None,
        "confidence": "low",
        "sources": [],
    }
    with pytest.raises(ValueError):
        accept(completed(store, t, unresolved), t)
    assert store.transaction(t["id"])["category"] == "Uncategorized"


def test_lookup_api_authenticated_read_only_status_and_strict_accept(store):
    t = add(store, counterparty="AGIONE TE/ICICI Ban", category="Uncategorized")
    app = create_app(store, start_scheduler=False)
    path = "/api/transactions/" + t["id"] + "/merchant-lookup"
    with LocalClient(app) as client:
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get(path).json()["state"] == "complete"
        assert not app.state.merchant_research.active
        assert client.post(path, json={}).status_code == 403
        headers = {"X-CSRF-Token": client.get("/api/session").json()["csrf"]}
        state = client.post(path, json={}, headers=headers).json()
        bad = {"lookup_id": state["lookup_id"], "remember": "false"}
        assert client.post(path + "/accept", json=bad, headers=headers).status_code == 400
        bad["remember"] = True
        assert client.post(path + "/accept", json=bad, headers=headers).status_code == 200
        assert client.get("/api/transactions?search=Emergent").json()[0]["id"] == t["id"]


def test_codex_web_lookup_uses_only_sanitized_label(store, monkeypatch):
    research = MerchantResearch(store)
    seen = {}

    class Process:
        def __init__(self, cmd, **kwargs):
            seen["cmd"] = cmd
            self.out = Path(cmd[cmd.index("-o") + 1])
            self.returncode = 0

        def communicate(self, prompt=None, timeout=None):
            seen["prompt"] = prompt
            self.out.write_text(json.dumps(result()))

    monkeypatch.setattr("backend.merchant_research.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("backend.merchant_research.subprocess.Popen", Process)
    assert research.research("EXMPL CLOUD", ["Subscriptions"], 0) == result()
    assert 'web_search="live"' in seen["cmd"]
    assert "--ignore-user-config" in seen["cmd"] and "--ephemeral" in seen["cmd"]
    data = json.loads(seen["prompt"].split("DATA:\n")[1])
    assert data == {"merchant_descriptor": "EXMPL CLOUD", "allowed_categories": ["Subscriptions"]}


@pytest.mark.parametrize("operation", ["erase", "restore", "dismiss", "edit"])
def test_late_results_cannot_repopulate_replaced_or_changed_data(store, monkeypatch, operation):
    t = add(store, counterparty="EXMPL CLOUD", category="Uncategorized")
    backup = make_backup(store, "synthetic-backup-password")
    started, finish, done = threading.Event(), threading.Event(), threading.Event()

    def slow(self, descriptor, categories, generation):
        started.set()
        assert finish.wait(5)
        return result()

    original = MerchantResearch.run

    def run(self, *args):
        try:
            original(self, *args)
        finally:
            done.set()

    monkeypatch.setattr(MerchantResearch, "research", slow)
    monkeypatch.setattr(MerchantResearch, "run", run)
    app = create_app(store, start_scheduler=False)
    with LocalClient(app) as client:
        research = app.state.merchant_research
        research.start(t["id"])
        try:
            assert started.wait(5)
            with pytest.raises(ValueError):
                research.start(add(store, counterparty="Another Business")["id"])
            if operation in ("erase", "restore"):
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
                assert (
                    client.post("/api/" + operation, json=body, headers=headers).status_code == 200
                )
            elif operation == "dismiss":
                research.dismiss(t["id"])
            else:
                store.update(t["id"], {"counterparty": "Changed business"})
        finally:
            finish.set()
            assert done.wait(5)
        state = store.get_setting("merchant_lookup:" + t["id"], {})
        assert "result" not in state


def test_accepted_matches_survive_restart_and_backup_but_lookups_do_not(store, tmp_path):
    t = add(store, counterparty="EXMPL CLOUD", category="Uncategorized")
    accept(completed(store, t), t)
    password = "synthetic-backup-password"
    backup = make_backup(store, password)
    other = Store(tmp_path / "restored.sqlite3", store.vault)
    try:
        restore_backup(other, password, backup)
        assert other.get_setting("merchant_lookup:" + t["id"]) is None
        assert other.transaction(t["id"])["merchant_display"] == "Example Cloud"
        assert (
            add(other, counterparty=t["counterparty"], category="Uncategorized")["category"]
            == "Subscriptions"
        )
    finally:
        other.close()


def test_failed_and_interrupted_lookups_can_retry(store, monkeypatch):
    t = add(store, counterparty="EXMPL CLOUD", category="Uncategorized")
    store.set_setting("merchant_lookup:" + t["id"], {"state": "running", "key": match_key(t)})
    research = MerchantResearch(store)
    assert research.status(t["id"])["state"] == "failed"

    def failure(*args):
        raise ValueError("Lookup timed out")

    monkeypatch.setattr(research, "research", failure)
    research.run(t["id"], match_key(t), "retry", "EXMPL CLOUD", ["Subscriptions"], 0)
    assert research.status(t["id"])["message"] == "Lookup timed out"
