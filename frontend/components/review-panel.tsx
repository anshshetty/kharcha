'use client';
import { useEffect, useState } from 'react';
import {
  ArrowLeft,
  CircleAlert,
  ExternalLink,
  Check,
  Search,
} from 'lucide-react';
import { api, money } from '@/lib/api';
import { useSourceEmail } from '@/hooks/use-source-email';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Blank, Picker, SectionTitle } from './common';

import type {
  Report,
  ReviewIssue,
  SourceEmail,
  TransactionSelection,
} from '@/lib/types';
type SenderSummary = {
  sender: string;
  count: number;
  review: number;
  templates: Set<string>;
};
export function ReviewPanel({
  report,
  notify,
  onError,
  onTransaction,
  onOverview,
  onClearFilters,
  sourceId,
  onSource: inspect,
  onBack,
  active,
  query,
  filter,
  onQueryChange: setQuery,
  onFilterChange: setFilter,
}: {
  sourceId: string;
  active: boolean;
  onSource: (id: string) => void;
  onBack: () => void;
  query: string;
  filter: string;
  onQueryChange: (value: string) => void;
  onFilterChange: (value: string) => void;
  report: Report | null;
  notify: (s: string) => void;
  onError: (s: string) => void;
  onTransaction: (t: TransactionSelection) => void;
  onOverview: () => void;
  onClearFilters: () => void;
}) {
  const [issues, setIssues] = useState<ReviewIssue[]>([]),
    [sources, setSources] = useState<SourceEmail[]>([]),
    [resolve, setResolve] = useState<ReviewIssue | null>(null),
    [note, setNote] = useState(''),
    [busy, setBusy] = useState(false),
    [loaded, setLoaded] = useState(false);
  const load = () =>
    Promise.all([
      api<ReviewIssue[]>('/review'),
      api<SourceEmail[]>('/sources'),
    ]).then(([nextIssues, nextSources]) => {
      setIssues(nextIssues);
      setSources(nextSources);
      setLoaded(true);
    });
  useEffect(() => {
    if (active) load().catch((e) => onError(e.message));
  }, [report, onError, active]);
  const { source, sourceError } = useSourceEmail(sourceId, onError);
  const complete = async () => {
    setBusy(true);
    try {
      await api('/review/' + resolve!.id + '/resolve', 'POST', { note });
      setResolve(null);
      setNote('');
      await load();
      notify('Review decision saved');
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const queryTerm = query.trim().toLowerCase();
  const hasFilters = !!queryTerm || filter !== 'all';
  const visible = issues.filter(
    (i) =>
      (filter === 'all' || i.kind === filter) &&
      (!queryTerm ||
        [i.message, i.subject, i.sender, i.counterparty]
          .join(' ')
          .toLowerCase()
          .includes(queryTerm)),
  );
  const senders = Object.values(
    sources.reduce((out: Record<string, SenderSummary>, s: SourceEmail) => {
      const key = s.sender || 'Unknown sender';
      out[key] ||= { sender: key, count: 0, review: 0, templates: new Set() };
      out[key].count++;
      out[key].review += s.status === 'review' ? 1 : 0;
      out[key].templates.add(s.template);
      return out;
    }, {}),
  ) as SenderSummary[];
  return (
    <>
      <div className="review-layout">
        <section className="panel">
          <SectionTitle
            title="Your review queue"
            detail="Largest known amounts first within each currency. Open a payment, check its evidence, then record your decision."
          />
          <div className="table-filters" style={{ padding: '0 0 15px' }}>
            <div className="search-input">
              <Search size={17} />
              <Input
                placeholder="Search review items…"
                aria-label="Search review items"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <Picker
              label="Review type"
              value={filter}
              onChange={setFilter}
              options={[
                { value: 'all', label: 'All review items' },
                ...Array.from(new Set(issues.map((i) => i.kind))).map((k) => ({
                  value: k,
                  label: k.replaceAll('_', ' '),
                })),
              ]}
            />
          </div>
          {visible.length ? (
            visible.slice(0, 100).map((i) => (
              <article className="review-item" key={i.id}>
                <div>
                  <CircleAlert size={18} />
                  <div>
                    <h3>{i.message}</h3>
                    {i.amount_minor != null && (
                      <p>
                        <strong>
                          {money(i.amount_minor, i.currency || 'INR')}
                        </strong>{' '}
                        · {i.counterparty} · {i.date}
                      </p>
                    )}
                    <p>{i.subject || i.sender || 'Transaction review'}</p>
                  </div>
                </div>
                <div className="settings-actions">
                  {i.transaction_id && (
                    <Button
                      variant="outline"
                      onClick={() => onTransaction({ id: i.transaction_id! })}
                    >
                      Open payment
                    </Button>
                  )}
                  {i.source_id && (
                    <Button
                      variant="ghost"
                      onClick={() => inspect(i.source_id!)}
                    >
                      Read source
                    </Button>
                  )}
                  {!i.derived && (
                    <Button variant="ghost" onClick={() => setResolve(i)}>
                      <Check size={15} />
                      Mark reviewed
                    </Button>
                  )}
                </div>
              </article>
            ))
          ) : !loaded ? (
            <output className="help-text">Loading your review queue…</output>
          ) : (
            <Blank
              title={
                hasFilters
                  ? 'No matching review items'
                  : 'No payments need review'
              }
              description={
                hasFilters
                  ? 'Try another search or clear the filters to see your queue.'
                  : 'Your current queue is clear. Return to this month’s spending; new items will appear here when they need your attention.'
              }
            >
              <Button
                variant={hasFilters ? 'outline' : 'default'}
                onClick={hasFilters ? onClearFilters : onOverview}
              >
                {hasFilters ? 'Clear review filters' : 'View monthly spending'}
              </Button>
            </Blank>
          )}
          {visible.length > 100 && (
            <p className="help-text">
              Showing 100 of {visible.length} items. Use the filter to focus
              your review.
            </p>
          )}
        </section>
        <div className="settings-stack">
          <section className="panel">
            <SectionTitle title="Import coverage" />
            <div className="issue-count">{issues.length}</div>
            <p className="help-text">
              Open review items across your history. This count is not a measure
              of how much of your real spending has been captured.
            </p>
            <div className="small-row">
              <span>Sources recorded</span>
              <b>{sources.length}</b>
            </div>
            <div className="small-row">
              <span>Awaiting interpretation</span>
              <b>{sources.filter((s) => s.status === 'review').length}</b>
            </div>
            <div className="small-row">
              <span>Intentionally excluded</span>
              <b>{sources.filter((s) => s.status === 'excluded').length}</b>
            </div>
            <p className="panel-footnote">
              Cash purchases, SMS-only alerts and unread statement attachments
              can leave gaps. A month without records is not verified zero
              spending.
            </p>
          </section>
          <section className="panel">
            <SectionTitle
              title="Discovered senders"
              detail="Templates are learned from your actual emails"
            />
            {senders.length ? (
              senders.map((s) => (
                <div className="coverage-source" key={s.sender}>
                  <b>{s.sender}</b>
                  <small>
                    {s.count} sources · {s.templates.size} formats · {s.review}{' '}
                    unparsed
                  </small>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      const first = sources.find((x) => x.sender === s.sender);
                      if (first) inspect(first.id);
                    }}
                  >
                    Inspect a sample
                  </Button>
                </div>
              ))
            ) : (
              <Blank
                title="No senders yet"
                description="Connect Gmail to discover financial email formats."
              />
            )}
          </section>
        </div>
      </div>
      <details
        className="panel transaction-panel"
        style={{ marginTop: 22, padding: 24 }}
      >
        <summary className="inventory-summary">
          Source email inventory{' '}
          <span>Inspect retained transaction evidence</span>
        </summary>
        <SectionTitle
          title="Retained sources"
          detail="Every candidate is parsed, excluded with a reason, or awaiting review"
        />
        {sources.slice(0, 80).map((s) => (
          <button
            className="small-row"
            key={s.id}
            style={{ width: '100%', textAlign: 'left' }}
            onClick={() => inspect(s.id)}
          >
            <span>
              <b>{s.subject || 'Unavailable message'}</b>
              <small>
                {s.sender} · {s.reason}
              </small>
            </span>
            <span
              className={'status-chip ' + (s.status === 'review' ? 'off' : '')}
            >
              {s.status}
            </span>
          </button>
        ))}
        {sources.length > 80 && (
          <p className="help-text">
            Showing the 80 most recent source messages.
          </p>
        )}
      </details>
      <Dialog open={!!sourceId} onOpenChange={(open) => !open && onBack()}>
        <DialogContent className="modal-wide">
          <Button variant="ghost" onClick={onBack} className="source-return">
            <ArrowLeft size={16} />
            Back to review
          </Button>
          <DialogHeader>
            <DialogTitle>{source?.subject || 'Source email'}</DialogTitle>
            <DialogDescription>
              {source?.sender} · {source?.received_at?.slice(0, 10)}
            </DialogDescription>
          </DialogHeader>
          <p className="help-text">
            {source?.reason}
            {source?.missing ? ' · Original no longer available in Gmail' : ''}
          </p>
          {sourceError ? (
            <p role="alert">{sourceError}</p>
          ) : !source ? (
            <output>Loading source email…</output>
          ) : source.body ? (
            <pre className="source-text">{source.body}</pre>
          ) : (
            <Blank
              title="Email body is not cached"
              description="This record comes from an export, is outside retention, or was unavailable. Open the original in Gmail if it still exists."
            />
          )}
          {!!source?.attachments?.length && (
            <p className="help-text">
              Attachments not parsed: {source?.attachments?.join(', ')}
            </p>
          )}
          <div className="settings-actions">
            <a
              className="inline-link"
              target="_blank"
              rel="noreferrer"
              href={
                'https://mail.google.com/mail/#all/' +
                encodeURIComponent(source?.gmail_id || '')
              }
            >
              <ExternalLink
                size={15}
                style={{ display: 'inline', marginRight: 6 }}
              />
              Open in Gmail
            </a>
            {source?.status === 'review' && (
              <Button
                onClick={() => {
                  onTransaction({
                    new: true,
                    source_id: source.id,
                    date: source.received_at?.slice(0, 10),
                  });
                }}
              >
                Add transaction from this source
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>
      <Dialog
        open={active && !!resolve}
        onOpenChange={(open) => !open && setResolve(null)}
      >
        <DialogContent className="modal-wide">
          <DialogHeader>
            <DialogTitle>Record your review</DialogTitle>
            <DialogDescription>{resolve?.message}</DialogDescription>
          </DialogHeader>
          <label className="form-label">
            What did you verify or decide?
            <Input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. Checked original email; this is a separate purchase"
            />
          </label>
          <p className="help-text">
            Marking reviewed acknowledges this issue. Edit the transaction first
            if its amount or treatment needs to change.
          </p>
          <Button disabled={busy || !note.trim()} onClick={complete}>
            Save review decision
          </Button>
        </DialogContent>
      </Dialog>
    </>
  );
}
