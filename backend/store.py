from __future__ import annotations

import calendar
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from collections import defaultdict
from contextlib import contextmanager, nullcontext
from datetime import datetime
from pathlib import Path

from .parser import IST, VERSION, minor, parse_email, has_transaction_evidence
from .categorization import KIND_CATEGORIES, apply_merchant_default, merchant_match
from .models import TransactionRecord, TransactionPatch, validated
from .email_auth import UNVERIFIED

CATEGORIES = list(
    dict.fromkeys(
        [
            "Uncategorized",
            "Food & dining",
            "Groceries",
            "Shopping",
            "Transport",
            "Rent & home",
            "Utilities",
            "Subscriptions",
            "Entertainment",
            "Travel",
            "Health",
            "Education",
            "Family & gifts",
            "Personal care",
            "Insurance",
            "Taxes",
            "Donations",
            "EMIs",
            "Fees & interest",
            "Other",
            *KIND_CATEGORIES.values(),
        ]
    )
)
KINDS = {
    "purchase",
    "person_payment",
    "emi",
    "fee",
    "refund",
    "income",
    "card_repayment",
    "own_transfer",
    "investment",
    "wallet_funding",
    "cash_withdrawal",
    "financed_purchase",
    "financing_adjustment",
    "reimbursement",
    "lending",
}
SPENDING = {"purchase", "person_payment", "emi", "fee"}


def now():
    return datetime.now(IST).isoformat()


