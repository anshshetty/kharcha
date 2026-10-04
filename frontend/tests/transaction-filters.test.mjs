import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  filterTransactions,
  newTransactionIds,
  spendingByCurrency,
  transactionHistory,
} from '../lib/transaction-filters.ts';

const base = {
  date: '2026-09-01',
  currency: 'INR',
  category: 'Food',
  kind: 'purchase',
  account: 'Card 1234',
  counterparty: 'Cafe',
  spend_minor: 1000,
  is_new: true,
};
const rows = [
  { ...base, id: 'current' },
  {
    ...base,
    id: 'older',
    date: '2025-12-31',
    currency: 'USD',
    spend_minor: 500,
  },
  { ...base, id: 'seen', is_new: false },
  { ...base, id: 'refund', kind: 'refund', spend_minor: -200 },
];
const filters = {
  month: '2026-09',
  currency: 'INR',
  category: 'all',
  kind: 'all',
  search: '',
  newOnly: false,
};

test('new view includes historical and foreign-currency imports; regular view keeps month and currency', () => {
  assert.deepEqual(
    filterTransactions(rows, filters).map(({ id }) => id),
    ['current', 'seen', 'refund'],
  );
  assert.deepEqual(
    filterTransactions(rows, { ...filters, newOnly: true }).map(({ id }) => id),
    ['current', 'older', 'refund'],
  );
});

test('new transaction IDs include only unseen transactions in the filtered view', () => {
  const visible = filterTransactions(rows, { ...filters, kind: 'purchase' });
  assert.deepEqual(newTransactionIds(visible), ['current']);
  // Arriving rows and rows hidden by month/type remain untouched by this request.
  assert.equal(newTransactionIds(visible).includes('older'), false);
  assert.equal(newTransactionIds(visible).includes('refund'), false);
});

test('automatically seen rows stay in place for the current visit, then leave the new view', () => {
  const visitIds = new Set(['current', 'older']);
  const afterViewing = rows.map((row) => ({ ...row, is_new: false }));
  assert.deepEqual(
    filterTransactions(
      afterViewing,
      { ...filters, newOnly: true },
      visitIds,
    ).map(({ id }) => id),
    ['current', 'older'],
  );
  assert.deepEqual(
    filterTransactions(afterViewing, { ...filters, newOnly: true }),
    [],
  );
  assert.deepEqual(
    filterTransactions(
      afterViewing,
      {
        ...filters,
        newOnly: true,
        search: 'does not match',
      },
      visitIds,
    ),
    [],
  );
});

test('search and split categories still narrow new transactions without marking anything seen', () => {
  const transaction = {
    ...base,
    id: 'split',
    merchant_display: 'Evening Market',
    allocations: [{ category: 'Groceries', amount_minor: 1000 }],
  };
  assert.deepEqual(
    filterTransactions([transaction], {
      ...filters,
      newOnly: true,
      category: 'Groceries',
      search: ' EVENING ',
    }),
    [transaction],
  );
  assert.equal(transaction.is_new, true);
});

test('spending is totaled by currency, including negative refunds', () => {
  assert.deepEqual(
    spendingByCurrency(filterTransactions(rows, { ...filters, newOnly: true })),
    [
      ['INR', 800],
      ['USD', 500],
    ],
  );
  assert.deepEqual(spendingByCurrency([]), []);
});

test('saved history includes months older than the rolling report, preserving empty recent months', () => {
  const saved = [
    { ...base, date: '2025-09-12' },
    { ...base, date: '2026-09-26' },
    { ...base, date: '2026-09-15' },
    { ...base, date: '2025-01-01', currency: 'USD' },
  ];
  const history = transactionHistory(saved, 'INR', [
    '2026-08',
    '2026-09',
    '2026-10',
  ]);
  assert.deepEqual(history, {
    count: 3,
    firstMonth: '2025-09',
    latestMonth: '2026-09',
    months: ['2026-10', '2026-09', '2026-08', '2025-09'],
  });
  assert.equal(
    filterTransactions(saved, { ...filters, month: history.latestMonth })
      .length,
    2,
  );
  assert.equal(
    filterTransactions(saved, { ...filters, month: history.firstMonth }).length,
    1,
  );
});

test('history for an empty currency keeps current month choices without inventing saved records', () => {
  assert.deepEqual(transactionHistory(rows, 'EUR', ['2026-10']), {
    count: 0,
    firstMonth: '',
    latestMonth: '',
    months: ['2026-10'],
  });
});

test('spending groups use accounting evidence IDs including linked refunds', () => {
  assert.deepEqual(
    filterTransactions(rows, {
      ...filters,
      group: 'regular',
      groupIds: ['current', 'refund', 'older'],
    }).map((t) => t.id),
    ['current', 'refund'],
  );
  assert.deepEqual(
    filterTransactions(rows, { ...filters, group: 'fixed', groupIds: [] }),
    [],
  );
});

test('category evidence includes linked refunds with a different stored category', () => {
  const linked = [
    { ...base, id: 'purchase', category: 'Shopping' },
    {
      ...base,
      id: 'refund',
      category: 'Uncategorized',
      kind: 'refund',
      spend_minor: -200,
    },
  ];
  assert.deepEqual(
    filterTransactions(linked, {
      ...filters,
      category: 'Shopping',
      categoryIds: ['purchase', 'refund'],
    }).map((t) => t.id),
    ['purchase', 'refund'],
  );
  assert.deepEqual(
    filterTransactions(linked, { ...filters, category: 'Shopping' }).map(
      (t) => t.id,
    ),
    ['purchase'],
  );
});
