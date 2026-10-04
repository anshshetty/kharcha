# Security policy

Kharcha is a local, single-user macOS application with paired browser access on
the same private IPv4 subnet. Only the latest source revision is supported. It
is not designed for Internet hosting, shared browser profiles, access outside
the local network, or use as an authoritative bank ledger.

## Report a vulnerability

Use the repository's **Security → Report a vulnerability** flow for a private
report. If that option is unavailable, open an issue asking for a private
reporting channel, without including exploit details, credentials, or personal
data. Do not publish real email bodies, transaction exports, OAuth files, runtime
tokens, or backups. Use synthetic examples and include the affected revision,
reproduction steps, expected behavior, and observed impact.

This repository has no staffed response SLA. Avoid sharing sensitive details
until a maintainer has established a private channel.

## Security boundaries

- The launcher binds the desktop backend to `127.0.0.1`. Every data API requires a random
  credential rotated at process startup. The current credential is in a `0600`
  runtime file inside the OS user's `0700` app directory. The launcher transfers
  it in a URL fragment, which the browser immediately removes and keeps in
  origin-scoped session storage. Credentials are never put in query strings,
  cookies shared across localhost ports, or source control.
- Mobile access is on by default unless disabled in Settings or for a launch.
  A separate HTTPS listener binds only the selected private Wi-Fi/Ethernet IPv4
  address (default port `8766`), requires clients in its detected subnet, and
  rejects unexpected Host/Origin headers. Forwarded headers cannot change its
  listener identity, client address, or protocol. Desktop credentials are never
  accepted by this listener, and mobile credentials never unlock desktop APIs.
  Network reachability alone grants no ledger access.
- Pairing invitations are random 256-bit, single-use links that expire after
  five minutes. They travel in a URL fragment removed by the phone interface.
  Pairing attempts are limited; the Mac owner approves the matching six-digit
  confirmation code. Polling uses a separate random credential, and only an
  approved request receives a mobile credential. Device sessions are held in
  memory, use individual CSRF tokens, and are revoked on restart, network
  changes, disabling access, restoring/erasing data, or explicit disconnection.
  Gmail authentication, device management, backups, restore, data erasure, and
  workspace-local import are denied on the mobile listener by the backend.
- HTTPS uses an app-specific local certificate authority. Its signing key and
  server key are `0600` files in the app directory's `0700` `mobile-tls` folder,
  excluded from portable backups. The public certificate is downloadable only
  from the authenticated desktop API. Transfer it directly from the Mac and
  explicitly trust it on the phone; no system trust is installed automatically.
  Trusting a local CA is a device-level trust decision: remove it from the phone
  when no longer using Kharcha. Changing the Mac's IP generates a fresh server
  certificate under the same CA. IPv6-only networks and guest-network isolation
  are not supported. Same-subnet checks do not establish a device's identity;
  pairing remains required even on trusted Wi-Fi.
- The unauthenticated health endpoint contains no ledger data. The OAuth callback
  is public because Google navigates to it; it requires an expiring, single-use
  state and PKCE. Host/origin checks and CSRF validation remain enabled.
- Other OS users cannot obtain the private runtime credential through the API.
  Software running as the same OS user, administrators, malicious browser
  extensions, and a compromised operating system are outside this boundary.
- Google credentials and the email-cache key use macOS Keychain. Cached source
  bodies are encrypted. Normalized ledger records are not database-encrypted;
  FileVault and OS access controls provide disk protection.
- Automatic email ingestion requires Gmail receiving-server authentication
  evidence as well as parseable content. Missing, failed, conflicting, or
  unsupported evidence stays in review. Matching a bank template is not proof of
  authenticity. Imported header metadata must come from the original Gmail API
  response, never from prose inside an email. See
  [RFC 8601](https://www.rfc-editor.org/rfc/rfc8601.html) for the receiving-server
  trust boundary and [Gmail's authentication guidance](https://support.google.com/mail/answer/180707).
- Backups use authenticated encryption with a password-derived key. Restore
  validates an isolated in-memory ledger before a transactional replacement.
  Temporary statement previews and local credentials are not portable backup
  content. Restore requires fresh AI consent and Gmail reconnection.
- Gmail sync is an optional, read-only connection to Google. Its OAuth grant is
  mailbox-wide. Kharcha downloads received message bodies in its sync window
  and parses them locally, including personal mail and unfamiliar senders or
  subjects. Spam, trash, sent messages and drafts are excluded. Automatic sync
  retains only authenticated, parsed transactions with completed-payment
  evidence. Unrelated, unverified and unconfirmed messages leave no source,
  body, subject or review-cache record; the retry queue retains opaque Gmail IDs.
  Old unlinked Gmail source/review records are removed at sync, while linked
  evidence and saved transactions are preserved. The local evidence check is a
  heuristic and can still miss or misclassify messages. Broader scanning adds
  no raw email fields to optional AI payloads; those contain extracted financial
  data only and retain their separate opt-in requirement.
- Merchant search
  sends only a sanitized label and category list through Codex for public web
  research; financial records and credentials are not part of that request.
- AI review is off by default. Enabling requires confirmation of a versioned
  notice describing the records, summaries, names, notes, and financial context
  sent to OpenAI through Codex. A local preview uses the runner's input builder.
  Old consent versions cannot start AI; the backend checks consent again before
  starting its process. Consent is not included in portable backups. Disabling it, deleting data, or restoring a backup cancels active work
  and invalidates late results. These operations cannot retract data already
  sent to a service, remove independently exported files, or promise forensic
  deletion from SSDs and OS backups.

## Maintainer release checks

Before publishing the repository, enable GitHub private vulnerability reporting
and require the CI checks on the default branch. Review the complete Git history
for private information; the lightweight credential-pattern check in CI is not a
complete secrets audit. Never attach local app data as test fixtures or build
artifacts. Re-run both dependency audits before releases and update lockfiles
when upstream fixes are available.

The release guard scans working files and staged blobs; `--history` also checks
reachable commits and blobs and rejects shallow history. It detects known
credential patterns and private file types, not arbitrary personal spending
details. Local recovery objects, reflogs, remote caches, screenshots and video
content need separate review. A successful pattern scan is not a privacy
certificate.

For distribution, use the reviewed source-only exporter described in
[Contributing](CONTRIBUTING.md#demos-and-source-packages). It excludes Git internals
and local files rather than copying the working folder. Historical leaks are
not fixed by `.gitignore` or by deleting a file from the current revision. Keep
an existing repository private if its history has not passed a personal-data
review. Use the [synthetic demo](docs/demo.md) for all public media.
