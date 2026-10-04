'use client';
import { useEffect, useState } from 'react';
import {
  Mail,
  ShieldCheck,
  Upload,
  Download,
  RefreshCw,
  Trash2,
  Link2,
  Plus,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
} from '@/components/ui/alert-dialog';
import { api, money, downloadTransactions } from '@/lib/api';
import { MobileAccess } from '@/components/mobile-access';
import { Blank, SectionTitle } from './common';
import { StatementImport } from './statement-import';
import { CategoryPreferences } from './category-preferences';
import { AIConsent } from './ai-consent';
import { FinancialPlan } from './financial-plan';
import Image from 'next/image';

import type {
  AppStatus,
  CategoryRule,
  Account,
  CategoryChoices,
} from '@/lib/types';
export function SettingsPanel({
  active,
  status,
  month,
  currency,
  onTransaction,
  categories,
  notify,
  onError,
  refresh,
}: {
  active: boolean;
  month: string;
  currency: string;
  onTransaction: (t: import('@/lib/types').TransactionSelection) => void;
  status: AppStatus | null;
  categories: string[];
  notify: (s: string) => void;
  onError: (s: string) => void;
  refresh: () => Promise<void>;
}) {
  const [section, setSection] = useState('connections');
  const [rules, setRules] = useState<CategoryRule[]>([]),
    [choices, setChoices] = useState<CategoryChoices | null>(null),
    [accounts, setAccounts] = useState<Account[]>([]),
    [name, setName] = useState(''),
    [busy, setBusy] = useState(false),
    [modal, setModal] = useState(''),
    [password, setPassword] = useState(''),
    [confirmation, setConfirmation] = useState(''),
    [restoreFile, setRestoreFile] = useState<File | null>(null),
    [localError, setLocalError] = useState('');
  const load = () =>
    Promise.all([
      api<CategoryRule[]>('/rules').then(setRules),
      api<Account[]>('/accounts').then(setAccounts),
      api<CategoryChoices>('/financial-context').then((context) =>
        setChoices({
          fixed_categories: context.fixed_categories,
          unavoidable_categories: context.unavoidable_categories,
          project_categories: context.project_categories,
        }),
      ),
    ]);
  useEffect(() => {
    if (active) load().catch((e) => onError(e.message));
  }, [onError, active]);
  const run = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true);
    setLocalError('');
    try {
      const r = await fn();
      notify(message);
      await refresh();
      await load();
      return r;
    } catch (e) {
      setLocalError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const connect = () =>
    run(async () => {
      const r = await api<{ url: string }>('/auth/start', 'POST', {});
      window.location.assign(r.url);
    }, 'Opening Google sign-in');
  const connected = status?.connection?.state === 'connected';
  const download = (data: string, filename: string) => {
    const blob = new Blob(
      [Uint8Array.from(atob(data), (c) => c.charCodeAt(0))],
      { type: 'application/octet-stream' },
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  };
  const backup = () =>
    run(async () => {
      const r = await api<{ file: string; filename: string }>(
        '/backup',
        'POST',
        { password },
      );
      download(r.file, r.filename);
      setModal('');
      setPassword('');
    }, 'Encrypted backup downloaded');
  const erase = () =>
    run(async () => {
      await api('/erase', 'POST', { confirmation });
      setModal('');
      setConfirmation('');
    }, 'Local ledger deleted');
  const restore = () =>
    run(async () => {
      if (!restoreFile) throw new Error('Choose a backup file');
      const bytes = new Uint8Array(await restoreFile.arrayBuffer());
      let binary = '';
      for (let i = 0; i < bytes.length; i += 32768)
        binary += String.fromCharCode(...bytes.slice(i, i + 32768));
      await api('/restore', 'POST', {
        file: btoa(binary),
        password,
        confirmation,
      });
      setModal('');
      setPassword('');
      setConfirmation('');
    }, 'Backup restored. Reconnect Gmail when ready');
  return (
    <>
      <div className="settings-layout">
        <nav className="settings-navigation" aria-label="Settings sections">
          <button
            aria-current={section === 'connections' ? 'page' : undefined}
            onClick={() => setSection('connections')}
          >
            Connections
          </button>
          {status?.mobile_client === false && (
            <button
              aria-current={section === 'mobile' ? 'page' : undefined}
              onClick={() => setSection('mobile')}
            >
              Mobile access
            </button>
          )}
          <button
            aria-current={section === 'imports' ? 'page' : undefined}
            onClick={() => setSection('imports')}
          >
            Imports
          </button>
          <button
            aria-current={section === 'categories' ? 'page' : undefined}
            onClick={() => setSection('categories')}
          >
            Categories & rules
          </button>
          <button
            aria-current={section === 'accounts' ? 'page' : undefined}
            onClick={() => setSection('accounts')}
          >
            Accounts
          </button>
          <button
            aria-current={section === 'ai' ? 'page' : undefined}
            onClick={() => setSection('ai')}
          >
            AI & context
          </button>
          <button
            aria-current={section === 'data' ? 'page' : undefined}
            onClick={() => setSection('data')}
          >
            Data & backups
          </button>
        </nav>
        <div className="settings-content">
          <div hidden={section !== 'connections'} className="settings-section">
            {status?.mobile_client === false ? (
              <>
                <section className="panel">
                  <SectionTitle
                    title="Your Gmail connection"
                    detail="Optional connection to Google. Email parsing and your ledger stay on this Mac."
                  />
                  {!connected && (
                    <div className="connection-illustration">
                      <Image
                        src="/illustrations/receipts.png"
                        width={200}
                        height={150}
                        alt=""
                        unoptimized
                      />
                      <div>
                        <h3>Bring your spending into view</h3>
                        <p>
                          Read-only Gmail import or a statement from your bank.
                          Your ledger stays on this Mac.
                        </p>
                      </div>
                    </div>
                  )}
                  <div className="small-row">
                    <div
                      style={{ display: 'flex', alignItems: 'center', gap: 12 }}
                    >
                      <span className="mail-icon">
                        <Mail size={21} />
                      </span>
                      <span>
                        <b>
                          {status?.connection?.email || 'One Gmail account'}
                        </b>
                        <small>
                          Local email scan · 6-month import · incremental
                          updates
                        </small>
                      </span>
                    </div>
                    <span
                      className={'status-chip ' + (!connected ? 'off' : '')}
                    >
                      {connected
                        ? 'Connected'
                        : status?.connection?.state === 'reconnect'
                          ? 'Reconnect needed'
                          : 'Not connected'}
                    </span>
                  </div>
                  <div className="gmail-privacy-note">
                    <ShieldCheck size={18} aria-hidden="true" />
                    <div>
                      <b>Email parsing stays on this Mac</b>
                      <p>
                        Sync reads received mail locally to find transactions,
                        including unfamiliar senders and subjects. Only
                        authenticated transaction evidence is saved. Unrelated
                        or unconfirmed mail is discarded.
                      </p>
                      <p>
                        AI spending review is separate and optional. If enabled,
                        it shares financial details with OpenAI without
                        including raw email bodies. You can review that data
                        before enabling it.
                      </p>
                    </div>
                  </div>
                  {!status?.configured && (
                    <>
                      <p className="help-text" style={{ marginTop: 16 }}>
                        Gmail sync starts after you complete the Google setup
                        and sign in. Existing reports can use imported files and
                        saved local data while Gmail is disconnected.
                      </p>
                      <ol className="setup-steps">
                        <li>
                          <span>
                            Create or choose a project in{' '}
                            <a
                              href="https://console.cloud.google.com/projectcreate"
                              target="_blank"
                              rel="noreferrer"
                            >
                              Google Cloud Console
                            </a>
                            . Enable the{' '}
                            <a
                              href="https://console.cloud.google.com/apis/library/gmail.googleapis.com"
                              target="_blank"
                              rel="noreferrer"
                            >
                              Gmail API
                            </a>{' '}
                            in that project.
                          </span>
                        </li>
                        <li>
                          <span>
                            Open{' '}
                            <a
                              href="https://console.cloud.google.com/auth/overview"
                              target="_blank"
                              rel="noreferrer"
                            >
                              Google Auth Platform
                            </a>
                            . Configure the app name and your email, choose an
                            External audience, and add your Gmail as a test user
                            while testing.
                          </span>
                        </li>
                        <li>
                          <span>
                            Under Data Access, add the scope{' '}
                            <b>
                              https://www.googleapis.com/auth/gmail.readonly
                            </b>
                            . This gives read-only access; the app does not mark
                            emails as read or change your mailbox.
                          </span>
                        </li>
                        <li>
                          <span>
                            Under Clients, create an OAuth client with
                            application type <b>Desktop app</b>. Download its
                            JSON file and select it below.
                          </span>
                        </li>
                      </ol>
                      <label className="file-label">
                        <Upload size={17} />
                        <span>Google Desktop OAuth JSON</span>
                        <input
                          type="file"
                          accept="application/json,.json"
                          disabled={busy}
                          onChange={(e) => {
                            const file = e.target.files?.[0];
                            if (file)
                              void run(
                                async () =>
                                  api(
                                    '/auth/configure',
                                    'POST',
                                    JSON.parse(await file.text()),
                                  ),
                                'Google client saved in macOS Keychain',
                              );
                          }}
                        />
                      </label>
                      <p className="help-text" style={{ marginTop: 13 }}>
                        Your Google password is never entered here. You sign in
                        on Google’s own page. The downloaded client
                        configuration stays in macOS Keychain.
                      </p>
                    </>
                  )}
                  {status?.configured && (
                    <div className="settings-actions">
                      <Button disabled={busy} onClick={connect}>
                        <Link2 size={16} />
                        {connected || status?.connection?.state === 'reconnect'
                          ? 'Reconnect Gmail'
                          : 'Sign in with Google'}
                      </Button>
                      {connected && (
                        <Button
                          variant="outline"
                          disabled={busy || status?.job?.state === 'running'}
                          onClick={() =>
                            run(
                              () => api('/auth/disconnect', 'POST', {}),
                              'Gmail disconnected. Your local history is retained',
                            )
                          }
                        >
                          Disconnect
                        </Button>
                      )}
                    </div>
                  )}
                  <p className="panel-footnote">
                    <CircleInfo />
                    Google’s Testing mode normally expires access after seven
                    days. For ongoing personal use, switch the OAuth audience
                    publishing status to In production. An unverified-app
                    warning may remain.{' '}
                    <a
                      className="inline-link"
                      href="https://support.google.com/cloud/answer/13464323"
                      target="_blank"
                      rel="noreferrer"
                    >
                      Google’s guidance
                    </a>
                  </p>
                  {status?.job && (
                    <div className="small-row">
                      <span>
                        <b>{status.job.phase}</b>
                        <small>
                          {status.job.processed} emails checked this run ·{' '}
                          {status.job.discovered} queued this run ·{' '}
                          {status.job.added || 0} transactions added
                        </small>
                      </span>
                      {status.job.state === 'running' ? (
                        <Button
                          variant="outline"
                          onClick={() =>
                            run(
                              () => api('/sync/stop', 'POST', {}),
                              'Stopping sync after the current request',
                            )
                          }
                        >
                          Stop sync
                        </Button>
                      ) : (
                        <Button
                          variant="outline"
                          disabled={!connected || busy}
                          onClick={() =>
                            run(() => api('/sync', 'POST', {}), 'Sync started')
                          }
                        >
                          <RefreshCw size={15} />
                          Sync
                        </Button>
                      )}
                    </div>
                  )}
                </section>
              </>
            ) : (
              <section className="panel">
                <SectionTitle title="Connected to your Mac" />
                <p className="help-text">
                  Edits save to the same ledger on your Mac. Gmail sign-in,
                  phone management, backups, restore, and deleting all data are
                  available on the Mac.
                </p>
              </section>
            )}
          </div>
          {status?.mobile_client === false && (
            <div hidden={section !== 'mobile'} className="settings-section">
              <MobileAccess active={active && section === 'mobile'} />
            </div>
          )}
          <div hidden={section !== 'imports'} className="settings-section">
            <StatementImport
              accounts={accounts}
              refresh={refresh}
              notify={notify}
            />
            <section className="panel">
              <SectionTitle
                title="Existing data & missing transactions"
                detail="Use an export while building up your Gmail coverage"
              />
              {status?.local_export_available && (
                <>
                  <p className="help-text">
                    An August 2026 transaction export is available in this
                    workspace. It contains summarized alerts and will be clearly
                    marked as provisional.
                  </p>
                  <div className="settings-actions">
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        run(
                          () => api('/import/local', 'POST', {}),
                          'August export imported. Existing records were not duplicated',
                        )
                      }
                    >
                      <Upload size={16} />
                      Import August export
                    </Button>
                  </div>
                </>
              )}
              <label className="form-label" style={{ marginTop: 20 }}>
                Import another Kharcha JSON export
                <Input
                  type="file"
                  accept=".json,application/json"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file)
                      void run(
                        async () =>
                          api('/import', 'POST', JSON.parse(await file.text())),
                        'Transactions imported as provisional data',
                      );
                  }}
                />
              </label>
              <p className="help-text">
                Use “Add transaction” in Transactions for cash, missing alerts,
                or statement-only details. You can also upload a bank statement
                PDF above.
              </p>
            </section>
          </div>
          <div hidden={section !== 'categories'} className="settings-section">
            <section className="panel">
              <SectionTitle
                title="Categories & automatic rules"
                detail="Teach the app where your money belongs"
              />
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(async () => {
                    await api('/categories', 'POST', { name });
                    setName('');
                  }, 'Category created');
                }}
                className="settings-actions"
                style={{ marginTop: 0 }}
              >
                <Input
                  aria-label="New category name"
                  placeholder="Add your own category"
                  maxLength={60}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  style={{ flex: 1, minWidth: 150 }}
                />
                <Button
                  disabled={busy || !name.trim()}
                  type="submit"
                  variant="outline"
                >
                  <Plus size={16} />
                  Add
                </Button>
              </form>
              <p className="help-text" style={{ marginTop: 12 }}>
                {categories.length} categories. Change a payment’s category in
                Transactions and choose which future payments should remember
                it.
              </p>
              {choices ? (
                <CategoryPreferences
                  categories={categories}
                  initial={choices}
                  onSaved={async () => {
                    notify('Category choices saved');
                    await refresh();
                    await load();
                  }}
                />
              ) : (
                <p className="help-text">
                  Category choices will appear when settings have loaded.
                </p>
              )}
              <div className="rule-list">
                {rules.length ? (
                  rules.map((r) => (
                    <div className="small-row" key={r.id}>
                      <span>
                        <b>
                          {r.merchant} → {r.category}
                        </b>
                        <small>
                          {r.scope === 'exact'
                            ? `${money(r.amount_minor, r.currency)} · ${r.direction} · confirmed person`
                            : `${r.currency} · ${r.direction} · ${r.scope === 'person' ? 'confirmed person, any amount' : r.scope === 'payee' ? 'exact payee name, any amount' : 'merchant name'}`}
                        </small>
                      </span>
                      <Button
                        variant="ghost"
                        aria-label={'Delete rule for ' + r.merchant}
                        onClick={() =>
                          run(
                            () => api('/rules/' + r.id, 'DELETE', {}),
                            'Rule deleted; prior categories retained',
                          )
                        }
                      >
                        <Trash2 size={15} />
                      </Button>
                    </div>
                  ))
                ) : (
                  <p className="help-text" style={{ padding: '20px 0 0' }}>
                    No rules yet. Your manual corrections always take priority.
                  </p>
                )}
              </div>
            </section>
          </div>
          <div hidden={section !== 'accounts'} className="settings-section">
            <section className="panel">
              <SectionTitle
                title="Your accounts"
                detail="Label accounts you own; review transfers between them"
              />
              {accounts.length ? (
                accounts.map((a) => (
                  <div className="small-row" key={a.name}>
                    <span>
                      <b>{a.name}</b>
                      <small>
                        {a.owned
                          ? 'Identified as yours'
                          : 'Ownership not confirmed'}
                      </small>
                    </span>
                    <Switch
                      aria-label={'I own ' + a.name}
                      checked={Boolean(a.owned)}
                      onCheckedChange={(owned) =>
                        run(
                          () =>
                            api('/accounts', 'POST', { name: a.name, owned }),
                          'Account ownership saved',
                        )
                      }
                    />
                  </div>
                ))
              ) : (
                <Blank
                  title="Accounts appear after import"
                  description="Account hints come from your emails. You can correct them in transaction details."
                />
              )}
            </section>
          </div>
          <div hidden={section !== 'ai'} className="settings-section">
            <section className="panel">
              <SectionTitle
                title="AI spending review"
                detail="Optional analysis through your signed-in Codex account"
              />
              <p className="help-text">
                Off by default. Your dashboard works without AI. Before
                enabling, review exactly what spending data will be sent to
                OpenAI through Codex. Gmail sync and merchant search are
                separate features.
              </p>
              <label className="settings-actions">
                <Switch
                  checked={status?.ai_advisor_enabled ?? false}
                  disabled={busy || !status}
                  onCheckedChange={(enabled) => {
                    if (enabled) {
                      setLocalError('');
                      setModal('ai-consent');
                    } else {
                      void run(
                        () =>
                          api('/ai-advisor/preferences', 'PUT', {
                            enabled: false,
                          }),
                        'AI spending review turned off',
                      );
                    }
                  }}
                />
                Allow AI spending review
              </label>
              <Button
                variant="link"
                onClick={() => {
                  setLocalError('');
                  setModal('ai-notice');
                }}
              >
                What data is sent?
              </Button>
            </section>
            {month && (
              <details className="financial-context-section">
                <summary>Financial context & target</summary>
                <FinancialPlan
                  month={month}
                  currency={currency}
                  onTransaction={onTransaction}
                />
              </details>
            )}
          </div>
          <div hidden={section !== 'data'} className="settings-section">
            {status?.mobile_client === false ? (
              <>
                <section className="panel">
                  <SectionTitle
                    title="Local data & backups"
                    detail="Your history is yours to keep"
                  />
                  <p className="help-text">
                    Normalized transactions stay on this Mac. Cached email
                    bodies are encrypted; access credentials live in macOS
                    Keychain. Backups include transactions, category edits,
                    rules, and any cached email evidence. You can create one
                    before connecting Gmail. They require a password and exclude
                    Google credentials.
                  </p>
                  <div className="settings-actions">
                    <Button
                      variant="outline"
                      onClick={() => {
                        setModal('backup');
                        setLocalError('');
                      }}
                    >
                      <Download size={16} />
                      Encrypted backup
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => {
                        setModal('restore');
                        setLocalError('');
                      }}
                    >
                      <Upload size={16} />
                      Restore
                    </Button>
                    <Button
                      variant="link"
                      onClick={() =>
                        void run(downloadTransactions, 'Transactions exported')
                      }
                    >
                      Export CSV
                    </Button>
                  </div>
                  <p
                    className="help-text"
                    style={{ marginTop: 18, wordBreak: 'break-word' }}
                  >
                    Data folder: {status?.data_directory}
                  </p>
                  <div className="settings-actions">
                    <Button
                      variant="outline"
                      disabled={busy || status?.job?.state === 'running'}
                      onClick={() =>
                        run(
                          () => api('/reparse', 'POST', {}),
                          'Cached emails reprocessed; corrections preserved',
                        )
                      }
                    >
                      <RefreshCw size={15} />
                      Reprocess cached emails
                    </Button>
                  </div>
                </section>
                <section className="panel danger-panel">
                  <SectionTitle title="Delete local data" />
                  <p>
                    Remove the ledger, cached emails, saved rules, and
                    credentials from this app. Gmail messages remain in your
                    mailbox. Previously exported files and backups remain
                    wherever you saved them.
                  </p>
                  <div className="settings-actions">
                    <Button
                      variant="destructive"
                      onClick={() => {
                        setModal('erase');
                        setLocalError('');
                      }}
                    >
                      Delete local data
                    </Button>
                  </div>
                </section>
              </>
            ) : (
              <section className="panel">
                <SectionTitle title="Connected to your Mac" />
                <p className="help-text">
                  Edits save to the same ledger on your Mac. Gmail sign-in,
                  phone management, backups, restore, and deleting all data are
                  available on the Mac.
                </p>
                <Button
                  variant="outline"
                  onClick={() =>
                    void run(downloadTransactions, 'Transactions exported')
                  }
                >
                  Export CSV
                </Button>
              </section>
            )}
          </div>
        </div>
      </div>
      {localError && !modal && (
        <div className="notice error" role="alert" style={{ marginTop: 20 }}>
          {localError}
        </div>
      )}
      {active && ['ai-consent', 'ai-notice'].includes(modal) && (
        <AIConsent
          open
          busy={busy}
          error={localError}
          enabled={status?.ai_advisor_enabled ?? false}
          onClose={() => setModal('')}
          onEnable={() =>
            void run(async () => {
              if (!status) throw new Error('Wait for app settings to load');
              await api('/ai-advisor/preferences', 'PUT', {
                enabled: true,
                consent_version: status.ai_advisor_consent_version,
              });
              setModal('');
            }, 'AI spending review enabled')
          }
        />
      )}
      <Dialog
        open={active && modal === 'backup'}
        onOpenChange={(open) => !open && setModal('')}
      >
        <DialogContent className="modal-wide">
          <DialogHeader>
            <DialogTitle>Encrypt your backup</DialogTitle>
            <DialogDescription>
              Choose a password of at least 12 characters. You will need it to
              restore this file.
            </DialogDescription>
          </DialogHeader>
          <label className="form-label">
            Backup password
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
          </label>
          {localError && <div className="notice error">{localError}</div>}
          <Button disabled={busy || password.length < 12} onClick={backup}>
            Download encrypted backup
          </Button>
        </DialogContent>
      </Dialog>
      <AlertDialog
        open={active && ['erase', 'restore'].includes(modal)}
        onOpenChange={(open) => !open && setModal('')}
      >
        <AlertDialogContent className="modal-wide">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {modal === 'erase'
                ? 'Delete this local ledger?'
                : 'Replace this ledger from a backup?'}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {modal === 'erase'
                ? 'This removes the app’s records and credentials. It does not delete anything in Gmail.'
                : 'Restoring replaces current records, corrections, and rules. Save a backup of this ledger first if you want to retain it.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {modal === 'restore' && (
            <>
              <label className="form-label">
                Backup file
                <Input
                  type="file"
                  accept=".mcb"
                  onChange={(e) => setRestoreFile(e.target.files?.[0] || null)}
                />
              </label>
              <label className="form-label">
                Backup password
                <Input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
            </>
          )}
          <label className="form-label">
            Type {modal === 'erase' ? 'DELETE' : 'REPLACE'} to confirm
            <Input
              value={confirmation}
              onChange={(e) => setConfirmation(e.target.value)}
            />
          </label>
          {localError && <div className="notice error">{localError}</div>}
          <div className="settings-actions">
            <Button variant="outline" onClick={() => setModal('')}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={
                busy ||
                confirmation !== (modal === 'erase' ? 'DELETE' : 'REPLACE')
              }
              onClick={modal === 'erase' ? erase : restore}
            >
              {modal === 'erase' ? 'Delete local data' : 'Restore backup'}
            </Button>
          </div>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
function CircleInfo() {
  return <ShieldCheck size={15} />;
}
