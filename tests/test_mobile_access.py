import asyncio
from datetime import datetime, timedelta, timezone
import ipaddress
import ssl
import subprocess
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import ExtendedKeyUsageOID
from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app import create_app
from backend.mobile_access import MobileAccess, MobileBoundary, Network, certificates, local_network
from .client import LocalClient
from .conftest import transaction

NETWORK = Network("192.168.1.10", "192.168.1.0/24")
ORIGIN = "https://192.168.1.10:8766"


def phone_client(app, client=("192.168.1.20", 43210), origin=ORIGIN):
    return TestClient(
        MobileBoundary(app, app.state.mobile_access, NETWORK), base_url=origin, client=client
    )


def pair(app, client, owner):
    mobile = app.state.mobile_access
    mobile.url = ORIGIN
    csrf = owner.get("/api/session").json()["csrf"]
    headers = {"X-CSRF-Token": csrf}
    invitation = owner.post("/api/mobile/invite", json={}, headers=headers).json()
    secret = invitation["url"].split("#pair=")[1]
    request = client.post(
        "/api/mobile/pair",
        json={"secret": secret, "name": "Test phone"},
        headers={"Origin": ORIGIN},
    )
    assert request.status_code == 200
    request = request.json()
    assert client.get("/api/transactions").status_code == 401
    assert client.post(
        "/api/mobile/pair-status", json=request, headers={"Origin": ORIGIN}
    ).json() == {"state": "waiting"}
    pending = owner.get("/api/mobile/status").json()["pending"]
    assert pending[0]["code"] == request["code"]
    assert "poll" not in pending[0]
    assert (
        owner.post("/api/mobile/approve", json={"id": request["id"]}, headers=headers).status_code
        == 200
    )
    response = client.post(
        "/api/mobile/pair-status", json=request, headers={"Origin": ORIGIN}
    ).json()
    assert response["state"] == "approved"
    client.headers["Authorization"] = "Bearer " + response["token"]
    return request, response["token"], headers


def test_default_on_paired_phone_edits_same_ledger_and_revocation(store):
    app = create_app(store, start_scheduler=False)
    assert app.state.mobile_access.enabled
    with LocalClient(app) as owner, phone_client(app) as phone:
        request, token, desktop_headers = pair(app, phone, owner)
        session = phone.get("/api/session").json()["csrf"]
        assert session != desktop_headers["X-CSRF-Token"]
        assert (
            phone.post("/api/transactions", json=transaction(), headers=desktop_headers).status_code
            == 403
        )
        response = phone.post(
            "/api/transactions",
            json=transaction(),
            headers={"Origin": ORIGIN, "X-CSRF-Token": session},
        )
        assert response.status_code == 200
        assert owner.get("/api/transactions").json()[0]["id"] == response.json()["id"]
        status = phone.get("/api/status").json()
        assert status["mobile_client"] and status["data_directory"] == ""
        assert owner.get("/api/status").json()["mobile_client"] is False
        assert phone.get("/api/export.csv").status_code == 200
        # A phone credential never unlocks the desktop listener.
        assert (
            TestClient(app)
            .get("/api/transactions", headers={"Authorization": "Bearer " + token})
            .status_code
            == 401
        )
        assert (
            owner.post(
                "/api/mobile/revoke", json={"id": request["id"]}, headers=desktop_headers
            ).status_code
            == 200
        )
        assert phone.get("/api/transactions").status_code == 401


