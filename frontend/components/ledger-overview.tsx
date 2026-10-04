'use client';
import {
  ChevronRight,
  Mail,
  ArrowUpRight,
  ArrowDownRight,
  Wallet,
  CreditCard,
  ScanLine,
  ArrowLeftRight,
  Landmark,
  CircleHelp,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Blank, Picker, SectionTitle } from './common';
import { TransactionTable } from './transaction-table';
import { money, monthName } from '@/lib/api';
import { categoryColor } from '@/lib/design-system';
import { transactionHistory } from '@/lib/transaction-filters';
import type {
  Report,
  Transaction,
  TransactionSelection,
  MonthTotals,
  NamedAmount,
} from '@/lib/types';
import type { View } from '@/lib/navigation';

type Props = {
  report: Report;
  month: string;
  currency: string;
  connected: boolean;
  rows: Transaction[];
  filtered: Transaction[];
  setMonth: (month: string) => void;
  setView: (view: View) => void;
  setSelected: (selection: TransactionSelection) => void;
  drill: (category?: string, kind?: string, search?: string) => void;
  onVisible: (ids: string[]) => Promise<void>;
  visibilityEnabled: boolean;
};
export function LedgerOverview({
  report,
  month,
  currency,
  connected,
  rows,
  filtered,
  setMonth,
  setView,
  setSelected,
  drill,
  onVisible,
  visibilityEnabled,
}: Props) {
  const total = report.totals;
  const history = transactionHistory(
    rows,
    currency,
    report.months.map((item) => item.month),
  );
  const selectedReport = report.months.find((m) => m.month === month);
  const prior = report.previous_totals.spend_minor;
  const change = prior
    ? Math.round(((total.spend_minor - prior) / prior) * 100)
    : null;
  return (
    <details className="full-ledger-details">
      <summary>
        <span>
          Full ledger details
          <small>All costs, transaction history and previous months</small>
        </span>
        <ChevronRight size={18} aria-hidden="true" />
      </summary>
      <div className="full-ledger-heading">
        <p>Showing all recorded costs for {month && monthName(month)}.</p>
        <Picker
          label="Ledger month"
          value={month}
          onChange={setMonth}
          options={history.months.map((value) => ({
            value,
            label:
              monthName(value) +
              (report.months.some(
                (item) => item.month === value && item.current,
              )
                ? ' · so far'
                : ''),
          }))}
        />
      </div>
      {!connected && (
        <div className="connect-banner">
          <span className="mail-icon">
            <Mail size={22} />
          </span>
          <div>
            <b>
              {rows.length
                ? 'Connect Gmail to complete the picture'
                : 'Your spending story starts in your inbox'}
            </b>
            <p>
              Import the last 6 months of financial emails. Keep everything on
              your Mac.
            </p>
          </div>
          <Button onClick={() => setView('settings')}>
            Connect Gmail
            <ArrowUpRight size={16} />
          </Button>
        </div>
      )}
      <div className="stat-grid">
        <section className="stat-card primary-stat">
          <div className="stat-label">
            Personal spending
            <Wallet size={17} />
          </div>
          <div className="big-money">
            {total?.count ? money(total.spend_minor, currency) : '—'}
          </div>
          <div className="stat-bottom">
            <span>
              {month && monthName(month)}
              {selectedReport?.current ? ' · month to date' : ''}
            </span>
            {change !== null && (
              <span className="change">
                {change >= 0 ? (
                  <ArrowUpRight size={14} />
                ) : (
                  <ArrowDownRight size={14} />
                )}{' '}
                {Math.abs(change)}%
              </span>
            )}
          </div>
        </section>
        <section className="stat-card">
          <div className="stat-label">
            EMI installments
            <CreditCard size={17} />
          </div>
          <div className="stat-money">{money(total?.emi_minor, currency)}</div>
          <div className="stat-bottom">
            <span>Counted in the month charged</span>
            <button
              aria-label="View EMI installments"
              onClick={() => drill('all', 'emi')}
            >
              <ArrowUpRight size={17} />
            </button>
          </div>
        </section>
        <section className="stat-card">
          <div className="stat-label">
            Money returned
            <ArrowDownRight size={17} />
          </div>
          <div className="stat-money green">
            {money(total?.refund_minor, currency)}
          </div>
          <div className="stat-bottom">
            <span>Refunds reducing this month’s spend</span>
            <button
              aria-label="View refunds"
              onClick={() => drill('all', 'refund')}
            >
              <ArrowUpRight size={17} />
            </button>
          </div>
        </section>
      </div>
      <div className="coverage-strip">
        <ScanLine size={17} />
        <span>
          {total?.count ? (
            <>
              <b>
                {total.provisional
                  ? `Provisional · review the supporting evidence`
                  : 'Based on recorded transactions'}
              </b>{' '}
              · {total.count} transactions in this month
            </>
          ) : (
            <b>No transactions found for this month. Coverage is unverified.</b>
          )}
        </span>
        <button onClick={() => setView('review')}>
          Check coverage
          <ChevronRight size={15} />
        </button>
      </div>
      <div className="chart-grid">
        <section className="panel trend-panel">
          <SectionTitle
            title="Your spending over time"
            detail="Six completed months, plus this month so far"
          />
          <div className="chart-legend">
            <span>
              <i />
              Personal spending
            </span>
            <span>
              <i className="light" />
              Current month
            </span>
          </div>
          <div className="bar-chart">
            {report.months.map((m: MonthTotals) => {
              const max = Math.max(
                ...report.months.map((x: MonthTotals) =>
                  Math.abs(x.spend_minor),
                ),
                1,
              );
              return (
                <button
                  key={m.month}
                  className={
                    'bar-column ' +
                    (m.month === month ? 'selected ' : '') +
                    (m.current ? 'current' : '')
                  }
                  onClick={() => setMonth(m.month)}
                  aria-label={`${monthName(m.month)}, ${m.count ? money(m.spend_minor, currency) : 'no data'}`}
                >
                  <span className="bar-amount">
                    {m.count ? money(m.spend_minor, currency, true) : 'No data'}
                  </span>
                  <span className="bar-space">
                    <span
                      className="bar"
                      style={{
                        height: m.count
                          ? Math.max(4, (Math.abs(m.spend_minor) / max) * 100) +
                            '%'
                          : '3px',
                      }}
                    />
                  </span>
                  <span className="bar-month">
                    {monthName(m.month, true)}
                    {m.current && <small>so far</small>}
                  </span>
                </button>
              );
            })}
          </div>
        </section>
        <section className="panel">
          <SectionTitle
            title="Where it went"
            detail="Personal spending by category"
            action="See all"
            onAction={() => drill()}
          />
          {report.categories.length ? (
            <div className="category-list">
              {report.categories.slice(0, 6).map((cat: NamedAmount) => (
                <button
                  key={cat.name}
                  className="category-row"
                  aria-label={cat.name}
                  onClick={() => drill(cat.name)}
                >
                  <span
                    className="category-dot"
                    style={{ background: categoryColor(cat.name) }}
                  />
                  <span className="category-copy">
                    <span>
                      {cat.name}
                      <b>{money(cat.amount_minor, currency)}</b>
                    </span>
                    <span className="category-track">
                      <i
                        style={{
                          width:
                            Math.min(
                              100,
                              (Math.max(0, cat.amount_minor) /
                                Math.max(total.gross_minor, 1)) *
                                100,
                            ) + '%',
                          background: categoryColor(cat.name),
                        }}
                      />
                    </span>
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <Blank
              title="No category breakdown yet"
              description="Connect Gmail or add a transaction to start understanding this month."
            />
          )}
        </section>
      </div>
      <div className="ledger-movement">
        <section className="panel">
          <SectionTitle
            title="Money moving elsewhere"
            detail="Shown separately from personal spending"
          />
          <button
            className="movement"
            onClick={() => drill('all', 'person_payment')}
          >
            <span className="movement-icon">
              <ArrowLeftRight size={20} />
            </span>
            <span>
              <b>Payments to people</b>
              <small>Your personal share counts as spending</small>
            </span>
            <strong>{money(total.people_minor, currency)}</strong>
          </button>
          <button className="movement" onClick={() => drill()}>
            <span className="movement-icon">
              <Landmark size={20} />
            </span>
            <span>
              <b>Transfers & other outflows</b>
              <small>Card bills, investments and unallocated cash</small>
            </span>
            <strong>{money(total.movement_minor, currency)}</strong>
          </button>
          <p className="panel-footnote">
            <CircleHelp size={15} />
            Paying your credit card bill does not count as a new purchase.
          </p>
        </section>
      </div>
      <section className="panel" style={{ marginBottom: 22 }}>
        <SectionTitle
          title="Your top merchants & people"
          detail="Personal spending by recipient"
          action="View transactions"
          onAction={() => drill()}
        />
        {report.merchants.length ? (
          <div className="merchant-breakdown">
            {report.merchants.slice(0, 6).map((m: NamedAmount, i: number) => (
              <button
                className="small-row"
                key={m.name}
                onClick={() => drill('all', 'all', m.name)}
                style={{ textAlign: 'left', width: '100%' }}
              >
                <span>
                  <b>{m.name}</b>
                  <small>{i + 1} in this month’s recorded spending</small>
                </span>
                <strong>{money(m.amount_minor, currency)}</strong>
              </button>
            ))}
          </div>
        ) : (
          <p className="help-text">
            Merchant and person totals appear when this month has recorded
            spending.
          </p>
        )}
      </section>
      <section className="panel transaction-panel">
        <SectionTitle
          title="Recent transactions"
          detail={month ? monthName(month) : ''}
          action="View transactions"
          onAction={() => drill()}
        />
        <TransactionTable
          rows={filtered.slice(0, 6)}
          onSelect={setSelected}
          onVisible={onVisible}
          visibilityEnabled={visibilityEnabled}
        />
      </section>
    </details>
  );
}
