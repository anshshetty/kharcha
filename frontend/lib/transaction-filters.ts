import type { Transaction } from './types';

export interface TransactionFilters {
  month: string;
  currency: string;
  category: string;
  kind: string;
  search: string;
  newOnly: boolean;
  group?: string;
  groupIds?: string[];
  categoryIds?: string[];
}

export function filterTransactions(
  rows: Transaction[],
  filters: TransactionFilters,
  currentVisitIds: ReadonlySet<string> = new Set(),
): Transaction[] {
  const query = filters.search.trim().toLowerCase();
  return rows.filter((transaction) => {
    // New imports can be dated in an older month or use another currency.
    if (filters.newOnly) {
      // Keep acknowledged rows in place until the user leaves this view.
      if (!transaction.is_new && !currentVisitIds.has(transaction.id))
        return false;
    } else if (
      (filters.month && !transaction.date.startsWith(filters.month)) ||
      transaction.currency !== filters.currency
    ) {
      return false;
    }
    if (
      filters.group &&
      filters.groupIds &&
      !filters.groupIds.includes(transaction.id)
    )
      return false;
    return (
      (filters.category === 'all' ||
        (filters.categoryIds
          ? filters.categoryIds.includes(transaction.id)
          : transaction.category === filters.category ||
            transaction.allocations?.some(
              (allocation) => allocation.category === filters.category,
            ))) &&
      (filters.kind === 'all' || transaction.kind === filters.kind) &&
      (!query ||
        [
          transaction.merchant_display,
          transaction.counterparty,
          transaction.account,
          transaction.reference,
          transaction.category,
        ]
          .join(' ')
          .toLowerCase()
          .includes(query))
    );
  });
}

export function newTransactionIds(rows: Transaction[]): string[] {
  return rows.filter((transaction) => transaction.is_new).map(({ id }) => id);
}

export function spendingByCurrency(rows: Transaction[]): [string, number][] {
  const totals = new Map<string, number>();
  for (const transaction of rows) {
    totals.set(
      transaction.currency,
      (totals.get(transaction.currency) || 0) + transaction.spend_minor,
    );
  }
  return [...totals.entries()].sort(([a], [b]) => a.localeCompare(b));
}

/** Saved months must remain reachable after they leave the rolling chart window. */
export function transactionHistory(
  rows: Transaction[],
  currency: string,
  recentMonths: string[] = [],
) {
  const saved = rows.filter((row) => row.currency === currency);
  const recordedMonths = [
    ...new Set(saved.map((row) => row.date.slice(0, 7))),
  ].sort();
  return {
    count: saved.length,
    firstMonth: recordedMonths[0] || '',
    latestMonth: recordedMonths.at(-1) || '',
    months: [...new Set([...recentMonths, ...recordedMonths])].sort().reverse(),
  };
}
