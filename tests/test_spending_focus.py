import pytest
from backend.spending_focus import spending_focus
from .test_accounting import add


def focus(store, currency="INR"):
    return spending_focus(store, currency, as_of="2026-09-13")


def test_protected_costs_and_exemptions_reconcile(store):
    # Invented round amounts exercise the accounting groups without real ledger values.
    store.set_setting(
        "financial_context",
        {
            "fixed_categories": ["Rent & home", "EMIs"],
            "unavoidable_categories": ["Home construction"],
        },
    )
    with store.tx() as db:
        db.execute("INSERT INTO categories VALUES('Home construction')")
    add(
        store,
        date="2026-09-01",
        category="Rent & home",
        amount_minor=2400000,
        allocations=[
            {"type": "personal", "amount_minor": 1500000},
            {"type": "reimbursable", "amount_minor": 900000},
        ],
    )
    add(store, date="2026-09-01", kind="emi", category="EMIs", amount_minor=1000000)
    add(store, date="2026-09-01", category="Home construction", amount_minor=500000)
    add(store, date="2026-09-01", category="Food & dining", amount_minor=200000)
    add(store, date="2026-09-01", amount_minor=90000000, excluded=True)
    f = focus(store)
    assert f["fixed_minor"] == 2500000
    assert f["unavoidable_minor"] == 500000
    assert f["other_minor"] == 200000
    assert f["spending_minor"] == f["fixed_minor"] + f["unavoidable_minor"] + f["other_minor"]
    assert [c["name"] for c in f["categories"]] == ["Food & dining"]
    assert f["other_count"] == 1


def test_split_categories_and_linked_refund_follow_original_cost_groups(store):
    store.set_setting(
        "financial_context",
        {"fixed_categories": ["Rent & home"], "unavoidable_categories": ["Home construction"]},
    )
    purchase = add(
        store,
        date="2026-09-01",
        amount_minor=10000,
        category="Other",
        allocations=[
            {"type": "personal", "category": "Rent & home", "amount_minor": 3000},
            {"type": "personal", "category": "Home construction", "amount_minor": 2000},
            {"type": "personal", "category": "Shopping", "amount_minor": 4000},
            {"type": "reimbursable", "amount_minor": 1000},
        ],
    )
    refund = add(
        store,
        date="2026-09-03",
        kind="refund",
        direction="credit",
        amount_minor=900,
        category="Uncategorized",
        counterparty="Bank refund",
    )
    store.update(refund["id"], {"linked_to": purchase["id"]})
    f = focus(store)
    assert (f["fixed_minor"], f["unavoidable_minor"], f["other_minor"]) == (2700, 1800, 3600)
    assert f["other_gross_minor"] == 4000
    assert f["other_refund_minor"] == 400
    assert f["merchants"][0]["amount_minor"] == 3600
    assert f["merchants"][0]["count"] == 1
    assert f["spending_minor"] == 8100


def test_merchant_aliases_group_without_changing_records(store):
    ids = [
        add(
            store,
            date="2026-09-01",
            counterparty=name,
            category="Food & dining",
            amount_minor=amount,
        )["id"]
        for name, amount in [
            ("SWIGGY", 100),
            ("Swiggy Bangalore", 200),
            ("UPI-K-123456789012-SWI", 300),
        ]
    ]
    store.update(ids[-1], {"account": "KOTAK •1234"})
    f = focus(store)
    assert len(f["repeat_merchants"]) == 1
    m = f["repeat_merchants"][0]
    assert (m["name"], m["count"], m["amount_minor"]) == ("Swiggy", 3, 600)
    assert set(m["transaction_ids"]) == set(ids)
    assert store.transaction(ids[1])["counterparty"] == "Swiggy Bangalore"


def test_currency_future_and_non_spending_filtered(store):
    add(store, date="2026-09-01", amount_minor=100)
    add(store, date="2026-09-01", currency="USD", amount_minor=200)
    add(store, date="2026-09-30", amount_minor=300)
    add(store, date="2026-08-30", amount_minor=400)
    add(store, date="2026-09-01", kind="card_repayment", amount_minor=500)
    assert focus(store)["other_minor"] == 100
    assert focus(store, "USD")["other_minor"] == 200
    with pytest.raises(ValueError):
        focus(store, "USD;")


def test_fully_refunded_merchant_stays_visible_without_false_spend(store):
    p = add(store, date="2026-09-01", amount_minor=100, category="Shopping")
    r = add(
        store,
        date="2026-09-02",
        amount_minor=100,
        category="Shopping",
        kind="refund",
        direction="credit",
    )
    store.update(r["id"], {"linked_to": p["id"]})
    f = focus(store)
    assert f["other_minor"] == 0
    assert f["other_gross_minor"] == f["other_refund_minor"] == 100
    assert f["merchants"][0]["amount_minor"] == 0


def test_evidence_contains_only_other_portion_and_current_ids(store):
    store.set_setting("financial_context", {"fixed_categories": ["Rent & home", "EMIs"]})
    protected = add(store, date="2026-09-01", kind="emi", category="EMIs")
    mixed = add(
        store,
        date="2026-09-01",
        amount_minor=10000,
        category="Other",
        allocations=[
            {"type": "personal", "category": "Rent & home", "amount_minor": 7000},
            {"type": "personal", "category": "Shopping", "amount_minor": 3000},
        ],
    )
    f = focus(store)
    assert [t["id"] for t in f["transactions"]] == [mixed["id"]]
    assert f["transactions"][0]["amount_minor"] == 3000
    assert protected["id"] not in f["categories"][0]["transaction_ids"]
