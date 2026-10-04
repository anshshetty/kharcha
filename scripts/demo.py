#!/usr/bin/env python3
"""Run a disposable, offline Kharcha with entirely invented financial records.

This entry point never opens an existing ledger, OS Keychain, Gmail or Codex.
The temporary ledger is removed on normal exit; its encryption key lives only in memory.
Dependencies and the frontend must already be installed/built (see README).
"""

from __future__ import annotations

import argparse
import calendar
from contextlib import contextmanager
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cryptography.fernet import Fernet  # noqa: E402

from backend.insights import validate_context  # noqa: E402
from backend.parser import IST, VERSION  # noqa: E402
from backend.store import Store, encode  # noqa: E402

DEMO_NOTICE = "SYNTHETIC DEMO · Fictional data · Gmail & AI offline"
ACCOUNT = "Demo Bank •0000"
CARD = "Demo Card •1111"
OFFLINE_LINK_GUARD = """<script>
(() => {
  const disabled = new WeakMap();
  const attributes = ['aria-disabled', 'title', 'role', 'tabindex'];
  function guardLink(link) {
    const href = link.getAttribute('href');
    if (href === null) return disabled.has(link);
    let external = true;
    try {
      external = new URL(href, window.location.href).origin !== window.location.origin;
    } catch (_) {}
    if (!external) {
      const previous = disabled.get(link);
      if (previous) {
        for (const name of attributes) {
          if (previous[name] === null) link.removeAttribute(name);
          else link.setAttribute(name, previous[name]);
        }
        disabled.delete(link);
      }
      return false;
    }
    if (!disabled.has(link)) {
      disabled.set(link, Object.fromEntries(attributes.map(name => [name, link.getAttribute(name)])));
    }
    // Removing href also prevents opening Gmail from a context menu or middle click.
    link.removeAttribute('href');
    link.setAttribute('aria-disabled', 'true');
    link.setAttribute('title', 'Unavailable in the offline demo. External websites stay closed.');
    link.setAttribute('role', 'link');
    link.setAttribute('tabindex', '-1');
    return true;
  }
  function inspect(node) {
    if (node.nodeType !== 1 && node.nodeType !== 9) return;
    if (node.matches?.('a[href]')) guardLink(node);
    for (const link of node.querySelectorAll('a[href]')) guardLink(link);
  }
  for (const type of ['click', 'auxclick', 'contextmenu']) {
    document.addEventListener(type, event => {
      const link = event.target instanceof Element ? event.target.closest('a') : null;
      if (link && guardLink(link)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }, true);
  }
  new MutationObserver(changes => {
    for (const change of changes) {
      if (change.type === 'attributes') inspect(change.target);
      else for (const node of change.addedNodes) inspect(node);
    }
  }).observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: ['href']});
  inspect(document);
})();
</script>"""


class MemoryVault:
    """A separate implementation that cannot fall through to OS credential storage."""

    def __init__(self):
        self.values = {}
        self._cipher = Fernet(Fernet.generate_key())

    def read(self, name):
        return self.values.get(name)

    def write(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)

    def encrypt(self, value):
        return self._cipher.encrypt(encode(value).encode())

    def decrypt(self, value):
        return json.loads(self._cipher.decrypt(value))


