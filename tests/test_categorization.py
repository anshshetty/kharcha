import json

import pytest

from backend.categorization import apply_merchant_default
from backend.store import Store, encode
from .conftest import email


def payment(name, **changes):
    return {
        "date": "2026-08-01",
        "direction": "debit",
        "kind": "purchase",
        "amount_minor": 50000,
        "currency": "INR",
        "counterparty": name,
        "category": "Uncategorized",
        **changes,
    }


@pytest.mark.parametrize(
    "name,category",
    [
        ("ZARA INDITEX TRENT RET", "Shopping"),
        ("Zara", "Shopping"),
        ("SWIGGY PVT LTD FOOD1", "Food & dining"),
        ("SWIGGYFOOD", "Food & dining"),
        ("swiggy@icici", "Food & dining"),
        ("Swiggy Instamart", "Groceries"),
        ("SWIGGY PVT LTD INSTAMART", "Groceries"),
        ("Blinkit", "Groceries"),
        ("ZEPTONOW", "Groceries"),
        ("Zepto Cafe", "Food & dining"),
        ("PYU*MYNTRA", "Shopping"),
        ("Amazon", "Shopping"),
        ("AMAZON PAY IN E COMMERCE", "Shopping"),
        ("Amazon Fresh", "Groceries"),
        ("Amazon Prime", "Subscriptions"),
        ("Bookmyshow", "Entertainment"),
        ("PVR LIMITED", "Entertainment"),
        ("MAKEMYTRIP", "Travel"),
        ("CREDPAYREDBUS", "Travel"),
        ("BHARTI AIRTEL LIMIT", "Utilities"),
        ("Apollo Pharmacy", "Health"),
        ("AMAZON PAY IN GROCERY", "Groceries"),
        ("UPI_ZOMATOFOOD", "Food & dining"),
        ("RAZ*Swiggy", "Food & dining"),
        ("WWW SWIGGY COM", "Food & dining"),
        ("UPI_NANDUS RAMAMURTHYN", "Groceries"),
        ("UPI_SAI RADHA PHARMA I", "Health"),
        ("NIAMY INTERACTIVE P", "Travel"),
        ("PROFILE SALON RAMAM", "Personal care"),
        ("UPI_CINEPLEX PRIVATE L", "Entertainment"),
    ],
)
def test_known_merchant_descriptors(name, category):
    categorized = apply_merchant_default(payment(name))
    assert categorized["category"] == category
    assert categorized["category_inference"]["reason"]


@pytest.mark.parametrize(
    "name",
    [
        "Amazon Pay",
        "Amazon Pay Wallet",
        "Paytm",
        "Razorpay",
        "CRED Club",
        "UPI-K-123456789012-SWI",
        "UPI-K-123456789012-ZEP",
        "Zara Example",
        "zara.example@ybl",
        "swiggyfan@ybl",
        "Bizarre Store",
        "Someone paid for Amazon",
        "Amazonia",
        "Unknown recipient",
    ],
)
def test_unknown_or_ambiguous_names_are_not_guessed(name):
    assert apply_merchant_default(payment(name))["category"] == "Uncategorized"


@pytest.mark.parametrize("code,category", [("SWI", "Food & dining"), ("ZEP", "Groceries")])
def test_supported_kotak_abbreviations_are_labeled_as_inferences(code, category):
    t = apply_merchant_default(payment(f"UPI-K-123456789012-{code}", account="KOTAK •1234"))
    assert t["category"] == category
    assert t["category_inference"]["basis"] == "bank_abbreviation"
    assert (
        apply_merchant_default(payment("UPI-K-123456789012-CRE", account="KOTAK •1234"))["category"]
        == "Uncategorized"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "emi", "category": "EMIs"},
        {"category": "Family & gifts"},
        {
            "kind": "person_payment",
            "counterparty_key": "person:confirmed",
            "identity_confirmed": True,
        },
    ],
)
def test_defaults_preserve_treatment_and_person_identity(changes):
    original = payment("Zara", **changes)
    result = apply_merchant_default(original)
    assert result["category"] == original["category"]
    assert result["kind"] == original["kind"]


@pytest.mark.parametrize(
    "kind,direction,category",
    [
        ("income", "credit", "Income"),
        ("card_repayment", "debit", "Card repayments"),
        ("card_repayment", "credit", "Card repayments"),
        ("investment", "debit", "Investments"),
        ("own_transfer", "debit", "Own transfers"),
        ("wallet_funding", "debit", "Wallet funding"),
    ],
)
def test_non_spending_types_have_categories_without_changing_cashflow(
    store, kind, direction, category
):
    t = store.create_manual(payment("Zara", kind=kind, direction=direction))
    assert t["category"] == category
    assert t["category_source"] == "automatic"
    assert t["kind"] == kind
    assert t["spend_minor"] == 0


