"""Password-protected backups, validated in isolation before replacing a ledger."""

import base64
import json
import os
import re
import sqlite3

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .models import TransactionPatch, validated
from .store import Store, encode
from .insights import validate_context

BACKUP_TABLES = [
    "settings",
    "sources",
    "transactions",
    "observations",
    "issues",
    "rules",
    "aliases",
    "accounts",
    "categories",
    "audit",
]
LOCAL_SETTINGS = {
    "connection",
    "oauth_configured",
    "history_id",
    "backfill",
    "gmail_scan_policy",
    "ai_advisor_enabled",
    "mobile_access_enabled",
}


def local_setting(key):
    # Discard obsolete plugin settings when exporting or restoring old ledgers.
    return key in LOCAL_SETTINGS or key.startswith(
        ("statement_preview:", "merchant_lookup", "plugin_", "ai_advisor_consent")
    )


def replace_tables(store, tables):
    """One transaction with no nested Store methods that could commit early."""
    with store.tx() as db:
        db.execute("PRAGMA defer_foreign_keys=ON")
        for name in reversed(BACKUP_TABLES):
            db.execute("DELETE FROM " + name)
        db.execute("DELETE FROM queue")
        db.execute("DELETE FROM jobs")
        for name in BACKUP_TABLES:
            for row in tables[name]:
                keys = list(row)
                db.execute(
                    "INSERT INTO "
                    + name
                    + "("
                    + ",".join(keys)
                    + ") VALUES("
                    + ",".join("?" for _ in keys)
                    + ")",
                    list(row.values()),
                )
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Backup has invalid record relationships")


def validate_ledger(staging):
    from .merchant_research import validate_result

    rows = staging.list_transactions(True)
    by_id = {row["id"]: row for row in rows}
    categories = {r[0] for r in staging.db.execute("SELECT name FROM categories")}
    for setting in staging.db.execute(
        "SELECT key,value FROM settings WHERE key LIKE 'merchant_match:%'"
    ):
        match = json.loads(setting["value"])
        if (
            not isinstance(match, dict)
            or match.get("key") != setting["key"]
            or not isinstance(match.get("accepted_at"), str)
        ):
            raise ValueError("Backup has an invalid merchant match")
        validate_result(match.get("result"), categories)
    for row in rows:
        match = row.get("merchant_identification")
        if match:
            validate_result(match.get("result"), categories)
        if row["category"] not in categories:
            raise ValueError("Backup refers to an unknown category")
        for allocation in row["allocations"]:
            if allocation.get("category") and allocation["category"] not in categories:
                raise ValueError("Backup split refers to an unknown category")
        parent = row["merged_into"]
        if parent and (parent == row["id"] or parent not in by_id or by_id[parent]["merged_into"]):
            raise ValueError("Backup has invalid merged transactions")
        if not parent:
            staging.validate_relationships(staging.db, row, row["id"])
    for rule in staging.db.execute("SELECT * FROM rules"):
        if rule["scope"] not in ("exact", "merchant") or rule["category"] not in categories:
            raise ValueError("Backup has an invalid category rule")
        if not rule["merchant"] or (rule["scope"] == "exact" and not rule["counterparty_key"]):
            raise ValueError("Backup category rule has no identity")
        if type(rule["amount_minor"]) is not int or rule["amount_minor"] <= 0:
            raise ValueError("Backup category rule has an invalid amount")
        if not re.fullmatch(r"[A-Z]{3}", rule["currency"] or "") or rule["direction"] not in (
            "debit",
            "credit",
        ):
            raise ValueError("Backup category rule has invalid matching fields")
    # Exercise consumers before any writes reach the original database.
    for currency in {row["currency"] for row in rows} | {"INR"}:
        staging.report(currency=currency)


