import pytest
from backend.store import Store
from .conftest import email


def add(store, **overrides):
    return store.create_manual(
        {
            "date": "2026-08-01",
            "direction": "debit",
            "kind": "purchase",
            "amount_minor": 100000,
            "currency": "INR",
            "counterparty": "Example Shop",
            "account": "TEST •1234",
            "category": "Shopping",
            **overrides,
        }
    )


def spend(store, month="2026-08"):
    return store.report(month)["totals"]["spend_minor"]


def test_three_alerts_one_purchase(store):
    for id in ["a", "b", "c"]:
        store.ingest(email(id), "test@example.test")
    assert len(store.list_transactions()) == 1
    assert spend(store) == 50000
    assert len(store.transaction(store.list_transactions()[0]["id"])["sources"]) == 3


def test_same_person_amount_not_duplicate(store):
    store.ingest(email("a"))
    store.ingest(email("b", email()["body"].replace("123456789012", "123456789099")))
    assert spend(store) == 100000
    assert len(store.list_transactions()) == 2


def test_no_reference_never_auto_merges(store):
    body = "INR 500 debited from account XX1234 at EXAMPLE SHOP on 01/08/2026."
    store.ingest(email("a", body))
    store.ingest(email("b", body))
    assert len(store.list_transactions()) == 2
    assert any(
        i["kind"] == "possible_duplicate" for t in store.list_transactions() for i in t["issues"]
    )


def test_card_repayment_excluded(store):
    add(store)
    add(store, kind="card_repayment")
    assert spend(store) == 100000


def test_emi_original_and_bill_not_counted_twice(store):
    add(store, kind="financed_purchase", amount_minor=6000000)
    add(store, kind="emi", amount_minor=1000000)
    add(store, kind="card_repayment", amount_minor=1000000)
    assert spend(store) == 1000000


def test_refund_reduces_receipt_month(store):
    purchase = add(store)
    refund = add(store, date="2026-09-01", kind="refund", direction="credit", amount_minor=20000)
    store.update(refund["id"], {"linked_to": purchase["id"]})
    assert spend(store) == 100000
    assert spend(store, "2026-09") == -20000


def test_split_and_repayment(store):
    outgoing = add(store, amount_minor=300000, kind="person_payment")
    store.update(
        outgoing["id"],
        {
            "allocations": [
                {"type": "personal", "amount_minor": 100000},
                {"type": "reimbursable", "amount_minor": 200000},
            ]
        },
    )
    returned = add(store, amount_minor=200000, kind="reimbursement", direction="credit")
    store.update(returned["id"], {"linked_to": outgoing["id"]})
    assert spend(store) == 100000


def test_overpayment_and_invalid_split_rejected(store):
    outgoing = add(store, amount_minor=300000)
    with pytest.raises(ValueError):
        store.update(
            outgoing["id"], {"allocations": [{"type": "personal", "amount_minor": 100000}]}
        )
    returned = add(store, amount_minor=200000, kind="reimbursement", direction="credit")
    with pytest.raises(ValueError):
        store.update(returned["id"], {"linked_to": outgoing["id"]})


@pytest.mark.parametrize(
    "kind",
    [
        "own_transfer",
        "investment",
        "wallet_funding",
        "cash_withdrawal",
        "card_repayment",
        "financing_adjustment",
        "financed_purchase",
        "lending",
    ],
)
def test_movements_excluded(store, kind):
    add(store, kind=kind)
    assert spend(store) == 0


def test_cash_allocation_only_counts_share(store):
    add(
        store,
        kind="cash_withdrawal",
        amount_minor=300000,
        allocations=[
            {"type": "personal", "amount_minor": 100000},
            {"type": "reimbursable", "amount_minor": 200000},
        ],
    )
    assert spend(store) == 100000


def test_foreign_currency_is_separate(store):
    add(store)
    add(store, currency="USD", amount_minor=5000)
    assert spend(store) == 100000
    assert store.report("2026-08", "USD")["totals"]["spend_minor"] == 5000


def test_restart_and_reparse_preserve_corrections(store):
    store.ingest(email())
    t = store.list_transactions()[0]
    store.update(t["id"], {"category": "Groceries", "amount_minor": 45000})
    store.reparse()
    store.ingest(email())
    assert len(store.list_transactions()) == 1
    assert spend(store) == 45000
    reopened = Store(store.path, store.vault)
    assert reopened.transaction(t["id"])["category"] == "Groceries"
    reopened.close()


def test_exact_rule_no_unconfirmed_or_other_amount_matches(store):
    t = add(
        store,
        counterparty="Example Person",
        kind="person_payment",
        counterparty_key="person:example",
        identity_confirmed=True,
    )
    store.save_rule(t["id"], "exact", [])
    for overrides, expected in [
        ({}, "Shopping"),
        ({"amount_minor": 200000}, "Uncategorized"),
        ({"currency": "USD"}, "Uncategorized"),
        ({"identity_confirmed": False}, "Uncategorized"),
        ({"counterparty_key": "person:different"}, "Uncategorized"),
    ]:
        new = add(
            store,
            counterparty="Example Person",
            kind="person_payment",
            category="Uncategorized",
            **{"counterparty_key": "person:example", "identity_confirmed": True, **overrides},
        )
        assert new["category"] == expected