def uid():
    return uuid.uuid4().hex


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, path, vault):
        self.path, self.vault = Path(path) if path is not None else None, vault
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(
            str(path) if path is not None else ":memory:", check_same_thread=False
        )
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,mailbox TEXT NOT NULL,sender TEXT,subject TEXT,received_at TEXT,body BLOB,attachments TEXT NOT NULL DEFAULT '[]',status TEXT,reason TEXT,template TEXT,parser_version TEXT,missing INTEGER DEFAULT 0,created_at TEXT);
        CREATE TABLE IF NOT EXISTS transactions(id TEXT PRIMARY KEY,data TEXT NOT NULL,overrides TEXT NOT NULL DEFAULT '{}',merged_into TEXT REFERENCES transactions(id),manual INTEGER DEFAULT 0,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES sources(id),event_key TEXT NOT NULL,data TEXT NOT NULL,transaction_id TEXT NOT NULL REFERENCES transactions(id),UNIQUE(source_id,event_key));
        CREATE TABLE IF NOT EXISTS issues(id TEXT PRIMARY KEY,source_id TEXT,transaction_id TEXT,kind TEXT,message TEXT,status TEXT NOT NULL DEFAULT 'open',created_at TEXT);
        CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY,scope TEXT NOT NULL,counterparty_key TEXT,merchant TEXT,amount_minor INTEGER,currency TEXT,direction TEXT,category TEXT NOT NULL,created_at TEXT);
        CREATE TABLE IF NOT EXISTS aliases(alias TEXT PRIMARY KEY,identity TEXT NOT NULL,name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS accounts(name TEXT PRIMARY KEY,owned INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS categories(name TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,state TEXT NOT NULL,phase TEXT,processed INTEGER DEFAULT 0,discovered INTEGER DEFAULT 0,error TEXT,started_at TEXT,finished_at TEXT);
        CREATE TABLE IF NOT EXISTS sync_additions(transaction_id TEXT PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,seen_at TEXT);
        CREATE TABLE IF NOT EXISTS queue(message_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,action TEXT NOT NULL,entity_id TEXT,before_data TEXT,after_data TEXT,created_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_sources_received ON sources(received_at);
        CREATE INDEX IF NOT EXISTS idx_observations_transaction ON observations(transaction_id);
        CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status);
        CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
        CREATE INDEX IF NOT EXISTS idx_sync_additions_job ON sync_additions(job_id);
        """)
        self.db.executemany(
            "INSERT OR IGNORE INTO categories VALUES(?)", [(c,) for c in CATEGORIES]
        )
        self.db.commit()
        if self.path is not None:
            self.path.chmod(0o600)
        self.categorize_existing()
        self.flag_legacy_source_authentication()

    def flag_legacy_source_authentication(self):
        """Preserve saved accounting, but do not silently trust pre-auth sources."""
        with self.tx() as db:
            sources = db.execute(
                "SELECT id FROM sources WHERE status='parsed' AND mailbox!='export' AND parser_version!=?",
                (VERSION,),
            ).fetchall()
            for row in sources:
                self.issue(
                    db,
                    "sender_authentication",
                    "This source predates sender authentication checks; verify its original payment evidence",
                    source_id=row["id"],
                )

    @contextmanager
    def tx(self):
        with self.lock:
            with self.db:
                yield self.db

    def get_setting(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.tx() as db:
            db.execute(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, encode(value)),
            )

    def audit(self, db, action, entity, before, after):
        db.execute(
            "INSERT INTO audit(action,entity_id,before_data,after_data,created_at) VALUES(?,?,?,?,?)",
            (action, entity, encode(before), encode(after), now()),
        )

    def issue(self, db, kind, message, transaction_id=None, source_id=None):
        identity = hashlib.sha256(
            encode([kind, message, transaction_id, source_id]).encode()
        ).hexdigest()[:32]
        db.execute(
            "INSERT OR IGNORE INTO issues(id,kind,message,transaction_id,source_id,created_at) VALUES(?,?,?,?,?,?)",
            (identity, kind, message, transaction_id, source_id, now()),
        )

    def list_transactions(self, include_merged=False):
        with self.lock:
            query = "SELECT * FROM transactions" + (
                "" if include_merged else " WHERE merged_into IS NULL"
            )
            rows = self.db.execute(query).fetchall()
            counts = dict(
                self.db.execute(
                    "SELECT transaction_id,COUNT(*) FROM observations GROUP BY transaction_id"
                )
            )
            additions = {
                row["transaction_id"]: row
                for row in self.db.execute("SELECT * FROM sync_additions")
            }
            issues = defaultdict(list)
            for row in self.db.execute("SELECT * FROM issues WHERE status='open'"):
                if row["transaction_id"]:
                    issues[row["transaction_id"]].append(dict(row))
            result = []
            for row in rows:
                overrides = json.loads(row["overrides"])
                value = {**json.loads(row["data"]), **overrides}
                from .merchant_research import current_identification

                identification = current_identification(value)
                if not identification:
                    value.pop("merchant_identification", None)
                from .spending_focus import merchant_name

                value["merchant_display"] = merchant_name(value)
                from .categorization import merchant_match

                value["merchant_recognized"] = bool(
                    merchant_match(value.get("counterparty", ""))
                ) and not str(value.get("counterparty_key") or "").startswith("person:")
                inference = value.get("category_inference", {})
                if "category" in overrides or value.get("category_manual"):
                    value.update(
                        category_source="manual", category_reason="Category chosen by you."
                    )
                elif value.get("rule_id"):
                    value.update(
                        category_source="rule", category_reason="Applied your saved category rule."
                    )
                elif inference.get("category") == value["category"] and inference.get("reason"):
                    value.update(category_source="automatic", category_reason=inference["reason"])
                else:
                    value.update(
                        category_source="default",
                        category_reason="No merchant category recognized yet."
                        if value["category"] == "Uncategorized"
                        else "Category supplied with the transaction.",
                    )
                value.update(
                    id=row["id"],
                    manual=bool(row["manual"]),
                    merged_into=row["merged_into"],
                    source_count=counts.get(row["id"], 0),
                    issues=issues[row["id"]],
                    corrected=bool(json.loads(row["overrides"])),
                    is_new=(
                        row["id"] in additions
                        and additions[row["id"]]["seen_at"] is None
                        and row["merged_into"] is None
                    ),
                    sync_job_id=(
                        additions[row["id"]]["job_id"] if row["id"] in additions else None
                    ),
                )
                value["spend_minor"] = self.spend(value)
                result.append(value)
            return sorted(result, key=lambda t: (t["date"], t["id"]), reverse=True)

    def sync_summary(self, job_id=None):
        with self.lock:
            row = self.db.execute(
                """SELECT COUNT(CASE WHEN a.job_id=? THEN 1 END) AS added,
                COUNT(CASE WHEN a.seen_at IS NULL THEN 1 END) AS unseen
                FROM sync_additions a JOIN transactions t ON t.id=a.transaction_id
                WHERE t.merged_into IS NULL""",
                (job_id,),
            ).fetchone()
        return dict(row)

    def mark_sync_seen(self, transaction_ids):
        if not isinstance(transaction_ids, list) or any(
            not isinstance(id, str) or not id for id in transaction_ids
        ):
            raise ValueError("Choose the transaction IDs to mark as seen")
        marked = 0
        with self.tx() as db:
            for id in set(transaction_ids):
                marked += db.execute(
                    """UPDATE sync_additions SET seen_at=?
                    WHERE transaction_id=? AND seen_at IS NULL
                    AND EXISTS (SELECT 1 FROM transactions
                        WHERE id=transaction_id AND merged_into IS NULL)""",
                    (now(), id),
                ).rowcount
        return marked

    def reconcile_sync_changes(self):
        """An email merged into an existing ledger entry is not a new transaction."""
        with self.tx() as db:
            db.execute(
                """DELETE FROM sync_additions WHERE transaction_id IN
                (SELECT id FROM transactions WHERE merged_into IS NOT NULL)""",
            )

    @staticmethod
    def spend(t):
        if t.get("excluded") or t.get("merged_into"):
            return 0
        if t["kind"] == "refund":
            return -t["amount_minor"]
        if t.get("allocations") and t["kind"] in SPENDING | {"cash_withdrawal", "lending"}:
            return sum(a["amount_minor"] for a in t["allocations"] if a["type"] == "personal")
        return t["amount_minor"] if t["kind"] in SPENDING and t["direction"] == "debit" else 0

    def transaction(self, id):
        found = next((t for t in self.list_transactions(True) if t["id"] == id), None)
        if not found:
            raise ValueError("Transaction not found")
        with self.lock:
            children = [
                r[0]
                for r in self.db.execute("SELECT id FROM transactions WHERE merged_into=?", (id,))
            ]
            ids = [id] + children
            placeholders = ",".join("?" for _ in ids)
            observations = self.db.execute(
                f"SELECT o.id AS observation_id,o.data,s.id,s.sender,s.subject,s.received_at,s.status,s.missing,s.body IS NOT NULL AS cached FROM observations o JOIN sources s ON o.source_id=s.id WHERE o.transaction_id IN ({placeholders})",
                ids,
            ).fetchall()
            found["sources"] = [{**dict(r), "data": json.loads(r["data"])} for r in observations]
            found["merged_children"] = children
            found["audit"] = [
                dict(r)
                for r in self.db.execute(
                    "SELECT action,created_at FROM audit WHERE entity_id=? ORDER BY id DESC LIMIT 30",
                    (id,),
                )
            ]
        return found

    def normalize(self, data):
        d = validated(TransactionRecord, data)
        for field in (
            "statement_details",
            "statement_key",
            "statement_filename",
            "category_inference",
            "category_manual",
            "rule_id",
            "financing_source_id",
            "separate_event",
        ):
            if field not in data:
                d.pop(field, None)
        if d.get("kind") not in KINDS:
            raise ValueError("Unknown transaction kind")
        if d.get("direction") not in ("debit", "credit"):
            raise ValueError("Direction must be debit or credit")
        if (
            not isinstance(d.get("amount_minor"), int)
            or isinstance(d["amount_minor"], bool)
            or d["amount_minor"] <= 0
        ):
            raise ValueError("Amount must be a positive integer in minor units")
        if not re.fullmatch(r"[A-Z]{3}", d.get("currency", "")):
            raise ValueError("Use a three-letter currency code")
        try:
            datetime.strptime(d["date"], "%Y-%m-%d")
        except (ValueError, KeyError):
            raise ValueError("Use a valid YYYY-MM-DD date") from None
        if d["kind"] in ("refund", "income", "reimbursement") and d["direction"] != "credit":
            raise ValueError("This transaction kind requires a credit")
        if d["kind"] in SPENDING and d["direction"] != "debit":
            raise ValueError("Spending requires a debit")
        if d["allocations"]:
            if d["direction"] != "debit":
                raise ValueError("Only outgoing transactions can be split")
            if d["kind"] not in SPENDING | {"cash_withdrawal", "lending"}:
                raise ValueError(
                    "Remove the split before classifying this as a transfer or financing movement"
                )
            if any(
                a.get("type") not in ("personal", "reimbursable", "lending")
                or not isinstance(a.get("amount_minor"), int)
                or a["amount_minor"] < 0
                for a in d["allocations"]
            ):
                raise ValueError("Invalid split")
            if sum(a["amount_minor"] for a in d["allocations"]) != d["amount_minor"]:
                raise ValueError("Split amounts must equal the original payment")
        return d

    def apply_rules(self, db, data):
        d = dict(data)
        alias = db.execute(
            "SELECT * FROM aliases WHERE alias=?", (d["counterparty"].strip().casefold(),)
        ).fetchone()
        if alias:
            d.update(
                counterparty_key=alias["identity"],
                identity_confirmed=True,
                counterparty=alias["name"],
            )
        d = apply_merchant_default(d)
        from .merchant_research import apply_identification, eligible, match_key, validate_result

        if eligible(d):
            remembered = self.get_setting(match_key(d))
            if remembered:
                validate_result(
                    remembered["result"], {r[0] for r in db.execute("SELECT name FROM categories")}
                )
                d = apply_identification(d, remembered)
        rules = db.execute(
            "SELECT * FROM rules ORDER BY CASE scope WHEN 'exact' THEN 0 ELSE 1 END,created_at DESC"
        ).fetchall()
        for rule in rules:
            if self.rule_matches(rule, d):
                d["category"] = rule["category"]
                d["rule_id"] = rule["id"]
                break
        return d

    @staticmethod
    def rule_matches(rule, t):
        if rule["scope"] in ("payee", "person"):
            if (
                t["kind"] not in SPENDING
                or rule["currency"] != t["currency"]
                or rule["direction"] != t["direction"]
            ):
                return False
            if rule["scope"] == "person":
                return bool(
                    t.get("identity_confirmed")
                    and t.get("counterparty_key")
                    and rule["counterparty_key"] == t["counterparty_key"]
                )
            return t["counterparty"].casefold().strip() == rule["merchant"].casefold().strip()
        if rule["scope"] == "exact":
            return bool(
                t.get("identity_confirmed")
                and t.get("counterparty_key")
                and all(
                    rule[k] == t.get(k)
                    for k in ("counterparty_key", "amount_minor", "currency", "direction")
                )
            )
        return (
            t["counterparty"].casefold().strip() == rule["merchant"].casefold().strip()
            and t["kind"] in SPENDING
        )

    def create_manual(self, data):
        d = self.normalize(data)
        id = uid()
        with self.tx() as db:
            d = self.apply_rules(db, d)
            if data.get("category") and data["category"] != "Uncategorized":
                d.update(category=data["category"], category_manual=True)
                d.pop("rule_id", None)
                d.pop("category_inference", None)
            self.validate_relationships(db, d, id)
            if not db.execute("SELECT 1 FROM categories WHERE name=?", (d["category"],)).fetchone():
                raise ValueError("Category does not exist")
            db.execute(
                "INSERT INTO transactions(id,data,manual,created_at) VALUES(?,?,1,?)",
                (id, encode(d), now()),
            )
            self.audit(db, "manual addition", id, None, d)
        return self.transaction(id)

    def categorize_existing(self):
        """Fill historical defaults without back-applying newly saved rules."""
        changed = 0
        with self.tx() as db:
            db.executemany("INSERT OR IGNORE INTO categories VALUES(?)", [(c,) for c in CATEGORIES])
            rows = db.execute("SELECT id,data,overrides FROM transactions").fetchall()
            for row in rows:
                base, overrides = json.loads(row["data"]), json.loads(row["overrides"])
                # User categories (including explicitly Uncategorized) and
                # historical saved-rule decisions always win.
                if "category" in overrides or base.get("category_manual") or base.get("rule_id"):
                    continue
                effective = {**base, **overrides}
                # Older summary imports mistook this merchant's CRED gateway
                # for a credit-card bill repayment. Keep user treatment intact.
                corrected_gateway = (
                    base.get("reference_namespace") == "legacy"
                    and effective.get("kind") == "card_repayment"
                    and "kind" not in overrides
                    and str(effective.get("counterparty", "")).strip().casefold() == "credpayredbus"
                )
                if corrected_gateway:
                    effective["kind"] = "person_payment"
                alias = db.execute(
                    "SELECT identity FROM aliases WHERE alias=?",
                    (effective["counterparty"].strip().casefold(),),
                ).fetchone()
                if alias:
                    effective["counterparty_key"] = alias["identity"]
                categorized = apply_merchant_default(effective)
                if categorized["category"] == effective["category"] and not corrected_gateway:
                    continue
                updated = {**base, "category": categorized["category"]}
                if categorized.get("category_inference"):
                    updated["category_inference"] = categorized["category_inference"]
                if corrected_gateway:
                    updated["kind"] = effective["kind"]
                db.execute(
                    "UPDATE transactions SET data=? WHERE id=?", (encode(updated), row["id"])
                )
                self.audit(
                    db,
                    "merchant categorization",
                    row["id"],
                    {"category": base["category"], "kind": base["kind"]},
                    {"category": updated["category"], "kind": updated["kind"]},
                )
                changed += 1
        return changed

    def update(self, id, changes, *, _db=None):
        if not isinstance(changes, dict):
            raise ValueError("Expected a transaction correction object")
        allowed = {
            "amount_minor",
            "currency",
            "date",
            "kind",
            "direction",
            "counterparty",
            "counterparty_key",
            "identity_confirmed",
            "account",
            "category",
            "allocations",
            "excluded",
            "avoidable",
            "linked_to",
            "notes",
            "original_currency",
            "original_amount_minor",
            "cash_source_id",
        }
        if set(changes) - allowed:
            raise ValueError("Unsupported correction")
        changes = validated(TransactionPatch, changes, partial=True)
        before = self.transaction(id)
        if before["merged_into"]:
            raise ValueError("Unmerge before editing this transaction")
        d = self.normalize({**before, **changes})
        with nullcontext(_db) if _db is not None else self.tx() as db:
            auto_category_changed = False
            default_category = before.get("category_source") == "automatic" or (
                before.get("category_source") == "default" and before["category"] == "Uncategorized"
            )
            if (
                default_category
                and "category" not in changes
                and {"counterparty", "counterparty_key", "kind", "direction"}.intersection(changes)
            ):
                d["category"] = "Uncategorized"
                d.pop("category_inference", None)
                d = apply_merchant_default(d)
                auto_category_changed = True
            if not db.execute("SELECT 1 FROM categories WHERE name=?", (d["category"],)).fetchone():
                raise ValueError("Category does not exist")
            self.validate_relationships(db, d, id)
            if d.get("linked_to"):
                parent = self.transaction(d["linked_to"])
                if (
                    parent["id"] == id
                    or parent["merged_into"]
                    or parent["direction"] != "debit"
                    or parent["currency"] != d["currency"]
                ):
                    raise ValueError("Link to an outgoing transaction in the same currency")
                others = [
                    t
                    for t in self.list_transactions()
                    if t["id"] != id
                    and t.get("linked_to") == parent["id"]
                    and t["kind"] == d["kind"]
                ]
                if d["kind"] == "reimbursement":
                    available = sum(
                        a["amount_minor"]
                        for a in parent.get("allocations", [])
                        if a["type"] in ("reimbursable", "lending")
                    )
                    if parent["kind"] == "lending" and not parent.get("allocations"):
                        available = parent["amount_minor"]
                elif d["kind"] == "refund":
                    available = self.spend(parent)
                else:
                    raise ValueError("Only refunds and reimbursements can be linked")
                if sum(t["amount_minor"] for t in others) + d["amount_minor"] > available:
                    raise ValueError("Linked credits exceed the remaining eligible amount")
            existing = json.loads(
                db.execute("SELECT overrides FROM transactions WHERE id=?", (id,)).fetchone()[0]
            )
            if auto_category_changed:
                base = json.loads(
                    db.execute("SELECT data FROM transactions WHERE id=?", (id,)).fetchone()[0]
                )
                base["category"] = d["category"]
                base.pop("category_inference", None)
                if d.get("category_inference"):
                    base["category_inference"] = d["category_inference"]
                db.execute("UPDATE transactions SET data=? WHERE id=?", (encode(base), id))
                self.audit(
                    db,
                    "merchant categorization",
                    id,
                    {"category": before["category"]},
                    {"category": d["category"]},
                )
            db.execute(
                "UPDATE transactions SET overrides=? WHERE id=?",
                (encode({**existing, **changes}), id),
            )
            db.execute("INSERT OR IGNORE INTO accounts(name) VALUES(?)", (d["account"],))
            self.audit(db, "correction", id, {k: before.get(k) for k in changes}, changes)
        return self.transaction(id)

    def save_edit(self, id, changes, remember_scope, edit_id, expected=None):
        """Save a correction and its future rule in one SQLite transaction.

        edit_id makes explicit retries safe if the first response is lost.
        Undo restores only this correction and stops the new rule; it never
        rewrites other transactions already imported with that rule.
        """
        if not isinstance(edit_id, str) or not re.fullmatch(r"[a-f0-9]{32}", edit_id):
            raise ValueError("Invalid edit identifier")
        if remember_scope not in ("none", "payee", "person", "exact"):
            raise ValueError("Invalid category matching scope")
        fingerprint = encode(
            {"id": id, "changes": changes, "scope": remember_scope, "expected": expected}
        )
        key = "edit-result:" + edit_id
        with self.tx() as db:
            previous = self.get_setting(key)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ValueError("This edit identifier was already used")
                return previous["result"]
            before = self.transaction(id)
            if expected is not None:
                if not isinstance(expected, dict) or set(expected) != set(changes):
                    raise ValueError("Invalid expected correction values")
                if any(encode(before.get(k)) != encode(v) for k, v in expected.items()):
                    raise ValueError("This payment changed. Reopen it before editing again.")
            raw_before = db.execute(
                "SELECT overrides FROM transactions WHERE id=?", (id,)
            ).fetchone()[0]
            data_before = db.execute("SELECT data FROM transactions WHERE id=?", (id,)).fetchone()[
                0
            ]
            updated = self.update(id, changes, _db=db)
            rule = None
            if remember_scope != "none":
                if "category" not in changes or updated["kind"] not in SPENDING:
                    raise ValueError("Remember categories only for spending payments")
                if updated["counterparty"].strip().casefold() in ("", "unknown recipient"):
                    raise ValueError("Identify the payee before remembering a category")
                if remember_scope in ("person", "exact") and not (
                    updated.get("identity_confirmed") and updated.get("counterparty_key")
                ):
                    raise ValueError("Confirm this person's identity or UPI ID first")
                rule = {
                    "id": uid(),
                    "scope": remember_scope,
                    "counterparty_key": updated.get("counterparty_key"),
                    "merchant": updated["counterparty"],
                    "amount_minor": updated["amount_minor"],
                    "currency": updated["currency"],
                    "direction": updated["direction"],
                    "category": updated["category"],
                    "created_at": now(),
                }
                db.execute(
                    "INSERT INTO rules VALUES(:id,:scope,:counterparty_key,:merchant,:amount_minor,:currency,:direction,:category,:created_at)",
                    rule,
                )
                self.audit(db, "save rule", rule["id"], None, {**rule, "historical_ids": []})
            raw_after = db.execute(
                "SELECT overrides FROM transactions WHERE id=?", (id,)
            ).fetchone()[0]
            result = {"transaction": updated, "undo_id": edit_id, "rule": rule}
            record = {
                "fingerprint": fingerprint,
                "result": result,
                "transaction_id": id,
                "before": raw_before,
                "after": raw_after,
                "data_before": data_before,
                "data_after": db.execute(
                    "SELECT data FROM transactions WHERE id=?", (id,)
                ).fetchone()[0],
                "rule_id": rule["id"] if rule else None,
            }
            db.execute("INSERT INTO settings VALUES(?,?)", (key, encode(record)))
            # Keep a bounded recent undo history, with every token local to the ledger.
            db.execute(
                "DELETE FROM settings WHERE key LIKE 'edit-result:%' AND rowid NOT IN (SELECT rowid FROM settings WHERE key LIKE 'edit-result:%' ORDER BY rowid DESC LIMIT 100)"
            )
            return result

    def undo_edit(self, id, edit_id):
        with self.tx() as db:
            key = "edit-result:" + str(edit_id)
            record = self.get_setting(key)
            if not record or record["transaction_id"] != id:
                raise ValueError("This edit is no longer available to undo")
            if record.get("undone"):
                return self.transaction(id)
            current = self.transaction(id)
            raw = db.execute("SELECT overrides FROM transactions WHERE id=?", (id,)).fetchone()[0]
            data_now = db.execute("SELECT data FROM transactions WHERE id=?", (id,)).fetchone()[0]
            if (
                current.get("merged_into")
                or raw != record["after"]
                or data_now != record["data_after"]
            ):
                raise ValueError(
                    "This payment changed again. Undo would replace a newer correction."
                )
            restored = self.normalize(
                {**json.loads(record["data_before"]), **json.loads(record["before"])}
            )
            self.validate_relationships(db, restored, id)
            db.execute(
                "UPDATE transactions SET data=?,overrides=? WHERE id=?",
                (record["data_before"], record["before"], id),
            )
            if record["rule_id"]:
                db.execute("DELETE FROM rules WHERE id=?", (record["rule_id"],))
            record["undone"] = True
            db.execute("UPDATE settings SET value=? WHERE key=?", (encode(record), key))
            self.audit(db, "undo correction", id, record["after"], record["before"])
            return self.transaction(id)

    def validate_relationships(self, db, d, id):
        transactions = self.list_transactions()
        if d.get("linked_to"):
            parent = next((t for t in transactions if t["id"] == d["linked_to"]), None)
            if (
                not parent
                or parent["id"] == id
                or parent["direction"] != "debit"
                or parent["currency"] != d["currency"]
            ):
                raise ValueError("Link to an outgoing transaction in the same currency")
            if d["direction"] != "credit" or d["kind"] not in ("refund", "reimbursement"):
                raise ValueError("Only incoming refunds or reimbursements can be linked")
            limit = self.refundable(parent) if d["kind"] == "refund" else self.reimbursable(parent)
            used = sum(
                t["amount_minor"]
                for t in transactions
                if t["id"] != id and t.get("linked_to") == parent["id"] and t["kind"] == d["kind"]
            )
            if used + d["amount_minor"] > limit:
                raise ValueError("Linked credits exceed the remaining eligible amount")
        children = [t for t in transactions if t.get("linked_to") == id]
        for kind, limit in [
            ("refund", self.refundable(d)),
            ("reimbursement", self.reimbursable(d)),
        ]:
            relevant = [t for t in children if t["kind"] == kind]
            if relevant and (
                d["direction"] != "debit"
                or any(t["currency"] != d["currency"] for t in relevant)
                or sum(t["amount_minor"] for t in relevant) > limit
            ):
                raise ValueError("Adjust linked refunds or repayments before changing this payment")
        if d.get("cash_source_id"):
            parent = next((t for t in transactions if t["id"] == d["cash_source_id"]), None)
            if (
                not parent
                or parent["id"] == id
                or parent["kind"] != "cash_withdrawal"
                or parent["currency"] != d["currency"]
                or d["direction"] != "debit"
                or d["kind"] not in SPENDING
            ):
                raise ValueError("Choose a cash withdrawal in the same currency")
            used = sum(
                t["amount_minor"]
                for t in transactions
                if t["id"] != id and t.get("cash_source_id") == parent["id"]
            ) + self.spend(parent)
            if used + d["amount_minor"] > parent["amount_minor"]:
                raise ValueError("Cash purchases exceed the unallocated withdrawal amount")
        cash_children = [t for t in transactions if t.get("cash_source_id") == id]
        if cash_children and (
            d["kind"] != "cash_withdrawal"
            or any(t["currency"] != d["currency"] for t in cash_children)
            or self.spend(d) + sum(t["amount_minor"] for t in cash_children) > d["amount_minor"]
        ):
            raise ValueError("Adjust linked cash purchases before reallocating this withdrawal")

    @staticmethod
    def reimbursable(t):
        if t.get("excluded"):
            return 0
        if t.get("allocations"):
            return sum(
                a["amount_minor"]
                for a in t["allocations"]
                if a["type"] in ("reimbursable", "lending")
            )
        return t["amount_minor"] if t["kind"] == "lending" else 0

    @staticmethod
    def refundable(t):
        return max(0, Store.spend(t))

    def ingest(
        self,
        email,
        mailbox="local",
        reparse=False,
        store_body=True,
        sync_job_id=None,
    ):
        try:
            result = parse_email(email)
            if sync_job_id is not None:
                # Reject invalid amounts/dates before any source is cached.
                for observation in result["observations"]:
                    self.normalize(observation)
        except (ValueError, OverflowError):
            if sync_job_id is not None:
                return "excluded"
            raise
        authentication = email.get("authentication", {})
        if sync_job_id is not None and (
            authentication.get("verified") is not True
            or not has_transaction_evidence(email, result)
        ):
            # Broad scanning must not turn unrelated or unconfirmed mail into
            # a review archive. Keep only authenticated transaction evidence.
            return "excluded"
        if result["status"] == "parsed" and authentication.get("verified") is not True:
            result.update(status="review", reason=UNVERIFIED, observations=[], audited=False)
        source_id = mailbox + ":" + email["id"]
        encrypted = (
            self.vault.encrypt({"body": email["body"], "authentication": authentication})
            if store_body
            and result["status"] != "excluded"
            and (sync_job_id is None or authentication.get("verified") is True)
            else None
        )
        with self.tx() as db:
            previous = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            if previous and previous["parser_version"] == VERSION and not reparse:
                return previous["status"]
            db.execute(
                """INSERT INTO sources(id,mailbox,sender,subject,received_at,body,attachments,status,reason,template,parser_version,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,sender=excluded.sender,subject=excluded.subject,received_at=excluded.received_at,status=excluded.status,reason=excluded.reason,template=excluded.template,parser_version=excluded.parser_version,attachments=excluded.attachments""",
                (
                    source_id,
                    mailbox,
                    email.get("sender", ""),
                    email.get("subject", ""),
                    email["received_at"],
                    encrypted,
                    encode(email.get("attachments", [])),
                    result["status"],
                    result["reason"],
                    result["template"],
                    VERSION,
                    now(),
                ),
            )
            old = {
                r["event_key"]: dict(r)
                for r in db.execute("SELECT * FROM observations WHERE source_id=?", (source_id,))
            }
            active_keys = set()
            for index, observation in enumerate(result["observations"]):
                d = self.apply_rules(db, self.normalize(observation))
                # Evidence text remains solely in encrypted source cache.
                d.pop("evidence", None)
                key = encode([d.get("reference"), d["kind"], d["direction"], index])
                # A verified parser correction to the meaning of one referenced
                # event must retain its canonical ID and all manual corrections.
                if (
                    result.get("audited")
                    and len(result["observations"]) == 1
                    and len(old) == 1
                    and key not in old
                    and d.get("reference")
                ):
                    previous_key, previous_observation = next(iter(old.items()))
                    prior = json.loads(previous_observation["data"])
                    if all(
                        prior.get(k) == d.get(k)
                        for k in (
                            "reference",
                            "reference_namespace",
                            "amount_minor",
                            "currency",
                            "account",
                            "date",
                        )
                    ):
                        db.execute(
                            "UPDATE observations SET event_key=? WHERE id=?",
                            (key, previous_observation["id"]),
                        )
                        old[key] = old.pop(previous_key)
                active_keys.add(key)
                tid = old[key]["transaction_id"] if key in old else self.find_duplicate(db, d)
                if not tid:
                    tid = uid()
                    db.execute(
                        "INSERT INTO transactions(id,data,created_at) VALUES(?,?,?)",
                        (tid, encode(d), now()),
                    )
                    if sync_job_id is not None:
                        db.execute(
                            "INSERT INTO sync_additions(transaction_id,job_id) VALUES(?,?)",
                            (tid, sync_job_id),
                        )
                elif key in old:
                    # Keep original canonical facts from other evidence when this source is secondary.
                    first = db.execute(
                        "SELECT o.id FROM observations o JOIN sources s ON s.id=o.source_id WHERE o.transaction_id=? ORDER BY (s.mailbox='export'),o.rowid LIMIT 1",
                        (tid,),
                    ).fetchone()
                    if first and first[0] == old[key]["id"]:
                        existing_row = db.execute(
                            "SELECT data,overrides FROM transactions WHERE id=?", (tid,)
                        ).fetchone()
                        existing_data = json.loads(existing_row["data"])
                        existing_overrides = json.loads(existing_row["overrides"])
                        if (
                            existing_data.get("category_inference", {}).get("basis")
                            == "item_details"
                            and d.get("category_inference", {}).get("merchant")
                            == existing_data["category_inference"]["merchant"]
                        ):
                            d.update(
                                category=existing_data["category"],
                                category_inference=existing_data["category_inference"],
                            )
                        if "category" not in existing_overrides and {
                            "counterparty",
                            "counterparty_key",
                            "kind",
                            "direction",
                        }.intersection(existing_overrides):
                            revised = apply_merchant_default(
                                {**d, **existing_overrides, "category": "Uncategorized"}
                            )
                            d["category"] = revised["category"]
                            d.pop("category_inference", None)
                            if (
                                revised.get("category_inference")
                                and revised["category_inference"]["category"] == revised["category"]
                            ):
                                d["category_inference"] = revised["category_inference"]
                        # Reprocessing is not permission to apply a newly created
                        # category rule to historical records. The preview endpoint
                        # updates old canonical categories explicitly.
                        if d.get("rule_id") or existing_data.get("rule_id"):
                            d["category"] = existing_data["category"]
                            d.pop("category_inference", None)
                            if existing_data.get("category_inference"):
                                d["category_inference"] = existing_data["category_inference"]
                            if existing_data.get("rule_id"):
                                d["rule_id"] = existing_data["rule_id"]
                            else:
                                d.pop("rule_id", None)
                        if existing_data.get("separate_event"):
                            d["separate_event"] = True
                        from .merchant_research import current_identification

                        identification = current_identification(
                            {**existing_data, **existing_overrides}
                        )
                        if identification:
                            d["merchant_identification"] = identification
                        if existing_data.get("linked_to"):
                            d["linked_to"] = existing_data["linked_to"]
                        if existing_data.get("financing_source_id"):
                            d["kind"] = "financed_purchase"
                            d["financing_source_id"] = existing_data["financing_source_id"]
                        db.execute("UPDATE transactions SET data=? WHERE id=?", (encode(d), tid))
                else:
                    # A receipt can provide item details absent from the first
                    # bank alert. Enrich only an automatic/unknown category.
                    row = db.execute(
                        "SELECT data,overrides FROM transactions WHERE id=?", (tid,)
                    ).fetchone()
                    canonical, overrides = json.loads(row["data"]), json.loads(row["overrides"])
                    current = canonical.get("category_inference", {})
                    incoming = d.get("category_inference", {})
                    if (
                        not {"category", "counterparty", "counterparty_key"}.intersection(overrides)
                        and not canonical.get("category_manual")
                        and not canonical.get("rule_id")
                        and not d.get("rule_id")
                        and incoming
                        and (
                            canonical["category"] == "Uncategorized"
                            or (
                                current.get("merchant") == incoming.get("merchant")
                                and current.get("basis") == "merchant"
                                and incoming.get("basis") == "item_details"
                            )
                        )
                    ):
                        canonical.update(category=d["category"], category_inference=incoming)
                        db.execute(
                            "UPDATE transactions SET data=? WHERE id=?", (encode(canonical), tid)
                        )
                db.execute(
                    "INSERT INTO observations(id,source_id,event_key,data,transaction_id) VALUES(?,?,?,?,?) ON CONFLICT(source_id,event_key) DO UPDATE SET data=excluded.data",
                    (uid(), source_id, key, encode(d), tid),
                )
                db.execute("INSERT OR IGNORE INTO accounts(name) VALUES(?)", (d["account"],))
                for warning in d["warnings"]:
                    self.issue(db, "evidence", warning, tid, source_id)
                if not result.get("audited"):
                    self.issue(
                        db,
                        "template_audit",
                        "This email format has not been audited against the original evidence",
                        tid,
                        source_id,
                    )
                else:
                    db.execute(
                        "UPDATE issues SET status='resolved' WHERE source_id=? AND kind='template_audit'",
                        (source_id,),
                    )
                    db.execute(
                        "UPDATE issues SET status='resolved' WHERE transaction_id=? AND kind='import_audit'",
                        (tid,),
                    )
                self.flag_possible_duplicates(db, tid, d)
                self.link_evidence(db, tid, d)
            for key, old_row in old.items():
                if key not in active_keys:
                    self.issue(
                        db,
                        "reparse_change",
                        "Parser interpretation changed; previous transaction retained for review",
                        old_row["transaction_id"],
                        source_id,
                    )
            if result["status"] == "review":
                db.execute(
                    "UPDATE issues SET status='resolved' WHERE source_id=? AND kind='unparsed' AND message!=?",
                    (source_id, result["reason"]),
                )
                self.issue(db, "unparsed", result["reason"], source_id=source_id)
            else:
                db.execute(
                    "UPDATE issues SET status='resolved' WHERE source_id=? AND kind IN ('unparsed','incomplete_source','plugin_pending')",
                    (source_id,),
                )
            if email.get("attachments"):
                self.issue(
                    db,
                    "attachment",
                    "Attachments are not parsed; transaction or EMI details may be missing",
                    source_id=source_id,
                )
        return result["status"]

    def discard_unretained_sources(self, mailbox):
        """Remove old unlinked Gmail review/cache records without touching the ledger."""
        if not mailbox:
            return
        with self.tx() as db:
            unused = [
                row[0]
                for row in db.execute(
                    """SELECT s.id FROM sources s WHERE s.mailbox=?
                AND NOT EXISTS (SELECT 1 FROM observations o WHERE o.source_id=s.id)
                AND NOT EXISTS (SELECT 1 FROM issues i
                    WHERE i.source_id=s.id AND i.transaction_id IS NOT NULL)""",
                    (mailbox,),
                )
            ]
            for id in unused:
                db.execute("DELETE FROM issues WHERE source_id=?", (id,))
                db.execute("DELETE FROM sources WHERE id=?", (id,))

    def link_evidence(self, db, tid, d):
        # Gmail backfill is newest-first: a refund or EMI conversion often
        # arrives in the ledger before its original purchase. Revisit matching
        # events when that earlier purchase is finally imported.
        if d["kind"] in SPENDING:
            if d.get("reference"):
                related = [
                    t
                    for t in self.list_transactions()
                    if t["id"] != tid
                    and t["kind"] in ("refund", "financed_purchase", "financing_adjustment")
                    and all(
                        t.get(k) == d.get(k)
                        for k in ("reference", "reference_namespace", "currency", "account")
                    )
                ]
                for event in related:
                    self.link_evidence(db, event["id"], event)
            return
        if d["kind"] not in ("refund", "financed_purchase", "financing_adjustment"):
            return
        if d["kind"] != "refund" and any(
            t.get("financing_source_id") == tid for t in self.list_transactions()
        ):
            return
        if not d.get("reference"):
            self.issue(
                db,
                "unlinked_event",
                "Confirm the original payment for this refund or financing adjustment",
                tid,
            )
            return
        candidates = []
        for t in self.list_transactions():
            if t["id"] == tid or t["direction"] != "debit" or t["kind"] not in SPENDING:
                continue
            if all(
                t.get(k) == d.get(k)
                for k in ("reference", "reference_namespace", "currency", "account")
            ):
                if d["kind"] == "refund" and d["amount_minor"] <= self.refundable(t):
                    candidates.append(t)
                elif d["kind"] != "refund" and d["amount_minor"] == t["amount_minor"]:
                    candidates.append(t)
        if len(candidates) != 1:
            self.issue(
                db,
                "unlinked_event",
                "Original payment could not be matched uniquely; review this event",
                tid,
            )
            return
        parent = candidates[0]
        if d["kind"] == "refund":
            proposed = {**d, "linked_to": parent["id"]}
            try:
                self.validate_relationships(db, proposed, tid)
            except ValueError:
                self.issue(
                    db,
                    "refund_amount",
                    "Refund exceeds the remaining personal share; review allocation",
                    tid,
                )
                return
            db.execute("UPDATE transactions SET data=? WHERE id=?", (encode(proposed), tid))
        else:
            row = db.execute(
                "SELECT data,overrides FROM transactions WHERE id=?", (parent["id"],)
            ).fetchone()
            if "kind" in json.loads(row["overrides"]):
                self.issue(
                    db,
                    "financing_conflict",
                    "Financing evidence conflicts with a manual treatment; confirm which should apply",
                    parent["id"],
                )
                return
            base = json.loads(row["data"])
            base["kind"] = "financed_purchase"
            base["financing_source_id"] = tid
            db.execute("UPDATE transactions SET data=? WHERE id=?", (encode(base), parent["id"]))
            self.audit(db, "financing confirmed", parent["id"], "purchase", "financed_purchase")
        db.execute(
            "UPDATE issues SET status='resolved' WHERE transaction_id=? AND kind='unlinked_event'",
            (tid,),
        )

    def find_duplicate(self, db, d):
        if not d.get("reference"):
            return None
        for row in db.execute(
            "SELECT id,data,overrides FROM transactions WHERE merged_into IS NULL"
        ):
            old = {**json.loads(row["data"]), **json.loads(row["overrides"])}
            if old.get("separate_event"):
                continue
            exact = all(
                old.get(k) == d.get(k)
                for k in (
                    "reference",
                    "reference_namespace",
                    "amount_minor",
                    "currency",
                    "direction",
                    "kind",
                )
            )
            account_ok = old.get("account") == d.get("account") or (
                d.get("reference_namespace") == "upi"
                and "Unknown account" in (old.get("account"), d.get("account"))
            )
            day_delta = abs(
                (datetime.fromisoformat(old["date"]) - datetime.fromisoformat(d["date"])).days
            )
            if exact and account_ok and day_delta <= 3:
                return row["id"]
        return None

    def flag_possible_duplicates(self, db, tid, d):
        for row in db.execute(
            "SELECT id,data,overrides FROM transactions WHERE id!=? AND merged_into IS NULL", (tid,)
        ):
            other = {**json.loads(row["data"]), **json.loads(row["overrides"])}
            if (
                d.get("reference")
                and other.get("reference")
                and d["reference"] != other["reference"]
            ):
                continue
            if (
                all(
                    d.get(k) == other.get(k)
                    for k in ("date", "amount_minor", "currency", "direction", "counterparty")
                )
                and d["counterparty"] != "Unknown recipient"
            ):
                self.issue(
                    db,
                    "possible_duplicate",
                    "Another payment has the same day, amount and recipient; review before merging",
                    tid,
                )
                self.issue(
                    db,
                    "possible_duplicate",
                    "Another payment has the same day, amount and recipient; review before merging",
                    row["id"],
                )

    def source(self, id):
        with self.lock:
            row = self.db.execute("SELECT * FROM sources WHERE id=?", (id,)).fetchone()
            if not row:
                raise ValueError("Source not found")
            result = dict(row)
            body = result.pop("body")
            cache = self.vault.decrypt(body) if body else {}
            result["body"] = cache.get("body")
            result["authentication"] = cache.get("authentication", {"verified": False})
            result["attachments"] = json.loads(result["attachments"])
            result["gmail_id"] = id.rsplit(":", 1)[-1]
            return result

    def list_sources(self):
        with self.lock:
            return [
                dict(r)
                for r in self.db.execute(
                    "SELECT id,sender,subject,received_at,status,reason,template,parser_version,body IS NOT NULL AS cached,missing FROM sources ORDER BY received_at DESC"
                )
            ]

    def reparse(self):
        count = 0
        for s in self.list_sources():
            if s["cached"]:
                # Historical partial imports must not become complete evidence on reparse.
                if s["parser_version"] in {"pending", "incomplete"}:
                    continue
                if self.db.execute(
                    "SELECT 1 FROM issues WHERE source_id=? AND kind='import_conflict' AND status='open'",
                    (s["id"],),
                ).fetchone():
                    continue
                email = self.source(s["id"])
                mailbox, email["id"] = s["id"].rsplit(":", 1)
                self.ingest(email, mailbox, reparse=True)
                count += 1
        return count

    def prune(self):
        today = datetime.now(IST)
        cutoff = today.replace(
            year=today.year - 1,
            day=min(today.day, calendar.monthrange(today.year - 1, today.month)[1]),
        ).isoformat()
        with self.tx() as db:
            db.execute(
                "UPDATE sources SET body=NULL WHERE received_at<? AND id NOT IN (SELECT source_id FROM issues WHERE status='open' AND source_id IS NOT NULL)",
                (cutoff,),
            )

    def import_export(self, payload):
        rows = payload.get("transactions")
        if not isinstance(rows, list):
            raise ValueError("Expected transactions list")
        count = 0
        with self.tx() as db:
            for row in rows:
                sid = "export:" + str(row["id"])
                if db.execute("SELECT 1 FROM sources WHERE id=?", (sid,)).fetchone():
                    continue
                direction = "credit" if row["kind"] == "Bank credit" else "debit"
                detail = row.get("detail", "")
                kind = (
                    "income"
                    if direction == "credit"
                    else "purchase"
                    if row["kind"] == "Card purchase"
                    else "person_payment"
                )
                # Legacy export may contain bill payments among ordinary bank debits.
                if (
                    direction == "debit"
                    and not merchant_match(detail)
                    and re.search(r"CRED|BBPS|BILLDESK|CARD.?PAY|CCPAY|CREDIT.?CARD", detail, re.I)
                ):
                    kind = "card_repayment"
                d = self.normalize(
                    {
                        "date": row["date"],
                        "direction": direction,
                        "kind": kind,
                        "amount_minor": minor(row["amount"]),
                        "currency": "INR",
                        "account": row.get("account", "Unknown account"),
                        "counterparty": detail or "Unknown recipient",
                        "reference": row.get("ref"),
                        "reference_namespace": "legacy",
                        "category": "Uncategorized",
                        "warnings": ["Imported summary; original email has not been verified"],
                    }
                )
                d = self.apply_rules(db, d)
                tid = uid()
                db.execute(
                    "INSERT INTO sources(id,mailbox,sender,subject,received_at,status,reason,template,parser_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        sid,
                        "export",
                        "Local transaction export",
                        detail,
                        row["date"] + "T12:00:00+05:30",
                        "parsed",
                        "Provisional import; original evidence unavailable",
                        "legacy-export",
                        "legacy",
                        now(),
                    ),
                )
                db.execute(
                    "INSERT INTO transactions(id,data,created_at) VALUES(?,?,?)",
                    (tid, encode(d), now()),
                )
                db.execute(
                    "INSERT INTO observations VALUES(?,?,?,?,?)", (uid(), sid, "0", encode(d), tid)
                )
                db.execute("INSERT OR IGNORE INTO accounts(name) VALUES(?)", (d["account"],))
                self.issue(
                    db,
                    "import_audit",
                    "Provisional imported summary; confirm type, amount and original email",
                    tid,
                    sid,
                )
                count += 1
            for row in payload.get("excluded", []):
                sid = "export:" + str(row["id"])
                db.execute(
                    "INSERT OR IGNORE INTO sources(id,mailbox,sender,subject,status,reason,template,parser_version,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        sid,
                        "export",
                        "Local transaction export",
                        row.get("subject", ""),
                        "excluded",
                        row.get("reason", "Excluded in supplied export"),
                        "legacy-export",
                        "legacy",
                        now(),
                    ),
                )
            self.audit(db, "import", None, None, {"count": count})
        return count

    def merge(self, source_id, target_id, *, preserve_target_allocations=False):
        source, target = self.transaction(source_id), self.transaction(target_id)
        if source_id == target_id or source["merged_into"] or target["merged_into"]:
            raise ValueError("Choose two separate transactions")
        if any(source[k] != target[k] for k in ("currency", "direction", "amount_minor")):
            raise ValueError(
                "Merged transactions must have equal amounts, currency and direction; correct them first"
            )
        if source.get("allocations") or (
            target.get("allocations") and not preserve_target_allocations
        ):
            raise ValueError("Remove splits before merging")
        with self.tx() as db:
            if db.execute(
                "SELECT 1 FROM transactions WHERE merged_into=?", (source_id,)
            ).fetchone():
                raise ValueError("Unmerge existing children first")
            for t in self.list_transactions():
                if t.get("linked_to") == source_id:
                    raise ValueError("Unlink repayments or refunds before merging")
            db.execute("UPDATE transactions SET merged_into=? WHERE id=?", (target_id, source_id))
            db.execute(
                "UPDATE issues SET status='resolved' WHERE kind='possible_duplicate' AND transaction_id IN (?,?)",
                (source_id, target_id),
            )
            self.audit(db, "merge", target_id, source_id, target_id)

    def unmerge(self, id):
        with self.tx() as db:
            row = db.execute("SELECT merged_into FROM transactions WHERE id=?", (id,)).fetchone()
            if not row or not row[0]:
                raise ValueError("This transaction is not merged")
            db.execute("UPDATE transactions SET merged_into=NULL WHERE id=?", (id,))
            self.audit(db, "unmerge", id, row[0], None)

    def separate_observation(self, id):
        with self.tx() as db:
            obs = db.execute("SELECT * FROM observations WHERE id=?", (id,)).fetchone()
            if not obs:
                raise ValueError("Observation not found")
            if (
                db.execute(
                    "SELECT COUNT(*) FROM observations WHERE transaction_id=?",
                    (obs["transaction_id"],),
                ).fetchone()[0]
                < 2
            ):
                raise ValueError("Already a separate transaction")
            new = uid()
            data = json.loads(obs["data"])
            data["separate_event"] = True
            db.execute(
                "INSERT INTO transactions(id,data,created_at) VALUES(?,?,?)",
                (new, encode(data), now()),
            )
            db.execute("UPDATE observations SET transaction_id=? WHERE id=?", (new, id))
            self.audit(db, "separate source", new, obs["transaction_id"], id)
            return new

    def rule_from_transaction(self, id, scope):
        t = self.transaction(id)
        if scope not in ("exact", "merchant"):
            raise ValueError("Invalid rule type")
        if scope == "exact" and not (t.get("counterparty_key") and t.get("identity_confirmed")):
            raise ValueError("Confirm this person's identity or UPI ID first")
        if t["counterparty"] == "Unknown recipient":
            raise ValueError("Name the recipient first")
        return {
            "id": uid(),
            "scope": scope,
            "counterparty_key": t.get("counterparty_key"),
            "merchant": t["counterparty"],
            "amount_minor": t["amount_minor"],
            "currency": t["currency"],
            "direction": t["direction"],
            "category": t["category"],
            "created_at": now(),
        }

    def rule_preview(self, id, scope):
        rule = self.rule_from_transaction(id, scope)
        return {
            "rule": rule,
            "matches": [
                t
                for t in self.list_transactions()
                if self.rule_matches(rule, t) and not t["corrected"]
            ],
        }

    def save_rule(self, id, scope, apply_ids):
        rule = self.rule_from_transaction(id, scope)
        matches = {
            t["id"]: t
            for t in self.list_transactions()
            if self.rule_matches(rule, t) and not t["corrected"]
        }
        if set(apply_ids) - set(matches):
            raise ValueError("The preview changed; preview the rule again")
        with self.tx() as db:
            db.execute(
                "INSERT INTO rules VALUES(:id,:scope,:counterparty_key,:merchant,:amount_minor,:currency,:direction,:category,:created_at)",
                rule,
            )
            for tid in apply_ids:
                data = json.loads(
                    db.execute("SELECT data FROM transactions WHERE id=?", (tid,)).fetchone()[0]
                )
                data.update(category=rule["category"], rule_id=rule["id"])
                db.execute("UPDATE transactions SET data=? WHERE id=?", (encode(data), tid))
            self.audit(db, "save rule", rule["id"], None, {**rule, "historical_ids": apply_ids})
        return rule

    def add_alias(self, alias, name, identity=None):
        if not alias.strip() or not name.strip():
            raise ValueError("Provide an alias and a name")
        identity = identity or "person:" + uid()
        with self.tx() as db:
            db.execute(
                "INSERT INTO aliases VALUES(?,?,?) ON CONFLICT(alias) DO UPDATE SET identity=excluded.identity,name=excluded.name",
                (alias.strip().casefold(), identity, name.strip()),
            )
        return identity

    def report(self, month=None, currency="INR"):
        current = datetime.now(IST)
        month = month or current.strftime("%Y-%m")
        try:
            selected = datetime.strptime(month, "%Y-%m")
        except ValueError:
            raise ValueError("Use YYYY-MM for the month") from None
        all_t = self.list_transactions()
        t = [x for x in all_t if x["currency"] == currency]
        dates = []
        for offset in range(6, -1, -1):
            number = current.year * 12 + current.month - 1 - offset
            dates.append(f"{number // 12:04d}-{number % 12 + 1:02d}")

        def totals(items):
            return {
                "spend_minor": sum(x["spend_minor"] for x in items),
                "gross_minor": sum(max(0, x["spend_minor"]) for x in items),
                "refund_minor": -sum(min(0, x["spend_minor"]) for x in items),
                "emi_minor": sum(x["spend_minor"] for x in items if x["kind"] == "emi"),
                "people_minor": sum(
                    x["spend_minor"] for x in items if x["kind"] == "person_payment"
                ),
                "movement_minor": sum(
                    x["amount_minor"]
                    for x in items
                    if x["direction"] == "debit" and not x["spend_minor"]
                ),
                "count": len(items),
                "review_count": sum(bool(x["issues"]) for x in items),
                "review_amount_minor": sum(x["amount_minor"] for x in items if x["issues"]),
            }

        months = [
            {
                "month": m,
                "current": m == current.strftime("%Y-%m"),
                **totals([x for x in t if x["date"].startswith(m)]),
            }
            for m in dates
        ]
        chosen = [x for x in t if x["date"].startswith(month)]
        groups = defaultdict(int)
        merchants = defaultdict(int)
        for x in chosen:
            if x["spend_minor"]:
                if x.get("allocations"):
                    for a in x["allocations"]:
                        if a["type"] == "personal":
                            groups[a.get("category") or x["category"]] += a["amount_minor"]
                else:
                    groups[x["category"]] += x["spend_minor"]
                merchants[x["counterparty"]] += x["spend_minor"]
        previous_number = selected.year * 12 + selected.month - 2
        previous_month = f"{previous_number // 12:04d}-{previous_number % 12 + 1:02d}"
        prev = [
            x
            for x in t
            if x["date"].startswith(previous_month)
            and (month != current.strftime("%Y-%m") or int(x["date"][-2:]) <= current.day)
        ]
        recurring = []
        by_person = defaultdict(list)
        for x in t:
            if x["spend_minor"] > 0 and x["counterparty"] != "Unknown recipient":
                by_person[(x["counterparty"], x["currency"])].append(x)
        for (name, _), items in by_person.items():
            items = sorted(items, key=lambda x: x["date"])
            if len(items) < 3:
                continue
            for idx in range(len(items) - 2):
                sample = items[idx : idx + 3]
                gaps = [
                    (
                        datetime.fromisoformat(sample[i + 1]["date"])
                        - datetime.fromisoformat(sample[i]["date"])
                    ).days
                    for i in (0, 1)
                ]
                amounts = [x["amount_minor"] for x in sample]
                if all(25 <= g <= 35 for g in gaps) and max(amounts) <= min(amounts) * 1.1:
                    recurring.append(
                        {
                            "name": name,
                            "amount_minor": amounts[-1],
                            "count": len(items),
                            "last_date": items[-1]["date"],
                        }
                    )
                    break
        with self.lock:
            sources = [
                dict(r)
                for r in self.db.execute(
                    "SELECT sender,status,template,COUNT(*) count,MIN(received_at) first_seen,MAX(received_at) last_seen FROM sources GROUP BY sender,status,template"
                )
            ]
            issues_count = self.db.execute(
                "SELECT COUNT(*) FROM issues WHERE status='open'"
            ).fetchone()[0]
            source_reviews = dict(
                self.db.execute(
                    "SELECT substr(s.received_at,1,7),COUNT(DISTINCT s.id) FROM sources s JOIN issues i ON i.source_id=s.id WHERE i.status='open' AND s.received_at IS NOT NULL GROUP BY substr(s.received_at,1,7)"
                )
            )
        selected_totals = totals(chosen)
        selected_totals["source_review_count"] = source_reviews.get(month, 0)
        selected_totals["provisional"] = bool(
            selected_totals["review_count"] or selected_totals["source_review_count"]
        )
        for m in months:
            m["source_review_count"] = source_reviews.get(m["month"], 0)
            m["provisional"] = bool(m["review_count"] or m["source_review_count"])
        previous_categories = defaultdict(int)
        for x in prev:
            previous_categories[x["category"]] += x["spend_minor"]
        growing = [
            {"category": k, "change_minor": v - previous_categories[k]}
            for k, v in groups.items()
            if v > previous_categories[k] and previous_categories[k] > 0
        ]
        return {
            "month": month,
            "currency": currency,
            "months": months,
            "totals": selected_totals,
            "previous_totals": totals(prev),
            "categories": [
                {"name": k, "amount_minor": v}
                for k, v in sorted(groups.items(), key=lambda kv: kv[1], reverse=True)
            ],
            "merchants": [
                {"name": k, "amount_minor": v}
                for k, v in sorted(merchants.items(), key=lambda kv: kv[1], reverse=True)
            ][:10],
            "recurring": recurring,
            "growing": sorted(growing, key=lambda x: x["change_minor"], reverse=True),
            "avoidable_minor": sum(x["spend_minor"] for x in chosen if x.get("avoidable")),
            "coverage": {
                "sources": sources,
                "open_issues": issues_count,
                "last_sync": self.get_setting("last_sync"),
                "scope": "Email alerts and explicitly imported data. Missing alerts and attachments may leave gaps.",
            },
            "currencies": sorted({x["currency"] for x in all_t} | {"INR"}),
        }

    def close(self):
        self.db.close()
