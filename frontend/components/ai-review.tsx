'use client';
import Image from 'next/image';
import { useEffect, useState } from 'react';
import {
  ArrowUpRight,
  ChevronRight,
  RefreshCw,
  Repeat2,
  Sparkles,
} from 'lucide-react';
import { api, money, monthName } from '@/lib/api';
import { Button } from '@/components/ui/button';
import {
  CategorySymbol,
  SpendingComposition,
  MonthlyTrend,
} from './spending-graphics';
import type { Report } from '@/lib/types';
import { categoryColor } from '@/lib/design-system';
import type { TransactionSelection } from '@/lib/types';

type Insight = {
  title: string;
  detail: string;
  confidence: string;
  transaction_ids: string[];
};
type Analysis = {
  state: string;
  stale?: boolean;
  message: string;
  generated_at?: string;
  focus_month?: string;
  currency?: string;
  result?: { summary: string; patterns: Insight[]; actions: Insight[] };
};
type SpendingGroup = {
  name: string;
  amount_minor: number;
  gross_minor: number;
  count: number;
  category?: string;
  transaction_ids: string[];
};
type EvidenceTransaction = {
  id: string;
  date: string;
  merchant: string;
  amount_minor: number;
};
type EvidenceLookup = Record<string, EvidenceTransaction>;
type SpendingFocus = {
  month: string;
  current: boolean;
  as_of: string;
  currency: string;
  spending_minor: number;
  fixed_minor: number;
  unavoidable_minor: number;
  other_minor: number;
  other_gross_minor: number;
  other_refund_minor: number;
  other_count: number;
  unclassified_minor: number;
  categories: SpendingGroup[];
  merchants: SpendingGroup[];
  repeat_merchants: SpendingGroup[];
  fixed_categories: SpendingGroup[];
  transactions?: EvidenceTransaction[];
  coverage: { last_sync: string | null; flagged_count: number };
  policy: { fixed_categories: string[]; unavoidable_categories: string[] };
};
type Props = {
  month: string;
  currency?: string;
  onTransaction: (t: TransactionSelection) => void;
  onSettings: () => void;
  report: Report;
  onMonth: (month: string) => void;
  onCategory: (category: string) => void;
  onGroup: (group: string) => void;
  onReview: () => void;
  revision: number;
};

function Evidence({
  ids,
  onTransaction,
  transactions = {},
  currency = 'INR',
}: {
  ids: string[];
  onTransaction: Props['onTransaction'];
  transactions?: EvidenceLookup;
  currency?: string;
}) {
  return (
    <div className="focus-evidence">
      {ids.map((id, index) => {
        const transaction = transactions[id];
        return (
          <Button
            key={id}
            variant="ghost"
            onClick={() => onTransaction({ id })}
          >
            {transaction ? (
              <>
                <span className="focus-evidence-copy">
                  <small>
                    {new Date(
                      transaction.date + 'T12:00:00',
                    ).toLocaleDateString('en-IN', {
                      day: 'numeric',
                      month: 'short',
                    })}
                  </small>
                  <span>{transaction.merchant}</span>
                </span>
                <span className="focus-evidence-amount">
                  {money(transaction.amount_minor, currency)}
                </span>
              </>
            ) : (
              <span>Transaction {index + 1}</span>
            )}
            <ArrowUpRight size={13} aria-hidden="true" />
          </Button>
        );
      })}
      {ids.some((id) => transactions[id]) && (
        <p className="focus-evidence-note">
          Amounts are each transaction’s contribution to regular spending.
        </p>
      )}
    </div>
  );
}

function Finding({
  item,
  onTransaction,
  transactions,
  currency,
}: {
  item: Insight;
  onTransaction: Props['onTransaction'];
  transactions: EvidenceLookup;
  currency: string;
}) {
  return (
    <article className="focus-finding">
      <h4>{item.title}</h4>
      <p>{item.detail}</p>
      {item.transaction_ids.length > 0 && (
        <details>
          <summary>Supporting transactions</summary>
          <Evidence
            ids={item.transaction_ids}
            onTransaction={onTransaction}
            transactions={transactions}
            currency={currency}
          />
        </details>
      )}
    </article>
  );
}