def test_historical_salary_and_other_income_backfill(store):
    ids = [
        store.create_manual(payment(name, kind="income", direction="credit"))["id"]
        for name in ("Example Employer", "Unknown sender")
    ]
    with store.tx() as db:
        for id in ids:
            d = json.loads(
                db.execute("SELECT data FROM transactions WHERE id=?", (id,)).fetchone()[0]
            )
            d["category"] = "Uncategorized"
            d.pop("category_inference", None)
            db.execute("UPDATE transactions SET data=? WHERE id=?", (encode(d), id))
    assert store.categorize_existing() == 2
    assert all(store.transaction(id)["category"] == "Income" for id in ids)
    assert store.categorize_existing() == 0


def test_changing_unrecognized_payment_to_income_assigns_income(store):
    t = store.create_manual(payment("Unknown sender"))
    t = store.update(t["id"], {"kind": "income", "direction": "credit"})
    assert t["category"] == "Income"
    assert t["category_source"] == "automatic"
    t = store.update(t["id"], {"kind": "purchase", "direction": "debit", "counterparty": "Swiggy"})
    assert t["category"] == "Food & dining"


def test_explicit_bank_card_bill_payment_is_not_an_expense(store):
    message = email(
        body="Your A/c no. XX1234 has been debited with INR 4000.00 on 01-08-2026 12:00:00 IST by CreditCard Payment XX 5678."
    )
    message["sender"] = "alerts@axis.bank.in"
    store.ingest(message)
    t = store.list_transactions()[0]
    assert (t["kind"], t["category"], t["spend_minor"]) == ("card_repayment", "Card repayments", 0)
    store.reparse()
    assert len(store.list_transactions()) == 1
    assert store.list_transactions()[0]["spend_minor"] == 0


@pytest.mark.parametrize(
    "evidence,category,basis",
    [
        ("Item: Rice 5kg\nItem: Lentils 1kg", "Groceries", "item_details"),
        ("Product: Mathematics textbook", "Education", "item_details"),
        ("Item: Prescription medicine", "Health", "item_details"),
        ("Item: Milk 1L\nItem: Headphones", "Shopping", "merchant"),
        ("Item: Rice\nItem: Unknown item", "Shopping", "merchant"),
        ("Item: Rice cooker", "Shopping", "merchant"),
        ("Offers on rice, milk and textbooks!", "Shopping", "merchant"),
    ],
)
def test_marketplace_uses_explicit_consistent_item_details(evidence, category, basis):
    result = apply_merchant_default(payment("Amazon", evidence=evidence))
    assert result["category"] == category
    assert result["category_inference"]["basis"] == basis


def test_parser_preserves_named_merchant_beside_gateway_vpa(store):
    store.ingest(
        email(
            body="INR 500 spent at SWIGGY FOOD on 01/08/2026. Paid via gateway.42@ybl. UPI Ref: 123456789012"
        )
    )
    t = store.list_transactions()[0]
    assert t["category"] == "Food & dining"
    assert t["counterparty"] == "SWIGGY FOOD"
    assert t["counterparty_key"] == "vpa:gateway.42@ybl"
    assert t["category_source"] == "automatic"


def test_manual_exact_and_merchant_rules_beat_defaults(store):
    merchant = store.create_manual(payment("Zara", category="Shopping"))
    store.save_rule(merchant["id"], "merchant", [])
    exact = store.create_manual(
        payment(
            "Zara",
            category="Family & gifts",
            counterparty_key="vpa:zara@icici",
            identity_confirmed=True,
        )
    )
    store.save_rule(exact["id"], "exact", [])
    matched = store.create_manual(
        payment("Zara", counterparty_key="vpa:zara@icici", identity_confirmed=True)
    )
    assert matched["category"] == "Family & gifts"
    assert matched["category_source"] == "rule"
    other_amount = store.create_manual(
        payment(
            "Zara", amount_minor=12300, counterparty_key="vpa:zara@icici", identity_confirmed=True
        )
    )
    assert other_amount["category"] == "Shopping"
    manual = store.create_manual(payment("Zara", category="Other"))
    assert manual["category"] == "Other"
    assert manual["category_source"] == "manual"