def populate(store, as_of):
    """Populate only an empty, caller-created demo store. All amounts are invented."""
    if store.db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]:
        raise ValueError("Demo generation requires a new, empty ledger")
    timestamp = as_of.isoformat() + "T12:00:00+05:30"
    sequence = 0

    def add(day, merchant, rupees, category, *, kind="purchase", **extra):
        nonlocal sequence
        if day > as_of:
            return None
        sequence += 1
        identity = uuid.uuid5(uuid.NAMESPACE_URL, f"kharcha-demo:{day}:{sequence}").hex
        record = store.normalize(
            {
                "date": day.isoformat(),
                "direction": "credit" if kind in {"refund", "reimbursement", "income"} else "debit",
                "kind": kind,
                "amount_minor": rupees * 100,
                "currency": "INR",
                "counterparty": merchant,
                "account": ACCOUNT,
                "category": category,
                "category_manual": True,
                "reference": f"DEMO-{sequence:05d}",
                "notes": "Entirely invented example for the Kharcha demo. No real account or payment.",
                **extra,
            }
        )
        source_id = "demo@example.test:" + identity
        subject = "Synthetic payment example · " + merchant
        body = (
            "FICTIONAL DEMO EMAIL — NOT A REAL PAYMENT\n\n"
            f"{record['currency']} {record['amount_minor'] / 100:,.2f} "
            f"{record['direction']} · {merchant}\n"
            f"Date: {day.isoformat()}\nAccount: {record['account']}\n"
            f"Reference: {record['reference']}\n\n"
            "Created by scripts/demo.py. All merchants, people and amounts are invented."
        )
        with store.tx() as db:
            store.validate_relationships(db, record, identity)
            db.execute(
                "INSERT INTO transactions(id,data,manual,created_at) VALUES(?,?,0,?)",
                (identity, encode(record), timestamp),
            )
            db.execute(
                "INSERT OR IGNORE INTO accounts(name,owned) VALUES(?,1)", (record["account"],)
            )
            db.execute(
                """INSERT INTO sources(id,mailbox,sender,subject,received_at,body,status,
                reason,template,parser_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    source_id,
                    "demo@example.test",
                    "alerts@example.test",
                    subject,
                    day.isoformat() + "T12:00:00+05:30",
                    store.vault.encrypt({"body": body, "authentication": {"verified": False}}),
                    "parsed",
                    "Synthetic fixture, not an authenticated bank email",
                    "synthetic-demo",
                    VERSION,
                    timestamp,
                ),
            )
            db.execute(
                "INSERT INTO observations VALUES(?,?,?,?,?)",
                (identity, source_id, "demo:" + identity, encode(record), identity),
            )
        return identity

    for offset in range(6, -1, -1):
        month_number = as_of.year * 12 + as_of.month - 1 - offset
        year, month = month_number // 12, month_number % 12 + 1

        def day(n):
            return date(year, month, min(n, calendar.monthrange(year, month)[1]))

        variation = (6 - offset) * 35
        add(day(1), "Demo Studio Payroll", 125000, "Income", kind="income")
        add(day(2), "Demo Maple House Rent", 24000, "Rent & home")
        add(day(3), "Demo Fibre Internet", 899, "Utilities")
        add(day(4), "Demo Laptop Installment", 4200, "EMIs", kind="emi", account=CARD)
        add(day(5), "Demo Cloudbox", 199, "Subscriptions", account=CARD)
        add(day(6), "Demo Cinema Club", 349, "Subscriptions", account=CARD)
        add(day(7), "Demo Health Cover", 1800, "Insurance")
        add(day(8), "Demo Power Utility", 1420 + variation, "Utilities")
        add(day(9), "Demo Future Fund", 12000, "Investments", kind="investment")
        add(day(10), "Demo Savings Account", 15000, "Own transfers", kind="own_transfer")
        add(day(11), "Demo Card Bill", 16480 + variation, "Card repayments", kind="card_repayment")
        add(
            day(12),
            "Demo Mira · family support",
            2500,
            "Family & gifts",
            kind="person_payment",
            counterparty_key="person:demo-mira",
            identity_confirmed=True,
        )
        add(day(13), "Demo Clinic", 850, "Health")
        add(day(14), "Demo Book Nook", 1240 + variation, "Education", account=CARD)
        add(day(18), "Demo Community Garden", 500, "Donations")
        add(day(22), "Demo Phone Plan", 399, "Utilities")
        for index, n in enumerate((3, 9, 14, 20, 27)):
            add(day(n), "Demo Green Basket", 1260 + index * 170 + variation, "Groceries")
        for index, n in enumerate((2, 5, 8, 11, 14, 17, 21, 24, 28)):
            add(
                day(n),
                "Demo Copper Cafe",
                280 + (index % 3) * 125 + variation,
                "Food & dining",
                avoidable=index % 3 == 0,
            )
        for index, n in enumerate((4, 7, 10, 13, 16, 19, 23, 26)):
            add(day(n), "Demo Metro Rides", 160 + index * 20, "Transport")
        shared = add(
            day(8),
            "Demo Lantern Table · shared dinner",
            3600,
            "Food & dining",
            allocations=[
                {"type": "personal", "amount_minor": 120000, "category": "Food & dining"},
                {"type": "reimbursable", "amount_minor": 240000, "person": "Demo Arun & Demo Tara"},
            ],
        )
        if shared:
            add(
                day(10),
                "Demo Arun & Demo Tara",
                2400,
                "Reimbursements",
                kind="reimbursement",
                linked_to=shared,
                counterparty_key="person:demo-friends",
                identity_confirmed=True,
            )
        purchase = add(day(6), "Demo Trail Outfitters", 4200 + variation, "Shopping", account=CARD)
        if purchase:
            add(
                day(12),
                "Demo Trail Outfitters · returned item",
                1200,
                "Shopping",
                kind="refund",
                linked_to=purchase,
                account=CARD,
            )
        add(day(15), "Demo Harbor Cinema", 780, "Entertainment", account=CARD)
        if offset in {1, 3, 5}:
            add(day(16), "Demo Coast Retreat", 11800 + variation, "Travel", account=CARD)
        if offset == 0:
            for n, amount in ((7, 1840), (12, 2190)):
                add(day(n), "Demo Homeworks", amount, "Shopping", account=CARD)
            unknown = add(day(13), "DEMO UPI · merchant unclear", 1650, "Uncategorized")
            duplicate = add(day(14), "Demo Copper Cafe", 615, "Food & dining")
            for identity, issue_kind, message in (
                (
                    unknown,
                    "missing_identity",
                    "Synthetic review example: confirm the merchant and category.",
                ),
                (
                    duplicate,
                    "possible_duplicate",
                    "Synthetic review example: compare the receipt before merging.",
                ),
            ):
                if identity:
                    with store.tx() as db:
                        store.issue(db, issue_kind, message, transaction_id=identity)
                        db.execute(
                            "UPDATE issues SET created_at=? WHERE transaction_id=?",
                            (timestamp, identity),
                        )
            add(day(11), "Demo Work Supplies · employer expense", 2750, "Shopping", excluded=True)
            add(day(12), "Demo ATM", 3000, "Cash withdrawals", kind="cash_withdrawal")
            add(day(9), "Demo Design Library", 29, "Education", currency="USD", account=CARD)

    store.set_setting(
        "financial_context",
        validate_context(
            {
                "priorities": "Synthetic plan: keep everyday spending within ₹55,000 and make room for a weekend trip.",
                "commitments": "Demo home rent ₹24,000; laptop installment ₹4,200; internet and health cover.",
                "people": "Demo Arun and Demo Tara share occasional dinners. Only the personal share counts as spending.",
                "notes": "Fictional demonstration. Refunds reduce expenses; transfers and card repayments do not count twice.",
                "project_categories": ["Travel"],
                "fixed_categories": ["Rent & home", "EMIs", "Insurance"],
                "unavoidable_categories": ["Utilities", "Health"],
                "monthly_target_minor": 5500000,
                "currency": "INR",
            }
        ),
    )
    store.set_setting("connection", {"state": "disconnected"})
    store.set_setting("ai_advisor_enabled", False)
    store.set_setting("last_sync", timestamp)
    store.set_setting("demo", {"synthetic": True, "as_of": as_of.isoformat()})
    return {"as_of": as_of.isoformat(), "transactions": sequence, "months": 7}


@contextmanager
def demo_store(as_of):
    # Explicit /tmp avoids environment-controlled TMPDIR and MONTHLYCOST_DATA_DIR.
    # No arbitrary destination/overwrite option is intentionally provided.
    with tempfile.TemporaryDirectory(prefix="kharcha-demo-", dir="/tmp") as directory:
        path = Path(directory)
        path.chmod(0o700)
        store = Store(path / "demo.sqlite3", MemoryVault())
        try:
            populate(store, as_of)
            yield store
        finally:
            store.close()


READ_ROUTES = re.compile(
    r"/api/(?:health|session|status|report|transactions(?:/[^/]+(?:/merchant-lookup)?)?"
    r"|sources(?:/[^/]+)?|review|categories|rules|accounts|financial-context"
    r"|financial-brief|spending-focus|ai-advisor(?:/preview)?|export\.csv)"
)
WRITE_ROUTES = {
    "POST": re.compile(
        r"/api/(?:transactions|transactions/[^/]+/undo-edit|review/[^/]+/resolve|categories|sync/seen|report\.pdf)"
    ),
    "PATCH": re.compile(r"/api/(?:transactions/[^/]+(?:/edit)?|financial-context)"),
    "PUT": re.compile(r"/api/financial-context"),
}


def illustrative_analysis(store, as_of, currency, month=None):
    """Locally computed example cards, explicitly distinguished from an AI run."""
    from backend.spending_focus import spending_focus

    focus = spending_focus(store, currency, as_of=as_of, month=month)
    patterns = []
    if focus["merchants"]:
        merchant = focus["merchants"][0]
        patterns.append(
            {
                "title": "Illustration · largest merchant",
                "detail": f"{merchant['name']} contributes {currency} "
                f"{merchant['amount_minor'] / 100:,.2f} to regular spending in this fictional month. "
                "Open the evidence to inspect each payment.",
                "confidence": "high",
                "transaction_ids": merchant["transaction_ids"][:4],
            }
        )
    if focus["repeat_merchants"]:
        merchant = max(focus["repeat_merchants"], key=lambda value: value["count"])
        patterns.append(
            {
                "title": "Illustration · repeated spending",
                "detail": f"{merchant['name']} appears {merchant['count']} times, totaling "
                f"{currency} {merchant['amount_minor'] / 100:,.2f}. "
                "Repeated payments are visible without assuming they are a subscription.",
                "confidence": "high",
                "transaction_ids": merchant["transaction_ids"][:4],
            }
        )
    shared = next(
        (
            row
            for row in store.list_transactions()
            if row["date"].startswith(focus["month"])
            and row["currency"] == currency
            and row["allocations"]
        ),
        None,
    )
    if shared:
        patterns.append(
            {
                "title": "Illustration · shared expense",
                "detail": f"The fictional dinner payment is {currency} {shared['amount_minor'] / 100:,.2f}; "
                f"only the personal share of {currency} {shared['spend_minor'] / 100:,.2f} "
                "counts toward spending. Open the payment to inspect the split.",
                "confidence": "high",
                "transaction_ids": [shared["id"]],
            }
        )
    return {
        "state": "complete",
        "focus_month": focus["month"],
        "currency": currency,
        "generated_at": as_of + "T12:00:00+05:30",
        "message": "Illustrative demo cards calculated locally. No AI service was called.",
        "result": {
            "summary": "Illustrative insights from fictional data, calculated locally without an AI service.",
            "patterns": patterns,
            "actions": [],
            "commitments": [],
            "uncertainties": [],
            "target": {"amount_minor": None, "currency": currency, "reason": ""},
        },
    }


def create_demo_app(store, port):
    """Reuse the real UI and accounting with only explicitly safe routes available."""
    from backend.app import create_app
    from fastapi.responses import HTMLResponse, JSONResponse

    if not isinstance(store.vault, MemoryVault) or store.path.name != "demo.sqlite3":
        raise ValueError("Only a disposable demo store can serve the demo")
    as_of = store.get_setting("demo", {}).get("as_of")
    if not as_of:
        raise ValueError("Missing synthetic demo fixture")
    previous_port = os.environ.get("MONTHLYCOST_PORT")
    os.environ["MONTHLYCOST_PORT"] = str(port)
    try:
        app = create_app(store=store, start_scheduler=False)
    finally:
        if previous_port is None:
            os.environ.pop("MONTHLYCOST_PORT", None)
        else:
            os.environ["MONTHLYCOST_PORT"] = previous_port

    @app.middleware("http")
    async def offline_demo(request, call_next):
        path, method = request.url.path, request.method
        if path.startswith("/api/"):
            allowed = (
                READ_ROUTES.fullmatch(path)
                if method in {"GET", "HEAD"}
                else (WRITE_ROUTES[method].fullmatch(path) if method in WRITE_ROUTES else None)
            )
            if not allowed:
                return JSONResponse({"error": "Disabled in the offline synthetic demo."}, 403)
        response = await call_next(request)
        if path == "/api/status" and response.status_code == 200:
            body = json.loads(b"".join([chunk async for chunk in response.body_iterator]))
            body.update(
                local_export_available=False, demo=True, data_directory="Disposable synthetic demo"
            )
            return JSONResponse(
                body,
                headers={
                    "Cache-Control": "no-store",
                    "X-Kharcha-Demo": "synthetic",
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                    "X-Frame-Options": "DENY",
                },
            )
        if path == "/api/spending-focus" and response.status_code == 200:
            from backend.spending_focus import spending_focus

            return JSONResponse(
                spending_focus(
                    store,
                    request.query_params.get("currency", "INR"),
                    as_of=as_of,
                    month=request.query_params.get("month"),
                ),
                headers={"Cache-Control": "no-store", "X-Kharcha-Demo": "synthetic"},
            )
        if path == "/api/ai-advisor" and response.status_code == 200:
            return JSONResponse(
                illustrative_analysis(
                    store,
                    as_of,
                    request.query_params.get("currency", "INR"),
                    request.query_params.get("month"),
                ),
                headers={"Cache-Control": "no-store", "X-Kharcha-Demo": "synthetic"},
            )
        if path in {"/", "/index.html"} and response.status_code == 200:
            body = b"".join([chunk async for chunk in response.body_iterator]).decode()
            ribbon = (
                '<div class="synthetic-demo-notice" role="note" style="position:fixed;bottom:12px;left:50%;'
                "transform:translateX(-50%);z-index:99999;padding:8px 16px;border-radius:99px;"
                "background:#13291f;color:#d7ffe7;border:1px solid #77a98c;"
                "font:600 12px/1.4 system-ui;text-align:center;max-width:90vw;"
                'box-shadow:0 2px 14px #0002;pointer-events:none">' + DEMO_NOTICE + "</div>"
            )
            return HTMLResponse(
                body.replace("</body>", ribbon + OFFLINE_LINK_GUARD + "</body>"),
                headers={
                    "Cache-Control": "no-store",
                    "X-Kharcha-Demo": "synthetic",
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                    "X-Frame-Options": "DENY",
                },
            )
        response.headers["X-Kharcha-Demo"] = "synthetic"
        return response

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=datetime.now(IST).date(),
        help="Last fixture day (YYYY-MM-DD); defaults to today in India",
    )
    parser.add_argument("--port", type=int, default=8875)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--check", action="store_true", help="Validate a temporary fixture and exit"
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or args.port == 8765:
        parser.error("Choose a port from 1024–65535 other than the production default 8765")
    if args.as_of.year < 2000:
        parser.error("Choose an as-of date in 2000 or later")
    if not args.check and not any(
        (ROOT / directory / "index.html").is_file()
        for directory in ("frontend/out", "frontend/dist/client")
    ):
        parser.error("Build the interface first: cd frontend && npm ci && npm run build")
    with demo_store(args.as_of) as store:
        if args.check:
            rows = store.list_transactions()
            print(
                json.dumps(
                    {
                        "synthetic": True,
                        "transactions": len(rows),
                        "as_of": str(args.as_of),
                        "months": len({row["date"][:7] for row in rows}),
                    }
                )
            )
            return
        import uvicorn

        # Bind before opening the browser. Never attach to an existing server.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(("127.0.0.1", args.port))
                listener.listen(128)
            except OSError:
                parser.error(
                    "Demo port is occupied; choose another --port. No existing app was opened."
                )
            app = create_demo_app(store, args.port)
            access = app.state.local_access
            access.publish()
            url = f"http://127.0.0.1:{args.port}/#access_token={access.token}"
            # Private file is useful for an automated screenshot session; never include in docs.
            launch_file = store.path.parent / "open-demo.url"
            descriptor = os.open(launch_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(url + "\n")
            server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))

            def open_when_ready():
                while not server.started and not server.should_exit:
                    threading.Event().wait(0.1)
                if server.started:
                    webbrowser.open(url)

            print(
                f"{DEMO_NOTICE}\nDemo: http://127.0.0.1:{args.port}\n"
                f"Private browser launch URL: {launch_file}\n"
                f"{len(store.list_transactions())} fictional transactions. "
                "Press Control-C to stop and remove this temporary ledger.",
                flush=True,
            )
            if not args.no_browser:
                threading.Thread(target=open_when_ready, daemon=True).start()
            try:
                server.run(sockets=[listener])
            except KeyboardInterrupt:
                pass
            finally:
                access.remove()


if __name__ == "__main__":
    main()