def test_listener_provenance_host_subnet_https_and_origin_checks(store):
    app = create_app(store, start_scheduler=False)
    with phone_client(app) as phone:
        # Even the actual desktop credential cannot authenticate over LAN.
        assert (
            phone.get(
                "/api/session", headers={"Authorization": "Bearer " + app.state.local_access.token}
            ).status_code
            == 401
        )
        assert phone.get("/api/health", headers={"Host": "evil.test"}).status_code == 403
        assert (
            phone.get("/api/health", headers={"Origin": "http://127.0.0.1:8765"}).status_code == 403
        )
        assert phone.get("/api/health", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert phone.post("/api/mobile/pair", json={}).status_code == 403
        assert (
            phone.post(
                "/api/mobile/pair", json={}, headers={"Origin": ORIGIN, "Content-Length": "2049"}
            ).status_code
            == 413
        )
    with phone_client(app, client=("192.168.2.20", 43210)) as other_subnet:
        assert (
            other_subnet.get("/api/health", headers={"X-Forwarded-For": "192.168.1.20"}).status_code
            == 403
        )
    with phone_client(app, origin="http://192.168.1.10:8766") as insecure:
        assert insecure.get("/api/health").status_code == 403
    with TestClient(app) as stranger:
        assert (
            stranger.get("/api/mobile/status", headers={"X-Monthlycost-Mobile": ORIGIN}).status_code
            == 401
        )
        assert stranger.get("/api/health", headers={"Host": "192.168.1.10:8765"}).status_code == 400


@pytest.mark.parametrize(
    "path",
    [
        "/api/auth/start",
        "/api/auth/configure",
        "/api/auth/callback",
        "/api/auth/disconnect",
        "/api/mobile/status",
        "/api/mobile/preferences",
        "/api/mobile/certificate",
        "/api/mobile/invite",
        "/api/mobile/approve",
        "/api/mobile/revoke",
        "/api/backup",
        "/api/restore",
        "/api/erase",
        "/api/import/local",
    ],
)
def test_phone_cannot_use_desktop_management_routes(store, path):
    app = create_app(store, start_scheduler=False)
    with LocalClient(app) as owner, phone_client(app) as phone:
        pair(app, phone, owner)
        csrf = phone.get("/api/session").json()["csrf"]
        for method in ("GET", "POST", "PUT"):
            assert (
                phone.request(
                    method, path, json={}, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
                ).status_code
                == 403
            )


def test_pairing_is_single_use_expiring_rate_limited_and_private(store, monkeypatch):
    mobile = MobileAccess(store, store.path.parent, 8765)
    mobile.url = ORIGIN
    clock = time.monotonic()
    monkeypatch.setattr("backend.mobile_access.time.monotonic", lambda: clock)
    invite = mobile.invite()["url"].split("#pair=")[1]
    with pytest.raises(ValueError, match="expired"):
        mobile.request_pair("incorrect", "Phone")
    with pytest.raises(ValueError, match="expired"):
        mobile.request_pair("invalid-☃", "Phone")
    request = mobile.request_pair(invite, "Phone")
    with pytest.raises(ValueError, match="expired"):
        mobile.request_pair(invite, "Phone")
    with pytest.raises(ValueError, match="ended"):
        mobile.poll(request["id"], "wrong")
    with pytest.raises(ValueError, match="ended"):
        mobile.poll(request["id"], "invalid-☃")
    mobile.approve(request["id"])
    with pytest.raises(ValueError, match="already approved"):
        mobile.approve(request["id"])
    token = mobile.poll(request["id"], request["poll_token"])["token"]
    with pytest.raises(ValueError, match="ended"):
        mobile.poll(request["id"], request["poll_token"])
    assert mobile.session("Bearer " + token)
    assert token not in repr(mobile.status())
    assert token not in repr(mobile.devices)
    clock += 61
    for _ in range(20):
        with pytest.raises(ValueError):
            mobile.request_pair("wrong", "Phone")
    with pytest.raises(ValueError, match="Too many"):
        mobile.request_pair("wrong", "Phone")
    clock += 61
    invite = mobile.invite()["url"].split("#pair=")[1]
    clock += 301
    with pytest.raises(ValueError, match="expired"):
        mobile.request_pair(invite, "Phone")
    invite = mobile.invite()["url"].split("#pair=")[1]
    abandoned = mobile.request_pair(invite, "Abandoned phone")
    mobile.approve(abandoned["id"])
    clock += 301
    assert not mobile.status()["pending"]
    assert len(mobile.status()["devices"]) == 1


def test_off_is_persisted_and_reenable_requires_fresh_pairing(store):
    app = create_app(store, start_scheduler=False)
    with LocalClient(app) as owner, phone_client(app) as phone:
        request, token, headers = pair(app, phone, owner)
        assert (
            owner.put(
                "/api/mobile/preferences", json={"enabled": "false"}, headers=headers
            ).status_code
            == 400
        )
        assert (
            owner.put(
                "/api/mobile/preferences", json={"enabled": False}, headers=headers
            ).status_code
            == 200
        )
        assert phone.get("/api/transactions").status_code == 401
        assert not create_app(store, start_scheduler=False).state.mobile_access.enabled
        assert (
            owner.put(
                "/api/mobile/preferences", json={"enabled": True}, headers=headers
            ).status_code
            == 200
        )
        assert phone.get("/api/transactions").status_code == 401
        with pytest.raises(ValueError):
            app.state.mobile_access.poll(request["id"], request["poll_token"])


def test_local_certificate_is_stable_private_and_valid_for_current_ip(tmp_path):
    certificate, key, root_path, fingerprint = certificates(tmp_path, NETWORK.address)
    root = x509.load_der_x509_certificate(root_path.read_bytes())
    leaf = x509.load_pem_x509_certificate(certificate.read_bytes())
    leaf.verify_directly_issued_by(root)
    assert leaf.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address(NETWORK.address)]
    assert (
        ExtendedKeyUsageOID.SERVER_AUTH
        in leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    )
    assert leaf.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=80)
    assert fingerprint == root.fingerprint(hashes.SHA256()).hex()
    for file in certificate.parent.iterdir():
        assert file.stat().st_mode & 0o777 == 0o600
    assert certificate.parent.stat().st_mode & 0o777 == 0o700
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    _, _, new_root, new_fingerprint = certificates(tmp_path, "192.168.1.11")
    assert new_root.read_bytes() == root_path.read_bytes()
    assert new_fingerprint == fingerprint


