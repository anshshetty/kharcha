"""Monthly spending facts, respecting the user's protected cost groups."""

from collections import defaultdict
import calendar
import re

from .categorization import merchant_match
from .store import now


def bucket(category, policy):
    for group in ("fixed", "unavoidable"):
        if category in policy.get(group + "_categories", []):
            return group
    return "other"


def expense_parts(t, by_id, policy=None):
    """Return (category, cost group, amount) with an exact ledger reconciliation."""
    policy = policy or {}
    amount = t["spend_minor"]
    if not amount:
        return []
    parent = by_id.get(t.get("linked_to")) if amount < 0 else None
    if parent and parent["spend_minor"] > 0:
        parts = expense_parts(parent, {}, policy)
        total = sum(p[2] for p in parts)
        # Allocate a linked refund in the original personal-category proportions.
        weights = [abs(amount) * p[2] // total for p in parts]
        remainder = abs(amount) - sum(weights)
        order = sorted(
            range(len(parts)), key=lambda i: abs(amount) * parts[i][2] % total, reverse=True
        )
        for i in order[:remainder]:
            weights[i] += 1
        return [(p[0], p[1], -weights[i]) for i, p in enumerate(parts)]
    if amount > 0 and t.get("allocations"):
        parts = [
            (a.get("category") or t["category"], a["amount_minor"])
            for a in t["allocations"]
            if a["type"] == "personal"
        ]
        if sum(p[1] for p in parts) == amount:
            return [(cat, bucket(cat, policy), value) for cat, value in parts]
    cat = t["category"]
    return [(cat, bucket(cat, policy), amount)]


def merchant_name(t):
    """Group known bank descriptors for display without changing ledger identities."""
    if str(t.get("counterparty_key", "")).startswith("person:") or t.get("identity_confirmed"):
        return t["counterparty"]
    from .merchant_research import current_identification

    identification = current_identification(t)
    if identification:
        return identification["result"]["merchant"]
    match = merchant_match(t["counterparty"])
    if match:
        return match["merchant"]
    abbreviated = re.fullmatch(r"UPI-K-\d{12}-(SWI|ZEP)", t["counterparty"], re.I)
    if abbreviated and str(t.get("account", "")).upper().startswith("KOTAK"):
        return "Swiggy" if abbreviated.group(1).upper() == "SWI" else "Zepto"
    return t["counterparty"]


def focus_period(month=None, as_of=None):
    """Use today for the current month and the last day for a completed month."""
    as_of = as_of or now()[:10]
    month = month or as_of[:7]
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month) or month.startswith("0000"):
        raise ValueError("Use YYYY-MM for the month")
    if month > as_of[:7]:
        raise ValueError("Choose the current month or an earlier month")
    if month < as_of[:7]:
        year, number = map(int, month.split("-"))
        as_of = f"{month}-{calendar.monthrange(year, number)[1]:02d}"
    return month, as_of


def spending_focus(store, currency="INR", *, rows=None, as_of=None, month=None):
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("Use a three-letter currency code")
    today = as_of or now()[:10]
    month, as_of = focus_period(month, today)
    context = store.get_setting("financial_context", {})
    policy = {key: context.get(key, []) for key in ("fixed_categories", "unavoidable_categories")}
    rows = store.list_transactions() if rows is None else rows
    by_id = {t["id"]: t for t in rows if t["currency"] == currency}
    selected = [t for t in by_id.values() if t["date"].startswith(month) and t["date"] <= as_of]
    totals = defaultdict(int)
    categories, merchants, fixed, unavoidable, evidence = {}, {}, {}, {}, {}
    other_ids, flagged_ids = set(), set()

    def record(groups, key, t, amount, category=None):
        item = groups.setdefault(
            key,
            {
                "name": key,
                "amount_minor": 0,
                "gross_minor": 0,
                "count": 0,
                "transaction_ids": [],
                "_paid_ids": set(),
            },
        )
        item["amount_minor"] += amount
        item["gross_minor"] += max(0, amount)
        if t["id"] not in item["transaction_ids"]:
            item["transaction_ids"].append(t["id"])
        if amount > 0:
            item["_paid_ids"].add(t["id"])
        item["count"] = len(item["_paid_ids"])
        if category is not None:
            item["category"] = (
                category
                if "category" not in item or item["category"] == category
                else "Multiple categories"
            )

    for t in selected:
        for cat, group, amount in expense_parts(t, by_id, policy):
            totals[group] += amount
            if group == "fixed":
                record(fixed, cat, t, amount)
            elif group == "unavoidable":
                record(unavoidable, cat, t, amount)
            if group != "other" or not amount:
                continue
            other_ids.add(t["id"])
            if t.get("issues"):
                flagged_ids.add(t["id"])
            totals["other_gross"] += max(0, amount)
            totals["other_refund"] += max(0, -amount)
            if cat in (
                "Uncategorized",
                "Other",
                "Wallet funding",
                "Own transfers",
                "Card repayments",
                "Investments",
            ):
                totals["unclassified"] += amount
            record(categories, cat, t, amount)
            # A linked refund belongs to the original merchant in the analytical view.
            merchant = by_id.get(t.get("linked_to"), t) if amount < 0 else t
            record(merchants, merchant_name(merchant), t, amount, cat)
            item = evidence.setdefault(
                t["id"],
                {
                    "id": t["id"],
                    "date": t["date"],
                    "merchant": merchant_name(merchant),
                    "amount_minor": 0,
                },
            )
            item["amount_minor"] += amount

    def finished(groups):
        return [
            {k: v for k, v in item.items() if not k.startswith("_")}
            for item in sorted(groups.values(), key=lambda x: (-x["amount_minor"], x["name"]))
        ]

    merchant_rows = finished(merchants)
    return {
        "month": month,
        "current": month == today[:7],
        "as_of": as_of,
        "currency": currency,
        "spending_minor": sum(t["spend_minor"] for t in selected),
        "fixed_minor": totals["fixed"],
        "unavoidable_minor": totals["unavoidable"],
        "other_minor": totals["other"],
        "other_gross_minor": totals["other_gross"],
        "other_refund_minor": totals["other_refund"],
        "other_count": len(other_ids),
        "unclassified_minor": totals["unclassified"],
        "categories": finished(categories),
        "merchants": merchant_rows,
        "transactions": list(evidence.values()),
        "repeat_merchants": sorted(
            [m for m in merchant_rows if m["count"] > 1],
            key=lambda x: (-x["count"], -x["amount_minor"]),
        ),
        "fixed_categories": finished(fixed),
        "unavoidable_categories": finished(unavoidable),
        "coverage": {
            "last_sync": store.get_setting("last_sync"),
            "flagged_count": len(flagged_ids),
        },
        "policy": policy,
        "group_ids": {
            "regular": sorted(other_ids),
            "fixed": sorted({id for g in fixed.values() for id in g["transaction_ids"]}),
            "unavoidable": sorted(
                {id for g in unavoidable.values() for id in g["transaction_ids"]}
            ),
        },
        "group_totals": {
            "regular": totals["other"],
            "fixed": totals["fixed"],
            "unavoidable": totals["unavoidable"],
        },
    }
