'use client';
import { useEffect, useRef } from 'react';
import { ArrowDownRight, ChevronRight } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Blank } from './common';
import { money, kindName } from '@/lib/api';
import type { Transaction, TransactionSelection } from '@/lib/types';
import { observeVisibleTransactions } from '@/lib/transaction-visibility';

export function TransactionTable({
  rows,
  onSelect,
  newOnly = false,
  onVisible,
  visibilityEnabled = true,
}: {
  rows: Transaction[];
  onSelect: (t: TransactionSelection) => void;
  newOnly?: boolean;
  onVisible: (ids: string[]) => Promise<void>;
  visibilityEnabled?: boolean;
}) {
  const body = useRef<HTMLTableSectionElement>(null);
  useEffect(() => {
    if (visibilityEnabled && body.current) {
      return observeVisibleTransactions(
        body.current.querySelectorAll<HTMLElement>('[data-new-transaction-id]'),
        onVisible,
      );
    }
  }, [rows, onVisible, visibilityEnabled]);
  if (!rows.length)
    return (
      <Blank
        title={
          newOnly
            ? 'No new transactions in this view'
            : 'No transactions in this view'
        }
        description={
          newOnly
            ? 'Transactions are marked as seen automatically when they appear on screen. Try clearing any search or category filters.'
            : 'Try another month or filter, connect Gmail, or add a missing transaction manually.'
        }
      />
    );
  return (
    <Table className="ledger-table">
      <TableHeader>
        <TableRow>
          <TableHead>Merchant / person</TableHead>
          <TableHead className="transaction-extra">Date</TableHead>
          <TableHead className="transaction-extra">Category</TableHead>
          <TableHead className="transaction-extra">Account</TableHead>
          <TableHead className="text-right transaction-extra">Amount</TableHead>
          <TableHead className="text-right">Personal spend</TableHead>
          <TableHead>
            <span className="sr-only">Open</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody ref={body}>
        {rows.map((t) => (
          <TableRow
            key={t.id}
            className={`transaction-row${t.is_new ? ' transaction-new' : ''}`}
            onClick={(event) => {
              event.currentTarget
                .querySelector('button')
                ?.focus({ preventScroll: true });
              onSelect(t);
            }}
          >
            <TableCell>
              <button
                className="merchant-cell"
                data-new-transaction-id={t.is_new ? t.id : undefined}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect(t);
                }}
              >
                <span
                  className={
                    'merchant-avatar ' +
                    (t.direction === 'credit' ? 'credit' : '')
                  }
                >
                  {t.direction === 'credit' ? (
                    <ArrowDownRight size={18} />
                  ) : (
                    (t.merchant_display || t.counterparty)
                      .slice(0, 1)
                      .toUpperCase()
                  )}
                </span>
                <span>
                  <b title={t.merchant_display || t.counterparty}>
                    {t.merchant_display || t.counterparty}
                  </b>
                  {t.merchant_display &&
                    t.merchant_display !== t.counterparty && (
                      <small>{t.counterparty}</small>
                    )}
                  <small>
                    {t.is_new && (
                      <span className="new-transaction-badge">New</span>
                    )}
                    {kindName(t.kind)}
                    {t.issues.length > 0 && (
                      <span className="review-dot"> · Review</span>
                    )}
                  </small>
                  <small className="transaction-mobile-meta">
                    {new Date(t.date + 'T12:00:00').toLocaleDateString(
                      'en-IN',
                      {
                        day: 'numeric',
                        month: 'short',
                        ...(newOnly ? { year: 'numeric' as const } : {}),
                      },
                    )}
                    {' · '}
                    {t.category}
                  </small>
                </span>
              </button>
            </TableCell>
            <TableCell className="muted transaction-extra">
              {new Date(t.date + 'T12:00:00').toLocaleDateString('en-IN', {
                day: 'numeric',
                month: 'short',
                ...(newOnly ? { year: 'numeric' as const } : {}),
              })}
            </TableCell>
            <TableCell className="transaction-extra">
              <span
                className={
                  'category-chip ' +
                  (t.category === 'Uncategorized' ? 'uncategorized' : '')
                }
              >
                {t.category}
              </span>
            </TableCell>
            <TableCell className="muted transaction-extra">
              {t.account}
            </TableCell>
            <TableCell className="amount-cell transaction-extra">
              {t.direction === 'credit' ? '+' : ''}
              {money(t.amount_minor, t.currency)}
            </TableCell>
            <TableCell
              className={'amount-cell ' + (t.spend_minor < 0 ? 'green' : '')}
            >
              {t.spend_minor ? (
                money(t.spend_minor, t.currency)
              ) : (
                <span className="muted">Excluded</span>
              )}
              {t.spend_minor !== t.amount_minor && (
                <small className="transaction-mobile-meta muted">
                  {money(t.amount_minor, t.currency)} total
                </small>
              )}
            </TableCell>
            <TableCell>
              <ChevronRight size={16} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
