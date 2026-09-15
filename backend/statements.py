"""Local, review-first PDF table import. Never infer a debit from a balance."""

import base64
import hashlib
import io
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .store import encode, now, uid


def amount(value):
    value = re.sub(r"(?i)INR|Rs\.?|₹", "", value or "").strip().replace(",", "")
    if value in ("", "-", "—"):
        return 0
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
        raise ValueError("Unclear amount")
    return int(Decimal(value) * 100)


def parse_tables(tables, month, account, currency):
    rows, warnings = [], []
    for table in tables:
        columns = None
        for cells in table:
            cells = [(c or "").strip() for c in cells]
            labels = [re.sub(r"[^a-z]", "", c.lower()) for c in cells]
            mapping = {}
            for i, label in enumerate(labels):
                if label in ("date", "trandate", "txndate", "transactiondate", "postingdate"):
                    mapping.setdefault("date", i)
                if any(
                    x in label
                    for x in ("narration", "description", "particulars", "transactiondetails")
                ):
                    mapping["counterparty"] = i
                if any(x in label for x in ("withdrawal", "debit")):
                    mapping["debit"] = i
                if any(x in label for x in ("deposit", "credit")):
                    mapping["credit"] = i
            if all(k in mapping for k in ("date", "counterparty", "debit", "credit")):
                columns = mapping
                continue
            if columns is None:
                continue
            try:
                rawdate = cells[columns["date"]].split("\n")[0]
                if not rawdate:
                    continue
                date = None
                for fmt in (
                    "%d/%m/%Y",
                    "%d-%m-%Y",
                    "%d/%m/%y",
                    "%d-%m-%y",
                    "%Y-%m-%d",
                    "%d %b %Y",
                    "%d-%b-%Y",
                    "%d %b %y",
                ):
                    try:
                        date = datetime.strptime(rawdate, fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        pass
                if date is None:
                    raise ValueError("Unclear date")
                if not date.startswith(month):
                    continue
                debit, credit = amount(cells[columns["debit"]]), amount(cells[columns["credit"]])
                if bool(debit) == bool(credit):
                    raise ValueError("Unclear debit or credit")
                description = " ".join(cells[columns["counterparty"]].split())
                if not description:
                    raise ValueError("Missing description")
                reference = re.search(r"UPI/(?:P2A|P2M|P2P)/([A-Z0-9-]+)(?:/|$)", description, re.I)
                rows.append(
                    dict(
                        reference=reference.group(1).upper() if reference else None,
                        reference_namespace="upi" if reference else "",
                        date=date,
                        counterparty=description,
                        amount_minor=debit or credit,
                        direction="debit" if debit else "credit",
                        kind="purchase" if debit else "income",
                        account=account,
                        currency=currency,
                        category="Uncategorized",
                    )
                )
            except (ValueError, IndexError, InvalidOperation):
                warnings.append("A table row could not be read: " + " | ".join(cells)[:220])
    return rows, warnings


def candidates(row, existing):
    # Withhold uncertain same-day matches rather than risk counting them twice.
    result = []
    reference = row.get("reference")
    if not reference:
        match = re.search(
            r"UPI/(?:P2A|P2M|P2P)/([A-Z0-9-]+)(?:/|$)", row.get("counterparty", ""), re.I
        )
        reference = match.group(1).upper() if match else None
    for t in existing:
        same_money = all(t.get(k) == row.get(k) for k in ("amount_minor", "currency", "direction"))
        same_reference = bool(reference and str(t.get("reference") or "").upper() == reference)
        if same_money and (same_reference or t.get("date") == row.get("date")):
            result.append(t["id"])
    return result


def enrichment_target(row, existing):
    ref = row.get("reference")
    if not ref:
        return None
    matched = [
        t
        for t in existing
        if t.get("reference_namespace") == "upi"
        and str(t.get("reference") or "").upper() == ref.upper()
        and all(t.get(k) == row.get(k) for k in ("amount_minor", "currency", "direction"))
    ]
    # A reference appearing on multiple ledger entries needs manual review.
    return matched[0] if len(matched) == 1 else None


def enrich(store, db, row, target, filename):
    record = db.execute(
        "SELECT data,overrides FROM transactions WHERE id=?", (target["id"],)
    ).fetchone()
    base, overrides = json.loads(record["data"]), json.loads(record["overrides"])
    before = dict(base)
    description = row["counterparty"]
    match = re.match(r"UPI/(?:P2A|P2M|P2P)/[A-Z0-9-]+/([^/]+)", description, re.I)
    name = match.group(1).strip() if match else ""
    old = str(target.get("counterparty") or "").strip()
    # Keep confirmed identities and all user corrections. Expand only missing,
    # raw payment descriptors or a prefix of the name supplied by the bank.
    if (
        name
        and "counterparty" not in overrides
        and not target.get("identity_confirmed")
        and (
            old.casefold() in ("", "unknown recipient", "unknown", "upi")
            or old.upper().startswith("UPI/")
            or (
                len(old) >= 3
                and name.casefold().startswith(old.casefold())
                and len(name) > len(old)
            )
        )
    ):
        base["counterparty"] = name
    base["statement_details"] = {
        "description": description,
        "date": row["date"],
        "filename": filename,
        "reference": row["reference"],
    }
    resolved = 0
    if base.get("date_basis") == "received" and "date" not in overrides:
        base.update(date=row["date"], date_basis="explicit")
        message = "Transaction date missing; email received date used"
        base["warnings"] = [w for w in base.get("warnings", []) if w != message]
        resolved = db.execute(
            "UPDATE issues SET status='resolved' WHERE transaction_id=? AND kind='evidence' AND message=? AND status='open'",
            (target["id"], message),
        ).rowcount
    if (
        "category" not in overrides
        and not base.get("category_manual")
        and not base.get("rule_id")
        and target.get("category") == "Uncategorized"
    ):
        effective = store.apply_rules(db, {**base, **overrides})
        for key in ("category", "category_inference", "rule_id"):
            if key in effective:
                base[key] = effective[key]
    if before == base:
        return 0, resolved
    db.execute("UPDATE transactions SET data=? WHERE id=?", (encode(base), target["id"]))
    store.audit(db, "statement details matched by UPI reference", target["id"], before, base)
    return 1, resolved


def preview(store, body):
    month, account, currency = (
        body.get("month", ""),
        body.get("account", "").strip(),
        body.get("currency", "INR"),
    )
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("Choose a valid month")
    if not account or len(account) > 100:
        raise ValueError("Choose or name the statement account")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("Use a three-letter currency code")
    try:
        content = base64.b64decode(body.get("file", ""), validate=True)
    except Exception:
        raise ValueError("Invalid PDF upload") from None
    if not content.startswith(b"%PDF-") or len(content) > 20_000_000:
        raise ValueError("Choose a PDF smaller than 20 MB")
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(content), password=body.get("password") or None) as pdf:
            if len(pdf.pages) > 100:
                raise ValueError("Use a statement with at most 100 pages")
            rows, warnings = [], []
            available_months = set()
            for page in pdf.pages:
                parsed, notices = parse_tables(page.extract_tables(), "", account, currency)
                if not parsed:
                    parsed, notices = parse_tables(
                        page.extract_tables(
                            {"vertical_strategy": "text", "horizontal_strategy": "text"}
                        ),
                        "",
                        account,
                        currency,
                    )
                available_months.update(row["date"][:7] for row in parsed)
                rows.extend(row for row in parsed if row["date"].startswith(month))
                warnings.extend(notices)
                if not parsed:
                    warnings.append(
                        f"Page {page.page_number}: no supported transactions found for this month. Check this page manually."
                    )
    except ValueError:
        raise
    except Exception:
        raise ValueError(
            "Could not read this PDF. Check the password and use a text-based bank statement."
        ) from None
    if not rows and available_months:
        choices = ", ".join(
            datetime.strptime(m, "%Y-%m").strftime("%B %Y") for m in sorted(available_months)
        )
        raise ValueError(
            f"No transactions found for {month}. This PDF contains transactions for {choices}. Select that month and preview again."
        )
    if not rows:
        raise ValueError(
            "No supported transactions found for this month. Use a text-based statement with Date, Description, Debit and Credit columns. Scans and other layouts need manual entry."
        )
    existing = store.list_transactions()
    digest = hashlib.sha256(content).hexdigest()
    for i, row in enumerate(rows):
        row["statement_key"] = hashlib.sha256(
            encode([digest, account.casefold(), currency, month, i]).encode()
        ).hexdigest()
        row["already_imported"] = any(
            t.get("statement_key") == row["statement_key"] for t in existing
        )
        row["possible_matches"] = candidates(row, existing)
        target = enrichment_target(row, existing)
        row["enrichment_target"] = target["id"] if target else None
    token = uid()
    # Encrypt extracted evidence; original PDF and password are never saved.
    store.set_setting(
        "statement_preview:" + token,
        store.vault.encrypt(
            {
                "rows": rows,
                "filename": str(body.get("filename", "Statement.pdf"))[:200],
                "month": month,
            }
        ).decode(),
    )
    return {
        "token": token,
        "rows": rows,
        "warnings": warnings,
        "note": "Check against the PDF. Extracted rows do not prove complete statement coverage. Review transfers, refunds and repayments before adding.",
    }


