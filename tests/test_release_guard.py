"""Synthetic Git repositories exercise publication leaks without touching local data."""

from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_secrets.py"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Synthetic Release Test")
    git(tmp_path, "config", "user.email", "release@example.invalid")
    (tmp_path / "README.md").write_text("A wholly synthetic repository.\n")
    git(tmp_path, "add", "README.md")
    git(tmp_path, "commit", "-qm", "Initial synthetic source")
    return tmp_path


def run_guard(repo, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=repo, capture_output=True, text=True
    )


def credential():
    # Construct at runtime so the guard can scan this test's source without an exception.
    return "ghp_" + "SYNTHETICNEVERVALID" * 3


def test_clean_source_and_placeholder_pass(repository):
    (repository / ".env.example").write_text("OPTIONAL_API_KEY=your-placeholder\n")
    result = run_guard(repository, "--history")
    assert result.returncode == 0, result.stderr
    assert "1 commits" in result.stdout
    assert "Manual review" in result.stdout


def test_working_secret_is_detected_without_printing_value(repository):
    value = credential()
    (repository / "notes.txt").write_text("First line\n" + value + "\n")
    result = run_guard(repository)
    assert result.returncode == 1
    assert "notes.txt:2" in result.stderr
    assert "GitHub token" in result.stderr
    assert value not in result.stdout + result.stderr


def test_staged_secret_is_detected_after_working_copy_is_cleaned(repository):
    value = credential()
    path = repository / "notes.txt"
    path.write_text(value)
    git(repository, "add", "notes.txt")
    path.write_text("Cleaned but not staged.\n")
    result = run_guard(repository)
    assert result.returncode == 1
    assert "index notes.txt" in result.stderr
    assert value not in result.stdout + result.stderr


def test_deleted_historical_secret_and_commit_message_are_scanned(repository):
    value = credential()
    (repository / "deleted.txt").write_text(value)
    git(repository, "add", "deleted.txt")
    git(repository, "commit", "-qm", "Synthetic credential " + value)
    git(repository, "rm", "-q", "deleted.txt")
    git(repository, "commit", "-qm", "Delete test leak")
    assert run_guard(repository).returncode == 0
    result = run_guard(repository, "--history")
    assert result.returncode == 1
    assert "Git commit" in result.stderr
    assert "Git blob" in result.stderr
    assert "3 commits" in result.stdout
    assert value not in result.stdout + result.stderr


def test_tag_metadata_is_scanned(repository):
    value = credential()
    git(repository, "tag", "-a", "synthetic-tag", "-m", value)
    result = run_guard(repository, "--history")
    assert result.returncode == 1
    assert "Git tag" in result.stderr
    assert value not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "name",
    [
        ".env.local",
        "ledger.sqlite3-wal",
        "ledger.db-shm",
        "runtime-8765.json",
        "google-credentials.json",
        "token.json",
        "backup.mcb",
        ".local-data/arbitrary.txt",
        "messages.eml",
        "recording.har",
        "archive.zip",
    ],
)
def test_private_filenames_are_blocked_even_without_secret_contents(repository, name):
    path = repository / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"Synthetic harmless bytes")
    result = run_guard(repository)
    assert result.returncode == 1
    assert "must not be published" in result.stderr


def test_deleted_private_path_is_found_in_history(repository):
    path = repository / "google-credentials.json"
    path.write_text("{}")
    git(repository, "add", path.name)
    git(repository, "commit", "-qm", "Synthetic private filename")
    git(repository, "rm", "-q", path.name)
    git(repository, "commit", "-qm", "Delete private filename")
    assert run_guard(repository).returncode == 0
    result = run_guard(repository, "--history")
    assert result.returncode == 1
    assert "google-credentials.json" in result.stderr
    assert "Git tree" in result.stderr


def test_ignored_data_is_not_read_or_modified_but_export_is_rejected(repository):
    (repository / ".gitignore").write_text(".local-data/\n")
    path = repository / ".local-data" / "ledger.sqlite3"
    path.parent.mkdir()
    original = b"SQLite format 3\x00" + credential().encode()
    path.write_bytes(original)
    before = path.stat()
    assert run_guard(repository).returncode == 0
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    exported = run_guard(repository, "--directory", str(path.parent))
    assert exported.returncode == 1
    assert "database" in exported.stderr
    assert credential() not in exported.stdout + exported.stderr


@pytest.mark.parametrize("content", [b"SQLite format 3\x00", b"MONTHLYCOST1\n", b"PK\x03\x04"])
def test_renamed_database_backup_and_archive_signatures_are_rejected(repository, content):
    (repository / "innocent.dat").write_bytes(content + b"synthetic")
    result = run_guard(repository)
    assert result.returncode == 1
    assert "innocent.dat" in result.stderr