export function AIReview(props: Props) {
  return (
    <ReviewContent
      key={`${props.month}:${props.currency || 'INR'}`}
      {...props}
    />
  );
}
function ReviewContent({
  month,
  currency = 'INR',
  onTransaction,
  onSettings,
  report,
  onMonth,
  onCategory,
  onGroup,
  onReview,
  revision,
}: Props) {
  const [focus, setFocus] = useState<SpendingFocus | null>(null);
  const [focusError, setFocusError] = useState('');
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [analysisError, setAnalysisError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    const query = new URLSearchParams({
      ...(month ? { month } : {}),
      currency,
    });
    const loadFocus = () =>
      api<SpendingFocus>(`/spending-focus?${query}`)
        .then((value) => {
          if (live) {
            setFocus(value);
            setFocusError('');
          }
        })
        .catch((error) => {
          if (live) setFocusError(error.message);
        });
    const loadAnalysis = () =>
      api<Analysis>(`/ai-advisor?${query}`)
        .then((value) => {
          if (live) {
            setAnalysis(value);
            setAnalysisError('');
          }
        })
        .catch((error) => {
          if (live) setAnalysisError(error.message);
        });
    void loadFocus();
    void loadAnalysis();
    const focusTimer = setInterval(loadFocus, 10000);
    const analysisTimer = setInterval(loadAnalysis, 5000);
    return () => {
      live = false;
      clearInterval(focusTimer);
      clearInterval(analysisTimer);
    };
  }, [month, currency, revision]);

  const refresh = async () => {
    setBusy(true);
    setAnalysisError('');
    try {
      setAnalysis(
        await api<Analysis>(
          `/ai-advisor/refresh?${new URLSearchParams({ ...(month ? { month } : {}), currency })}`,
          'POST',
          {},
        ),
      );
    } catch (error) {
      setAnalysisError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const result =
    focus &&
    analysis?.focus_month === focus.month &&
    analysis.currency === currency
      ? analysis.result
      : undefined;
  const categories =
    focus?.categories.filter(
      (group) => group.gross_minor > 0 || group.amount_minor !== 0,
    ) || [];
  const topCategories = categories.slice(0, 6);
  const remainingCategories = categories.slice(6);
  const evidenceTransactions: EvidenceLookup = Object.fromEntries(
    (focus?.transactions || []).map((transaction) => [
      transaction.id,
      transaction,
    ]),
  );
  const categoryRow = (group: SpendingGroup) => {
    const share = focus?.other_gross_minor
      ? Math.round((group.gross_minor / focus.other_gross_minor) * 100)
      : 0;
    const contents = (
      <>
        <span className="focus-category-title">
          <span>
            <CategorySymbol name={group.name} />
            {group.name}
            <ChevronRight size={14} aria-hidden="true" />
          </span>
          <strong>{money(group.amount_minor, currency)}</strong>
        </span>
        <span className="focus-category-meta">
          <span>
            {group.count} {group.count === 1 ? 'transaction' : 'transactions'}
          </span>
          <span>{share}% of purchases</span>
        </span>
        <span className="focus-category-track" aria-hidden="true">
          <span style={{ width: `${Math.min(100, Math.max(0, share))}%` }} />
        </span>
      </>
    );
    return (
      <button
        className="focus-category category-drill"
        key={group.name}
        style={
          {
            '--category-color': categoryColor(group.name),
          } as React.CSSProperties
        }
        onClick={() => onCategory(group.name)}
        aria-label={`View ${group.name} payments: ${money(group.amount_minor, currency)}`}
      >
        {contents}
      </button>
    );
  };

  return (
    <div className="spending-focus">
      <section className="focus-dashboard" aria-labelledby="focus-title">
        {focusError && (
          <p className="focus-error" role="alert">
            {focusError}
          </p>
        )}
        {!focus && !focusError && (
          <div className="report-loading" aria-live="polite">
            <div />
            <div />
            <span>Loading recorded spending…</span>
          </div>
        )}
        {focus && (
          <>
            <div className="report-opening">
              <section className="spending-summary">
                <h2 id="focus-title">Recorded personal spending</h2>
                <div className="spending-total">
                  {report.totals.count
                    ? money(focus.spending_minor, currency)
                    : '—'}
                </div>
                <p>
                  {monthName(focus.month)}
                  {focus.current ? ' so far' : ''} · after refunds
                </p>
                {!report.totals.count && (
                  <div className="spending-empty">
                    <Image
                      src="/illustrations/receipts.png"
                      width={176}
                      height={132}
                      alt=""
                      unoptimized
                    />
                    <div>
                      <strong>No payments recorded this month</strong>
                      <p>
                        Choose an earlier month or import your first
                        transactions.
                      </p>
                      <Button variant="outline" onClick={onSettings}>
                        Connect or import <ArrowUpRight size={14} />
                      </Button>
                    </div>
                  </div>
                )}
                <SpendingComposition
                  regular={focus.other_minor}
                  fixed={focus.fixed_minor}
                  unavoidable={focus.unavoidable_minor}
                  currency={currency}
                  onGroup={onGroup}
                />
                <div className="spending-summary-foot">
                  <span>
                    Recorded payments only; missing costs may affect totals.
                  </span>
                  <button onClick={onSettings}>
                    Edit groups <ArrowUpRight size={13} />
                  </button>
                </div>
              </section>
              <MonthlyTrend
                months={report.months}
                selected={month}
                currency={currency}
                onSelect={onMonth}
              />
            </div>
            {(focus.coverage.flagged_count > 0 ||
              report.coverage.open_issues > 0) && (
              <button className="review-prompt" onClick={onReview}>
                <span>
                  <strong>
                    {report.coverage.open_issues ||
                      focus.coverage.flagged_count}{' '}
                    items need review
                  </strong>
                  <span>Resolve unclear details behind your numbers.</span>
                </span>
                <span>
                  Open review <ArrowUpRight size={16} />
                </span>
              </button>
            )}
            <div className="focus-breakdowns">
              <section
                className="focus-category-section"
                aria-labelledby="focus-categories-title"
              >
                <div className="focus-section-heading">
                  <h3 id="focus-categories-title">
                    Where regular spending went
                  </h3>
                  <p>
                    Category totals after refunds. Bars show share of purchases.
                  </p>
                </div>
                {categories.length ? (
                  <>
                    <div className="focus-category-list">
                      {topCategories.map(categoryRow)}
                    </div>
                    {remainingCategories.length > 0 && (
                      <details className="focus-more">
                        <summary>
                          {remainingCategories.length} more{' '}
                          {remainingCategories.length === 1
                            ? 'category'
                            : 'categories'}
                        </summary>
                        <div className="focus-category-list">
                          {remainingCategories.map(categoryRow)}
                        </div>
                      </details>
                    )}
                  </>
                ) : (
                  <p className="focus-empty">
                    No regular expenses recorded for this month.
                  </p>
                )}
              </section>
              <section
                className="focus-repeat-section"
                aria-labelledby="focus-repeat-title"
              >
                <div className="focus-section-heading">
                  <h3 id="focus-repeat-title">
                    <Repeat2 size={17} aria-hidden="true" />
                    Repeated spending
                  </h3>
                  <p>Places you paid more than once this month.</p>
                </div>
                {focus.repeat_merchants.length ? (
                  <div className="focus-repeat-list">
                    {focus.repeat_merchants.slice(0, 5).map((merchant) => (
                      <details className="focus-merchant" key={merchant.name}>
                        <summary>
                          <span className="repeat-merchant-identity">
                            <CategorySymbol
                              name={merchant.category || 'Other'}
                              size={20}
                            />
                            <span>
                              <strong>{merchant.name}</strong>
                              <small>
                                {merchant.count} payments
                                {merchant.category
                                  ? ` · ${merchant.category}`
                                  : ''}
                              </small>
                            </span>
                          </span>
                          <span className="focus-merchant-amount">
                            {money(merchant.amount_minor, currency)}
                            <ChevronRight size={14} aria-hidden="true" />
                          </span>
                        </summary>
                        <Evidence
                          ids={merchant.transaction_ids}
                          onTransaction={onTransaction}
                          transactions={evidenceTransactions}
                          currency={currency}
                        />
                      </details>
                    ))}
                  </div>
                ) : (
                  <p className="focus-empty">
                    No repeated merchants in this month’s regular spending.
                  </p>
                )}
                <p className="focus-neutral-note">
                  These expenses include essentials. Repetition alone doesn’t
                  mean a purchase was wasteful.
                </p>
              </section>
            </div>
            <div className="focus-ledger-note">
              <span>
                Regular spending includes {focus.other_count} payments
                {focus.other_refund_minor > 0
                  ? ` and ${money(focus.other_refund_minor, currency)} in refunds`
                  : ''}
                .
              </span>
              {focus.unclassified_minor !== 0 && (
                <span>
                  {money(focus.unclassified_minor, currency)} included in other
                  spending has unclear purpose or classification.
                </span>
              )}
              {focus.coverage.flagged_count > 0 && (
                <span>
                  {focus.coverage.flagged_count}{' '}
                  {focus.coverage.flagged_count === 1
                    ? 'record has'
                    : 'records have'}{' '}
                  unresolved evidence.
                </span>
              )}
              {focus.coverage.last_sync && (
                <span>
                  Last sync{' '}
                  {new Date(focus.coverage.last_sync).toLocaleString('en-IN', {
                    day: 'numeric',
                    month: 'short',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </span>
              )}
            </div>
          </>
        )}
      </section>

      <details className="focus-ai" open={result ? true : undefined}>
        <summary className="insights-summary">
          <span>
            <Sparkles size={17} /> Spending insights
          </span>
          <span>
            {analysis?.state === 'disabled'
              ? 'Optional · off'
              : 'Patterns backed by your payments'}
            <ChevronRight size={16} />
          </span>
        </summary>
        <section aria-labelledby="focus-ai-title">
          <div className="focus-heading">
            <div className="focus-title-group">
              <span className="section-icon">
                <Sparkles size={20} aria-hidden="true" />
              </span>
              <div>
                <h2 id="focus-ai-title">What stands out</h2>
                <p>AI findings about this month’s regular spending.</p>
              </div>
            </div>
            <Button
              variant="outline"
              disabled={busy || analysis?.state === 'running'}
              onClick={refresh}
            >
              <RefreshCw
                size={14}
                className={analysis?.state === 'running' ? 'spin' : ''}
                aria-hidden="true"
              />
              {analysis?.state === 'running'
                ? 'Analysing…'
                : 'Refresh insights'}
            </Button>
          </div>
          {analysisError && (
            <p className="focus-error" role="alert">
              {analysisError}
            </p>
          )}
          {(analysis?.state !== 'complete' || analysis?.stale) && (
            <output className="focus-analysis-status">
              {analysis?.message || 'Checking insights…'}
            </output>
          )}
          {result && (
            <>
              {result.patterns?.length > 0 && (
                <div className="focus-findings">
                  {result.patterns.slice(0, 3).map((item, index) => (
                    <Finding
                      key={`pattern-${index}`}
                      item={item}
                      onTransaction={onTransaction}
                      transactions={evidenceTransactions}
                      currency={currency}
                    />
                  ))}
                </div>
              )}
              {result.actions?.length > 0 && (
                <div className="focus-decisions">
                  <h3>
                    {focus?.current
                      ? 'Worth a decision this month'
                      : 'Lessons for future spending'}
                  </h3>
                  <div className="focus-findings">
                    {result.actions.slice(0, 2).map((item, index) => (
                      <Finding
                        key={`action-${index}`}
                        item={item}
                        onTransaction={onTransaction}
                        transactions={evidenceTransactions}
                        currency={currency}
                      />
                    ))}
                  </div>
                </div>
              )}
              {!result.patterns?.length && !result.actions?.length && (
                <p className="focus-empty">
                  No additional finding supported by this month’s records.
                </p>
              )}
            </>
          )}
          <div className="focus-ai-footer">
            {result && analysis?.generated_at && (
              <span>
                Reviewed{' '}
                {new Date(analysis.generated_at).toLocaleString('en-IN', {
                  day: 'numeric',
                  month: 'short',
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </span>
            )}
            <details>
              <summary>About these insights</summary>
              <p>
                Uses your signed-in Codex allowance. Selected ledger details are
                sent to Codex. The current month refreshes at most daily after
                sync. Use Refresh insights to review a previous month. Your
                saved corrections and exemptions stay in place.
              </p>
            </details>
          </div>
        </section>
      </details>
    </div>
  );
}
