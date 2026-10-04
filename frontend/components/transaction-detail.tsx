'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  Save,
  ShieldCheck,
  Link2,
  Check,
  LoaderCircle,
  Undo2,
} from 'lucide-react';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { api, money, kindName, kinds } from '@/lib/api';
import { useSourceEmail } from '@/hooks/use-source-email';
import { observeVisibleTransactions } from '@/lib/transaction-visibility';
import { Picker, Blank } from './common';
import { MerchantLookup } from './merchant-lookup';
import { createAutosave, type SaveState } from '@/lib/autosave';
import { merchantMatch } from '@/lib/payee';
import { SplitGraphic } from './spending-graphics';

import type {
  Transaction,
  TransactionForm,
  TransactionSelection,
  Allocation,
  Evidence,
  ReviewIssue,
} from '@/lib/types';
type EditResult = {
  transaction: Transaction;
  undo_id: string;
  rule: { id: string } | null;
};
type EditDraft = TransactionForm & {
  amount_minor: number;
  allocations: Allocation[];
  remember_scope: string;
};
function transactionDraft(t: Transaction): EditDraft {
  return {
    date: t.date,
    direction: t.direction,
    kind: t.kind,
    currency: t.currency,
    counterparty: t.counterparty,
    account: t.account,
    category: t.category,
    excluded: !!t.excluded,
    avoidable: !!t.avoidable,
    linked_to: t.linked_to || null,
    cash_source_id: t.cash_source_id || null,
    notes: t.notes || '',
    amount_minor: t.amount_minor,
    allocations: t.allocations?.length
      ? [
          {
            type: 'personal',
            amount_minor: t.allocations
              .filter((a) => a.type === 'personal')
              .reduce((n, a) => n + a.amount_minor, 0),
            category: t.category,
          },
          {
            type: 'reimbursable',
            amount_minor: t.allocations
              .filter((a) => a.type === 'reimbursable')
              .reduce((n, a) => n + a.amount_minor, 0),
          },
          {
            type: 'lending',
            amount_minor: t.allocations
              .filter((a) => a.type === 'lending')
              .reduce((n, a) => n + a.amount_minor, 0),
          },
        ]
      : [],
    remember_scope: ['purchase', 'person_payment', 'emi', 'fee'].includes(
      t.kind,
    )
      ? merchantMatch(t)
        ? 'payee'
        : t.identity_confirmed && t.counterparty_key
          ? 'person'
          : 'none'
      : 'none',
  };
}
export function TransactionDetail({
  selected,
  returnLabel,
  sourceId,
  onSource: inspect,
  rows,
  categories,
  onClose,
  onSaved,
  onError: _onError,
  onVisible,
}: {
  selected: TransactionSelection;
  returnLabel: string;
  sourceId: string;
  onSource: (id: string) => void;
  rows: Transaction[];
  categories: string[];
  onClose: () => void;
  onSaved: (s: string) => void;
  onError: (s: string) => void;
  onVisible: (ids: string[]) => Promise<void>;
}) {
  const [tx, setTx] = useState<Transaction | null>(null),
    [form, setForm] = useState<TransactionForm>({
      date: '',
      direction: 'debit',
      kind: 'purchase',
      currency: 'INR',
      counterparty: '',
      account: '',
      category: 'Uncategorized',
      excluded: false,
      avoidable: false,
      linked_to: null,
      cash_source_id: null,
      notes: '',
    }),
    [amount, setAmount] = useState(''),
    [personal, setPersonal] = useState(''),
    [reimbursable, setReimbursable] = useState(''),
    [lending, setLending] = useState(''),
    [splitting, setSplitting] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [mergeTarget, setMergeTarget] = useState('none'),
    [aliasName, setAliasName] = useState('');
  const [saveState, setSaveState] = useState<SaveState>('saved');
  const [rememberScope, setRememberScope] = useState('none');
  const [undoId, setUndoId] = useState('');
  const callbacks = useRef({ onSaved });
  useEffect(() => {
    callbacks.current = { onSaved };
  }, [onSaved]);
  const rawTx = useRef<Transaction | null>(null);
  const submitted = useRef('');
  const latestDraft = useRef('');
  const saver = useRef<ReturnType<
    typeof createAutosave<EditDraft, EditResult>
  > | null>(null);
  const initialized = useRef(false);
  const detail = useRef<HTMLDivElement>(null);
  const isNew =
    tx?.is_new && rows.find((row) => row.id === tx.id)?.is_new !== false;
  useEffect(() => {
    if (detail.current && isNew && !sourceId) {
      return observeVisibleTransactions(
        detail.current.querySelectorAll<HTMLElement>(
          '[data-new-transaction-id]',
        ),
        onVisible,
      );
    }
  }, [tx, isNew, sourceId, onVisible]);
  const initialize = useCallback((input: Partial<Transaction>) => {
    const t: Transaction = {
      id: '',
      is_new: false,
      sync_job_id: null,
      date: '',
      amount_minor: 0,
      currency: 'INR',
      direction: 'debit',
      kind: 'purchase',
      counterparty: '',
      account: '',
      category: 'Uncategorized',
      spend_minor: 0,
      issues: [],
      ...input,
    };
    rawTx.current = t;
    setTx(t);
    setRememberScope(transactionDraft(t).remember_scope);
    if (t.id && !saver.current) {
      saver.current = createAutosave(transactionDraft(t), {
        persist: async (value, previous, operationId) => {
          const { remember_scope, ...body } = value;
          const changes = Object.fromEntries(
            Object.entries(body).filter(
              ([key, v]) =>
                JSON.stringify(v) !==
                JSON.stringify(previous[key as keyof EditDraft]),
            ),
          );
          if (
            remember_scope !== previous.remember_scope &&
            remember_scope !== 'none'
          )
            changes.category = value.category;
          const expected = Object.fromEntries(
            Object.keys(changes).map((key) => [
              key,
              rawTx.current?.[key as keyof Transaction] ?? null,
            ]),
          );
          const result = await api<EditResult>(
            '/transactions/' + t.id + '/edit',
            'PATCH',
            {
              changes,
              expected,
              remember_scope: 'category' in changes ? remember_scope : 'none',
              edit_id: operationId,
            },
          );
          rawTx.current = result.transaction;
          return {
            value: { ...transactionDraft(result.transaction), remember_scope },
            result,
          };
        },
        onState: (state, error) => {
          setSaveState(state);
          if (error) setError(error);
          else if (state === 'saving' || state === 'saved') setError('');
        },
        onSaved: (result, value) => {
          submitted.current = JSON.stringify(value);
          setTx(result.transaction);
          setUndoId(result.undo_id);
          if (latestDraft.current === submitted.current) {
            setForm((f) => ({ ...f, category: result.transaction.category }));
            try {
              sessionStorage.removeItem('kharcha.draft:' + t.id);
            } catch {
              /* Storage is optional. */
            }
          }
          callbacks.current.onSaved('');
        },
      });
    } else if (t.id && saver.current)
      saver.current.replace(transactionDraft(t));
    initialized.current = true;
    setForm({
      date: t.date,
      direction: t.direction,
      kind: t.kind,
      currency: t.currency,
      counterparty: t.counterparty,
      account: t.account,
      category: t.category,
      excluded: !!t.excluded,
      avoidable: !!t.avoidable,
      linked_to: t.linked_to || null,
      cash_source_id: t.cash_source_id || null,
      notes: t.notes || '',
    });
    setAmount(String(t.amount_minor / 100));
    setAliasName(t.counterparty);
    setSplitting(!!t.allocations?.length);
    setPersonal(
      String(
        (t.allocations || [])
          .filter((a: Allocation) => a.type === 'personal')
          .reduce((n: number, a: Allocation) => n + a.amount_minor, 0) / 100,
      ),
    );
    setReimbursable(
      String(
        (t.allocations || [])
          .filter((a: Allocation) => a.type === 'reimbursable')
          .reduce((n: number, a: Allocation) => n + a.amount_minor, 0) / 100,
      ),
    );
    setLending(
      String(
        (t.allocations || [])
          .filter((a: Allocation) => a.type === 'lending')
          .reduce((n: number, a: Allocation) => n + a.amount_minor, 0) / 100,
      ),
    );
  }, []);
  const load = useCallback(
    () =>
      selected.new
        ? initialize({
            id: '',
            date:
              selected.date ||
              new Date().toLocaleDateString('en-CA', {
                timeZone: 'Asia/Kolkata',
              }),
            direction: 'debit',
            kind: 'purchase',
            currency: 'INR',
            counterparty: '',
            account: 'Cash / manual',
            category: 'Uncategorized',
            amount_minor: 0,
            issues: [],
            sources: [],
          })
        : api<Transaction>('/transactions/' + selected.id)
            .then((t) => {
              initialize(t);
              try {
                const saved = sessionStorage.getItem('kharcha.draft:' + t.id);
                if (saved) {
                  const d = JSON.parse(saved);
                  if (d.form && typeof d.amount === 'string') {
                    setForm(d.form);
                    setAmount(d.amount);
                    setPersonal(d.personal);
                    setReimbursable(d.reimbursable);
                    setLending(d.lending);
                    setSplitting(d.splitting);
                    setRememberScope(d.rememberScope || 'none');
                  }
                }
              } catch {
                /* The original ledger remains authoritative. */
              }
            })
            .catch((e) => setError(e.message)),
    [selected.new, selected.id, selected.date, initialize],
  );
  useEffect(() => {
    const initial = setTimeout(() => {
      void load();
    }, 0);
    return () => clearTimeout(initial);
  }, [load]);
  const change = <K extends keyof TransactionForm>(
    key: K,
    value: TransactionForm[K],
  ) => setForm((f: TransactionForm) => ({ ...f, [key]: value }));
  const minor = (v: string) => {
    if (!/^\d+(\.\d{1,2})?$/.test(v))
      throw new Error('Amounts must have at most two decimal places');
    return Math.round(Number(v) * 100);
  };
  let draft: EditDraft | null = null;
  try {
    const allocations = splitting
      ? [
          {
            type: 'personal',
            amount_minor: minor(personal || '0'),
            category: form.category,
          },
          { type: 'reimbursable', amount_minor: minor(reimbursable || '0') },
          { type: 'lending', amount_minor: minor(lending || '0') },
        ]
      : [];
    const amount_minor = minor(amount);
    if (
      form.counterparty.trim() &&
      /^[A-Z]{3}$/.test(form.currency) &&
      /^\d{4}-\d{2}-\d{2}$/.test(form.date) &&
      (!splitting ||
        allocations.reduce((n, a) => n + a.amount_minor, 0) === amount_minor)
    ) {
      draft = {
        ...form,
        amount_minor,
        allocations,
        remember_scope: rememberScope,
      };
    }
  } catch {
    /* Incomplete input stays in the draft until it validates. */
  }
  const draftJSON = draft ? JSON.stringify(draft) : '';

  useEffect(() => {
    if (selected.new || !initialized.current || !saver.current) return;
    latestDraft.current = draftJSON;
    const controller = saver.current;
    if (draftJSON) controller.update(JSON.parse(draftJSON));
    else controller.pause();
    if (controller.dirty) {
      try {
        sessionStorage.setItem(
          'kharcha.draft:' + selected.id,
          JSON.stringify({
            form,
            amount,
            personal,
            reimbursable,
            lending,
            splitting,
            rememberScope,
          }),
        );
      } catch {
        /* Storage is optional. */
      }
    }
  }, [
    draftJSON,
    selected.new,
    selected.id,
    form,
    amount,
    personal,
    reimbursable,
    lending,
    splitting,
    rememberScope,
  ]);
  useEffect(() => {
    const beforeLeave = (event: BeforeUnloadEvent) => {
      if (saver.current?.dirty) {
        event.preventDefault();
        Reflect.set(event, 'returnValue', '');
      }
    };
    window.addEventListener('beforeunload', beforeLeave);
    return () => {
      window.removeEventListener('beforeunload', beforeLeave);
      saver.current?.dispose();
    };
  }, []);
  const closeDetail = async () => {
    if (selected.new || !saver.current || (await saver.current.flush()))
      onClose();
    else
      setError(
        'Your edits are still here. Complete the fields or retry saving before closing.',
      );
  };
  const reloadSaved = async () => {
    setBusy(true);
    try {
      await saver.current?.discard();
      const saved = await api<Transaction>('/transactions/' + selected.id);
      initialize(saved);
      setUndoId('');
      setError('');
      sessionStorage.removeItem('kharcha.draft:' + saved.id);
    } catch (e) {
      saver.current?.pause();
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const undo = async () => {
    if (!saver.current || !(await saver.current.flush())) return;
    setBusy(true);
    try {
      const t = await api<Transaction>(
        '/transactions/' + tx!.id + '/undo-edit',
        'POST',
        { edit_id: undoId },
      );
      saver.current.replace(transactionDraft(t));
      initialize(t);
      setUndoId('');
      setError('');
      sessionStorage.removeItem('kharcha.draft:' + t.id);
      onSaved('');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const run = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true);
    setError('');
    try {
      const result = await fn();
      onSaved(message);
      return result;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const save = () =>
    run(async () => {
      const amount_minor = minor(amount);
      const allocations = splitting
        ? [
            {
              type: 'personal',
              amount_minor: minor(personal || '0'),
              category: form.category,
            },
            { type: 'reimbursable', amount_minor: minor(reimbursable || '0') },
            { type: 'lending', amount_minor: minor(lending || '0') },
          ]
        : [];
      const body = { ...form, amount_minor, allocations };
      if (selected.new) {
        const result = await api(
          selected.source_id
            ? '/sources/' +
                encodeURIComponent(selected.source_id) +
                '/transaction'
            : '/transactions',
          'POST',
          body,
        );
        onClose();
        return result;
      }
      const result = await api<Transaction>(
        '/transactions/' + tx!.id,
        'PATCH',
        Object.fromEntries(
          Object.entries(body).filter(
            ([key, value]) =>
              JSON.stringify(value) !==
              JSON.stringify(tx![key as keyof Transaction]),
          ),
        ),
      );
      initialize(result);
      return result;
    }, 'Transaction saved');
  const confirmIdentity = () =>
    run(async () => {
      const identity = await api<{ identity: string }>('/aliases', 'POST', {
        alias: tx!.counterparty,
        name: aliasName,
        identity: tx!.counterparty_key || undefined,
      });
      const updated = await api<Transaction>(
        '/transactions/' + tx!.id,
        'PATCH',
        {
          counterparty_key: identity.identity,
          identity_confirmed: true,
          counterparty: aliasName,
        },
      );
      initialize(updated);
    }, 'Person identity confirmed. Future matching emails can use this alias');
  const { source, sourceError } = useSourceEmail(sourceId, setError);
  const eligibleLinks = rows.filter(
    (t) =>
      t.id !== tx?.id &&
      t.direction === 'debit' &&
      t.currency === form.currency,
  );
  const eligibleMerges = rows.filter(
    (t) =>
      t.id !== tx?.id &&
      t.direction === tx?.direction &&
      t.amount_minor === tx?.amount_minor &&
      t.currency === tx?.currency,
  );
  return (
    <>
      <Sheet open onOpenChange={(open) => !open && void closeDetail()}>
        <SheetContent className="drawer" ref={detail}>
          <div className="detail-return">
            <Button variant="ghost" onClick={() => void closeDetail()}>
              <ArrowLeft size={16} />
              Back to {returnLabel}
            </Button>
            {!selected.new && (
              <div className="save-indicator" aria-live="polite">
                {saveState === 'saving' ? (
                  <LoaderCircle size={14} className="spin" />
                ) : (
                  <Check size={14} />
                )}
                {saveState === 'saved'
                  ? 'Saved'
                  : saveState === 'saving'
                    ? 'Saving…'
                    : saveState === 'error'
                      ? 'Not saved'
                      : saveState === 'invalid'
                        ? 'Complete your edits'
                        : 'Saving shortly…'}
                {saveState === 'error' && (
                  <button onClick={() => void saver.current?.retry()}>
                    Retry
                  </button>
                )}
                {saveState === 'invalid' && (
                  <button disabled={busy} onClick={() => void reloadSaved()}>
                    Discard edits
                  </button>
                )}
                {undoId && saveState === 'saved' && (
                  <button disabled={busy} onClick={() => void undo()}>
                    <Undo2 size={13} /> Undo
                  </button>
                )}
              </div>
            )}
          </div>
          <SheetHeader>
            <SheetTitle>
              {selected.new
                ? 'Add a transaction'
                : tx?.merchant_display ||
                  tx?.counterparty ||
                  'Transaction details'}
            </SheetTitle>
            <SheetDescription>
              {selected.new
                ? 'Fill a gap in your email history.'
                : 'Edits save automatically. Your corrections survive future syncs.'}
            </SheetDescription>
          </SheetHeader>
          {error && (
            <div
              className="notice error"
              role="alert"
              style={{ marginTop: 15 }}
            >
              {error}
              {!selected.new &&
                (saveState === 'invalid' || saveState === 'error') && (
                  <Button
                    variant="outline"
                    disabled={busy}
                    onClick={() => void reloadSaved()}
                  >
                    Reload saved payment
                  </Button>
                )}
            </div>
          )}
          {tx && (
            <>
              {!selected.new && (
                <>
                  <div
                    className="detail-amount"
                    data-new-transaction-id={tx.is_new ? tx.id : undefined}
                  >
                    {money(tx.amount_minor, tx.currency)}
                  </div>
                  <p className="help-text">
                    {money(tx.spend_minor, tx.currency)} counted as personal
                    spending
                  </p>
                  {tx.direction === 'debit' &&
                    ['purchase', 'person_payment'].includes(tx.kind) &&
                    !tx.identity_confirmed &&
                    !tx.counterparty_key?.startsWith('person:') && (
                      <details className="merchant-identification">
                        <summary>Don’t recognize this payment?</summary>
                        <MerchantLookup
                          key={tx.id + tx.counterparty + tx.currency + tx.kind}
                          tx={tx}
                          disabled={
                            busy ||
                            Object.entries(form).some(
                              ([key, value]) =>
                                JSON.stringify(value) !==
                                JSON.stringify(
                                  tx[key as keyof Transaction] ??
                                    (value === null
                                      ? null
                                      : value === false
                                        ? false
                                        : ''),
                                ),
                            ) ||
                            Math.round(Number(amount) * 100) !==
                              tx.amount_minor ||
                            splitting !== !!tx.allocations?.length ||
                            (splitting &&
                              [
                                ['personal', personal],
                                ['reimbursable', reimbursable],
                                ['lending', lending],
                              ].some(
                                ([type, value]) =>
                                  Math.round(Number(value) * 100) !==
                                  (tx.allocations || [])
                                    .filter((a) => a.type === type)
                                    .reduce((n, a) => n + a.amount_minor, 0),
                              ))
                          }
                          onUpdated={(updated, message) => {
                            initialize(updated);
                            onSaved(message);
                          }}
                        />
                      </details>
                    )}
                </>
              )}
              <form
                style={{ marginTop: 24 }}
                onSubmit={(e) => {
                  e.preventDefault();
                  if (selected.new) void save();
                  else void saver.current?.flush();
                }}
              >
                <div className="category-editor">
                  {' '}
                  <label className="form-label">
                    Category
                    <Picker
                      label="Category"
                      value={form.category || 'Uncategorized'}
                      onChange={(v) => {
                        change('category', v);
                      }}
                      options={categories}
                    />
                    {tx?.category_reason && form.category === tx.category && (
                      <span className="help-text">
                        {tx.category_source === 'automatic'
                          ? 'Auto-categorized. '
                          : ''}
                        {tx.category_reason}
                      </span>
                    )}
                  </label>
                  {!selected.new &&
                    ['purchase', 'person_payment', 'emi', 'fee'].includes(
                      form.kind,
                    ) && (
                      <div className="category-memory">
                        <Picker
                          label="Remember category for"
                          value={rememberScope}
                          onChange={setRememberScope}
                          options={[
                            { value: 'none', label: 'This payment only' },
                            {
                              value: 'payee',
                              label: 'Future payments with this payee name',
                            },
                            ...(tx.identity_confirmed && tx.counterparty_key
                              ? [
                                  {
                                    value: 'person',
                                    label:
                                      'Future payments to this confirmed person',
                                  },
                                  {
                                    value: 'exact',
                                    label: 'This person + this exact amount',
                                  },
                                ]
                              : []),
                          ]}
                        />
                        <small>
                          {rememberScope === 'none'
                            ? 'This edit changes only this payment. Existing rules stay active.'
                            : `Remembered for ${form.direction === 'debit' ? 'outgoing' : 'incoming'} spending in ${form.currency}. Earlier payments stay as they are.`}
                        </small>
                      </div>
                    )}
                </div>
                <details
                  className="payment-metadata"
                  open={selected.new || undefined}
                >
                  <summary>
                    Payment details{' '}
                    <small>Date, amount, account & treatment</small>
                  </summary>
                  <div className="form-grid">
                    <label className="form-label form-full">
                      Merchant or person
                      <Input
                        required
                        value={form.counterparty || ''}
                        onChange={(e) => change('counterparty', e.target.value)}
                      />
                    </label>
                    <label className="form-label">
                      Amount
                      <Input
                        required
                        inputMode="decimal"
                        value={amount}
                        onChange={(e) => setAmount(e.target.value)}
                      />
                    </label>
                    <label className="form-label">
                      Currency
                      <Input
                        required
                        pattern="[A-Z]{3}"
                        maxLength={3}
                        value={form.currency || 'INR'}
                        onChange={(e) =>
                          change('currency', e.target.value.toUpperCase())
                        }
                      />
                    </label>
                    <label className="form-label">
                      Transaction date
                      <Input
                        required
                        type="date"
                        value={form.date || ''}
                        onChange={(e) => change('date', e.target.value)}
                      />
                    </label>
                    <label className="form-label">
                      Direction
                      <Picker
                        label="Direction"
                        value={form.direction || 'debit'}
                        onChange={(v) => change('direction', v)}
                        options={[
                          { value: 'debit', label: 'Outgoing' },
                          { value: 'credit', label: 'Incoming' },
                        ]}
                      />
                    </label>
                    <label className="form-label">
                      Treatment
                      <Picker
                        label="Transaction treatment"
                        value={form.kind || 'purchase'}
                        onChange={(v) =>
                          setForm((f: TransactionForm) => ({
                            ...f,
                            kind: v,
                            direction: [
                              'refund',
                              'income',
                              'reimbursement',
                            ].includes(v)
                              ? 'credit'
                              : f.direction,
                          }))
                        }
                        options={kinds.map((k) => ({
                          value: k,
                          label: kindName(k),
                        }))}
                      />
                    </label>
                    <label className="form-label form-full">
                      Account
                      <Input
                        value={form.account || ''}
                        onChange={(e) => change('account', e.target.value)}
                      />
                    </label>
                  </div>
                </details>
                <details className="payment-adjustments">
                  <summary>
                    Adjust spending <small>Splits, refunds & exclusions</small>
                  </summary>
                  {['refund', 'reimbursement'].includes(form.kind) && (
                    <label className="form-label">
                      Link to the original payment
                      <Picker
                        label="Original payment"
                        value={form.linked_to || 'none'}
                        onChange={(v) =>
                          change('linked_to', v === 'none' ? null : v)
                        }
                        options={[
                          { value: 'none', label: 'Not linked' },
                          ...eligibleLinks.map((t) => ({
                            value: t.id,
                            label: `${t.date} · ${t.counterparty} · ${money(t.amount_minor, t.currency)}`,
                          })),
                        ]}
                      />
                      <span className="help-text">
                        For reimbursements, split out the reimbursable share on
                        the original payment first.
                      </span>
                    </label>
                  )}
                  {form.direction === 'debit' &&
                    ['purchase', 'person_payment'].includes(form.kind) && (
                      <label className="form-label">
                        Cash withdrawal funding this purchase (optional)
                        <Picker
                          label="Cash source"
                          value={form.cash_source_id || 'none'}
                          onChange={(v) =>
                            change('cash_source_id', v === 'none' ? null : v)
                          }
                          options={[
                            {
                              value: 'none',
                              label: 'Not linked to a cash withdrawal',
                            },
                            ...rows
                              .filter(
                                (t) =>
                                  t.kind === 'cash_withdrawal' &&
                                  t.currency === form.currency &&
                                  t.id !== tx.id,
                              )
                              .map((t) => ({
                                value: t.id,
                                label: `${t.date} · ${money(t.amount_minor, t.currency)} · ${t.account}`,
                              })),
                          ]}
                        />
                        <span className="help-text">
                          Linked purchases cannot exceed the withdrawal’s
                          unallocated cash.
                        </span>
                      </label>
                    )}
                  <div className="toggle-row">
                    <span>
                      Exclude from spending
                      <small>
                        Keep this record and its evidence in your history.
                      </small>
                    </span>
                    <Switch
                      checked={!!form.excluded}
                      onCheckedChange={(v) => change('excluded', v)}
                      aria-label="Exclude from spending"
                    />
                  </div>
                  <div className="toggle-row">
                    <span>
                      Mark as avoidable
                      <small>
                        A purchase you would choose differently next time.
                      </small>
                    </span>
                    <Switch
                      checked={!!form.avoidable}
                      onCheckedChange={(v) => change('avoidable', v)}
                      aria-label="Mark as avoidable"
                    />
                  </div>
                  {form.direction === 'debit' && (
                    <div className="drawer-section">
                      <div className="toggle-row">
                        <span>
                          Split a shared payment
                          <small>
                            Only your personal share counts as spending.
                          </small>
                        </span>
                        <Switch
                          checked={splitting}
                          onCheckedChange={(v) => {
                            setSplitting(v);
                            if (v && !Number(personal)) setPersonal(amount);
                          }}
                          aria-label="Split payment"
                        />
                      </div>
                      {splitting && (
                        <>
                          <SplitGraphic
                            amounts={[
                              Number(personal || 0),
                              Number(reimbursable || 0),
                              Number(lending || 0),
                            ]}
                            total={Number(amount)}
                            currency={form.currency}
                          />
                          <label className="form-label">
                            Your personal share
                            <Input
                              inputMode="decimal"
                              value={personal}
                              onChange={(e) => setPersonal(e.target.value)}
                            />
                          </label>
                          <div className="split-line">
                            <label className="form-label">
                              Reimbursable share
                              <Input
                                inputMode="decimal"
                                value={reimbursable}
                                onChange={(e) =>
                                  setReimbursable(e.target.value)
                                }
                              />
                            </label>
                            <label className="form-label">
                              Money lent
                              <Input
                                inputMode="decimal"
                                value={lending}
                                onChange={(e) => setLending(e.target.value)}
                              />
                            </label>
                          </div>
                          <p className="help-text">
                            All shares must total{' '}
                            {money(
                              Math.round(Number(amount) * 100),
                              form.currency,
                            )}
                            . Repayments settle the non-personal share without
                            subtracting it twice.
                          </p>
                        </>
                      )}
                    </div>
                  )}
                  {tx?.statement_details && (
                    <section className="drawer-section">
                      <h3>Matched bank statement details</h3>
                      <p style={{ overflowWrap: 'anywhere' }}>
                        {tx.statement_details.description}
                      </p>
                      <p className="help-text">
                        {tx.statement_details.date} ·{' '}
                        {tx.statement_details.filename}
                        <br />
                        UPI reference: {tx.statement_details.reference}
                      </p>
                    </section>
                  )}
                </details>
                <label className="form-label" style={{ marginTop: 20 }}>
                  Your notes
                  <Input
                    value={form.notes || ''}
                    onChange={(e) => change('notes', e.target.value)}
                  />
                </label>
                {selected.new && (
                  <Button type="submit" disabled={busy}>
                    <Save size={16} /> Add transaction
                  </Button>
                )}
              </form>
              {!selected.new && (
                <>
                  {!tx.identity_confirmed && (
                    <details className="drawer-section identity-section">
                      <summary>Confirm a person’s identity</summary>
                      <p className="help-text">
                        Confirm a person or UPI identity once to match future
                        payments even when the amount changes.
                      </p>
                      <Input
                        aria-label="Confirmed person name"
                        value={aliasName}
                        onChange={(e) => setAliasName(e.target.value)}
                      />
                      <Button
                        variant="outline"
                        disabled={
                          busy || saveState !== 'saved' || !aliasName.trim()
                        }
                        onClick={confirmIdentity}
                      >
                        <ShieldCheck size={16} /> Confirm this person
                      </Button>
                    </details>
                  )}
                  <section className="drawer-section">
                    <h3>Supporting evidence</h3>
                    {tx.sources?.map((s: Evidence) => (
                      <div key={s.observation_id} className="source-card">
                        <b>{s.subject || 'Transaction source'}</b>
                        <small>
                          {s.sender} · {s.received_at?.slice(0, 10)}
                          {s.missing ? ' · original unavailable' : ''}
                        </small>
                        <div className="settings-actions">
                          <Button variant="ghost" onClick={() => inspect(s.id)}>
                            Read source
                          </Button>
                          <a
                            className="inline-link"
                            href={
                              'https://mail.google.com/mail/#all/' +
                              encodeURIComponent(s.id.split(':').pop() || '')
                            }
                            target="_blank"
                            rel="noreferrer"
                          >
                            Gmail ↗
                          </a>
                          {(tx.sources?.length || 0) > 1 && (
                            <Button
                              variant="ghost"
                              disabled={busy}
                              onClick={() =>
                                run(async () => {
                                  await api(
                                    '/observations/' +
                                      s.observation_id +
                                      '/separate',
                                    'POST',
                                    {},
                                  );
                                  await load();
                                }, 'Source separated into its own transaction')
                              }
                            >
                              Separate transaction
                            </Button>
                          )}
                        </div>
                      </div>
                    ))}
                    {!tx.sources?.length && (
                      <p className="help-text">
                        Manually added transaction; no email source.
                      </p>
                    )}
                  </section>
                  <section className="drawer-section">
                    <h3>Duplicate decisions</h3>
                    <p className="help-text">
                      Merge only after checking the evidence. Equal amounts
                      alone are not proof of a duplicate.
                    </p>
                    <div style={{ marginTop: 13 }}>
                      <Picker
                        label="Merge with another transaction"
                        value={mergeTarget}
                        onChange={setMergeTarget}
                        options={[
                          {
                            value: 'none',
                            label: 'Choose a matching transaction',
                          },
                          ...eligibleMerges.map((t) => ({
                            value: t.id,
                            label: `${t.date} · ${t.counterparty} · ${t.account}`,
                          })),
                        ]}
                      />
                    </div>
                    <div className="drawer-actions">
                      <Button
                        variant="outline"
                        disabled={busy || mergeTarget === 'none'}
                        onClick={() =>
                          run(async () => {
                            await api<Transaction>(
                              '/transactions/' + tx!.id + '/merge',
                              'POST',
                              { target_id: mergeTarget },
                            );
                            onClose();
                          }, 'Duplicate merged; original records retained')
                        }
                      >
                        <Link2 size={16} />
                        Merge into selected
                      </Button>
                    </div>
                    {tx.merged_children?.map((id: string) => (
                      <Button
                        key={id}
                        variant="outline"
                        onClick={() =>
                          run(async () => {
                            await api<Transaction>(
                              '/transactions/' + id + '/unmerge',
                              'POST',
                              {},
                            );
                            await load();
                          }, 'Transactions unmerged')
                        }
                      >
                        Undo a previous merge
                      </Button>
                    ))}
                  </section>
                  {tx.issues?.length > 0 && (
                    <section className="drawer-section">
                      <h3>Open questions</h3>
                      {tx.issues.map((i: ReviewIssue) => (
                        <p
                          className="help-text"
                          style={{ marginBottom: 8 }}
                          key={i.id}
                        >
                          • {i.message}
                        </p>
                      ))}
                    </section>
                  )}
                  {!!tx.audit?.length && (
                    <section className="drawer-section">
                      <h3>Change history</h3>
                      {tx.audit?.map(
                        (
                          a: { action: string; created_at: string },
                          i: number,
                        ) => (
                          <div className="small-row" key={i}>
                            <span>{a.action}</span>
                            <small>
                              {new Date(a.created_at).toLocaleDateString()}
                            </small>
                          </div>
                        ),
                      )}
                    </section>
                  )}
                </>
              )}
            </>
          )}
        </SheetContent>
      </Sheet>
      <Dialog open={!!sourceId} onOpenChange={(v) => !v && onClose()}>
        <DialogContent className="modal-wide">
          <Button variant="ghost" onClick={onClose} className="source-return">
            <ArrowLeft size={16} />
            Back to transaction
          </Button>
          <DialogHeader>
            <DialogTitle>{source?.subject || 'Source email'}</DialogTitle>
            <DialogDescription>{source?.sender}</DialogDescription>
          </DialogHeader>
          {sourceError ? (
            <p role="alert">{sourceError}</p>
          ) : !source ? (
            <output>Loading source email…</output>
          ) : source.body ? (
            <pre className="source-text">{source.body}</pre>
          ) : (
            <Blank
              title="Original body not cached"
              description="This may be an imported summary. Connect Gmail to fetch the original evidence."
            />
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