def test_binary_token_and_secret_filename_are_redacted(repository):
    value = credential()
    (repository / (value + ".dat")).write_bytes(b"\xff\x00" + value.encode())
    result = run_guard(repository)
    assert result.returncode == 1
    assert "[REDACTED]" in result.stderr
    assert value not in result.stdout + result.stderr


def test_symlink_target_is_never_read(repository, tmp_path_factory):
    external = tmp_path_factory.mktemp("outside") / "private.txt"
    external.write_text(credential())
    (repository / "linked.txt").symlink_to(external)
    result = run_guard(repository)
    assert result.returncode == 1
    assert "target was not read" in result.stderr
    assert "GitHub token" not in result.stderr


def test_shallow_history_is_not_reported_as_complete(repository, tmp_path_factory):
    clone = tmp_path_factory.mktemp("clone-parent") / "shallow"
    git(repository, "clone", "-q", "--depth", "1", repository.as_uri(), str(clone))
    result = run_guard(clone, "--history")
    assert result.returncode == 1
    assert "shallow clone" in result.stderr


def test_extracted_source_scan_does_not_consult_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text(".env\n")
    (tmp_path / ".env").write_text("SYNTHETIC=true\n")
    result = run_guard(tmp_path, "--directory", str(tmp_path))
    assert result.returncode == 1
    assert '".env"' in result.stderr


def test_extracted_source_rejects_embedded_git_metadata(repository):
    result = run_guard(repository, "--directory", str(repository))
    assert result.returncode == 1
    assert "Git metadata must not be included" in result.stderr


@pytest.mark.parametrize(
    "content, label",
    [
        ("GOCSPX-" + "SyntheticNeverValid" * 2, "Google OAuth client secret"),
        ('{"refresh_token": "' + "SyntheticNeverValid" * 2 + '"}', "serialized credential"),
        ("EXAMPLE_API_KEY=" + "SyntheticNeverValid" * 2, "environment credential"),
    ],
)
def test_additional_credential_shapes(repository, content, label):
    (repository / "settings.txt").write_text(content)
    result = run_guard(repository)
    assert result.returncode == 1
    assert label in result.stderr
    assert content not in result.stdout + result.stderr


def test_checked_in_ignore_rules_protect_local_data_and_allow_demo_screenshots(repository):
    project = SCRIPT.parents[1]
    (repository / ".gitignore").write_bytes((project / ".gitignore").read_bytes())
    private_names = [
        "ledger.sqlite3-wal",
        "ledger.db",
        "backup.mcb.bak",
        "credentials.json",
        "runtime-8765.json",
        ".local-data/custom-name",
        "preview.har",
        "source.zip",
    ]
    for name in private_names:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"Synthetic private-file sentinel")
        assert git(repository, "check-ignore", name).stdout.strip()
    screenshots = repository / "docs" / "screenshots"
    screenshots.mkdir(parents=True)
    (screenshots / "demo.png").write_bytes(b"Synthetic image placeholder")
    assert run_guard(repository).returncode == 0
    untracked = git(repository, "ls-files", "--others", "--exclude-standard").stdout.decode()
    assert "docs/screenshots/demo.png" in untracked


def test_url_access_token_is_detected_without_printing_value(repository):
    value = "SyntheticNeverValid" * 3
    (repository / "link.txt").write_text("http://localhost:8765/#access_token=" + value)
    result = run_guard(repository)
    assert result.returncode == 1
    assert "credential in URL" in result.stderr
    assert value not in result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["working", "index", "history", "directory"])
def test_credential_in_filename_is_rejected_and_redacted(repository, mode):
    value = credential()
    name = value + ".txt"
    directory = repository / "snapshot" if mode == "directory" else repository
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text("Harmless contents; credential exists only in the filename.\n")
    arguments = []
    if mode == "index":
        git(repository, "add", name)
        path.unlink()  # Only the index retains the sensitive filename.
    elif mode == "history":
        git(repository, "add", name)
        git(repository, "commit", "-qm", "Synthetic filename regression")
        git(repository, "rm", "-q", name)
        git(repository, "commit", "-qm", "Remove synthetic filename")
        assert run_guard(repository).returncode == 0
        arguments = ["--history"]
    elif mode == "directory":
        arguments = ["--directory", str(directory)]
    result = run_guard(repository, *arguments)
    assert result.returncode == 1
    assert "possible GitHub token in filename" in result.stderr
    assert "[REDACTED]" in result.stderr
    assert value not in result.stdout + result.stderr


@pytest.mark.parametrize("name", [".GIT", ".Git", ".gIt"])
def test_extracted_source_rejects_case_variants_of_git_metadata(tmp_path, name):
    metadata = tmp_path / "nested" / name
    metadata.mkdir(parents=True)
    (metadata / "config").write_text("[user]\nemail = synthetic@example.invalid\n")
    result = run_guard(tmp_path, "--directory", str(tmp_path))
    assert result.returncode == 1
    assert "Git metadata must not be included" in result.stderr
