'use client';
import { useState } from 'react';
import { Upload } from 'lucide-react';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { SectionTitle } from './common';
import { api, money, kinds, kindName } from '@/lib/api';
import type { Account } from '@/lib/types';

type Row = {
  date: string;
  counterparty: string;
  amount_minor: number;
  direction: string;
  kind: string;
  currency: string;
  already_imported: boolean;
  possible_matches: string[];
  enrichment_target?: string | null;
};
type Preview = { token: string; rows: Row[]; warnings: string[]; note: string };
const previousMonth = () => {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
};
export function StatementImport({
  accounts,
  refresh,
  notify,
}: {
  accounts: Account[];
  refresh: () => Promise<void>;
  notify: (message: string) => void;
}) {
  const [month, setMonth] = useState(previousMonth),
    [account, setAccount] = useState(''),
    [currency, setCurrency] = useState('INR');
  const [file, setFile] = useState<File | null>(null),
    [password, setPassword] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null),
    [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const invalidate = () => {
    setPreview(null);
    setSelected([]);
    setError('');
  };
  const upload = async () => {
    setBusy(true);
    invalidate();
    try {
      if (!file) throw new Error('Choose a PDF statement');
      if (file.size > 20_000_000)
        throw new Error('Choose a PDF smaller than 20 MB');
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = '';
      for (let i = 0; i < bytes.length; i += 32768)
        binary += String.fromCharCode(...bytes.slice(i, i + 32768));
      const result = await api<Preview>('/statements/preview', 'POST', {
        file: btoa(binary),
        filename: file.name,
        password,
        month,
        account,
        currency,
      });
      setPreview(result);
      setPassword('');
      setSelected(
        result.rows.flatMap((row, index) =>
          !row.already_imported && !row.possible_matches.length ? [index] : [],
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    if (!preview) return;
    setBusy(true);
    setError('');
    try {
      const result = await api<{
        imported: number;
        skipped: number;
        updated: number;
        resolved: number;
      }>('/statements/import', 'POST', {
        token: preview.token,
        rows: selected.map((index) => ({
          index,
          kind: preview.rows[index].kind,
        })),
      });
      setPreview(null);
      setSelected([]);
      notify(
        `${result.imported} statement transactions added; ${result.skipped} duplicates skipped; ${result.updated} existing transactions enriched; ${result.resolved} review items resolved. Check Review & coverage for classification.`,
      );
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel">
      <SectionTitle
        title="Bank statement PDF"
        detail="Find missing transactions whenever you want"
      />
      <p className="help-text">
        Upload last month’s statement or choose any month. PDFs are processed on
        this Mac. Supports text-based tables with separate debit and credit
        columns; scanned statements need manual entry.
      </p>
      <div className="settings-actions">
        <label className="form-label">
          Month
          <Input
            type="month"
            value={month}
            disabled={busy}
            onChange={(e) => {
              setMonth(e.target.value);
              invalidate();
            }}
          />
        </label>
        <label className="form-label">
          Account
          <Input
            list="statement-accounts"
            placeholder="Use the account label from your ledger"
            value={account}
            disabled={busy}
            onChange={(e) => {
              setAccount(e.target.value);
              invalidate();
            }}
          />
          <datalist id="statement-accounts">
            {accounts.map((a) => (
              <option key={a.name} value={a.name}>
                {a.name}
              </option>
            ))}
          </datalist>
        </label>
        <label className="form-label">
          Currency
          <Input
            value={currency}
            maxLength={3}
            disabled={busy}
            onChange={(e) => {
              setCurrency(e.target.value.toUpperCase());
              invalidate();
            }}
          />
        </label>
      </div>
      <label className="form-label">
        Statement PDF (up to 20 MB)
        <Input
          type="file"
          accept=".pdf,application/pdf"
          disabled={busy}
          onChange={(e) => {
            setFile(e.target.files?.[0] || null);
            invalidate();
          }}
        />
      </label>
      <label className="form-label">
        PDF password (if required)
        <Input
          type="password"
          autoComplete="off"
          value={password}
          disabled={busy}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      <p className="help-text">
        The original PDF and password are not saved. Extracted preview data is
        encrypted locally.
      </p>
      <div className="settings-actions">
        <Button
          disabled={busy || !file || !account.trim() || !month}
          onClick={upload}
        >
          <Upload size={16} />
          {busy ? 'Working…' : 'Preview statement'}
        </Button>
      </div>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {preview && (
        <div style={{ marginTop: 20 }}>
          <p>
            <b>{preview.rows.length} extracted rows</b> ·{' '}
            {preview.rows.filter((r) => r.already_imported).length} already
            imported ·{' '}
            {
              preview.rows.filter(
                (r) => !r.already_imported && r.possible_matches.length > 0,
              ).length
            }{' '}
            possible matches
          </p>
          <p className="help-text">
            {preview.note} Same-day amounts are only possible matches, including
            other accounts. Matching UPI references can fill missing names,
            dates and categories on existing entries while preserving your
            corrections. Uncertain details stay in review. Only rows without an
            existing match are selected. Existing and uncertain matches are
            skipped, and checked again when saving.
          </p>
          {preview.warnings.length > 0 && (
            <details>
              <summary>
                {preview.warnings.length} extraction warnings — coverage may be
                incomplete
              </summary>
              {preview.warnings.map((w, i) => (
                <p key={i} className="help-text">
                  {w}
                </p>
              ))}
            </details>
          )}
          <div style={{ maxHeight: 500, overflow: 'auto' }}>
            {preview.rows.map((row, index) => (
              <div
                key={index}
                style={{
                  borderBottom: '1px solid var(--border)',
                  padding: '14px 0',
                }}
              >
                <label
                  style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}
                >
                  <input
                    type="checkbox"
                    disabled={
                      busy ||
                      row.already_imported ||
                      row.possible_matches.length > 0
                    }
                    checked={selected.includes(index)}
                    onChange={(e) =>
                      setSelected((v) =>
                        e.target.checked
                          ? [...v, index]
                          : v.filter((i) => i !== index),
                      )
                    }
                  />
                  <span>
                    <b>
                      {money(row.amount_minor, row.currency)}{' '}
                      {row.direction === 'debit' ? 'out' : 'in'}
                    </b>{' '}
                    · {row.date}
                    <br />
                    {row.counterparty}
                    <br />
                    <small>
                      {row.already_imported
                        ? 'Already imported'
                        : row.possible_matches.length
                          ? 'Possible existing transaction — skipped to prevent duplicates'
                          : 'No same-day amount match found'}
                    </small>
                  </span>
                </label>
                {row.possible_matches.map((id) => (
                  <a
                    key={id}
                    href={`#/settings?transaction=${encodeURIComponent(id)}`}
                    className="inline-link"
                    style={{ display: 'inline-block', marginRight: 12 }}
                  >
                    Compare transaction
                  </a>
                ))}
                <label className="form-label">
                  Transaction type
                  <select
                    disabled={
                      busy ||
                      row.already_imported ||
                      row.possible_matches.length > 0
                    }
                    value={row.kind}
                    onChange={(e) =>
                      setPreview({
                        ...preview,
                        rows: preview.rows.map((r, i) =>
                          i === index ? { ...r, kind: e.target.value } : r,
                        ),
                      })
                    }
                  >
                    {kinds
                      .filter((k) =>
                        row.direction === 'credit'
                          ? [
                              'income',
                              'refund',
                              'reimbursement',
                              'own_transfer',
                              'financing_adjustment',
                            ].includes(k)
                          : !['income', 'refund', 'reimbursement'].includes(k),
                      )
                      .map((k) => (
                        <option key={k} value={k}>
                          {kindName(k)}
                        </option>
                      ))}
                  </select>
                </label>
              </div>
            ))}
          </div>
          <div className="settings-actions">
            <Button
              disabled={
                busy ||
                (!selected.length &&
                  !preview.rows.some((row) => row.enrichment_target))
              }
              onClick={save}
            >
              Add {selected.length} missing & update matched transactions
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
