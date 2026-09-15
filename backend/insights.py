"""Read-only explanations of the ledger, plus validated user financial context."""

DEFAULT_CONTEXT = {
    "priorities": "",
    "commitments": "",
    "people": "",
    "notes": "",
    "project_categories": [],
    "fixed_categories": [],
    "unavoidable_categories": [],
    "monthly_target_minor": None,
    "currency": "INR",
}


def validate_context(data):
    if not isinstance(data, dict) or set(data) - set(DEFAULT_CONTEXT):
        raise ValueError("Unsupported financial context field")
    result = {**DEFAULT_CONTEXT, **data}
    for key in ("priorities", "commitments", "people", "notes"):
        if not isinstance(result[key], str) or len(result[key]) > 10000:
            raise ValueError("Context text must be under 10,000 characters")
    for key in ("project_categories", "fixed_categories", "unavoidable_categories"):
        categories = result[key]
        if (
            not isinstance(categories, list)
            or len(categories) > 100
            or any(not isinstance(x, str) or not x.strip() or len(x) > 100 for x in categories)
        ):
            raise ValueError("Use a list of up to 100 category names")
        result[key] = list(dict.fromkeys(x.strip() for x in categories))
    if set(result["fixed_categories"]) & set(result["unavoidable_categories"]):
        raise ValueError("Choose either fixed or unavoidable for each category")
    target = result["monthly_target_minor"]
    if target is not None and (type(target) is not int or not 0 < target <= 100000000000):
        raise ValueError("Monthly target must be a positive amount")
    if result["currency"] not in ("INR", "USD", "EUR", "GBP"):
        raise ValueError("Unsupported target currency")
    return result


def review_items(store):
    transactions = {t["id"]: t for t in store.list_transactions()}
    with store.lock:
        items = [
            dict(r)
            for r in store.db.execute(
                "SELECT i.*,s.subject,s.sender FROM issues i LEFT JOIN sources s ON i.source_id=s.id WHERE i.status='open'"
            )
        ]
    for t in transactions.values():
        if (
            t["category"] in ("Own transfers", "Card repayments", "Investments")
            and t["spend_minor"] > 0
        ):
            items.append(
                {
                    "id": "classification:" + t["id"],
                    "kind": "classification_conflict",
                    "message": "This category suggests a money movement, but the transaction type still counts it as spending. Confirm its type or mark it exempt.",
                    "transaction_id": t["id"],
                    "source_id": None,
                    "status": "open",
                    "created_at": t["date"],
                    "derived": True,
                }
            )
    for item in items:
        t = transactions.get(item.get("transaction_id"))
        item.update(
            amount_minor=t["amount_minor"] if t else None,
            currency=t["currency"] if t else None,
            counterparty=t["counterparty"] if t else None,
            date=t["date"] if t else None,
            spend_minor=t["spend_minor"] if t else None,
        )
    return sorted(
        items,
        key=lambda i: (
            i.get("amount_minor") is None,
            i.get("currency") != "INR",
            i.get("currency") or "",
            -(i.get("amount_minor") or 0),
        ),
    )


def brief(store, month, currency):
    from .spending_focus import expense_parts

    report = store.report(month, currency)
    context = {**DEFAULT_CONTEXT, **store.get_setting("financial_context", {})}
    all_rows = store.list_transactions()
    by_id = {t["id"]: t for t in all_rows if t["currency"] == currency}
    rows = [
        t
        for t in by_id.values()
        if t["date"].startswith(report["month"]) and t["currency"] == currency
    ]
    project = sum(
        amount
        for t in rows
        for category, _, amount in expense_parts(t, by_id)
        if category in context["project_categories"]
    )
    total = report["totals"]["spend_minor"]
    everyday = total - project
    target = context["monthly_target_minor"] if context["currency"] == currency else None
    ranked = sorted(
        (t for t in rows if t["spend_minor"] > 0), key=lambda t: t["spend_minor"], reverse=True
    )
    return {
        "month": report["month"],
        "currency": currency,
        "context": context,
        "last_sync": report["coverage"]["last_sync"],
        "spending_minor": total,
        "project_spending_minor": project,
        "everyday_spending_minor": everyday,
        "target_minor": target,
        "remaining_minor": target - everyday if target is not None else None,
        "review_count": report["totals"]["review_count"],
        "source_review_count": report["totals"]["source_review_count"],
        "provisional": report["totals"]["provisional"],
        "categories": report["categories"],
        "top_transactions": [
            {k: t.get(k) for k in ("id", "date", "counterparty", "category", "spend_minor")}
            for t in ranked[:8]
        ],
        "limitations": [
            "Email evidence is not a complete bank ledger.",
            "A target comparison is not an available bank balance.",
            "Project categories are separated for planning; total spending is unchanged.",
        ],
        "excluded_count": sum(bool(t.get("excluded")) for t in rows),
    }