def restore_backup(store, password, content):
    staging = Store(None, store.vault)
    try:
        magic, salt, encrypted = content.split(b"\n", 2)
        if magic != b"MONTHLYCOST1":
            raise ValueError("Invalid backup format")
        salt = base64.urlsafe_b64decode(salt)
        if len(salt) != 16:
            raise ValueError("Invalid backup salt")
        key = base64.urlsafe_b64encode(
            Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode())
        )
        payload = json.loads(Fernet(key).decrypt(encrypted))
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or set(payload.get("tables", {})) != set(BACKUP_TABLES)
        ):
            raise ValueError("Unsupported backup version")
        prepared = {name: [] for name in BACKUP_TABLES}
        for name, rows in payload["tables"].items():
            columns = staging.db.execute("PRAGMA table_info(" + name + ")").fetchall()
            expected = {r[1] for r in columns}
            if not isinstance(rows, list):
                raise ValueError("Invalid backup records")
            for original in rows:
                if not isinstance(original, dict) or set(original) != expected:
                    raise ValueError("Backup schema does not match this app version")
                row = dict(original)
                if name == "settings":
                    if not isinstance(row["key"], str):
                        raise ValueError("Invalid setting name")
                    if local_setting(row["key"]) or row["key"].startswith("ai_advisor"):
                        continue
                    value = json.loads(row["value"])
                    if row["key"] == "financial_context":
                        row["value"] = encode(validate_context(value))
                    elif row["key"] in (
                        "bound_mailbox",
                        "last_sync",
                    ) and not isinstance(value, str):
                        raise ValueError("Invalid backup setting")
                if name == "sources":
                    attachments = json.loads(row["attachments"])
                    if not isinstance(attachments, list) or any(
                        not isinstance(x, str) for x in attachments
                    ):
                        raise ValueError("Invalid source attachments")
                    if row["body"]:
                        cache = row["body"]["plaintext_cache"]
                        if not isinstance(cache, dict) or not isinstance(cache.get("body"), str):
                            raise ValueError("Invalid cached email")
                        authentication = cache.get("authentication", {"verified": False})
                        if (
                            not isinstance(authentication, dict)
                            or type(authentication.get("verified")) is not bool
                        ):
                            raise ValueError("Invalid sender authentication evidence")
                        if authentication["verified"] and (
                            authentication.get("receiver") != "mx.google.com"
                            or not isinstance(authentication.get("domain"), str)
                        ):
                            raise ValueError("Invalid sender authentication provenance")
                        row["body"] = store.vault.encrypt(cache)
                if name == "transactions":
                    data, overrides = json.loads(row["data"]), json.loads(row["overrides"])
                    # Canonical records must contain their required display
                    # fields; silently supplying defaults would hide damage.
                    if not isinstance(data, dict) or not {
                        "date",
                        "counterparty",
                        "account",
                        "category",
                        "amount_minor",
                        "currency",
                        "direction",
                        "kind",
                    } <= set(data):
                        raise ValueError("Backup transaction is incomplete")
                    data = staging.normalize(data)
                    overrides = validated(TransactionPatch, overrides, partial=True)
                    staging.normalize({**data, **overrides})
                    row.update(data=encode(data), overrides=encode(overrides))
                if name == "observations":
                    row["data"] = encode(staging.normalize(json.loads(row["data"])))
                for column in columns:
                    field, kind, required, primary = column[1], column[2], column[3], column[5]
                    value = row[field]
                    if value is None:
                        if required or primary:
                            raise ValueError("Backup is missing a required value")
                    elif kind == "TEXT" and not isinstance(value, str):
                        raise ValueError("Invalid backup text")
                    elif kind == "INTEGER" and type(value) is not int:
                        raise ValueError("Invalid backup number")
                    elif kind == "BLOB" and not isinstance(value, bytes):
                        raise ValueError("Invalid backup evidence")
                prepared[name].append(row)
        replace_tables(staging, prepared)
        staging.categorize_existing()
        staging.flag_legacy_source_authentication()
        staging.set_setting(
            "connection", {"state": "disconnected", "email": staging.get_setting("bound_mailbox")}
        )
        staging.set_setting("ai_advisor_enabled", False)
        validate_ledger(staging)
        ready = {
            name: [dict(row) for row in staging.db.execute("SELECT * FROM " + name)]
            for name in BACKUP_TABLES
        }
        replace_tables(store, ready)
    except (
        InvalidToken,
        KeyError,
        TypeError,
        AttributeError,
        json.JSONDecodeError,
        sqlite3.IntegrityError,
    ):
        raise ValueError("The password is incorrect or the backup is damaged") from None
    finally:
        staging.close()


def make_backup(store, password):
    if len(password) < 12:
        raise ValueError("Use a backup password of at least 12 characters")
    salt = os.urandom(16)
    key = base64.urlsafe_b64encode(
        Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode())
    )
    tables = {}
    with store.lock:
        for name in BACKUP_TABLES:
            rows = []
            for row in store.db.execute("SELECT * FROM " + name):
                value = dict(row)
                if name == "settings" and local_setting(value["key"]):
                    continue
                if name == "sources" and value["body"]:
                    # Re-encrypted within the password-protected archive. No OAuth credentials.
                    value["body"] = {"plaintext_cache": store.vault.decrypt(value["body"])}
                rows.append(value)
            tables[name] = rows
    encrypted = Fernet(key).encrypt(encode({"version": 1, "tables": tables}).encode())
    return b"MONTHLYCOST1\n" + base64.urlsafe_b64encode(salt) + b"\n" + encrypted
