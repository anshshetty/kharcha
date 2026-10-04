# Contributing to Kharcha

Use Python 3.11+ and Node.js 22.13+. Running the real application requires macOS
and Keychain. Tests use temporary SQLite ledgers and an in-memory vault; they
must never depend on Gmail, Codex sign-in, or a contributor's private app data.

## Set up and check a change

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
npm ci --prefix frontend
.venv/bin/python -m pytest -q
.venv/bin/ruff check backend scripts tests
.venv/bin/ruff format --check backend scripts tests
.venv/bin/python scripts/check_secrets.py --history
npm run check --prefix frontend
.venv/bin/pip-audit -r requirements.lock.txt --no-deps --disable-pip
npm audit --prefix frontend
```

Run `.venv/bin/ruff format backend scripts tests` and
`npm run format --prefix frontend` to format code. CI also builds the static
frontend and tests both Python 3.11 and 3.12. Never disable a check to hide a
failure; explain any necessary configuration change in the pull request.

## Development

Keep the local architecture: a static React frontend and FastAPI with separate
desktop and paired HTTPS LAN access. For frontend development, disable the mobile
listener and start the backend using
`MONTHLYCOST_MOBILE=0 MONTHLYCOST_PORT=8765 .venv/bin/python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8766 --no-access-log --no-proxy-headers`,
then `npm run dev --prefix frontend`. Use a separate `MONTHLYCOST_DATA_DIR` for
development. With both processes running, the normal launcher authenticates
through the development proxy and opens the frontend. Do not expose either
development server to the network. Mobile access serves only the built static
frontend; it shares the production ledger and has its own authentication boundary.

The launcher refreshes installed dependencies when their lockfiles change.
Update the appropriate manifest and lockfile together. Development tools are
pinned separately in `requirements-dev.txt`. Do not commit `.venv`,
`node_modules`, build output, runtime credentials, or app data.

## Design and test expectations

- Keep API authentication checks on every data route. Do not transfer local
  access tokens to other origins or persist them in browser local storage.
- Validate public mutation fields strictly. Keep amounts as integer minor units;
  preserve manual corrections, identities, and accounting relationships.
- Treat source emails, PDFs, and imported files as untrusted data. New parser
  fixtures must be synthetic or thoroughly anonymized, with no account numbers,
  Gmail IDs, personal names, or real references. Test failed/missing sender
  authentication separately from a template's parsing behavior.
- Preserve transaction atomicity. Test that failed imports/restores leave the
  original ledger intact and that canceled background work cannot save results.
- Keep reports and views focused. Shared rendering belongs in `frontend/components`,
  navigation in `frontend/lib/navigation.ts`, validation in `backend/models.py`,
  and backup handling in `backend/backup.py`.
- Add regression tests for behavioral and security fixes. For small visual-only
  changes, lint, type checking, and a build are usually enough.

Explain the problem, resulting behavior, and relevant checks in the pull request.
Contributions are distributed under the repository's MIT license. Report security
issues through the private process in [SECURITY.md](SECURITY.md).

## Demos and source packages

Use the [disposable synthetic demo](docs/demo.md) for screenshots, bug reports,
and interface changes. Do not point development scripts at an existing personal
ledger. A different data directory by itself does not isolate Keychain; the demo
uses a separate memory-only vault as well.

After reviewing and committing the intended source files, create a source-only
package with:

```sh
python3 scripts/export_source.py --output /tmp/kharcha-source.zip
```

The exporter includes current contents of tracked files, refuses ignored files
and symlinks, excludes Git history, scans a temporary snapshot, and adds a SHA-256
file manifest. Before a new file is committed, include it deliberately with an
exact `--include docs/example.md` argument. Untracked files are otherwise omitted.
It will not overwrite an existing package.

Never zip the entire working folder. Review the extracted package with
`python3 scripts/check_secrets.py --directory /path/to/extracted/kharcha` and
inspect all screenshots. Pattern matching cannot establish that financial prose
or image pixels are synthetic. To publish a history-free package, initialize a
fresh repository from its extracted source and use a public-safe Git commit
identity, such as your GitHub-provided noreply email. Do not copy an old `.git`
directory into it.
