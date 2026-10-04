import uuid
import pytest
from backend.store import Store
from .test_accounting import add


def edit(store, tx, changes=None, scope="payee", key=None):
    changes = changes or {"category": "Groceries"}
    return store.save_edit(
        tx["id"], changes, scope, key or uuid.uuid4().hex, {k: tx.get(k) for k in changes}
    )


def test_category_and_future_rule_are_atomic(store):
    t = add(store)
    with pytest.raises(ValueError, match="Confirm this person"):
        edit(store, t, scope="person")
    assert store.transaction(t["id"])["category"] == "Shopping"
    assert store.db.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 0
    assert (
        store.db.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'edit-result:%'").fetchone()[
            0
        ]
        == 0
    )


def test_remembered_payee_survives_restart_and_new_amount(store):
    t = add(store)
    edit(store, t)
    path, vault = store.path, store.vault
    store.close()
    reopened = Store(path, vault)
    try:
        next_payment = add(reopened, category="Uncategorized", amount_minor=27000)
        assert next_payment["category"] == "Groceries"
        assert next_payment["category_source"] == "rule"
        assert (
            add(reopened, category="Uncategorized", counterparty="Different Shop")["category"]
            == "Uncategorized"
        )
        assert add(reopened, category="Uncategorized", currency="USD")["category"] != "Groceries"
        assert (
            add(reopened, category="Uncategorized", direction="credit", kind="income")["category"]
            != "Groceries"
        )
        # A later manual correction remains authoritative after restart.
        reopened.update(next_payment["id"], {"category": "Travel"})
        reopened.categorize_existing()
        assert reopened.transaction(next_payment["id"])["category"] == "Travel"
    finally:
        reopened.close()


def test_confirmed_person_can_match_different_amounts_but_not_other_people(store):
    identity = store.add_alias("Alex", "Alex", "person:alex")
    t = add(store, counterparty="Alex", kind="person_payment")
    assert t["counterparty_key"] == identity
    edit(store, t, scope="person")
    assert (
        add(
            store,
            counterparty="Alex",
            kind="person_payment",
            amount_minor=20000,
            category="Uncategorized",
        )["category"]
        == "Groceries"
    )
    assert (
        add(store, counterparty="Other Alex", kind="person_payment", category="Uncategorized")[
            "category"
        ]
        != "Groceries"
    )
    assert (
        add(store, counterparty="Alex", kind="own_transfer", category="Uncategorized")["category"]
        != "Groceries"
    )


def test_this_payment_only_does_not_create_rule(store):
    t = add(store)
    edit(store, t, scope="none")
    assert store.transaction(t["id"])["category"] == "Groceries"
    assert add(store, category="Uncategorized")["category"] == "Uncategorized"


def test_retry_is_idempotent_and_rejects_reused_identifier(store):
    t = add(store)
    key = uuid.uuid4().hex
    first = edit(store, t, key=key)
    assert edit(store, t, key=key) == first
    assert store.db.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 1
    with pytest.raises(ValueError, match="already used"):
        edit(store, t, changes={"category": "Travel"}, key=key)


def test_undo_restores_category_and_stops_future_rule_without_rewriting_history(store):
    t = add(store)
    result = edit(store, t)
    imported = add(store, category="Uncategorized")
    restored = store.undo_edit(t["id"], result["undo_id"])
    assert restored["category"] == "Shopping"
    assert store.transaction(imported["id"])["category"] == "Groceries"
    assert add(store, category="Uncategorized")["category"] == "Uncategorized"
    assert store.undo_edit(t["id"], result["undo_id"])["category"] == "Shopping"


def test_undo_refuses_to_replace_newer_edits(store):
    t = add(store)
    result = edit(store, t)
    store.update(t["id"], {"notes": "Later correction"})
    with pytest.raises(ValueError, match="changed again"):
        store.undo_edit(t["id"], result["undo_id"])
    assert store.transaction(t["id"])["notes"] == "Later correction"


def test_stale_edit_refuses_without_creating_rule(store):
    t = add(store)
    store.update(t["id"], {"category": "Travel"})
    with pytest.raises(ValueError, match="changed"):
        edit(store, t)
    assert store.transaction(t["id"])["category"] == "Travel"
    assert store.db.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 0


def test_undo_restores_automatic_category_after_treatment_change(store):
    t = add(store, counterparty="Swiggy", category="Uncategorized")
    result = edit(store, t, {"kind": "own_transfer"}, scope="none")
    assert result["transaction"]["spend_minor"] == 0
    restored = store.undo_edit(t["id"], result["undo_id"])
    assert restored["category"] == "Food & dining"
    assert restored["spend_minor"] == t["spend_minor"]


def test_edit_api_uses_existing_local_auth_and_csrf(store):
    from backend.app import create_app
    from .client import LocalClient

    t = add(store)
    with LocalClient(create_app(store, start_scheduler=False)) as client:
        payload = {
            "changes": {"category": "Groceries"},
            "remember_scope": "payee",
            "edit_id": uuid.uuid4().hex,
        }
        assert client.patch(f"/api/transactions/{t['id']}/edit", json=payload).status_code == 403
        csrf = client.get("/api/session").json()["csrf"]
        headers = {"X-CSRF-Token": csrf}
        response = client.patch(f"/api/transactions/{t['id']}/edit", json=payload, headers=headers)
        assert response.status_code == 200
        assert response.json()["transaction"]["category"] == "Groceries"
        assert (
            client.post(
                f"/api/transactions/{t['id']}/undo-edit",
                json={"edit_id": payload["edit_id"]},
                headers=headers,
            ).json()["category"]
            == "Shopping"
        )
        assert (
            client.patch(
                f"/api/transactions/{t['id']}/edit", json=[1, 2], headers=headers
            ).status_code
            == 400
        )