def test_manual_category_wins_and_backfill_is_explicit(store):
    a = add(store, category="Groceries")
    b = add(store, category="Uncategorized")
    store.update(b["id"], {"category": "Travel"})
    preview = store.rule_preview(a["id"], "merchant")
    assert b["id"] not in [t["id"] for t in preview["matches"]]
    store.save_rule(a["id"], "merchant", [])
    assert store.transaction(b["id"])["category"] == "Travel"


def test_merge_unmerge(store):
    a = add(store)
    b = add(store)
    store.merge(b["id"], a["id"])
    assert spend(store) == 100000
    store.unmerge(b["id"])
    assert spend(store) == 200000


def test_separate_auto_merged_observation(store):
    store.ingest(email("a"))
    store.ingest(email("b"))
    t = store.transaction(store.list_transactions()[0]["id"])
    store.separate_observation(t["sources"][1]["observation_id"])
    assert spend(store) == 100000
    store.reparse()
    assert len(store.list_transactions()) == 2


def test_cache_encrypted(store):
    store.ingest(email())
    encrypted = store.db.execute("SELECT body FROM sources").fetchone()[0]
    assert b"EXAMPLE SHOP" not in encrypted
    assert "EXAMPLE SHOP" in store.source("local:abc123")["body"]


def test_import_is_idempotent(store):
    payload = {
        "transactions": [
            {
                "id": "legacy123",
                "date": "2026-08-01",
                "kind": "Bank debit",
                "amount": 50,
                "detail": "Example Person",
                "account": "TEST •1234",
            }
        ]
    }
    assert store.import_export(payload) == 1
    assert store.import_export(payload) == 0
    assert len(store.list_transactions()) == 1
    assert store.list_transactions()[0]["issues"]


def test_parent_edit_cannot_overallocate_linked_repayment(store):
    parent = add(
        store,
        amount_minor=300000,
        allocations=[
            {"type": "personal", "amount_minor": 100000},
            {"type": "reimbursable", "amount_minor": 200000},
        ],
    )
    add(
        store, kind="reimbursement", direction="credit", amount_minor=200000, linked_to=parent["id"]
    )
    with pytest.raises(ValueError):
        store.update(parent["id"], {"allocations": []})
    with pytest.raises(ValueError):
        add(store, kind="reimbursement", direction="credit", amount_minor=1, linked_to=parent["id"])


def test_cash_purchase_cannot_also_be_allocated_from_withdrawal(store):
    parent = add(store, kind="cash_withdrawal", amount_minor=300000)
    add(store, amount_minor=100000, cash_source_id=parent["id"])
    with pytest.raises(ValueError):
        store.update(parent["id"], {"allocations": [{"type": "personal", "amount_minor": 300000}]})
    assert spend(store) == 100000


def test_refund_with_matching_reference_links_to_purchase(store):
    store.ingest(email("buy"))
    store.ingest(
        email(
            "refund",
            "Refund of INR 200 credited to account XX1234 on 02/08/2026. UPI Ref: 123456789012",
        )
    )
    txs = store.list_transactions()
    refund = next(t for t in txs if t["kind"] == "refund")
    assert refund["linked_to"]
    assert spend(store) == 30000


def test_conversion_excludes_original_and_survives_reparse(store):
    store.ingest(email("buy"))
    store.ingest(
        email(
            "finance",
            "INR 500 purchase converted to EMI on account XX1234 on 01/08/2026. UPI Ref: 123456789012",
        )
    )
    assert spend(store) == 0
    store.reparse()
    assert spend(store) == 0


def test_excluded_body_is_not_cached(store):
    store.ingest(
        email("otp", "Your OTP is 123456 for an INR 500 purchase.", subject="Your payment OTP")
    )
    assert store.source("local:otp")["body"] is None


def test_unparsed_email_makes_month_provisional(store):
    add(store)
    store.ingest(email("unclear", "Your transaction amount is not clearly stated."))
    report = store.report("2026-08")
    assert report["totals"]["provisional"]
    assert report["totals"]["source_review_count"] == 1


def test_card_repayment_cannot_keep_spending_split(store):
    t = add(store, allocations=[{"type": "personal", "amount_minor": 100000}])
    with pytest.raises(ValueError):
        store.update(t["id"], {"kind": "card_repayment"})


def test_reparse_does_not_apply_future_only_rule_to_history(store):
    store.ingest(email())
    old = store.list_transactions()[0]
    example = add(store, counterparty=old["counterparty"], category="Groceries")
    store.save_rule(example["id"], "merchant", [])
    store.reparse()
    assert store.transaction(old["id"])["category"] == "Uncategorized"


def test_newest_first_import_links_refund_when_purchase_arrives(store):
    store.ingest(
        email(
            "refund",
            "Refund of INR 200 credited to account XX1234 on 02/08/2026. UPI Ref: 123456789012",
        )
    )
    store.ingest(email("buy"))
    refund = next(t for t in store.list_transactions() if t["kind"] == "refund")
    assert refund["linked_to"]
    assert not any(i["kind"] == "unlinked_event" for i in refund["issues"])
    assert spend(store) == 30000


def test_newest_first_conversion_excludes_purchase(store):
    store.ingest(
        email(
            "finance",
            "INR 500 purchase converted to EMI on account XX1234 on 01/08/2026. UPI Ref: 123456789012",
        )
    )
    store.ingest(email("buy"))
    assert spend(store) == 0
