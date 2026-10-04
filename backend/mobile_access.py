"""Encrypted LAN access with an independent listener and revocable phone sessions."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from starlette.responses import JSONResponse
import uvicorn

PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class MobileServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # The main loopback server owns process signal handling.
        yield


@dataclass(frozen=True)
class Network:
    address: str
    subnet: str


def command(*args):
    return subprocess.run(
        args, capture_output=True, text=True, timeout=3, check=True
    ).stdout.strip()


def local_network():
    """Prefer Wi-Fi; wired Macs can also serve phones on the same LAN. Ignore VPNs."""
    if sys.platform != "darwin":
        raise ValueError("Automatic mobile access currently requires macOS.")
    interfaces = []
    ports = command("/usr/sbin/networksetup", "-listallhardwareports")
    for block in ports.split("\n\n"):
        if "Hardware Port: Wi-Fi" in block or "Hardware Port: AirPort" in block:
            interfaces.extend(
                line.split(": ", 1)[1] for line in block.splitlines() if line.startswith("Device: ")
            )
    try:
        route = command("/sbin/route", "-n", "get", "default")
    except (OSError, subprocess.SubprocessError):
        # A Wi-Fi LAN does not need an Internet default route.
        route = ""
    interfaces.extend(
        line.split(":", 1)[1].strip()
        for line in route.splitlines()
        if line.strip().startswith("interface:") and line.split(":", 1)[1].strip().startswith("en")
    )
    for interface in dict.fromkeys(interfaces):
        try:
            address = command("/usr/sbin/ipconfig", "getifaddr", interface)
            details = command("/sbin/ifconfig", interface)
            line = next(
                line
                for line in details.splitlines()
                if line.strip().startswith("inet " + address + " ")
            )
            fields = line.split()
            mask = str(ipaddress.IPv4Address(int(fields[fields.index("netmask") + 1], 16)))
            network = ipaddress.ip_network(address + "/" + mask, strict=False)
            if any(network.subnet_of(private) for private in PRIVATE_NETWORKS):
                return Network(address, str(network))
        except (OSError, subprocess.SubprocessError, ValueError, StopIteration):
            continue
    raise ValueError(
        "Connect this Mac to a private Wi-Fi or Ethernet network to use mobile access."
    )


def private_write(path, data):
    """Private, atomic writes, including when an old pathname is a symlink."""
    import tempfile

    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def certificates(directory, address):
    directory = Path(directory) / "mobile-tls"
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    root_path, key_path = directory / "root.cer", directory / "root-key.pem"
    now = datetime.now(timezone.utc)
    if root_path.exists() and key_path.exists():
        root = x509.load_der_x509_certificate(root_path.read_bytes())
        root_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        if root.not_valid_after_utc < now + timedelta(days=100):
            raise ValueError(
                "The mobile certificate has expired. Remove mobile-tls from the app data folder and set up certificate trust again."
            )
    else:
        root_key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Kharcha local mobile access")])
        root = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(root_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), critical=False
            )
            .add_extension(
                x509.KeyUsage(False, False, False, False, False, True, True, False, False),
                critical=True,
            )
            .sign(root_key, hashes.SHA256())
        )
        private_write(
            key_path,
            root_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        private_write(root_path, root.public_bytes(serialization.Encoding.DER))
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Kharcha on " + address)]))
        .issuer_name(root.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(address))]),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, False, False, False, False),
            critical=True,
        )
        .sign(root_key, hashes.SHA256())
    )
    cert_path, leaf_key_path = directory / "server.pem", directory / "server-key.pem"
    private_write(
        cert_path,
        leaf.public_bytes(serialization.Encoding.PEM)
        + root.public_bytes(serialization.Encoding.PEM),
    )
    private_write(
        leaf_key_path,
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    return cert_path, leaf_key_path, root_path, root.fingerprint(hashes.SHA256()).hex()


class MobileBoundary:
    """Listener provenance is an ASGI scope field, never a client-controlled header."""

    def __init__(self, app, mobile, network):
        self.app, self.mobile, self.network = app, mobile, network
        self.origin = f"https://{network.address}:{mobile.port}"

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            # This listener shares the desktop application's lifecycle.
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            return
        client = scope.get("client")
        try:
            on_lan = client and ipaddress.ip_address(client[0]) in ipaddress.ip_network(
                self.network.subnet
            )
        except ValueError:
            on_lan = False
        headers = dict(scope["headers"])
        if not self.mobile.enabled:
            return await JSONResponse(
                {"error": "Mobile access is turned off. Pair again after enabling it on the Mac."},
                401,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
        if (
            not on_lan
            or scope["scheme"] != "https"
            or headers.get(b"host", b"").decode("latin-1") != self.origin.removeprefix("https://")
        ):
            return await JSONResponse(
                {"error": "Mobile access requires the current local Wi-Fi connection."}, 403
            )(scope, receive, send)
        await self.app({**scope, "monthlycost.mobile": self.origin}, receive, send)


class MobileAccess:
    def __init__(self, store, directory, port):
        self.store, self.directory = store, Path(directory)
        self.preference = store.get_setting("mobile_access_enabled", True) is True
        self.port = int(
            os.environ.get("MONTHLYCOST_MOBILE_PORT", str(port + 1 if port < 65535 else 8766))
        )
        if not 1024 <= self.port <= 65535 or self.port == port:
            raise ValueError("Choose a separate mobile port between 1024 and 65535")
        self.lock = threading.RLock()
        self.offer = None
        self.pending = {}
        self.devices = {}
        self.url = None
        self.error = None
        self.fingerprint = None
        self.root_path = None
        self.server = None
        self.wake = asyncio.Event()
        self.loop = None
        self.attempts = deque()

    @property
    def enabled(self):
        return os.environ.get("MONTHLYCOST_MOBILE", "1") != "0" and self.preference

    def clear(self):
        with self.lock:
            self.offer = None
            self.pending.clear()
            self.devices.clear()

    def configure(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Choose whether mobile access is enabled")
        if enabled and os.environ.get("MONTHLYCOST_MOBILE", "1") == "0":
            raise ValueError("Restart Kharcha without MONTHLYCOST_MOBILE=0 to enable Wi-Fi access")
        self.store.set_setting("mobile_access_enabled", enabled)
        self.preference = enabled
        if not enabled:
            self.clear()
        if self.loop:
            self.loop.call_soon_threadsafe(self.wake.set)

    def prune(self):
        now = time.monotonic()
        expired_ids = {key for key, value in self.pending.items() if value["expires"] <= now}
        self.devices = {
            key: value for key, value in self.devices.items() if value["id"] not in expired_ids
        }
        self.pending = {key: value for key, value in self.pending.items() if value["expires"] > now}
        if self.offer and self.offer["expires"] <= now:
            self.offer = None

    def status(self):
        with self.lock:
            self.prune()
            return {
                "enabled": self.enabled,
                "ready": bool(self.enabled and self.url),
                "url": self.url if self.enabled else None,
                "error": self.error,
                "fingerprint": self.fingerprint,
                "pending": [
                    {"id": key, "name": value["name"], "code": value["code"]}
                    for key, value in self.pending.items()
                    if "token" not in value
                ],
                "devices": [
                    {"id": value["id"], "name": value["name"]} for value in self.devices.values()
                ],
            }

    def invite(self):
        with self.lock:
            if not self.enabled or not self.url:
                raise ValueError("Wait for mobile access to connect to Wi-Fi")
            self.offer = {"secret": secrets.token_urlsafe(32), "expires": time.monotonic() + 300}
            return {"url": self.url + "/#pair=" + self.offer["secret"], "expires_in": 300}

    def request_pair(self, secret, name):
        with self.lock:
            self.prune()
            now = time.monotonic()
            while self.attempts and self.attempts[0] <= now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 20:
                raise ValueError("Too many pairing attempts. Wait one minute and try again.")
            self.attempts.append(now)
            if (
                not self.enabled
                or not self.offer
                or not isinstance(secret, str)
                or not secrets.compare_digest(secret.encode(), self.offer["secret"].encode())
            ):
                raise ValueError(
                    "This pairing link has expired or was used. Create a new one on the Mac."
                )
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
                raise ValueError("Enter a device name between 1 and 60 characters")
            if len(self.pending) >= 5 or len(self.devices) >= 20:
                raise ValueError("Remove an existing device or wait before pairing another phone")
            self.offer = None
            identity, poll = secrets.token_urlsafe(16), secrets.token_urlsafe(32)
            code = str(secrets.randbelow(1000000)).zfill(6)
            self.pending[identity] = {
                "name": name.strip(),
                "poll": poll,
                "code": code,
                "expires": time.monotonic() + 300,
            }
            return {"id": identity, "poll_token": poll, "code": code}

    def approve(self, identity):
        with self.lock:
            self.prune()
            request = self.pending.get(identity)
            if not self.enabled or not request or "token" in request:
                raise ValueError("This pairing request has expired or was already approved")
            token = secrets.token_urlsafe(32)
            request["token"] = token
            self.devices[hashlib.sha256(token.encode()).hexdigest()] = {
                "id": identity,
                "name": request["name"],
                "csrf": secrets.token_urlsafe(32),
            }

    def poll(self, identity, credential):
        with self.lock:
            self.prune()
            request = self.pending.get(identity) if isinstance(identity, str) else None
            if (
                not self.enabled
                or not request
                or not isinstance(credential, str)
                or not secrets.compare_digest(credential.encode(), request["poll"].encode())
            ):
                raise ValueError("Pairing ended. Create a new pairing link on the Mac.")
            if "token" in request:
                self.pending.pop(identity)
                return {"state": "approved", "token": request["token"]}
            return {"state": "waiting"}

    def session(self, authorization):
        if not self.enabled or not authorization.startswith("Bearer "):
            return None
        with self.lock:
            self.prune()
            return self.devices.get(hashlib.sha256(authorization[7:].encode()).hexdigest())

    def revoke(self, identity):
        with self.lock:
            self.pending.pop(identity, None)
            self.devices = {
                key: value for key, value in self.devices.items() if value["id"] != identity
            }

    async def run(self, app):
        self.loop = asyncio.get_running_loop()
        task, sock, current = None, None, None
        renew_at = 0

        async def stop():
            nonlocal task, sock, current
            self.url = None
            self.clear()
            if self.server:
                self.server.should_exit = True
            if task:
                try:
                    await task
                except Exception as error:
                    self.error = f"Mobile HTTPS listener stopped: {error}"
            if sock:
                sock.close()
            self.server, task, sock, current = None, None, None, None

        try:
            while True:
                self.wake.clear()
                try:
                    network = await asyncio.to_thread(local_network) if self.enabled else None
                    if (
                        network != current
                        or (task and task.done())
                        or (task and time.monotonic() >= renew_at)
                    ):
                        await stop()
                    if network and task is None:
                        cert, key, root, fingerprint = await asyncio.to_thread(
                            certificates, self.directory, network.address
                        )
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        sock.bind((network.address, self.port))
                        sock.setblocking(False)
                        config = uvicorn.Config(
                            MobileBoundary(app, self, network),
                            host=network.address,
                            port=self.port,
                            ssl_certfile=str(cert),
                            ssl_keyfile=str(key),
                            lifespan="off",
                            access_log=False,
                            proxy_headers=False,
                            timeout_graceful_shutdown=3,
                        )
                        self.server = MobileServer(config)
                        task = asyncio.create_task(self.server.serve(sockets=[sock]))
                        for _ in range(100):
                            if self.server.started or task.done():
                                break
                            await asyncio.sleep(0.01)
                        if not self.server.started or task.done():
                            raise ValueError("The mobile HTTPS listener could not start")
                        current = network
                        renew_at = time.monotonic() + 60 * 86400
                        self.url = f"https://{network.address}:{self.port}"
                        self.root_path, self.fingerprint = root, fingerprint
                    self.error = None
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    await stop()
                    self.error = f"Mobile access unavailable: {error}"
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass
        finally:
            await stop()
            self.loop = None