def test_existing_backfill_is_persistent_idempotent_and_preserves_user_choices(store):
    ids = []
    for _ in range(5):
        ids.append(store.create_manual(payment("Unrecognized shop"))["id"])
    with store.tx() as db:
        for id in ids:
            data = json.loads(
                db.execute("SELECT data FROM transactions WHERE id=?", (id,)).fetchone()[0]
            )
            data["counterparty"] = "Swiggy"
            db.execute("UPDATE transactions SET data=? WHERE id=?", (encode(data), id))
    store.update(ids[1], {"category": "Family & gifts"})
    store.update(ids[2], {"category": "Uncategorized"})
    store.update(ids[3], {"notes": "A note does not pin a category"})
    # A saved rule is intentionally future-only and must not apply on restart.
    reference = store.create_manual(payment("Swiggy", category="Other"))
    store.save_rule(reference["id"], "merchant", [])
    with store.tx() as db:
        data = json.loads(
            db.execute("SELECT data FROM transactions WHERE id=?", (ids[4],)).fetchone()[0]
        )
        data.update(category="Other", rule_id="previous-rule")
        db.execute("UPDATE transactions SET data=? WHERE id=?", (encode(data), ids[4]))
    old_spend = sum(t["spend_minor"] for t in store.list_transactions())
    restarted = Store(store.path, store.vault)
    try:
        assert [restarted.transaction(id)["category"] for id in ids] == [
            "Food & dining",
            "Family & gifts",
            "Uncategorized",
            "Food & dining",
            "Other",
        ]
        assert restarted.categorize_existing() == 0
        assert sum(t["spend_minor"] for t in restarted.list_transactions()) == old_spend
    finally:
        restarted.close()


def test_confirmed_person_alias_blocks_merchant_guess(store):
    store.add_alias("Zara", "Zara", "person:zara")
    t = store.create_manual(payment("Zara", kind="person_payment"))
    assert t["category"] == "Uncategorized"


def test_merchant_correction_recomputes_default_and_survives_reparse(store):
    store.ingest(email(body="INR 500 spent at Swiggy on 01/08/2026."))
    t = store.list_transactions()[0]
    store.update(t["id"], {"counterparty": "Zara"})
    assert store.transaction(t["id"])["category"] == "Shopping"
    store.reparse()
    assert store.transaction(t["id"])["category"] == "Shopping"
    store.update(t["id"], {"category": "Family & gifts"})
    store.update(t["id"], {"counterparty": "Blinkit"})
    store.reparse()
    assert store.transaction(t["id"])["category"] == "Family & gifts"


def test_more_detailed_duplicate_receipt_refines_category_without_changing_total(store):
    text = "INR 500 debited from account XX1234 at AMAZON on 01/08/2026. UPI Ref: 123456789012"
    store.ingest(email("bank", text))
    store.ingest(email("receipt", text + "\nItem: Mathematics textbook"))
    assert len(store.list_transactions()) == 1
    t = store.list_transactions()[0]
    assert (t["category"], t["spend_minor"]) == ("Education", 50000)
    store.reparse()
    assert store.transaction(t["id"])["category"] == "Education"
    store.update(t["id"], {"category": "Family & gifts"})
    store.reparse()
    assert store.transaction(t["id"])["category"] == "Family & gifts"


def test_export_defaults_and_payment_gateway_is_not_card_bill(store):
    payload = {
        "transactions": [
            {
                "id": "redbus",
                "date": "2026-08-01",
                "kind": "Bank debit",
                "amount": 500,
                "detail": "CREDPAYREDBUS",
            }
        ]
    }
    store.import_export(payload)
    t = store.list_transactions()[0]
    assert (t["category"], t["spend_minor"]) == ("Travel", 50000)
    # Simulate the old import version and upgrade it on restart/backfill.
    with store.tx() as db:
        data = json.loads(
            db.execute("SELECT data FROM transactions WHERE id=?", (t["id"],)).fetchone()[0]
        )
        data.update(kind="card_repayment", category="Uncategorized")
        data.pop("category_inference")
        db.execute("UPDATE transactions SET data=? WHERE id=?", (encode(data), t["id"]))
    assert store.categorize_existing() == 1
    assert store.transaction(t["id"])["spend_minor"] == 50000
    assert store.import_export(payload) == 0


def test_new_duplicate_evidence_does_not_back_apply_future_only_rule(store):
    text = "INR 500 debited from account XX1234 at AMAZON on 01/08/2026. UPI Ref: 123456789012"
    store.ingest(email("bank", text))
    original = store.list_transactions()[0]
    reference = store.create_manual(payment("AMAZON", category="Family & gifts"))
    store.save_rule(reference["id"], "merchant", [])
    store.ingest(email("receipt", text + "\nItem: Mathematics textbook"))
    assert store.transaction(original["id"])["category"] == "Shopping"
    store.reparse()
    assert store.transaction(original["id"])["category"] == "Shopping"
