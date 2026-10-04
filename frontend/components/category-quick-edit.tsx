'use client';
import { useState } from 'react';
import { Check, LoaderCircle, ChevronDown } from 'lucide-react';
import { api } from '@/lib/api';
import { merchantMatch } from '@/lib/payee';
import type { Transaction } from '@/lib/types';
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover';
import { Picker } from './common';
import { Button } from './ui/button';
export function CategoryQuickEdit({
  transaction: t,
  categories,
  onUpdated,
}: {
  transaction: Transaction;
  categories: string[];
  onUpdated: (message: string) => void;
}) {
  const [open, setOpen] = useState(false),
    [category, setCategory] = useState(t.category),
    [scope, setScope] = useState(
      merchantMatch(t)
        ? 'payee'
        : t.identity_confirmed && t.counterparty_key
          ? 'person'
          : 'none',
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [undoId, setUndoId] = useState('');
  const [attempt, setAttempt] = useState<{
    edit_id: string;
    changes: { category: string };
    expected: { category: string };
    remember_scope: string;
  } | null>(null);
  const [lastCategory, setLastCategory] = useState(t.category);
  if (lastCategory !== t.category && !busy && !attempt) {
    setLastCategory(t.category);
    setCategory(t.category);
  }
  const eligible = ['purchase', 'person_payment', 'emi', 'fee'].includes(
    t.kind,
  );
  const save = async (value: string, retry = false) => {
    setCategory(value);
    setBusy(true);
    setError('');
    const request =
      retry && attempt
        ? attempt
        : {
            edit_id: crypto.randomUUID().replaceAll('-', ''),
            changes: { category: value },
            expected: { category: t.category },
            remember_scope: eligible ? scope : 'none',
          };
    setAttempt(request);
    try {
      const result = await api<{ transaction: Transaction; undo_id: string }>(
        '/transactions/' + t.id + '/edit',
        'PATCH',
        request,
      );
      setCategory(result.transaction.category);
      setUndoId(result.undo_id);
      setAttempt(null);
      onUpdated('');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const undo = async () => {
    setBusy(true);
    setError('');
    try {
      const result = await api<Transaction>(
        '/transactions/' + t.id + '/undo-edit',
        'POST',
        { edit_id: undoId },
      );
      setCategory(result.category);
      setUndoId('');
      onUpdated('');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className={
          'category-chip category-edit-trigger' +
          (category === 'Uncategorized' ? ' uncategorized' : '')
        }
        aria-label={`Edit category for ${t.merchant_display || t.counterparty}`}
      >
        <span>{category}</span>
        <ChevronDown size={11} />
      </PopoverTrigger>
      <PopoverContent
        className="quick-category-popover"
        align="start"
        data-category-editor
        onClick={(event) => event.stopPropagation()}
      >
        <strong>{t.merchant_display || t.counterparty}</strong>
        <fieldset disabled={busy || !!attempt}>
          <legend className="sr-only">
            Category and future payment matching
          </legend>
          {eligible && (
            <label>
              Apply category to
              <Picker
                label="Apply category to"
                value={scope}
                onChange={setScope}
                options={[
                  { value: 'none', label: 'This payment only' },
                  {
                    value: 'payee',
                    label: 'Future payments with this payee name',
                  },
                  ...(t.identity_confirmed && t.counterparty_key
                    ? [
                        {
                          value: 'person',
                          label: 'Future payments to this confirmed person',
                        },
                        {
                          value: 'exact',
                          label: 'This person + this exact amount',
                        },
                      ]
                    : []),
                ]}
              />
            </label>
          )}
          <label>
            Category
            <Picker
              label="Choose category"
              value={category}
              onChange={(value) => void save(value)}
              options={categories}
            />
          </label>
        </fieldset>
        <p className="help-text">
          {scope === 'none'
            ? 'This edit changes only this payment. Existing rules stay active.'
            : 'Future matches use the same currency and direction. Earlier payments stay as they are.'}
        </p>
        <div className="save-indicator" aria-live="polite">
          {busy ? (
            <LoaderCircle size={14} className="spin" />
          ) : (
            <Check size={14} />
          )}{' '}
          {busy
            ? 'Saving…'
            : error
              ? 'Not saved'
              : undoId
                ? 'Saved'
                : 'Choose a category to save automatically'}
          {undoId && !busy && !attempt && (
            <button onClick={() => void undo()}>Undo</button>
          )}
        </div>
        {error && (
          <div role="alert">
            <p>{error}</p>
            {attempt && (
              <Button
                variant="outline"
                onClick={() => void save(attempt.changes.category, true)}
              >
                Retry save
              </Button>
            )}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
