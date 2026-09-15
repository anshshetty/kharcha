#!/usr/bin/env python3
"""Export reviewed source without Git history, ignored files or local app data.

Tracked files use their current working-tree contents. New files require an
explicit --include argument until committed. The export never changes Git.
"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def source_paths(root, includes):
    result = subprocess.run(
        ["git", "ls-files", "--cached", "-z"], cwd=root, check=True, capture_output=True
    )
    names = set(result.stdout.decode().split("\0")) - {""}
    names.update(includes)
    selected = []
    for name in sorted(names):
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or any(part == ".." or part.casefold() == ".git" for part in relative.parts)
            or "\\" in name
            or str(relative) != name
            or name.casefold() == "source_manifest.json"
        ):
            raise ValueError("Unsafe source path; export stopped")
        path = root / name
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root):
            raise ValueError(f"Symlink must not be exported: {name}")
        if path.is_dir():
            raise ValueError(f"Include individual reviewed files, not directories: {name}")
        if not path.exists():
            if name in includes:
                raise ValueError(f"Included file does not exist: {name}")
            continue  # A file intentionally deleted in the working tree.
        ignored = subprocess.run(["git", "check-ignore", "--no-index", "-q", "--", name], cwd=root)
        if ignored.returncode == 0:
            raise ValueError(f"Ignored file must not be exported: {name}")
        if ignored.returncode != 1:
            raise ValueError("Could not verify ignore rules; export stopped")
        selected.append((name, path))
    return selected


def export(root, destination, includes=()):
    root, destination = Path(root).resolve(), Path(destination).absolute()
    if destination.exists():
        raise ValueError("Output already exists; choose a new filename")
    selected = source_paths(root, includes)
    if not selected:
        raise ValueError("No reviewed source files found")
    with tempfile.TemporaryDirectory(prefix="kharcha-source-") as temporary:
        snapshot = Path(temporary) / "kharcha"
        snapshot.mkdir()
        for name, path in selected:
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            target.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
        subprocess.run(
            [sys.executable, str(root / "scripts/check_secrets.py"), "--directory", str(snapshot)],
            check=True,
            cwd=root,
        )
        manifest = {
            "format": 1,
            "scope": "Reviewed working-tree source only; no Git history or local application data",
            "files": {
                name: hashlib.sha256((snapshot / name).read_bytes()).hexdigest()
                for name, _ in selected
            },
        }
        (snapshot / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(snapshot.rglob("*")):
                if path.is_file():
                    # Fixed dates and normalized modes avoid machine/user metadata.
                    info = zipfile.ZipInfo("kharcha/" + path.relative_to(snapshot).as_posix())
                    info.compress_type = zipfile.ZIP_DEFLATED
                    mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                    info.external_attr = (0o100000 | mode) << 16
                    archive.writestr(info, path.read_bytes())
    return len(selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--include", action="append", default=[], help="Exact new source file to include"
    )
    args = parser.parse_args()
    try:
        count = export(ROOT, args.output, args.include)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Source export failed: {error}\n")
    print(f"Exported {count} reviewed source files without Git history.")


if __name__ == "__main__":
    main()