def test_network_discovery_ignores_vpn_and_public_subnets(monkeypatch):
    monkeypatch.setattr("backend.mobile_access.sys.platform", "darwin")

    def fake(*args):
        if args[-1] == "-listallhardwareports":
            return "Hardware Port: Wi-Fi\nDevice: en0\n"
        if args[0] == "/sbin/route":
            return "interface: utun5"
        if args[0] == "/sbin/ifconfig":
            return "inet 192.168.1.10 netmask 0xffffff00 broadcast 192.168.1.255"
        return "192.168.1.10"

    monkeypatch.setattr("backend.mobile_access.command", fake)
    assert local_network() == NETWORK

    def without_internet(*args):
        if args[0] == "/sbin/route":
            raise subprocess.CalledProcessError(1, args)
        return fake(*args)

    monkeypatch.setattr("backend.mobile_access.command", without_internet)
    assert local_network() == NETWORK

    def public_address(*args):
        return fake(*args).replace("192.168.1.10", "203.0.113.10")

    monkeypatch.setattr("backend.mobile_access.command", public_address)
    with pytest.raises(ValueError, match="private Wi-Fi"):
        local_network()


def test_real_tls_listener_starts_stops_and_rebinds_without_touching_lan(store, monkeypatch):
    import socket

    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    monkeypatch.setenv("MONTHLYCOST_MOBILE_PORT", str(port))
    network = Network("127.0.0.1", "127.0.0.0/8")
    monkeypatch.setattr("backend.mobile_access.local_network", lambda: network)
    app = create_app(store, start_scheduler=False)
    mobile = app.state.mobile_access

    async def ready(expected):
        for _ in range(200):
            if bool(mobile.url) == expected:
                return
            await asyncio.sleep(0.02)
        raise AssertionError(mobile.status())

    async def check():
        task = asyncio.create_task(mobile.run(app))
        try:
            await ready(True)
            root = x509.load_der_x509_certificate(mobile.root_path.read_bytes())
            context = ssl.create_default_context(
                cadata=root.public_bytes(serialization.Encoding.PEM).decode()
            )
            async with httpx.AsyncClient(verify=context, trust_env=False) as client:
                response = await client.get(mobile.url + "/api/health")
                assert response.status_code == 200
                assert (await client.get(mobile.url + "/api/transactions")).status_code == 401
                assert (
                    await client.get(
                        mobile.url + "/api/health", headers={"X-Forwarded-Proto": "http"}
                    )
                ).status_code == 200
            mobile.configure(False)
            await ready(False)
            mobile.configure(True)
            await ready(True)
            invitation = mobile.invite()["url"].split("#pair=")[1]
            request = mobile.request_pair(invitation, "Moving phone")
            mobile.approve(request["id"])
            token = mobile.poll(request["id"], request["poll_token"])["token"]
            assert mobile.session("Bearer " + token)
            monkeypatch.setattr(
                "backend.mobile_access.local_network", lambda: Network("127.0.0.1", "127.0.0.0/16")
            )
            mobile.wake.set()
            for _ in range(200):
                if not mobile.session("Bearer " + token):
                    break
                await asyncio.sleep(0.02)
            assert not mobile.session("Bearer " + token)
            await ready(True)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert mobile.url is None
        assert not mobile.devices

    asyncio.run(check())
