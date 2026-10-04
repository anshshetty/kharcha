'use client';

import { useState } from 'react';
import { Download } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { downloadSpendingPdf, kindName } from '@/lib/api';
import type { Transaction } from '@/lib/types';

type Filters = {
  category: string;
  kind: string;
  search: string;
  group: string;
  new_only: boolean;
  visit_ids: string[];
};

export function PdfExport({
  month,
  currency,
  rows,
  filters,
}: {
  month: string;
  currency: string;
  rows: Transaction[];
  filters: Filters;
}) {
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [includeTransactions, setIncludeTransactions] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const labels = [
    filters.new_only
      ? 'New transactions · all currencies, separate totals'
      : currency,
  ];
  if (filters.category !== 'all') labels.push('Category: ' + filters.category);
  if (filters.kind !== 'all') labels.push('Type: ' + kindName(filters.kind));
  if (filters.search.trim()) labels.push('Search: ' + filters.search.trim());
  if (filters.group) labels.push('Spending group: ' + filters.group);

  function show() {
    const lastDay = new Date(
      Number(month.slice(0, 4)),
      Number(month.slice(5, 7)),
      0,
    ).getDate();
    const newRows = rows.filter(
      (row) => row.is_new || filters.visit_ids.includes(row.id),
    );
    const dates = newRows.map((row) => row.date).sort();
    setStart(filters.new_only && dates.length ? dates[0] : month + '-01');
    setEnd(
      filters.new_only && dates.length
        ? dates[dates.length - 1]
        : month + '-' + String(lastDay).padStart(2, '0'),
    );
    setError('');
    setSaved(false);
    setOpen(true);
  }

  async function download() {
    if (!start || !end || start > end) {
      setError('Choose a start date on or before the end date.');
      return;
    }
    setBusy(true);
    setError('');
    setSaved(false);
    try {
      await downloadSpendingPdf(
        {
          start,
          end,
          currency,
          ...filters,
          include_transactions: includeTransactions,
        },
        `kharcha-${start}-to-${end}.pdf`,
      );
      setSaved(true);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button variant="outline" onClick={show}>
        <Download size={16} />
        Export PDF
      </Button>
      <Dialog
        open={open}
        onOpenChange={(value) => {
          if (!busy) setOpen(value);
        }}
      >
        <DialogContent className="pdf-export-dialog">
          <DialogHeader>
            <DialogTitle>Export spending report</DialogTitle>
            <DialogDescription>
              Choose inclusive report dates. The active filters below apply
              across this range.
            </DialogDescription>
          </DialogHeader>
          <div className="pdf-date-range">
            <label>
              From
              <Input
                type="date"
                value={start}
                onChange={(event) => {
                  setStart(event.target.value);
                  setSaved(false);
                }}
                disabled={busy}
                required
              />
            </label>
            <label>
              To
              <Input
                type="date"
                value={end}
                onChange={(event) => {
                  setEnd(event.target.value);
                  setSaved(false);
                }}
                disabled={busy}
                required
              />
            </label>
          </div>
          <p className="pdf-filter-scope">
            Applied filters: {labels.join(' · ')}.
          </p>
          {filters.new_only && (
            <p className="help-text">
              Includes New payments viewed during this visit. Currencies are
              reported separately without conversion.
            </p>
          )}
          {(filters.category !== 'all' || filters.group) && (
            <p className="help-text">
              Filters select matching payments. Totals include each payment’s
              full personal share, including other split categories.
            </p>
          )}
          <label className="pdf-transaction-option">
            <input
              type="checkbox"
              checked={includeTransactions}
              onChange={(event) => {
                setIncludeTransactions(event.target.checked);
                setSaved(false);
              }}
              disabled={busy}
            />
            Include transaction list
          </label>
          <p className="help-text">
            Includes total personal spending, category and payment-account
            breakdowns, and charts. Generated locally on your Mac. The
            downloaded file contains financial data.
          </p>
          {error && (
            <p role="alert" className="text-destructive">
              {error}
            </p>
          )}
          {saved && (
            <output>
              PDF download started. On a phone, use the browser’s download or
              save controls.
            </output>
          )}
          <DialogFooter>
            <Button disabled={busy} onClick={() => void download()}>
              <Download size={16} />
              {busy ? 'Preparing PDF…' : 'Download PDF'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
