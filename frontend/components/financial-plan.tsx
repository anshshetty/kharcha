'use client';
import { useEffect, useState } from 'react';
import { api, money } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import type { TransactionSelection } from '@/lib/types';

type Context = {
  priorities: string;
  commitments: string;
  people: string;
  notes: string;
  project_categories: string[];
  monthly_target_minor: number | null;
  currency: string;
};
type Brief = {
  month: string;
  currency: string;
  context: Context;
  spending_minor: number;
  project_spending_minor: number;
  everyday_spending_minor: number;
  target_minor: number | null;
  remaining_minor: number | null;
  review_count: number;
  source_review_count: number;
  provisional: boolean;
  excluded_count: number;
  top_transactions: {
    id: string;
    date: string;
    counterparty: string;
    category: string;
    spend_minor: number;
  }[];
};
type Props = {
  month: string;
  currency: string;
  onTransaction: (t: TransactionSelection) => void;
};
export function FinancialPlan(props: Props) {
  return (
    <FinancialPlanContent key={`${props.month}:${props.currency}`} {...props} />
  );
}
function FinancialPlanContent({ month, currency, onTransaction }: Props) {
  const [brief, setBrief] = useState<Brief | null>(null),
    [context, setContext] = useState<Context | null>(null),
    [error, setError] = useState(''),
    [saved, setSaved] = useState(''),
    [busy, setBusy] = useState(false),
    [target, setTarget] = useState('');
  useEffect(() => {
    let active = true;
    api<Brief>('/financial-brief?' + new URLSearchParams({ month, currency }))
      .then((b) => {
        if (active) {
          setBrief(b);
          setContext(b.context);
          setTarget(
            b.context.monthly_target_minor === null
              ? ''
              : String(b.context.monthly_target_minor / 100),
          );
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [month, currency]);
  useEffect(() => {
    let active = true;
    const timer = setInterval(() => {
      api<Brief>('/financial-brief?' + new URLSearchParams({ month, currency }))
        .then((b) => {
          if (active) setBrief(b);
        })
        .catch(() => {});
    }, 10000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [month, currency]);
  const save = async () => {
    if (!context) return;
    setBusy(true);
    setError('');
    setSaved('');
    try {
      if (target && (!/^\d+(\.\d{1,2})?$/.test(target) || Number(target) <= 0))
        throw new Error(
          'Enter a positive target with up to two decimal places, or leave it blank.',
        );
      const value = {
        priorities: context.priorities,
        commitments: context.commitments,
        people: context.people,
        notes: context.notes,
        currency: context.currency,
        monthly_target_minor: target ? Math.round(Number(target) * 100) : null,
      };
      const updated = await api<Context>('/financial-context', 'PATCH', value);
      setBrief(
        await api<Brief>(
          '/financial-brief?' + new URLSearchParams({ month, currency }),
        ),
      );
      setContext(updated);
      setSaved('Your financial context is saved on this Mac.');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel financial-plan">
      <h2>Your spending plan</h2>
      <p className="help-text">
        {month} · Separate major projects from everyday spending. Your ledger
        total stays unchanged.
      </p>
      {error && <p role="alert">{error}</p>}
      {!brief && !error && <output>Loading your spending plan…</output>}
      {brief && (
        <>
          <div className="plan-totals">
            <div>
              <small>Everyday spending</small>
              <strong>{money(brief.everyday_spending_minor, currency)}</strong>
            </div>
            <div>
              <small>Major projects</small>
              <strong>{money(brief.project_spending_minor, currency)}</strong>
            </div>
            <div>
              <small>Total recorded spending</small>
              <strong>{money(brief.spending_minor, currency)}</strong>
            </div>
          </div>
          {brief.target_minor !== null && (
            <p>
              {(brief.remaining_minor || 0) >= 0
                ? 'Remaining against your everyday target: '
                : 'Over your everyday target: '}
              <strong>
                {money(Math.abs(brief.remaining_minor || 0), currency)}
              </strong>
              . This is a target comparison, not your available bank balance.
            </p>
          )}
          <p className="help-text">
            {brief.review_count} transactions and {brief.source_review_count}{' '}
            source emails need review in this month. {brief.excluded_count}{' '}
            exempt records are excluded. Email coverage may be incomplete;
            current-month figures are month to date.
          </p>
          <details>
            <summary>Largest recorded expenses — inspect the evidence</summary>
            {brief.top_transactions.map((t) => (
              <div className="small-row" key={t.id}>
                <span>
                  <b>{t.counterparty}</b>
                  <small>
                    {t.date} · {t.category} · {money(t.spend_minor, currency)}
                  </small>
                </span>
                <Button
                  variant="outline"
                  onClick={() => onTransaction({ id: t.id })}
                >
                  View transaction
                </Button>
              </div>
            ))}
            {!brief.top_transactions.length && (
              <p>No recorded spending in this month.</p>
            )}
          </details>
        </>
      )}
      {context && (
        <details className="financial-context">
          <summary>Your financial context & target</summary>
          <p className="help-text">
            Save only what you want Codex to use when reviewing this project.
            These notes stay in the local app and are included in its financial
            brief. Saving does not send them to an AI service.
          </p>
          {(['priorities', 'commitments', 'people', 'notes'] as const).map(
            (key) => (
              <label key={key}>
                {
                  {
                    priorities: 'What you want to achieve',
                    commitments: 'Regular commitments and responsibilities',
                    people: 'People, shared payments and reimbursements',
                    notes: 'Other confirmed context',
                  }[key]
                }
                <Textarea
                  value={context[key]}
                  maxLength={10000}
                  onChange={(e) =>
                    setContext({ ...context, [key]: e.target.value })
                  }
                />
              </label>
            ),
          )}
          <p className="help-text">
            Choose major-project categories in Categories &amp; automatic rules
            below.
            {brief?.context.project_categories.length
              ? ' Selected: ' + brief.context.project_categories.join(' · ')
              : ' No major-project categories selected.'}
          </p>
          <label>
            Monthly everyday-spending target (optional)
            <Input
              inputMode="decimal"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="Choose your own target"
            />
          </label>
          <label>
            Target currency
            <select
              value={context.currency}
              onChange={(e) =>
                setContext({ ...context, currency: e.target.value })
              }
            >
              {['INR', 'USD', 'EUR', 'GBP'].map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
          <p className="help-text">
            Targets are compared only with spending in the same currency.
            Major-project categories are excluded from that comparison.
          </p>
          <Button disabled={busy} onClick={save}>
            {busy ? 'Saving…' : 'Save financial context'}
          </Button>
          {saved && <output>{saved}</output>}
        </details>
      )}
    </section>
  );
}
