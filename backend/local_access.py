"""Per-process API access, bootstrapped by the OS user's private launcher file.

Browser credentials use origin-scoped sessionStorage, not localhost cookies:
cookies would also be sent to unrelated services on other localhost ports.
"""

import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile


def runtime_path(directory, port):
    return Path(directory) / f"runtime-{port}.json"


def read_runtime(directory, port):
    path = runtime_path(directory, port)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Kharcha's runtime file must be private to your OS user")
        data = json.loads(stream.read(4096))
    if (
        not isinstance(data, dict)
        or data.get("port") != port
        or not isinstance(data.get("token"), str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", data["token"])
    ):
        raise ValueError("Invalid Kharcha runtime file")
    return data


class LocalAccess:
    def __init__(self, directory, port):
        self.directory = Path(directory)
        self.port = port
        self.token = secrets.token_urlsafe(32)

    def authorized(self, authorization):
        return secrets.compare_digest(authorization.encode(), ("Bearer " + self.token).encode())

    def publish(self):
        path = runtime_path(self.directory, self.port)
        # The enclosing app directory is private; atomic replacement never
        # follows a pre-existing runtime-file symlink.
        with tempfile.NamedTemporaryFile(mode="w", dir=self.directory, delete=False) as stream:
            temp = Path(stream.name)
            try:
                os.fchmod(stream.fileno(), 0o600)
                json.dump({"port": self.port, "pid": os.getpid(), "token": self.token}, stream)
                stream.flush()
                os.fsync(stream.fileno())
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)

    def remove(self):
        try:
            current = read_runtime(self.directory, self.port)
            if secrets.compare_digest(current["token"], self.token):
                runtime_path(self.directory, self.port).unlink()
        except (OSError, ValueError):
            pass
