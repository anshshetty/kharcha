'use client';
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { api } from '@/lib/api';

export function AIConsent({
  open,
  busy,
  error,
  enabled,
  onClose,
  onEnable,
}: {
  open: boolean;
  busy: boolean;
  error: string;
  enabled: boolean;
  onClose: () => void;
  onEnable: () => void;
}) {
  const [preview, setPreview] = useState('');
  const [previewError, setPreviewError] = useState('');
  useEffect(() => {
    if (!open) return;
    let active = true;
    void api('/ai-advisor/preview?currency=INR').then(
      (data) => active && setPreview(JSON.stringify(data, null, 2)),
      (e: Error) => active && setPreviewError(e.message),
    );
    return () => {
      active = false;
    };
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={(value) => !value && !busy && onClose()}>
      <DialogContent
        className="modal-wide"
        style={{ maxHeight: '85vh', overflowY: 'auto' }}
      >
        <DialogHeader>
          <DialogTitle>Allow AI insights to send spending data?</DialogTitle>
          <DialogDescription>
            AI insights are optional and off by default. Enabling them sends the
            following data to OpenAI servers through your signed-in Codex
            account. This analysis does not run locally.
          </DialogDescription>
        </DialogHeader>
        <ul className="privacy-list">
          <li>
            Selected-month regular expenses: dates, amounts, currency, merchant
            and payee names, categories, transaction types, internal IDs, notes,
            corrections, and review warnings.
          </li>
          <li>
            Spending totals and category summaries, including fixed and
            unavoidable totals, merchant breakdowns, repeated purchases, prior
            merchant payment counts, and record coverage.
          </li>
          <li>
            Your saved financial context: priorities, commitments, people,
            notes, spending target, currency, and category preferences.
          </li>
        </ul>
        <p className="help-text">
          Raw email bodies, statement PDFs, Gmail credentials, and the separate
          bank-account and payment-reference fields are not included. Names and
          free-text notes may still contain personal information you entered.
        </p>
        <p className="help-text">
          While enabled, the app can send updated data for automatic reviews up
          to once a day after Gmail sync for the current month, plus reviews you
          refresh manually for the selected month. It uses your Codex allowance.
          Turning it off cancels active work and prevents future reviews; it
          cannot retract data already sent. Restoring a backup requires consent
          again.
        </p>
        <details>
          <summary>Preview current INR data — generated locally</summary>
          <p className="help-text">
            This is the structured input for an INR review right now. Future
            reviews use updated data; another month or currency uses its own
            records. Previewing sends nothing to OpenAI.
          </p>
          {previewError ? (
            <p role="alert">{previewError}</p>
          ) : (
            <pre style={{ maxHeight: 220, overflow: 'auto', fontSize: 12 }}>
              {preview || 'Loading local preview…'}
            </pre>
          )}
        </details>
        {error && (
          <p role="alert" className="notice error">
            {error}
          </p>
        )}
        <div className="settings-actions">
          <Button variant="outline" disabled={busy} onClick={onClose}>
            {enabled ? 'Close' : 'Keep AI off'}
          </Button>
          {!enabled && (
            <Button disabled={busy} onClick={onEnable}>
              Allow sending data &amp; enable AI
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
