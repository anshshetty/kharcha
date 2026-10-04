from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .gmail import GmailAuth, SyncEngine
from .security import Vault, data_directory
from .local_access import LocalAccess
from .mobile_access import MobileAccess
from .models import TransactionInput, validated
from .store import Store, encode
from .backup import BACKUP_TABLES, make_backup, restore_backup

ROOT = Path(__file__).resolve().parent.parent


def create_app(store=None, start_scheduler=True):
    data_dir = data_directory() if store is None else store.path.parent
    store = store or Store(data_dir / "monthlycost.sqlite3", Vault())
    port = int(os.environ.get("MONTHLYCOST_PORT", "8765"))
    base = f"http://127.0.0.1:{port}"
    auth = GmailAuth(store, store.vault, base)
    sync = SyncEngine(store, auth)
    from .ai_advisor import Advisor, CONSENT_VERSION, enabled, snapshot

    advisor = Advisor(store)
    from .merchant_research import MerchantResearch

    merchant_research = MerchantResearch(store)
    access = LocalAccess(data_dir, port)
    mobile = MobileAccess(store, data_dir, port)
    csrf = secrets.token_urlsafe(32)

    @contextmanager
    def replacing_data():
        if not sync.lock.acquire(blocking=False):
            raise ValueError("Stop sync before replacing or deleting data")
        try:
            with auth.lock, advisor.suspend(), merchant_research.suspend(), store.lock:
                auth.pending.clear()
                yield
        finally:
            sync.lock.release()

    async def schedule():
        while True:
            if store.get_setting("connection", {}).get("state") == "connected":
                sync.start()
            await asyncio.sleep(900)

    async def schedule_advisor():
        while True:
            if not sync.lock.locked() and store.get_setting("last_sync"):
                await asyncio.to_thread(advisor.start)
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(app):
        if start_scheduler:
            access.publish()
        task = asyncio.create_task(schedule()) if start_scheduler else None
        ai_task = asyncio.create_task(schedule_advisor()) if start_scheduler else None
        mobile_task = asyncio.create_task(mobile.run(app)) if start_scheduler else None
        yield
        if mobile_task:
            mobile_task.cancel()
            try:
                await mobile_task
            except asyncio.CancelledError:
                pass
        advisor.close()
        merchant_research.close()
        if start_scheduler:
            access.remove()
        if ai_task:
            ai_task.cancel()
        sync.stop.set()
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Kharcha local API", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.auth = auth
    app.state.sync = sync
    app.state.local_access = access
    app.state.mobile_access = mobile
    app.state.advisor = advisor
    app.state.merchant_research = merchant_research

    @app.middleware("http")
    async def local_security(request, call_next):
        mobile_origin = request.scope.get("monthlycost.mobile")
        if not mobile_origin and request.url.hostname not in (
            ["127.0.0.1", "localhost", "testserver"]
            if not start_scheduler
            else ["127.0.0.1", "localhost"]
        ):
            return JSONResponse({"error": "Invalid host header"}, 400)
        origin = request.headers.get("origin")
        allowed_origins = (
            (mobile_origin,) if mobile_origin else (base, "http://localhost:" + str(port))
        )
        if origin and origin not in allowed_origins:
            return JSONResponse({"error": "Cross-origin requests are blocked"}, 403)
        # Google's OAuth redirect retains cross-site metadata on the final
        # document navigation. Permit only the app shell, never data APIs.
        oauth_landing = (
            request.method == "GET"
            and request.url.path == "/"
            and request.headers.get("sec-fetch-mode") == "navigate"
            and request.headers.get("sec-fetch-dest") == "document"
        )
        if (
            request.headers.get("sec-fetch-site") == "cross-site"
            and request.url.path != "/api/auth/callback"
            and not oauth_landing
        ):
            return JSONResponse({"error": "Cross-site requests are blocked"}, 403)
        pairing = bool(
            mobile_origin and request.url.path in ("/api/mobile/pair", "/api/mobile/pair-status")
        )
        if mobile_origin and (
            request.url.path.startswith("/api/auth/")
            or (request.url.path.startswith("/api/mobile/") and not pairing)
            or request.url.path
            in ("/api/backup", "/api/restore", "/api/erase", "/api/import/local")
        ):
            return JSONResponse({"error": "Use Kharcha on the Mac for this action."}, 403)
        public_api = (
            pairing
            or request.url.path == "/api/health"
            or (not mobile_origin and request.url.path == "/api/auth/callback")
        )
        phone_session = (
            mobile.session(request.headers.get("authorization", "")) if mobile_origin else None
        )
        request.state.phone_session = phone_session
        if request.url.path.startswith("/api/") and not public_api:
            authorized = (
                bool(phone_session)
                if mobile_origin
                else access.authorized(request.headers.get("authorization", ""))
            )
            if not authorized:
                return JSONResponse(
                    {
                        "error": "Pair this phone from Settings → Mobile access on the Mac."
                        if mobile_origin
                        else "Open Kharcha using Start Kharcha.command to unlock this browser."
                    },
                    401,
                    headers={"Cache-Control": "no-store"},
                )
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            expected_csrf = phone_session["csrf"] if phone_session else csrf
            if pairing and origin != mobile_origin:
                return JSONResponse({"error": "Pairing requires the same HTTPS origin"}, 403)
            if not pairing and request.headers.get("x-csrf-token") != expected_csrf:
                return JSONResponse({"error": "Refresh the app before making changes"}, 403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"error": "JSON required"}, 415)
        length = request.headers.get("content-length", "0")
        if not length.isdigit() or int(length) > (2048 if pairing else 100_000_000):
            return JSONResponse({"error": "Request too large"}, 413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif response.headers.get("content-type", "").startswith("text/html"):
            # Revalidate the app shell after upgrades instead of letting an
            # older cached page keep loading an obsolete client bundle.
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(ValueError)
    async def bad_request(request, error):
        return JSONResponse({"error": str(error)}, 400)

    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(request, error):
        return JSONResponse(
            {"error": "That change conflicts with existing records. Refresh and try again"}, 409
        )

    @app.exception_handler(KeyError)
    async def missing_field(request, error):
        return JSONResponse(
            {"error": "A required field is missing. Check the form and try again"}, 400
        )

    @app.get("/api/session")
    def session(request: Request):
        phone = request.state.phone_session
        return {"csrf": phone["csrf"] if phone else csrf}

    @app.get("/api/mobile/status")
    def mobile_status():
        return mobile.status()

    @app.put("/api/mobile/preferences")
    async def mobile_preferences(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"enabled"}:
            raise ValueError("Choose whether mobile access is enabled")
        mobile.configure(body["enabled"])
        return mobile.status()

    @app.get("/api/mobile/certificate")
    def mobile_certificate():
        if not mobile.root_path:
            raise ValueError("Wait for mobile HTTPS to start before downloading its certificate")
        return Response(
            mobile.root_path.read_bytes(),
            media_type="application/x-x509-ca-cert",
            headers={"Content-Disposition": 'attachment; filename="Kharcha-Mobile.cer"'},
        )

    @app.post("/api/mobile/invite")
    def mobile_invite():
        return mobile.invite()

    @app.post("/api/mobile/pair")
    async def mobile_pair(request: Request):
        if not request.scope.get("monthlycost.mobile"):
            raise ValueError("Open the pairing link on your phone")
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Enter a pairing link and device name")
        return mobile.request_pair(body.get("secret"), body.get("name"))

    @app.post("/api/mobile/pair-status")
    async def mobile_pair_status(request: Request):
        if not request.scope.get("monthlycost.mobile"):
            raise ValueError("Open the pairing link on your phone")
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Invalid pairing request")
        return mobile.poll(body.get("id"), body.get("poll_token"))

    @app.post("/api/mobile/approve")
    async def mobile_approve(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("id"), str):
            raise ValueError("Choose a pending phone")
        mobile.approve(body["id"])
        return mobile.status()

    @app.post("/api/mobile/revoke")
    async def mobile_revoke(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("id"), str):
            raise ValueError("Choose a phone to disconnect")
        mobile.revoke(body["id"])
        return mobile.status()

    @app.get("/api/status")
    def status(request: Request):
        with store.lock:
            job = store.db.execute(
                "SELECT * FROM jobs ORDER BY started_at DESC,rowid DESC LIMIT 1"
            ).fetchone()
            count = store.db.execute(
                "SELECT COUNT(*) FROM transactions WHERE merged_into IS NULL"
            ).fetchone()[0]
            changes = store.sync_summary(job["id"] if job else None)
            backfill = store.get_setting("backfill")
            scan = None
            if job and job["state"] == "running" and backfill:
                scan = {
                    "months": backfill.get("months", 6),
                    "resumed": backfill.get("origin_job_id") != job["id"],
                    "policy_update": bool(
                        backfill.get("rescan") and store.get_setting("history_id")
                    ),
                }
        return {
            "connection": store.get_setting("connection", {"state": "disconnected"}),
            "configured": store.get_setting("oauth_configured", False),
            "ai_advisor_enabled": enabled(store),
            "ai_advisor_consent_version": CONSENT_VERSION,
            "last_sync": store.get_setting("last_sync"),
            "job": {**dict(job), "added": changes["added"], "scan": scan} if job else None,
            "new_transaction_count": changes["unseen"],
            "transaction_count": count,
            "local_export_available": not request.scope.get("monthlycost.mobile")
            and (ROOT / "august-2026-transactions.json").exists(),
            "data_directory": "" if request.scope.get("monthlycost.mobile") else str(data_dir),
            "mobile_client": bool(request.scope.get("monthlycost.mobile")),
            "parser_note": "Local evidence parser. Original mailbox templates require audit.",
        }

    @app.post("/api/auth/configure")
    async def configure(request: Request):
        auth.configure(await request.json())
        return {"configured": True}

    @app.post("/api/auth/start")
    def start_auth():
        return {"url": auth.start()}

    @app.get("/api/auth/callback")
    def callback(state: str = "", code: str = "", error: str = ""):
        if error:
            return HTMLResponse(
                "<h1>Gmail was not connected</h1><p>Return to Kharcha and try again when ready.</p>",
                400,
            )
        try:
            auth.complete(state, code)
            sync.start()
            return RedirectResponse("/?connected=1", status_code=303)
        except ValueError as exc:
            import html

            return HTMLResponse(
                "<h1>Could not connect Gmail</h1><p>"
                + html.escape(str(exc))
                + "</p><a href='/'>Return to Kharcha</a>",
                400,
            )

    @app.post("/api/auth/disconnect")
    def disconnect():
        if sync.lock.locked():
            raise ValueError("Wait for sync to finish or stop it before disconnecting")
        auth.disconnect()
        return {"disconnected": True}

    @app.post("/api/sync")
    def start_sync():
        if store.get_setting("connection", {}).get("state") != "connected":
            raise ValueError("Connect Gmail first")
        return {"id": sync.start()}

    @app.post("/api/sync/stop")
    def stop_sync():
        sync.stop.set()
        return {"stopping": True}

    @app.post("/api/sync/seen")
    async def mark_sync_seen(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"transaction_ids"}:
            raise ValueError("Choose the transaction IDs to mark as seen")
        return {"marked": store.mark_sync_seen(body["transaction_ids"])}

    @app.get("/api/report")
    def report(month: str | None = None, currency: str = "INR"):
        return store.report(month, currency)

    @app.get("/api/transactions")
    def transactions(
        month: str = "",
        category: str = "",
        search: str = "",
        currency: str = "",
        kind: str = "",
        review: bool = False,
    ):
        return [
            t
            for t in store.list_transactions()
            if (not month or t["date"].startswith(month))
            and (
                not category
                or t["category"] == category
                or any(a.get("category") == category for a in t.get("allocations", []))
            )
            and (not currency or t["currency"] == currency)
            and (not kind or t["kind"] == kind)
            and (not review or t["issues"])
            and (
                not search
                or search.casefold()
                in encode(
                    [
                        t["counterparty"],
                        t.get("merchant_display"),
                        t["account"],
                        t.get("reference"),
                        t["category"],
                    ]
                ).casefold()
            )
        ]

    @app.get("/api/transactions/{id}")
    def transaction(id: str):
        return store.transaction(id)

    @app.get("/api/transactions/{id}/merchant-lookup")
    def merchant_status(id: str):
        return merchant_research.status(id)

    @app.post("/api/transactions/{id}/merchant-lookup")
    def merchant_lookup(id: str):
        return merchant_research.start(id)

    @app.post("/api/transactions/{id}/merchant-lookup/accept")
    async def merchant_accept(id: str, request: Request):
        return merchant_research.accept(id, await request.json())

    @app.post("/api/transactions/{id}/merchant-lookup/dismiss")
    def merchant_dismiss(id: str):
        return merchant_research.dismiss(id)

    @app.delete("/api/transactions/{id}/merchant-lookup/match")
    def merchant_forget(id: str):
        return merchant_research.forget(id)

    @app.post("/api/transactions")
    async def manual(request: Request):
        return store.create_manual(validated(TransactionInput, await request.json(), partial=True))

    @app.patch("/api/transactions/{id}")
    async def update(id: str, request: Request):
        return store.update(id, await request.json())

    @app.patch("/api/transactions/{id}/edit")
    async def save_edit(id: str, request: Request):
        data = await request.json()
        if not isinstance(data, dict) or set(data) - {
            "changes",
            "remember_scope",
            "edit_id",
            "expected",
        }:
            raise ValueError("Expected a correction with an explicit category matching scope")
        return store.save_edit(
            id,
            data.get("changes", {}),
            data.get("remember_scope", "none"),
            data.get("edit_id"),
            data.get("expected"),
        )

    @app.post("/api/transactions/{id}/undo-edit")
    async def undo_edit(id: str, request: Request):
        data = await request.json()
        return store.undo_edit(id, data.get("edit_id"))

    @app.post("/api/transactions/{id}/merge")
    async def merge(id: str, request: Request):
        body = await request.json()
        store.merge(id, body["target_id"])
        return {"merged": True}

    @app.post("/api/transactions/{id}/unmerge")
    def unmerge(id: str):
        store.unmerge(id)
        return {"unmerged": True}

    @app.post("/api/observations/{id}/separate")
    def separate(id: str):
        return {"id": store.separate_observation(id)}

    @app.get("/api/sources")
    def sources():
        return store.list_sources()

    @app.get("/api/sources/{id}")
    def source(id: str):
        return store.source(id)

    @app.post("/api/sources/{id}/transaction")
    async def source_transaction(id: str, request: Request):
        store.source(id)
        data = validated(TransactionInput, await request.json(), partial=True)
        tx = store.create_manual(data)
        with store.tx() as db:
            from .store import uid

            db.execute(
                "INSERT INTO observations VALUES(?,?,?,?,?)",
                (uid(), id, "manual:" + tx["id"], encode(data), tx["id"]),
            )
            db.execute(
                "UPDATE sources SET status='parsed',reason='Manually interpreted' WHERE id=?", (id,)
            )
            db.execute(
                "UPDATE issues SET status='resolved' WHERE source_id=? AND kind IN ('unparsed','decode')",
                (id,),
            )
        return tx

    @app.post("/api/reparse")
    def reparse():
        if sync.lock.locked():
            raise ValueError("Wait for sync to finish before reparsing")
        return {"processed": store.reparse()}

    @app.get("/api/review")
    def review():
        from .insights import review_items

        return review_items(store)

    @app.post("/api/review/{id}/resolve")
    async def resolve(id: str, request: Request):
        body = await request.json()
        with store.tx() as db:
            row = db.execute("SELECT * FROM issues WHERE id=?", (id,)).fetchone()
            if not row:
                raise ValueError("Review item not found")
            note = body.get("note", "").strip()
            if not note:
                raise ValueError("Add a short note explaining the review decision")
            db.execute("UPDATE issues SET status='resolved' WHERE id=?", (id,))
            store.audit(
                db, "review: " + note, row["transaction_id"] or row["source_id"], id, "resolved"
            )
        return {"resolved": True}

    @app.get("/api/categories")
    def categories():
        with store.lock:
            return [r[0] for r in store.db.execute("SELECT name FROM categories ORDER BY name")]

    @app.post("/api/categories")
    async def add_category(request: Request):
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"name"} or not isinstance(body["name"], str):
            raise ValueError("Enter a category name")
        name = body["name"].strip()
        if not name or len(name) > 60:
            raise ValueError("Category must be 1–60 characters")
        with store.tx() as db:
            db.execute("INSERT OR IGNORE INTO categories VALUES(?)", (name,))
        return {"name": name}

    @app.get("/api/rules")
    def rules():
        with store.lock:
            return [
                dict(r) for r in store.db.execute("SELECT * FROM rules ORDER BY created_at DESC")
            ]

    @app.post("/api/rules/preview")
    async def preview_rule(request: Request):
        data = await request.json()
        return store.rule_preview(data["transaction_id"], data.get("scope", "exact"))

    @app.post("/api/rules")
    async def save_rule(request: Request):
        data = await request.json()
        return store.save_rule(
            data["transaction_id"], data.get("scope", "exact"), data.get("apply_ids", [])
        )

    @app.delete("/api/rules/{id}")
    def delete_rule(id: str):
        with store.tx() as db:
            db.execute("DELETE FROM rules WHERE id=?", (id,))
        return {"deleted": True}

    @app.post("/api/aliases")
    async def alias(request: Request):
        data = await request.json()
        return {"identity": store.add_alias(data["alias"], data["name"], data.get("identity"))}

    @app.get("/api/ai-advisor")
    def ai_status(currency: str = "INR", month: str | None = None):
        return advisor.status(currency, month)

    @app.get("/api/ai-advisor/preview")
    def ai_preview(currency: str = "INR", month: str | None = None):
        # Local-only preview of the same structured input used by the runner.
        return snapshot(store, currency, month)

    @app.put("/api/ai-advisor/preferences")
    async def ai_preferences(request: Request):
        body = await request.json()
        if (
            not isinstance(body, dict)
            or set(body) - {"enabled", "consent_version"}
            or type(body.get("enabled")) is not bool
        ):
            raise ValueError("Choose whether to enable AI spending review")
        if body["enabled"] and (
            type(body.get("consent_version")) is not int
            or body["consent_version"] != CONSENT_VERSION
        ):
            raise ValueError(
                "Review and confirm the AI data-sharing notice before enabling insights"
            )
        with advisor.suspend(), store.tx() as db:
            db.executemany(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [
                    ("ai_advisor_enabled", encode(body["enabled"])),
                    (
                        "ai_advisor_consent_version",
                        encode(CONSENT_VERSION if body["enabled"] else None),
                    ),
                ],
            )
        return {"enabled": body["enabled"]}

    @app.get("/api/spending-focus")
    def spending_focus(currency: str = "INR", month: str | None = None):
        from .spending_focus import spending_focus as focus

        return focus(store, currency, month=month)

    @app.post("/api/ai-advisor/refresh")
    def ai_refresh(currency: str = "INR", month: str | None = None):
        if sync.lock.locked():
            raise ValueError("Wait for Gmail sync to finish so analysis uses a consistent snapshot")
        return advisor.start(force=True, currency=currency, month=month)

    @app.get("/api/financial-context")
    def financial_context():
        from .insights import DEFAULT_CONTEXT

        return {**DEFAULT_CONTEXT, **store.get_setting("financial_context", {})}

    @app.put("/api/financial-context")
    @app.patch("/api/financial-context")
    async def save_financial_context(request: Request):
        from .insights import validate_context

        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Use a financial context object")
        with advisor.suspend(), store.tx() as db:
            before = store.get_setting("financial_context", {})
            data = validate_context({**before, **body} if request.method == "PATCH" else body)
            db.execute(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("financial_context", json.dumps(data)),
            )
            store.audit(db, "financial context", "financial_context", before, data)
        return data

    @app.get("/api/financial-brief")
    def financial_brief(month: str = None, currency: str = "INR"):
        from .insights import brief

        return brief(store, month, currency)

    @app.get("/api/accounts")
    def accounts():
        with store.lock:
            return [dict(r) for r in store.db.execute("SELECT * FROM accounts ORDER BY name")]

    @app.post("/api/accounts")
    async def account(request: Request):
        data = await request.json()
        if (
            not isinstance(data, dict)
            or set(data) - {"name", "owned"}
            or not isinstance(data.get("name"), str)
            or type(data.get("owned", False)) is not bool
        ):
            raise ValueError("Provide an account name and a true or false ownership flag")
        name = data.get("name", "").strip()
        if not name or len(name) > 100:
            raise ValueError("Account names must contain 1–100 characters")
        with store.tx() as db:
            db.execute(
                "INSERT INTO accounts(name,owned) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET owned=excluded.owned",
                (name, int(data.get("owned", False))),
            )
        return {"saved": True}

    @app.post("/api/statements/preview")
    async def statement_preview(request: Request):
        from .statements import preview

        body = await request.json()
        return await asyncio.to_thread(preview, store, body)

    @app.post("/api/statements/import")
    async def statement_import(request: Request):
        from .statements import commit

        return commit(store, await request.json())

    @app.post("/api/import")
    async def import_data(request: Request):
        return {"imported": store.import_export(await request.json())}

    @app.post("/api/import/local")
    def import_local():
        path = ROOT / "august-2026-transactions.json"
        if not path.exists():
            raise ValueError("No local transaction export found")
        return {"imported": store.import_export(json.loads(path.read_text()))}

    @app.get("/api/export.csv")
    def export_csv():
        output = io.StringIO()
        writer = csv.writer(output)
        fields = [
            "date",
            "counterparty",
            "account",
            "amount_minor",
            "currency",
            "direction",
            "kind",
            "category",
            "spend_minor",
            "excluded",
            "avoidable",
            "reference",
        ]
        writer.writerow(fields)
        for t in store.list_transactions():
            values = []
            for f in fields:
                value = t.get(f, "")
                if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
                    value = "'" + value
                values.append(value)
            writer.writerow(values)
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=monthlycost-transactions.csv"},
        )

    @app.post("/api/backup")
    async def backup(request: Request):
        body = await request.json()
        content = make_backup(store, body.get("password", ""))
        return {"file": base64.b64encode(content).decode(), "filename": "monthlycost-backup.mcb"}

    @app.post("/api/restore")
    async def restore(request: Request):
        body = await request.json()
        if body.get("confirmation") != "REPLACE":
            raise ValueError("Confirm replacement of the current ledger")
        with replacing_data():
            restore_backup(
                store,
                body.get("password", ""),
                base64.b64decode(body.get("file", ""), validate=True),
            )
            store.vault.delete("gmail-token")
            mobile.clear()
            store.set_setting("mobile_access_enabled", mobile.preference)
        return {"restored": True}

    @app.post("/api/erase")
    async def erase(request: Request):
        if (await request.json()).get("confirmation") != "DELETE":
            raise ValueError("Type DELETE to remove the local ledger")
        with replacing_data():
            with store.tx() as db:
                db.execute("PRAGMA defer_foreign_keys=ON")
                for name in reversed(BACKUP_TABLES):
                    db.execute("DELETE FROM " + name)
                db.execute("DELETE FROM queue")
                db.execute("DELETE FROM jobs")
                from .store import CATEGORIES

                db.executemany("INSERT INTO categories VALUES(?)", [(c,) for c in CATEGORIES])
            for name in ("gmail-token", "oauth-client", "email-cache-key"):
                store.vault.delete(name)
            mobile.clear()
            store.set_setting("mobile_access_enabled", mobile.preference)
            with store.lock:
                store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                store.db.execute("VACUUM")
        return {"deleted": True}

    @app.get("/api/health")
    def health():
        return {"ok": True, "app": "Kharcha"}

    # Static export is produced from the Sites React frontend. All data stays in FastAPI.
    public = ROOT / "frontend/out"
    if not public.exists():
        public = ROOT / "frontend/dist/client"
    if public.exists():
        app.mount("/", StaticFiles(directory=public, html=True), name="frontend")
    else:

        @app.get("/")
        def missing_frontend():
            return HTMLResponse(
                "<h1>Kharcha</h1><p>Build the frontend with npm run build in the frontend folder, then restart the app.</p>",
                503,
            )

    return app
