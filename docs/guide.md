# Kharcha guide

A local Gmail spending tracker for one person, one Gmail account, and primarily INR. The app runs on this Mac, with optional paired browser access from a phone on the same private network. The app uses Gmail read-only OAuth, local parsers, encrypted email caching, and a persistent SQLite ledger. Optional automatic spending analysis uses the signed-in Codex runtime; selected ledger details are sent to Codex. Email fetching and accounting remain local.

## Start the app

Double-click **Start Kharcha.command** in Finder, or run:

```sh
./"Start Kharcha.command"
```

The launcher opens **http://127.0.0.1:8765** and unlocks the app for that browser tab. Opening the base URL in a fresh tab does not grant access to the ledger; run the launcher again to unlock it. Keep the launcher running while using the app. Control-C stops it. The launcher authenticates before reusing an existing instance, refreshes dependencies when lockfiles change, and builds changed frontend assets when needed. After upgrading from a version without local authentication, stop the old instance first. Starting the backend rotates the private access credential, so use the launcher to reopen stale browser tabs.

The first setup requires Python 3.11+ and Node.js 22.13+ with npm. Dependencies are locked in `requirements.lock.txt` and `frontend/package-lock.json`. Initial installation and builds require internet access. Subsequent reporting works offline; Gmail synchronization requires internet and runs on startup and every 15 minutes while the backend is running. No login-time daemon or public server is installed.

## Use Kharcha on your phone

Wi-Fi access is **on by default**. The desktop interface remains at
`http://127.0.0.1:8765`; phones use a separate HTTPS address shown in
**Settings → Mobile access**. Both devices must be on the same private IPv4
subnet. A wired Mac also works when it shares the phone's local network.

1. On the Mac, open **Settings → Mobile access → First-time certificate setup**.
   Download `Kharcha-Mobile.cer` and transfer it directly to the phone, for example
   with AirDrop. Only the public certificate is transferred.