def commit(store, body):
    key = "statement_preview:" + str(body.get("token", ""))
    with store.lock:
        saved = store.get_setting(key)
        if not saved:
            raise ValueError("Preview expired. Upload the statement again")
        payload = store.vault.decrypt(saved.encode())
        selections = body.get("rows", [])
        if not isinstance(selections, list):
            raise ValueError("Invalid row selection")
        prepared = []
        seen = set()
        for selection in selections:
            index = selection.get("index")
            if type(index) is not int or not 0 <= index < len(payload["rows"]) or index in seen:
                raise ValueError("Invalid row selection")
            seen.add(index)
            row = {
                k: v
                for k, v in payload["rows"][index].items()
                if k not in ("already_imported", "possible_matches", "enrichment_target")
            }
            row["kind"] = selection.get("kind", row["kind"])
            row["statement_filename"] = payload["filename"]
            prepared.append(store.normalize(row))
        imported = skipped = updated = resolved = 0
        existing = store.list_transactions()
        with store.tx() as db:
            for evidence in payload["rows"]:
                target = enrichment_target(evidence, existing)
                if target:
                    changed, closed = enrich(store, db, evidence, target, payload["filename"])
                    updated += changed
                    resolved += closed
            for row in prepared:
                if any(
                    t.get("statement_key") == row["statement_key"] for t in existing
                ) or candidates(row, existing):
                    skipped += 1
                    continue
                id = uid()
                row = store.apply_rules(db, row)
                db.execute(
                    "INSERT INTO transactions(id,data,manual,created_at) VALUES(?,?,1,?)",
                    (id, encode(row), now()),
                )
                store.issue(
                    db,
                    "statement_review",
                    "Verify statement classification and possible duplicates against the PDF",
                    transaction_id=id,
                )
                store.audit(db, "statement import", id, None, row)
                existing.append({**row, "id": id})
                imported += 1
            db.execute("DELETE FROM settings WHERE key=?", (key,))
        return {"imported": imported, "skipped": skipped, "updated": updated, "resolved": resolved}
