'use client';
import {
  ArrowUpRight,
  ShoppingBag,
  Utensils,
  ShoppingBasket,
  House,
  TrainFront,
  HeartPulse,
  Ticket,
  BookOpen,
  Repeat,
  Shapes,
  ShieldCheck,
} from 'lucide-react';
import { money, monthName } from '@/lib/api';
import { categoryColor } from '@/lib/design-system';
import type { MonthTotals } from '@/lib/types';
export function CategorySymbol({
  name,
  size = 18,
}: {
  name: string;
  size?: number;
}) {
  const n = name.toLowerCase();
  const Icon = n.includes('grocer')
    ? ShoppingBasket
    : n.includes('food') || n.includes('dining')
      ? Utensils
      : n.includes('shopping')
        ? ShoppingBag
        : n.includes('rent') || n.includes('home')
          ? House
          : n.includes('transport') || n.includes('travel')
            ? TrainFront
            : n.includes('health')
              ? HeartPulse
              : n.includes('entertain')
                ? Ticket
                : n.includes('education')
                  ? BookOpen
                  : n.includes('subscription')
                    ? Repeat
                    : n.includes('insurance')
                      ? ShieldCheck
                      : Shapes;
  return (
    <span
      className="category-symbol"
      style={{ '--category-color': categoryColor(name) } as React.CSSProperties}
    >
      <Icon size={size} aria-hidden="true" />
    </span>
  );
}
export function SpendingComposition({
  regular,
  fixed,
  unavoidable,
  currency,
  onGroup,
}: {
  regular: number;
  fixed: number;
  unavoidable: number;
  currency: string;
  onGroup: (group: string) => void;
}) {
  const groups = [
    { id: 'regular', name: 'Regular', value: regular },
    { id: 'fixed', name: 'Fixed', value: fixed },
    { id: 'unavoidable', name: 'Unavoidable', value: unavoidable },
  ];
  const positive = groups.reduce((sum, g) => sum + Math.max(0, g.value), 0);
  const signed = groups.some((g) => g.value < 0);
  return (
    <div className="spending-composition">
      <div className="composition-ribbon" aria-label="Spending composition">
        {groups
          .filter((g) => g.value > 0)
          .map((g) => (
            <button
              key={g.id}
              className={'composition-segment segment-' + g.id}
              style={{ flex: g.value }}
              onClick={() => onGroup(g.id)}
              aria-label={`View ${g.name.toLowerCase()} spending: ${money(g.value, currency)}, ${positive ? Math.round((g.value / positive) * 100) : 0}% of positive amounts`}
              title={`${g.name}: ${money(g.value, currency)}`}
            >
              {g.value / positive >= 0.1 && (
                <>
                  <span>{Math.round((g.value / positive) * 100)}%</span>
                  <ArrowUpRight size={14} />
                </>
              )}
            </button>
          ))}
        {!positive && (
          <span className="composition-zero">
            No positive spending recorded
          </span>
        )}
      </div>
      <div className="composition-legend">
        {groups.map((g) => (
          <button key={g.id} onClick={() => onGroup(g.id)}>
            <span>
              <i className={'segment-' + g.id} />
              {g.name}
              <ArrowUpRight size={13} />
            </span>
            <strong>{money(g.value, currency)}</strong>
          </button>
        ))}
      </div>
      {signed && (
        <p className="help-text">
          Ribbon shows positive amounts; negative groups reflect net refunds.
        </p>
      )}
    </div>
  );
}
export function MonthlyTrend({
  months,
  selected,
  currency,
  onSelect,
}: {
  months: MonthTotals[];
  selected: string;
  currency: string;
  onSelect: (month: string) => void;
}) {
  const limit = Math.max(...months.map((m) => Math.abs(m.spend_minor)), 1);
  const negative = months.some((m) => m.spend_minor < 0);
  return (
    <section className="monthly-trend" aria-labelledby="trend-title">
      <div className="report-section-heading">
        <h2 id="trend-title">The monthly picture</h2>
        <span>Recorded personal spending</span>
      </div>
      <div className={'trend-columns' + (negative ? ' trend-signed' : '')}>
        {months.map((m) => (
          <button
            key={m.month}
            className={
              'trend-month' +
              (m.month === selected ? ' is-selected' : '') +
              (m.current ? ' is-current' : '')
            }
            onClick={() => onSelect(m.month)}
            aria-pressed={m.month === selected}
            aria-label={`${monthName(m.month)}: ${money(m.spend_minor, currency)}${m.current ? ', month to date' : ''}${!m.count ? ', no recorded payments' : ''}`}
            title={`${monthName(m.month)} · ${money(m.spend_minor, currency)}${m.current ? ' · so far' : ''}`}
          >
            <span className="trend-value">
              {m.count ? money(m.spend_minor, currency, true) : '—'}
            </span>
            <span className="trend-bar-space">
              <span
                className={
                  'trend-bar' + (m.spend_minor < 0 ? ' is-negative' : '')
                }
                style={{
                  height: `${m.count ? Math.max(3, (Math.abs(m.spend_minor) / limit) * (negative ? 50 : 100)) : 0}%`,
                }}
              />
            </span>
            <span className="trend-month-name">
              {new Date(m.month + '-15T12:00:00').toLocaleDateString('en-IN', {
                month: 'short',
              })}
            </span>
            <span className="trend-current-label">
              {m.current ? 'So far' : m.month === selected ? 'Selected' : ''}
            </span>
          </button>
        ))}
      </div>
      <p className="trend-note">
        Select a month to explore it. This month is partial; compare completed
        months like for like.
      </p>
    </section>
  );
}
export function SplitGraphic({
  amounts,
  total,
  currency,
}: {
  amounts: number[];
  total: number;
  currency: string;
}) {
  const labels = ['Your share', 'Reimbursable', 'Lent'];
  const valid =
    amounts.every((n) => Number.isFinite(n) && n >= 0) &&
    Number.isFinite(total) &&
    total > 0;
  const sum = amounts.reduce((a, b) => a + b, 0);
  return (
    <figure className="split-graphic">
      <figcaption>Where this payment goes</figcaption>
      <div className="split-ribbon" aria-hidden="true">
        {valid &&
          amounts.map(
            (n, i) =>
              n > 0 && (
                <span
                  className={'split-segment-' + i}
                  key={i}
                  style={{ flex: n }}
                />
              ),
          )}
      </div>
      <div>
        {amounts.map((n, i) => (
          <span key={i}>
            <i className={'split-segment-' + i} />
            {labels[i]}
            <strong>
              {Number.isFinite(n) ? money(Math.round(n * 100), currency) : '—'}
            </strong>
          </span>
        ))}
      </div>
      <p aria-live="polite">
        {valid && Math.round(sum * 100) === Math.round(total * 100)
          ? 'Balanced · only your share counts as spending'
          : `Complete the split to total ${Number.isFinite(total) ? money(Math.round(total * 100), currency) : 'the payment amount'}.`}
      </p>
    </figure>
  );
}
