"""Publication snapshots cannot silently include ignored data or Git internals."""

import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

from scripts.export_source import export, source_paths


@pytest.fixture
def source_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("private/\n*.sqlite3\n")
    (tmp_path / "README.md").write_text("# Example source\n")
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "scripts/check_secrets.py",
        tmp_path / "scripts/check_secrets.py",
    )
    (tmp_path / "private").mkdir()
    (tmp_path / "private/ledger.sqlite3").write_text("PRIVATE DATA SENTINEL")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    return tmp_path


def test_export_omits_private_git_and_unreviewed_files(source_repo, tmp_path):
    (source_repo / "unreviewed.txt").write_text("NOT REVIEWED")
    destination = tmp_path / "source.zip"
    export(source_repo, destination)
    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
        assert not any("/.git/" in name or "/private/" in name for name in names)
        assert "kharcha/unreviewed.txt" not in names
        assert "kharcha/README.md" in names
        manifest = json.loads(archive.read("kharcha/SOURCE_MANIFEST.json"))
        assert set(manifest["files"]) == {".gitignore", "README.md", "scripts/check_secrets.py"}
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())


@pytest.mark.parametrize(
    "name",
    [
        "private/ledger.sqlite3",
        ".git/config",
        ".GIT/config",
        "../outside",
        "scripts",
        "SOURCE_MANIFEST.json",
        "source_manifest.json",
    ],
)
def test_export_rejects_unsafe_includes(source_repo, name):
    with pytest.raises(ValueError):
        source_paths(source_repo, [name])


def test_export_rejects_symlinks(source_repo):
    (source_repo / "link.txt").symlink_to(source_repo / "private/ledger.sqlite3")
    with pytest.raises(ValueError, match="Symlink"):
        source_paths(source_repo, ["link.txt"])


def test_export_does_not_overwrite_existing_output(source_repo):
    destination = source_repo / "already-there.zip"
    destination.write_bytes(b"KEEP")
    with pytest.raises(ValueError, match="already exists"):
        export(source_repo, destination)
    assert destination.read_bytes() == b"KEEP"
