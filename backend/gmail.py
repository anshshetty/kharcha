from __future__ import annotations

import base64
import calendar
import hashlib
import json
import secrets
import threading
import time
from datetime import datetime
from urllib.parse import urlencode

import httpx

from .email_filter import (
    POLICY_VERSION,
    is_received_message,
    received_query,
)
from .parser import IST, decode_message
from .store import encode, now, uid

API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SYNC_MONTHS = 6


class ReconnectRequired(Exception):
    pass


class GmailAuth:
    def __init__(self, store, vault, base_url):
        self.store, self.vault, self.base_url = store, vault, base_url
        self.pending = {}
        self.lock = threading.RLock()

    def configure(self, document):
        if not isinstance(document, dict):
            raise ValueError("Upload a Google Desktop OAuth client JSON object")
        cfg = document.get("installed")
        if (
            not isinstance(cfg, dict)
            or not isinstance(cfg.get("client_id"), str)
            or not cfg["client_id"].endswith(".apps.googleusercontent.com")
            or not isinstance(cfg.get("client_secret", ""), str)
        ):
            raise ValueError("Upload the OAuth JSON for a Desktop app, not a Web application")
        self.vault.write(
            "oauth-client",
            encode({"client_id": cfg["client_id"], "client_secret": cfg.get("client_secret", "")}),
        )
        self.store.set_setting("oauth_configured", True)

    def start(self):
        raw = self.vault.read("oauth-client")
        if not raw:
            raise ValueError("Add your Google Desktop OAuth client first")
        cfg = json.loads(raw)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        with self.lock:
            self.pending = {
                state: {"verifier": verifier, "expires": time.time() + 600, "config": cfg}
            }
        params = {
            "client_id": cfg["client_id"],
            "redirect_uri": self.base_url + "/api/auth/callback",
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)

    def complete(self, state, code):
        with self.lock:
            return self._complete(state, code)

    def _complete(self, state, code):
        with self.lock:
            pending = self.pending.pop(state, None)
        if not pending or pending["expires"] < time.time():
            raise ValueError("Sign-in expired. Start connecting again from the app")
        cfg = pending["config"]
        with httpx.Client(timeout=30) as client:
            response = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    **cfg,
                    "code": code,
                    "code_verifier": pending["verifier"],
                    "grant_type": "authorization_code",
                    "redirect_uri": self.base_url + "/api/auth/callback",
                },
            )
            if response.status_code != 200:
                raise ValueError("Google could not complete sign-in. Please reconnect")
            token = response.json()
            granted = set(token.get("scope", SCOPE).split())
            if SCOPE not in granted:
                raise ValueError("Gmail read-only access was not granted")
            profile = client.get(
                API + "/profile", headers={"Authorization": "Bearer " + token["access_token"]}
            )
            if profile.status_code != 200:
                raise ValueError(
                    "Gmail API is unavailable. Check that it is enabled in your Google project"
                )
            mailbox = profile.json()["emailAddress"].lower()
        bound = self.store.get_setting("bound_mailbox")
        if bound and bound != mailbox:
            raise ValueError(
                "This ledger belongs to a different Gmail account. Reconnect that account or erase this ledger before switching"
            )
        if not token.get("refresh_token"):
            raise ValueError("Google did not grant persistent access. Reconnect with consent")
        token["expires_at"] = time.time() + token.get("expires_in", 3600)
        self.vault.write("gmail-token", encode(token))
        self.store.set_setting("bound_mailbox", mailbox)
        self.store.set_setting("connection", {"state": "connected", "email": mailbox})
        return mailbox

    def token(self):
        with self.lock:
            raw = self.vault.read("gmail-token")
            if not raw:
                raise ReconnectRequired("Connect Gmail to synchronize")
            token = json.loads(raw)
            if token.get("expires_at", 0) > time.time() + 90:
                return token["access_token"]
            cfg = json.loads(self.vault.read("oauth-client") or "{}")
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        **cfg,
                        "grant_type": "refresh_token",
                        "refresh_token": token["refresh_token"],
                    },
                )
            if response.status_code != 200:
                if response.status_code in (400, 401):
                    self.store.set_setting(
                        "connection",
                        {"state": "reconnect", "email": self.store.get_setting("bound_mailbox")},
                    )
                    raise ReconnectRequired(
                        "Google access expired or was revoked. Reconnect Gmail; your history is safe"
                    )
                raise ValueError("Google token refresh is temporarily unavailable")
            token.update(response.json())
            token["expires_at"] = time.time() + token.get("expires_in", 3600)
            self.vault.write("gmail-token", encode(token))
            return token["access_token"]

    def disconnect(self):
        raw = self.vault.read("gmail-token")
        if raw:
            token = json.loads(raw)
            try:
                with httpx.Client(timeout=15) as client:
                    client.post(
                        "https://oauth2.googleapis.com/revoke",
                        data={"token": token.get("refresh_token", token.get("access_token"))},
                    )
            except httpx.HTTPError:
                pass
        self.vault.delete("gmail-token")
        self.store.set_setting(
            "connection",
            {"state": "disconnected", "email": self.store.get_setting("bound_mailbox")},
        )


