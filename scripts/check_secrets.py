"""Release guard for known credentials/private files, not a proof of privacy.

By default scan both the index and publishable working files. --history also
checks every reachable Git object, including deleted files and commit/tag text.
--directory scans an extracted release in full, without consulting .gitignore.
Potential values and commit messages are never included in diagnostics. This
cannot recognize all personal/financial data or inspect pixels in screenshots.
"""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import subprocess
import sys

PATTERNS = {
    "private key": r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
    "Google OAuth token": r"ya29\.[A-Za-z0-9_-]{20,}|1//[A-Za-z0-9_-]{25,}",
    "Google OAuth client secret": r"GOCSPX-[A-Za-z0-9_-]{20,}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}",
    "OpenAI key": r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{35,}",
    "AWS access key": r"(?:AKIA|ASIA)[A-Z0-9]{16}",
    "Google API key": r"AIza[0-9A-Za-z0-9_-]{35}",
    "Slack token": r"xox[baprs]-[A-Za-z0-9-]{20,}",
    "Stripe secret": r"(?:sk|rk)_live_[A-Za-z0-9]{20,}",
    "serialized credential": (
        r"""(?i)["'](?:api[_-]?key|access_token|refresh_token|client_secret)["']"""
        r"""\s*:\s*["'][A-Za-z0-9_./+=-]{24,}["']"""
    ),
    "credential in URL": r"[?#&](?:access_token|refresh_token|api_key)=[A-Za-z0-9_./+=-]{24,}",
    "environment credential": (
        r"(?m)^\s*(?:export\s+)?[A-Z_]*(?:API_KEY|ACCESS_TOKEN|REFRESH_TOKEN|CLIENT_SECRET)"
        r"""\s*=\s*["']?[A-Za-z0-9_./+=-]{24,}"""
    ),
}
COMPILED = {label: re.compile(pattern) for label, pattern in PATTERNS.items()}
PRIVATE_DIRECTORIES = {".local-data", ".demo-data", "backups", "exports", "imports"}
PRIVATE_SUFFIXES = (
    ".sqlite",
    ".sqlite3",
    ".db",
    ".mcb",
    ".fernet",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".zip",
    ".tar",
    ".tgz",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".eml",
    ".mbox",
    ".har",
)


def git(*args, cwd=None, data=None):
    return subprocess.run(
        ["git", *args], cwd=cwd, input=data, check=True, capture_output=True
    ).stdout


def safe_location(location):
    for pattern in COMPILED.values():
        location = pattern.sub("[REDACTED]", location)
    return json.dumps(location, ensure_ascii=True)


def private_file(name):
    path = Path(name.lower())
    base = path.name
    if any(part in PRIVATE_DIRECTORIES for part in path.parts):
        return True
    if base.startswith(".env") and base != ".env.example":
        return True
    if base.startswith(("client_secret", "runtime-")):
        return True
    if (
        "credentials" in base
        or base in {"token.json", "tokens.json", "oauth.json", "id_rsa", "id_ed25519", "id_ecdsa"}
        or base.endswith("-transactions.json")
    ):
        return True
    if re.search(r"\.(?:sqlite3?|db|mcb|fernet)(?:-(?:wal|shm|journal))?(?:\..*)?$", base):
        return True
    return base.endswith(PRIVATE_SUFFIXES)