2. On iPhone, install the downloaded certificate under **Settings → General →
   VPN & Device Management**, then enable **Kharcha local mobile access** under
   **General → About → Certificate Trust Settings**. Apple explains the separate
   trust step in [its certificate instructions](https://support.apple.com/en-us/102390).
   On Android, install it as a CA certificate under the device's security settings;
   the exact menu varies. Do not bypass a browser certificate warning. Remove this
   certificate from the phone when you no longer use Kharcha mobile access.
3. On the Mac, choose **Pair a phone** and scan the QR with the phone's camera.
   Enter a device name on the phone and choose **Request access**.
4. Compare the six-digit code on both screens. On the Mac, approve the matching
   request. The phone opens the same ledger automatically.

The invitation is single-use and expires after five minutes; approval must also
finish within five minutes of the request. Phone access lasts in that browser tab
until the app restarts, the network changes, or you disconnect it. Refresh keeps
the session; a fresh browser tab needs pairing. Device labels are supplied by the
phone, so approve by the matching code, not the name alone.

You can view reports, review transactions, enter or edit transactions, upload
statements, and export CSV from the phone. Edits update the Mac's ledger. Gmail
sign-in, phone management, encrypted backups, restore, and deleting all data stay
on the Mac. Optional AI consent still applies to requests made from a phone.

Keep the Mac awake with the launcher running. To stop access, disable **Allow
access over Wi-Fi**; this immediately revokes all phone sessions and saves your
choice. Turning it back on requires fresh pairing. **Disconnect** revokes one
phone. If the Mac changes IP address, the app updates the phone URL and revokes
old sessions; rescan a new link without reinstalling the same root certificate.

If access is unavailable, check the status in this panel. macOS may ask whether
to allow incoming connections to Python; allow it only if you want phone access.
Guest Wi-Fi often isolates devices, and a VPN or firewall may block access. A
port conflict can be resolved with `MONTHLYCOST_MOBILE_PORT`, using a different
port from `MONTHLYCOST_PORT`. IPv6-only and public-address networks are not supported.
No router port forwarding is needed.

For a desktop-only launch, run `python3 scripts/launch.py --no-mobile` or set
`MONTHLYCOST_MOBILE=0`. This overrides Wi-Fi access for that process and retains
the saved Settings preference. Stop an existing instance before using a different
launch override. A phone never needs your Gmail or Mac account password.

## Connect Gmail

Open **Connections & rules** and follow the steps there:

1. Create/select your project in [Google Cloud Console](https://console.cloud.google.com/) and [enable Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com).
2. Configure Google Auth Platform branding and audience. For a personal Gmail account use External, and add your Gmail as a test user while the app is in Testing. This app requests `https://www.googleapis.com/auth/gmail.readonly`.
   In Data Access, add that read-only scope and save.
3. Create an OAuth client of type **Desktop app**, download its JSON, and select it in the local app. A Web application client is rejected.
4. Click **Sign in with Google**. Sign in on Google's page and grant Gmail read-only access. If using a Codex embedded browser and Google blocks sign-in, open the app URL in Safari or Chrome and connect there.
5. Google redirects back to `http://127.0.0.1:8765/api/auth/callback`. The initial import starts automatically. You can stop and resume it.

Google's Testing mode normally expires refresh tokens after seven days. For ongoing personal use set the audience publishing status to **In production**. [Personal-use apps can qualify for a verification exception](https://support.google.com/cloud/answer/13464323), although the unverified-app warning may remain. Tokens can still expire or be revoked; the app keeps your history and offers reconnect. Publishing the OAuth audience does **not** publish or host this app.

Google credentials are stored in macOS Keychain under `MonthlyCost.local`. You may see a normal macOS Keychain permission prompt. There is no plaintext credential fallback. The app never needs your Google password, Gmail app password, or sending/modification permissions.

### Which emails are synced

Kharcha reads received mail from the last 6 months and checks message bodies
locally for transactions. Later syncs check new received messages. Spam, trash,
sent mail and drafts are excluded. Sender addresses, subject wording, replies
and Gmail categories do not block scanning, so a receipt from a small seller or
an unfamiliar bank subject can still be found.

Only authenticated messages with completed transaction evidence are retained.
Generic formats need an amount, payment-completion wording and an account,
payment reference or receipt identifier in the body. Personal discussions,
unconfirmed payments and unrecognized messages are discarded without retaining
their bodies, subjects, senders or review entries. The retry queue keeps opaque
Gmail IDs. The parser is not a perfect classifier: verify unclear transactions
and use statement import or manual entry for missing ones.

The first sync after this change scans the 6-month window again to recover mail
missed by the former sender/subject filters. Existing transaction evidence is
reused and duplicate transactions are not added. Old Gmail review-cache entries
with no linked transaction are removed; saved ledger records and manual evidence
are preserved. An interrupted 12-month import restarts discovery within the last
6 months; transactions already saved remain in your ledger.

Google's `gmail.readonly` permission covers the mailbox. Scanning and parsing
run on this Mac. Optional AI spending review is separate: if enabled it sends
structured financial details to OpenAI, without raw email bodies or subjects.

### Seeing what a sync added

The sync result shows how many transactions were added, including **No new
transactions** when nothing new entered the ledger. New Gmail transactions have
a **New** badge. Choose **See new transactions** to view unseen additions across
months and currencies. A transaction is automatically marked as seen after it is
visible on screen for a moment, including in transaction details. Its **New**
badge appears only on that first viewing. Off-screen rows and hidden tabs do not
count as viewing. Rows stay in place during the current visit to the new view;
seen rows will no longer appear there on the next visit or reload. This is saved
across syncs and restarts. Messages that match an existing transaction or imported
summary do not count as additions. Viewing a row does not resolve its separate
evidence-review warnings.

## What is ready, and what still needs real-email validation

The Gmail connector, caching, ledger, reporting, editing, exact-match categorization rules, shared expenses, review queue, imports, CSV export, encrypted backups, and restore are implemented. Local synthetic fixtures test the spending rules and a conservative generic financial-email parser.

**No claim is made that all of your institutions' templates are verified.** Verify imported transactions against your statements before relying on the totals. Automatic Gmail sync retains only authenticated messages with completed transaction evidence; unknown or ambiguous formats and missing, failed, or ambiguous authentication are discarded without keeping source bodies or review entries. Explicit local imports can retain unclear evidence in Review & coverage. The local source inspector groups retained senders/templates, and parsed generic templates begin with an audit item. Existing sources imported before authentication checks are flagged for review when the app starts; their saved accounting is preserved until explicitly corrected. New institution/template-specific parsers should use synthetic fixtures. Do not commit actual emails, OAuth JSON, or private transaction exports.

Previously exported transaction summaries can be imported from the app. They are treated as provisional summarized evidence. Reimporting does not duplicate records. When Gmail fetches the corresponding original message, matching records are reconciled, preserving user corrections. Imported messages may lack precise person identities or classifications; inspect them rather than treating an opaque UPI description as a named merchant. Keep personal exports outside source control.

## Spending behavior

Navigation keeps the selected screen, month, currency, filters, transaction, and source email in the local URL. Browser Back/Forward and the labeled return buttons retrace these steps; search typing does not add a history entry for every letter. Refresh keeps the current view. Transaction filters are shown as removable chips with a single clear-all action, and do not affect the Overview's recent transactions. On mobile, the bottom navigation provides direct access to Overview, Transactions, Review, and Settings.

- Six completed months are shown with a separate current-month bar. The current month opens by default. Reporting uses Asia/Kolkata dates and integer monetary values.
- Purchases and payments to people count as spending. Card bill repayments, own transfers, investments, wallet funding, loan proceeds, and financing adjustments are separate movements.
- EMI installments count in their charge month. When financing is established, exclude the original financed purchase. Missing EMI details are not inferred from the card bill total.
- Refunds reduce the month received. Link a refund to its original purchase where possible. A partial refund of a shared bill needs the user's personal-share allocation reviewed.
- Shared payments can be split into personal, reimbursable, and lending portions. Only personal spending counts. Link incoming repayments to the non-personal share to avoid subtracting it twice.
- Cash withdrawals stay unallocated until the user records personal spending against them. Do not allocate a withdrawal and independently add the same cash purchase a second time.
- Equal person/amount is a **category-rule match**, never sufficient proof of a duplicate. Automatic duplicate merges require compatible references and event evidence. Manual merges can be undone.
- A person-and-amount rule requires a confirmed identity or UPI ID, exact amount, currency, and direction. Category changes made manually win. Historical application requires an explicit preview.
- Recognized merchant names are categorized locally: Zara and Myntra → Shopping, Swiggy and Zomato → Food & dining, Instamart/Blinkit/Zepto → Groceries, BookMyShow/PVR → Entertainment, and MakeMyTrip/redBus → Travel. Amazon defaults to Shopping; Fresh and Prime have service-specific defaults, and clear, explicitly labelled purchased-item details can refine an Amazon/Flipkart category. Mixed or unclear baskets keep the broad Shopping default. Amazon Pay alone and opaque UPI fragments do not identify what was purchased.
- Built-in defaults fill existing uncategorized records and future imports. Income transactions receive Income, and card repayments, investments, own transfers, wallet funding, and other recognized transaction types receive their corresponding categories without counting as purchases. Merchant defaults also recognize bank wrappers, grocery-specific Amazon descriptions, and additional dining, travel, health, and personal-care merchants. Exact Kotak UPI descriptors ending in SWI or ZEP receive clearly labelled Swiggy/Zepto inferences; other opaque fragments stay unresolved.
- Defaults never replace your category corrections or saved rules, establish a person's identity, or change duplicates. Transaction details explain the category choice; unknown merchants remain Uncategorized. Changing a transaction type recomputes an automatic category. These suggestions do not remove evidence-review warnings.
- Transaction details include **Identify merchant** for unfamiliar outgoing purchase/payment descriptors. The lookup searches public company and billing pages with the signed-in Codex runtime, then shows a likely brand, suggested category, confidence, and supporting links. Only the sanitized merchant label and available category names are sent; account fields, amounts, dates, notes, references, and email bodies are not included. Web queries are external and use your Codex allowance. Opening transaction details does not start a web search.
- Accept a supported match to fill an uncategorized payment and optionally remember it for future imports with the exact same bank description and currency. Manual categories, saved rules, splits, accounting types, and original bank descriptions are preserved. Other historical payments are not changed by accepting a match. Identified brands appear beside the original description in the transaction list and can be searched by brand. **Not this merchant** dismisses a suggestion; **Forget this match** removes it from this payment and future imports, preserving later category corrections. Previously saved matches on other payments remain. Unclear results stay unresolved. Accepted matches are included in encrypted backups; temporary lookup results are omitted. Erase, restore, and cancellation reject late lookup results.
- `AGIONE TE/ICICI Ban` has a built-in, sourced inference to **Emergent → Subscriptions**, using [Emergent's company information](https://emergent.sh/info), which names Agione Technologies Private Limited. This fills uncategorized historical records on startup and future imports. The truncated descriptor is labelled a likely match; the source does not establish a specific purchase or recurring plan.
- Foreign-currency values remain separate. A verified INR settlement can be entered as a correction while preserving the original amount in notes. No exchange rates are invented.

## Privacy and data management

Data lives in `~/Library/Application Support/MonthlyCost/monthlycost.sqlite3`, outside the repository, with access restricted to the current OS user. Normalized ledger records and source metadata are **not database-encrypted**; use macOS FileVault for disk-level protection. Relevant cached email bodies are encrypted with a Keychain-held key. Unrelated or unconfirmed mail read during sync is discarded without saving its subject or body. The app renders source text without remote images or executable HTML. Data APIs require a random per-process bearer credential. A private `runtime-PORT.json` file in the app-data directory lets the same OS user launch an authenticated browser session. The launcher passes the credential in a URL fragment, which the browser removes and stores only in origin-scoped session storage. It is never included in backups or sent as a localhost cookie. Other programs running as your own OS user are outside this protection boundary; see [SECURITY.md](../SECURITY.md).

The default body retention window is 12 months; unresolved source cases keep their evidence. Transactions, corrections, rules, and source references remain until you delete them. Disconnecting Gmail preserves local data. Gmail deletions do not erase your ledger. Deleting local data removes app records and Keychain entries, but does not affect Gmail or previously exported files/backups. Removed database pages are compacted; this is not a promise of forensic secure deletion on SSDs or system backups.

Encrypted `.mcb` backups use a password-derived key and include cached evidence and ledger records, but no Google credentials. Restore validates a separate in-memory ledger before replacing the current ledger in one transaction. Invalid backups leave the original intact. Reconnect Gmail and re-enable AI review afterwards. Temporary statement previews are omitted; upload the PDF again if needed. Deletion and restore cancel active AI work and reject late results. Protect your backup password—there is no recovery service. CSV exports are readable files, not encrypted backups, and express amounts in minor units as identified by the column names.

## Development and checks

See [CONTRIBUTING.md](../CONTRIBUTING.md) for setup and development guidance.

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check backend scripts tests
.venv/bin/ruff format --check backend scripts tests
.venv/bin/python scripts/check_secrets.py
npm run check --prefix frontend
.venv/bin/pip-audit -r requirements.lock.txt --no-deps --disable-pip
npm audit --prefix frontend
```

GitHub Actions runs the tests, lint, formatting, type checks, production build,
credential-pattern checks, and dependency audits. It uses synthetic temporary
ledgers; no live Gmail, Codex, or Keychain credentials are needed.

For frontend development, set `MONTHLYCOST_MOBILE=0` and run the backend on 8766 while keeping `MONTHLYCOST_PORT=8765` (the browser/OAuth origin), then `npm run dev` in `frontend`. The frontend proxies `/api` to that backend. With both processes running, launch Kharcha normally to unlock the development browser through that proxy. Use a separate `MONTHLYCOST_DATA_DIR` for development. Production uses FastAPI on 8765 for desktop access and a separate HTTPS listener on 8766 for paired phones, serving the same exported frontend assets; Node is not a production server.

Runtime customization uses `MONTHLYCOST_PORT`, `MONTHLYCOST_MOBILE_PORT`, `MONTHLYCOST_MOBILE`, and `MONTHLYCOST_DATA_DIR` environment variables. The launcher does not automatically load `.env` files. Real credentials must be imported through the app, not placed in environment files or source control.

Scanned PDF/OCR statements and unsupported statement layouts, SMS ingestion, automatic FX conversion, multiple Gmail accounts, remote access, and cloud AI extraction of raw emails are outside this version. Email-only totals always depend on available evidence; no records for a month do not establish zero spending.

## Bank statement uploads

In Settings (Connections & rules), open **Bank statement PDF**. Choose a month (defaults to last month), use the account label from your ledger, select the currency, and upload the PDF. Enter its password only if required. Previewing never changes totals.

The local parser supports text-based tables with Date, Description/Narration/Particulars, Debit/Withdrawal and Credit/Deposit columns. Limits are 20 MB and 100 pages. It tries ruled tables and text-aligned tables. Scans, combined amount/direction columns and other layouts are not supported. Check every page against the original: the extracted row count is not proof of complete coverage, and parsing warnings identify rows/pages needing manual entry.

Compare possible existing transactions using the preview links; matching date, currency, direction and amount is a suggestion, not a confirmed match. Rows without matches are selected automatically; existing and uncertain matches are disabled and skipped again when saving. Check the selected rows and their type, especially transfers, credit-card repayments and refunds. Only selected rows enter reports. Imported entries are also listed in Review & coverage so classification can be checked. The same PDF/account/month/currency cannot add the same row twice; differently generated or overlapping PDFs are also checked against the current ledger. Same-day amount/currency/direction matches are conservatively withheld; UPI references also detect matching transactions across posting dates. Uncertain matches can be reviewed separately, rather than added by this importer.

The original PDF and password are not saved. Extracted previews are encrypted locally; imported transactions retain the filename and an import identity, and are included in backups. Upload again whenever you want to reconcile another month. Restart Kharcha after installing an updated backend.

## Spending plan and financial context

Settings includes **Your spending plan**. It separates user-selected
major-project categories from everyday spending without changing the ledger's
accounting totals. No project category is selected by default. Optional
monthly targets apply only to everyday spending in the chosen currency; they are
not estimates of cash available. Financial context, priorities, commitments, and
recipient notes are saved locally in settings and included in encrypted backups.
When automatic AI analysis is enabled, saved context is included in the analysis sent to Codex.

A trusted local integration running as the same OS user can read `/api/financial-context` and
`/api/financial-brief?month=YYYY-MM&currency=INR` from the running local app using `Authorization: Bearer <token>` from its private `runtime-PORT.json` file. Keep the token out of prompts, logs, and shared files. The
brief includes transaction IDs for evidence lookup, major-project separation,
exempt counts, and coverage warnings. A useful request is: “Review my August
spending using my saved financial context. Explain major changes with supporting
transactions and list unresolved assumptions before suggesting actions.” This
is a read-only integration, not an embedded AI chat. No additional AI API key or
paid service is required for Codex to read this local project.

The review queue sorts known amounts within each currency, INR first, and shows
recipient, amount, and date. Classification conflicts are computed from effective
records; changing a category alone does not change the transaction's accounting
meaning. Open the transaction to correct its type or exempt it. Such conflicts
cannot be dismissed without correcting the record. Unknown-amount source issues
remain in the queue and must not be treated as zero-value risks.

Saving a statement also enriches existing transactions with unique matching UPI references, amounts, currencies and directions. It preserves user corrections and confirmed identities, fills missing or truncated names from the bank narration, and applies known merchant categories to uncategorized entries. The original narration is visible in transaction details. A missing-date warning can be resolved when the statement supplies the date; other review issues remain open unless the evidence actually answers them.


## Automatic AI spending review

Overview opens on the current month. Use the **Month** picker beside **Currency**
to view any previous month, including older saved history. The regular-spending
breakdown, supporting transactions, AI insights and full ledger details all follow
the selected month. Completed months include every day; only the current month
is labeled “so far.” In **Settings → Categories
& automatic rules**, add your own categories and choose which are **Fixed** or
**Unavoidable**. Those categories remain in ledger totals but are excluded from
the main expense breakdown and AI insight evidence. No category or transaction
type is protected by default, including rent and installments. Each category
can belong to only one spending group, and you can change it back to Regular.
Optionally mark categories as **Major project** to separate them from your
everyday spending target; this does not automatically protect them from AI
suggestions. These choices are saved locally and included in encrypted backups.
Existing custom categories and saved project choices are retained; older implicit
fixed/unavoidable assumptions are removed, so select your preferences once in Settings.

Category totals use personal allocations; linked refunds follow the
original expense categories. Known merchant aliases are grouped for display,
without modifying transaction identities. Regular spending is not assumed to be
avoidable. Full ledger details remain available below the main insights.

`GET /api/spending-focus?currency=INR&month=YYYY-MM` provides selected-month totals, categories,
merchants, repeated purchases and coverage directly from the ledger, even when
AI is unavailable. Codex interprets the main drivers and repeated purchases;
there is no forced savings target, arbitrary percentage cut, or obligation to
produce recommendations. Each insight links to selected-month evidence. Omit
`month` to use the current month. AI status, input preview and refresh also accept
`month`; reviews are cached separately for each month and currency. Use **Refresh
insights** to generate a previous month’s review when AI is enabled. Browsing
months does not start an AI run. Automatic reviews continue to focus on the
current month. The AI
never changes exemptions, identities, amounts, categories or accounting types.

`GET /api/financial-context` includes `fixed_categories`, `unavoidable_categories`
and `project_categories` (all empty for new users). `PATCH /api/financial-context`
saves only supplied fields, preserving other notes and choices; `PUT` replaces
the context. Spending focus exposes `fixed_minor`, `unavoidable_minor` and
`other_minor` (regular spending), together with the selected category policy.
Changing preferences invalidates old AI insights before they can be reused.

Enable **Allow AI spending review** in Connections & rules, read the data-sharing notice, then choose **Allow sending data & enable AI**. The notice names OpenAI as the recipient through Codex and includes a local preview of the current INR input. Consent is versioned; older enablement settings require confirmation of this notice. AI is disabled for new ledgers and after a restore. Turning it off cancels the current review. After sync finishes, the app checks for analysis once a minute. Automatic
attempts are limited to one per day across currencies; manual refresh has a
one-minute cooldown. Analysis uses the existing signed-in Codex allowance.
The app sends selected-month regular-spending dates, amounts, currency, merchant/payee names, categories, transaction types, internal IDs, notes, corrections, and review warnings. It also sends category and merchant totals (including fixed/unavoidable summaries), repeated purchases, aggregate prior merchant activity, record coverage, and saved financial context: priorities, commitments, people, notes, targets, and category preferences. Raw email bodies, statement PDFs, Gmail credentials, and separate account/reference fields are excluded. Names and free-text notes can contain personal information. The preview is created locally from the same snapshot function used for the AI input; later reviews use updated data. Disabling cannot retract data already sent. Results are stored locally and in backups.
Changed ledger data hides outdated insights until regenerated, while the factual
breakdown updates independently. No separate daemon is installed; the app must
remain running.

The runner uses an isolated temporary working directory, read-only execution,
ephemeral sessions, and disabled shell, app, browser, hook, and multi-agent tools.
Structured responses are validated, including transaction IDs. A still-current review remains visible if refresh fails; stale reviews are hidden. Signing out of Codex or reaching its usage limit
can prevent new reviews; the UI shows a retryable failure. The recommendations
are AI interpretations of incomplete records, not verified account balances.

## License and security reporting

Kharcha is available under the [MIT license](../LICENSE). Read
[SECURITY.md](../SECURITY.md) for its threat model, private reporting process, and
maintainer release checks. Local email-based totals remain provisional evidence,
not verified bank balances.

### Export a spending PDF

Choose **Export PDF** on Overview or Transactions, set an inclusive date range,
and choose whether to include the transaction list. **Download PDF** creates a
paginated A4 report on the Mac and downloads it to the current browser, including
a paired phone. No external PDF service or automatic sharing is used. On a phone,
use the browser's download/save controls if it previews the file.

Overview exports the selected currency with no transaction filters. Transactions
also carries its active category, type, search and spending-group filters into the
chosen range. New transactions includes payments viewed during the current visit
and reports each currency separately without conversion. The selected range
replaces the screen's month; applied filters are printed in the report.

Total personal spending includes regular, fixed and unavoidable costs, personal
split shares and refunds received in the selected dates. Card bills, transfers,
income and excluded payments contribute zero. Category/group filters select whole
matching payments, so their totals can include other personal split categories.
The payment breakdown uses recorded **accounts**, since payment methods are not
reliably stored. The optional list distinguishes the original debit/credit amount
from its personal-spending contribution. Reports do not include source email
bodies, private notes, authentication tokens or automatic external links.

The bundled report font supports Latin and Cyrillic text. Characters it cannot
render appear explicitly as `[U+code]`; currency amounts use ISO codes such as
`INR` for unambiguous printing. Coverage warnings remain visible. Empty selections
produce a report stating that no records matched, rather than implying complete
financial coverage.