class SyncEngine:
    def __init__(self, store, auth):
        self.store, self.auth = store, auth
        self.lock = threading.Lock()
        self.stop = threading.Event()
        with store.tx() as db:
            db.execute(
                "UPDATE jobs SET state='interrupted',error='App stopped during sync; resume to continue' WHERE state='running'"
            )

    def request(self, path, params=None):
        for attempt in range(6):
            if self.stop.is_set():
                raise ValueError("Sync stopped")
            try:
                with httpx.Client(timeout=40) as client:
                    result = client.get(
                        API + path,
                        params=params,
                        headers={"Authorization": "Bearer " + self.auth.token()},
                    )
                if result.status_code == 401:
                    self.store.set_setting(
                        "connection",
                        {"state": "reconnect", "email": self.store.get_setting("bound_mailbox")},
                    )
                    raise ReconnectRequired("Gmail authorization needs reconnection")
                if result.status_code == 404:
                    return None
                if result.status_code == 403:
                    reason = result.json().get("error", {}).get("errors", [{}])[0].get("reason", "")
                    if reason not in ("rateLimitExceeded", "userRateLimitExceeded"):
                        raise ValueError(
                            "Gmail access is blocked or its quota is exhausted; check Google project settings"
                        )
                elif result.status_code not in (429, 500, 502, 503, 504):
                    if result.status_code != 200:
                        raise ValueError("Gmail could not process the request")
                    return result.json()
                retry = result.headers.get("Retry-After", "")
                delay = min(60, float(retry)) if retry.isdigit() else min(32, 2**attempt)
            except (httpx.TimeoutException, httpx.NetworkError):
                delay = min(32, 2**attempt)
            if attempt == 5:
                break
            self.stop.wait(delay)
        raise ValueError("Gmail is temporarily unavailable. Progress is saved; try syncing again")

    def start(self):
        if not self.lock.acquire(blocking=False):
            with self.store.lock:
                row = self.store.db.execute(
                    "SELECT id FROM jobs WHERE state='running' ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
                return row[0] if row else None
        self.stop.clear()
        id = uid()
        with self.store.tx() as db:
            db.execute(
                "INSERT INTO jobs(id,state,phase,started_at) VALUES(?,'running','Starting',?)",
                (id, now()),
            )
        threading.Thread(target=self._run, args=(id,), daemon=True).start()
        return id

    def phase(self, id, phase):
        with self.store.tx() as db:
            db.execute("UPDATE jobs SET phase=? WHERE id=?", (phase, id))

    def _run(self, id):
        try:
            self.auth.token()
            self.store.discard_unretained_sources(self.store.get_setting("bound_mailbox"))
            history = self.store.get_setting("history_id")
            if (
                history
                and not self.store.get_setting("backfill")
                and self.store.get_setting("gmail_scan_policy") == POLICY_VERSION
            ):
                if not self.history(id, history):
                    self.full(id)
            else:
                self.full(id)
            self.store.prune()
            self.store.set_setting("last_sync", now())
            with self.store.tx() as db:
                db.execute(
                    "UPDATE jobs SET state='complete',phase='Up to date',finished_at=? WHERE id=?",
                    (now(), id),
                )
        except Exception as error:
            # Never log exception payloads, HTTP URLs with codes, or email bodies.
            message = (
                str(error)
                if isinstance(error, (ValueError, ReconnectRequired))
                else "Sync could not finish. Check your connection and macOS Keychain access, then retry"
            )
            with self.store.tx() as db:
                db.execute(
                    "UPDATE jobs SET state='failed',error=?,finished_at=? WHERE id=?",
                    (message, now(), id),
                )
        finally:
            self.lock.release()

    def enqueue(self, db, ids, id, rescan=False):
        for message_id in ids:
            db.execute(
                "INSERT OR IGNORE INTO queue(message_id,job_id) VALUES(?,?)", (message_id, id)
            )
            if rescan:
                # Revisit IDs rejected by a previous policy, but only when they
                # are rediscovered inside this backfill's received-mail window.
                db.execute(
                    "UPDATE queue SET status='pending',job_id=? WHERE message_id=?",
                    (id, message_id),
                )
        count = db.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        db.execute("UPDATE jobs SET discovered=? WHERE id=?", (count, id))

    def consume(self, id):
        self.phase(id, "Scanning emails locally for transactions")
        mailbox = self.store.get_setting("bound_mailbox")
        with self.store.lock:
            pending = [
                r[0]
                for r in self.store.db.execute(
                    "SELECT message_id FROM queue WHERE status='pending'"
                )
            ]
        for message_id in pending:
            if self.stop.is_set():
                raise ValueError("Sync stopped; progress has been saved")
            source_id = mailbox + ":" + message_id
            with self.store.lock:
                cached = self.store.db.execute(
                    "SELECT 1 FROM sources WHERE id=?", (source_id,)
                ).fetchone()
                if cached:
                    # A prior run may have stopped after ingest but before the
                    # export merge/queue checkpoint. Finish without refetching.
                    self.reconcile_export(message_id, source_id)
                    self.store.reconcile_sync_changes()
            if not cached:
                # Scan the complete received message locally. The store retains
                # only authenticated transaction evidence; discovery does not
                # infer relevance from a sender, subject, or Gmail category.
                message = self.request("/messages/" + message_id, {"format": "full"})
                if is_received_message(message):
                    try:
                        email = decode_message(message)
                        if email["id"] != message_id or not isinstance(email["subject"], str):
                            email = None
                    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
                        # Broad scans include unrelated and malformed messages.
                        # Keep only their opaque queue ID, never source stubs or
                        # review records that would retain personal headers.
                        email = None
                    if email is not None:
                        with self.store.lock:
                            self.store.ingest(email, mailbox, sync_job_id=id)
                            # Existing exported summaries refer to Gmail message IDs.
                            self.reconcile_export(message_id, source_id)
                            self.store.reconcile_sync_changes()
            with self.store.tx() as db:
                db.execute("UPDATE queue SET status='done' WHERE message_id=?", (message_id,))
                db.execute("UPDATE jobs SET processed=processed+1 WHERE id=?", (id,))

    def reconcile_export(self, message_id, source_id):
        with self.store.lock:
            old = self.store.db.execute(
                "SELECT transaction_id FROM observations WHERE source_id=?",
                ("export:" + message_id,),
            ).fetchone()
            new = self.store.db.execute(
                "SELECT transaction_id FROM observations WHERE source_id=?", (source_id,)
            ).fetchone()
        if old and new and old[0] != new[0]:
            old_t, new_t = self.store.transaction(old[0]), self.store.transaction(new[0])
            if (
                not old_t["merged_into"]
                and not new_t["merged_into"]
                and all(old_t[k] == new_t[k] for k in ("amount_minor", "currency", "direction"))
            ):
                with self.store.tx() as db:
                    fresh = db.execute(
                        "SELECT data FROM transactions WHERE id=?", (new[0],)
                    ).fetchone()[0]
                    db.execute("UPDATE transactions SET data=? WHERE id=?", (fresh, old[0]))
                # The same Gmail message ID and matching amount identify this
                # export's original evidence. Keep its user-entered splits.
                self.store.merge(new[0], old[0], preserve_target_allocations=True)

    def full(self, id):
        state = self.store.get_setting("backfill")
        restart_window = not state or state.get("months") != SYNC_MONTHS
        if restart_window or state.get("filter_policy") != POLICY_VERSION:
            if restart_window:
                current = datetime.now(IST)
                year, month = divmod(current.year * 12 + current.month - 1 - SYNC_MONTHS, 12)
                month += 1
                cutoff = current.replace(
                    year=year,
                    month=month,
                    day=min(current.day, calendar.monthrange(year, month)[1]),
                )
                window = f"after:{int(cutoff.timestamp())} before:{int(current.timestamp()) + 1}"
            else:
                window = state["window"]
            # Query changes invalidate saved page cursors. Keep the original
            # history baseline to replay arrivals during an interrupted import.
            baseline = state["baseline"] if state else self.request("/profile")["historyId"]
            rescan = self.store.get_setting("gmail_scan_policy") != POLICY_VERSION or bool(
                state and (state.get("rescan") or state.get("filter_policy") != POLICY_VERSION)
            )
            discard_pending = bool(state) and restart_window
            state = {
                "baseline": baseline,
                "window": window,
                "months": SYNC_MONTHS,
                "query_index": 0,
                "page": None,
                "filter_policy": POLICY_VERSION,
                "rescan": rescan,
                "queries": [received_query(window)],
            }
            with self.store.tx() as db:
                if discard_pending:
                    # Rediscover pending IDs within the shorter window. Saved
                    # sources, transactions and completed queue entries stay.
                    db.execute("DELETE FROM queue WHERE status='pending'")
                db.execute(
                    "INSERT INTO settings VALUES('backfill',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (encode(state),),
                )
        while state["query_index"] < len(state["queries"]):
            self.phase(id, f"Discovering received emails across {SYNC_MONTHS} months")
            params = {
                "q": state["queries"][state["query_index"]],
                "maxResults": 500,
                "includeSpamTrash": "false",
            }
            if state["page"]:
                params["pageToken"] = state["page"]
            result = self.request("/messages", params)
            if result is None:
                raise ValueError("Gmail discovery is unavailable")
            state["page"] = result.get("nextPageToken")
            if not state["page"]:
                state["query_index"] += 1
            with self.store.tx() as db:
                self.enqueue(
                    db,
                    [m["id"] for m in result.get("messages", [])],
                    id,
                    rescan=state.get("rescan", False),
                )
                db.execute(
                    "INSERT INTO settings VALUES('backfill',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (encode(state),),
                )
            self.consume(id)
        self.consume(id)
        if not self.history(id, state["baseline"]):
            # A very long import can outlive Gmail's cursor. Restart discovery;
            # cached messages make the second pass inexpensive and idempotent.
            self.store.set_setting("backfill", None)
            raise ValueError(
                "Gmail history expired during import. Resume sync to safely repeat discovery"
            )
        with self.store.tx() as db:
            db.executemany(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [("backfill", encode(None)), ("gmail_scan_policy", encode(POLICY_VERSION))],
            )

    def history(self, id, start):
        page = None
        latest = start
        self.phase(id, "Checking new Gmail activity")
        while True:
            params = {
                "startHistoryId": start,
                "maxResults": 500,
                "fields": "historyId,nextPageToken,history(messagesAdded(message(id,labelIds)),messagesDeleted(message(id)))",
            }
            if page:
                params["pageToken"] = page
            result = self.request("/history", params)
            if result is None:
                return False
            ids = []
            deleted = []
            for h in result.get("history", []):
                for item in h.get("messagesAdded", []):
                    msg = item["message"]
                    if is_received_message(msg):
                        ids.append(msg["id"])
                deleted.extend(item["message"]["id"] for item in h.get("messagesDeleted", []))
            with self.store.tx() as db:
                self.enqueue(db, ids, id)
                for message_id in deleted:
                    db.execute(
                        "UPDATE sources SET missing=1 WHERE id=?",
                        (self.store.get_setting("bound_mailbox") + ":" + message_id,),
                    )
            self.consume(id)
            latest = result.get("historyId", latest)
            page = result.get("nextPageToken")
            if not page:
                break
        self.store.set_setting("history_id", latest)
        return True
