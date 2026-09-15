from __future__ import annotations

import json
import os
from pathlib import Path

import keyring
from cryptography.fernet import Fernet

SERVICE = "MonthlyCost.local"


class Vault:
    """No plaintext fallback if the macOS Keychain is unavailable."""

    def __init__(self, test_key=None):
        self.test_key = test_key
        self._test_values = {}

    def read(self, name):
        if self.test_key is not None:
            return self._test_values.get(name)
        return keyring.get_password(SERVICE, name)

    def write(self, name, value):
        if self.test_key is not None:
            self._test_values[name] = value
            return
        keyring.set_password(SERVICE, name, value)

    def delete(self, name):
        if self.test_key is not None:
            self._test_values.pop(name, None)
            return
        try:
            keyring.delete_password(SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            pass

    def cipher(self):
        value = self.test_key or self.read("email-cache-key")
        if not value:
            value = Fernet.generate_key().decode()
            self.write("email-cache-key", value)
        return Fernet(value.encode() if isinstance(value, str) else value)

    def encrypt(self, value):
        return self.cipher().encrypt(json.dumps(value, ensure_ascii=False).encode())

    def decrypt(self, value):
        return json.loads(self.cipher().decrypt(value))


def data_directory():
    path = Path(
        os.environ.get(
            "MONTHLYCOST_DATA_DIR", Path.home() / "Library/Application Support/MonthlyCost"
        )
    )
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path
