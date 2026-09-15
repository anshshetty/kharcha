import pytest
from cryptography.fernet import Fernet
from backend.security import Vault
from backend.store import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "ledger.sqlite3", Vault(test_key=Fernet.generate_key()))
    yield value
    value.close()


def transaction(**kwargs):
    return dict(
        date="2026-08-01",
        direction="debit",
        kind="purchase",
        amount_minor=100000,
        currency="INR",
        counterparty="Example Shop",
        account="TEST •1234",
        category="Shopping",
        **kwargs,
    )


def email(id="abc123", body=None, subject="Transaction alert"):
    return {
        "id": id,
        "sender": "alerts@example-bank.test",
        "subject": subject,
        "received_at": "2026-08-01T12:00:00+05:30",
        "body": body
        or "INR 500.00 debited from account XX1234 at EXAMPLE SHOP on 01/08/2026. UPI Ref: 123456789012",
        "attachments": [],
        "authentication": {
            "verified": True,
            "domain": "example-bank.test",
            "receiver": "mx.google.com",
        },
    }


def authenticated_headers(sender):
    """Synthetic Gmail delivery metadata, not real financial email headers."""
    import re

    sender = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", sender)[-1]
    domain = sender.rsplit("@", 1)[-1]
    return [
        {"name": "From", "value": sender},
        {
            "name": "Received",
            "value": "from mail.example.test by mx.google.com with ESMTPS id synthetic",
        },
        {
            "name": "Authentication-Results",
            "value": f"mx.google.com; spf=pass smtp.mailfrom={domain}; dkim=pass header.d={domain}; dmarc=pass header.from={domain}",
        },
    ]
