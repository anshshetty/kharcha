#!/usr/bin/env python3
"""Launch a built, local-only Kharcha without a Node development server."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def unlocked_url(url, port):
    from backend.local_access import read_runtime

    directory = Path(
        os.environ.get(
            "MONTHLYCOST_DATA_DIR", Path.home() / "Library/Application Support/MonthlyCost"
        )
    )
    try:
        token = read_runtime(directory, port)["token"]
        request = urllib.request.Request(
            url + "/api/session", headers={"Authorization": "Bearer " + token}
        )
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            request, timeout=2
        ) as response:
            if not isinstance(json.load(response).get("csrf"), str):
                raise ValueError("Invalid session response")
    except (OSError, ValueError) as error:
        raise RuntimeError(
            "The running app could not authenticate this OS user. Stop the older Kharcha instance and launch it again."
        ) from error
    return url + "/#access_token=" + token


def main():
    parser = argparse.ArgumentParser(description="Start Kharcha on this Mac")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MONTHLYCOST_PORT", "8765")))
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535")
    url = f"http://127.0.0.1:{args.port}"
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
            if json.load(response).get("app") in {"Kharcha", "MonthlyCost"}:
                launch_url = unlocked_url(url, args.port)
                print("Kharcha is already running at " + url)
                if not args.no_browser:
                    webbrowser.open(launch_url)
                return
    except RuntimeError:
        raise
    except Exception:
        pass
    python = ROOT / ".venv/bin/python"
    if not python.exists():
        print("Preparing the local Python environment…")
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
    python_stamp = ROOT / ".venv/monthlycost-lock.sha256"
    python_digest = hashlib.sha256((ROOT / "requirements.lock.txt").read_bytes()).hexdigest()
    if not python_stamp.exists() or python_stamp.read_text() != python_digest:
        subprocess.run(
            [str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.lock.txt")],
            check=True,
        )
        python_stamp.write_text(python_digest)
    node_stamp = ROOT / "frontend/node_modules/.monthlycost-lock.sha256"
    node_digest = hashlib.sha256((ROOT / "frontend/package-lock.json").read_bytes()).hexdigest()
    node_changed = not node_stamp.exists() or node_stamp.read_text() != node_digest
    if node_changed:
        subprocess.run(["npm", "ci"], cwd=ROOT / "frontend", check=True)
        node_stamp.write_text(node_digest)
    candidates = [ROOT / "frontend/out/index.html", ROOT / "frontend/dist/client/index.html"]
    built = next((p for p in candidates if p.exists()), None)
    sources = [
        p
        for directory in ["app", "components", "lib", "hooks"]
        for p in (ROOT / "frontend" / directory).rglob("*")
        if p.is_file()
    ]
    sources += [
        ROOT / "frontend/package-lock.json",
        ROOT / "frontend/vite.config.ts",
        ROOT / "frontend/next.config.ts",
    ]
    if node_changed or not built or any(p.stat().st_mtime > built.stat().st_mtime for p in sources):
        print("Building the local interface…")
        subprocess.run(["npm", "run", "build"], cwd=ROOT / "frontend", check=True)
    env = {**os.environ, "MONTHLYCOST_PORT": str(args.port)}
    process = subprocess.Popen(
        [
            str(python),
            "-m",
            "uvicorn",
            "backend.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
            "--no-access-log",
        ],
        cwd=ROOT,
        env=env,
    )
    try:
        for _ in range(60):
            if process.poll() is not None:
                raise RuntimeError("Kharcha could not start. The port may already be in use.")
            try:
                with urllib.request.urlopen(url + "/api/health", timeout=0.5) as response:
                    if json.load(response).get("app") in {"Kharcha", "MonthlyCost"}:
                        break
            except Exception:
                time.sleep(0.25)
        else:
            raise RuntimeError("Kharcha took too long to start.")
        print(
            "\nKharcha is ready: "
            + url
            + "\nKeep this window open while syncing. Press Control-C to stop.\n",
            flush=True,
        )
        launch_url = unlocked_url(url, args.port)
        if not args.no_browser:
            webbrowser.open(launch_url)
        process.wait()
    except KeyboardInterrupt:
        pass
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