class Guard:
    def __init__(self):
        self.failures = set()
        self.files = 0
        self.objects = defaultdict(int)

    def fail(self, location, reason):
        self.failures.add(f"{safe_location(location)}: {reason}")

    def path(self, name, location, mode=None):
        for label, pattern in COMPILED.items():
            if pattern.search(name):
                self.fail(location, f"possible {label} in filename")
        if private_file(name):
            self.fail(location, "private runtime/data file or opaque archive must not be published")
        if mode in {"120000", "160000"}:
            self.fail(location, "symlink or submodule requires a separate publication review")

    def content(self, content, location):
        if content.startswith((b"SQLite format 3\x00", b"MONTHLYCOST1\n")):
            self.fail(location, "database or encrypted backup content must not be published")
        if (
            content.startswith(
                (
                    b"PK\x03\x04",
                    b"PK\x05\x06",
                    b"PK\x07\x08",
                    b"\x1f\x8b",
                    b"7z\xbc\xaf\x27\x1c",
                    b"Rar!",
                    b"BZh",
                    b"\xfd7zXZ\x00",
                )
            )
            or content[257:262] == b"ustar"
        ):
            self.fail(location, "opaque archive requires extraction and a separate review")
        # Latin-1 preserves ASCII token bytes even inside binary or invalid UTF-8 files.
        text = content.decode("latin-1")
        for label, pattern in COMPILED.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                self.fail(f"{location}:{line}", f"possible {label}")

    def file(self, path, name):
        self.path(name, name)
        if path.is_symlink():
            self.fail(name, "symlink requires a separate publication review; target was not read")
            return
        if not path.exists():
            return  # A staged deletion is still inspected through its index/history objects.
        if not path.is_file():
            self.fail(name, "non-regular file cannot be inspected")
            return
        try:
            self.content(path.read_bytes(), name)
            self.files += 1
        except OSError:
            self.fail(name, "file could not be read")

    def tree(self, content, location, oid_bytes):
        position = 0
        while position < len(content):
            end = content.index(b"\0", position)
            mode, name = content[position:end].split(b" ", 1)
            name = name.decode("utf-8", "surrogateescape")
            self.path(name, f"{location} entry {name}", mode.decode())
            position = end + 1 + oid_bytes

    def git_objects(self, root, objects, oid_bytes):
        # Request one object at a time to avoid buffering an entire repository in memory.
        with subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            for oid, locations in objects.items():
                process.stdin.write(oid.encode() + b"\n")
                process.stdin.flush()
                header = process.stdout.readline().decode().strip().split()
                if len(header) != 3 or header[1] not in {"blob", "commit", "tag", "tree"}:
                    self.fail(f"Git object {oid[:12]}", "object is missing or unreadable")
                    continue
                _, kind, size = header
                content = process.stdout.read(int(size))
                terminator = process.stdout.read(1)
                if len(content) != int(size) or terminator != b"\n":
                    self.fail(f"Git object {oid[:12]}", "object could not be read completely")
                    break
                self.objects[kind] += 1
                location = f"Git {kind} {oid[:12]}"
                if kind == "tree":
                    self.tree(content, location, oid_bytes)
                else:
                    self.content(content, "; ".join(sorted(locations)) or location)
            process.stdin.close()
            if process.wait() != 0:
                self.fail("Git object scan", "Git failed while reading objects")


def scan_repository(guard, history):
    root = Path(git("rev-parse", "--show-toplevel").decode().strip())
    objects = defaultdict(set)
    names = set()
    for entry in git("ls-files", "--stage", "-z", cwd=root).split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, oid, _stage = metadata.decode().split()
        name = raw_name.decode("utf-8", "surrogateescape")
        guard.path(name, f"index {name}", mode)
        names.add(name)
        if mode != "160000":
            objects[oid].add(f"index {name}")
    names.update(
        name.decode("utf-8", "surrogateescape")
        for name in git("ls-files", "--others", "--exclude-standard", "-z", cwd=root).split(b"\0")
        if name
    )
    for name in sorted(names):
        guard.file(root / name, name)
    if history:
        if git("rev-parse", "--is-shallow-repository", cwd=root).strip() == b"true":
            guard.fail(
                "Git history", "shallow clone cannot prove complete history coverage; fetch fully"
            )
        for oid in git(
            "rev-list", "--objects", "--all", "--no-object-names", cwd=root
        ).splitlines():
            objects[
                oid.decode()
            ]  # Every locally reachable object, including commit and tag metadata.
    oid_bytes = (
        32 if git("rev-parse", "--show-object-format", cwd=root).strip() == b"sha256" else 20
    )
    guard.git_objects(root, objects, oid_bytes)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--history", action="store_true", help="also scan all locally reachable history"
    )
    modes.add_argument(
        "--directory", type=Path, help="scan every file in an extracted release directory"
    )
    args = parser.parse_args(argv)
    guard = Guard()
    try:
        if args.directory is not None:
            root = args.directory
            if root.is_symlink() or not root.is_dir():
                parser.error("--directory must be an existing directory, not a symlink")
            for path in sorted(root.rglob("*")):
                name = str(path.relative_to(root))
                if any(part.casefold() == ".git" for part in path.relative_to(root).parts):
                    guard.fail(name, "Git metadata must not be included in a source-only export")
                if not path.is_dir() or path.is_symlink():
                    guard.file(path, name)
        else:
            scan_repository(guard, args.history)
    except (OSError, subprocess.CalledProcessError, ValueError):
        print(
            "Release guard could not complete; resolve the repository/read error and rerun.",
            file=sys.stderr,
        )
        return 2
    scope = f"{guard.files} files, {sum(guard.objects.values())} Git objects"
    if args.history:
        scope += f" ({guard.objects['commit']} commits, {guard.objects['blob']} blobs)"
    print(f"Scanned {scope}.")
    if guard.failures:
        print("\n".join(sorted(guard.failures)), file=sys.stderr)
        return 1
    print("Known credential-pattern and private-file checks passed.")
    print(
        "Manual review is still required for personal/financial data and screenshot or video contents."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
