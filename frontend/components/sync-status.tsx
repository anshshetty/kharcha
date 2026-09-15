'use client';
import { CheckCheck, CircleAlert, RefreshCw } from 'lucide-react';
import type { AppStatus } from '@/lib/types';
import { Button } from '@/components/ui/button';

export function SyncStatus({
  status,
  onSettings,
  onNewTransactions,
}: {
  status: AppStatus | null;
  onSettings: () => void;
  onNewTransactions: () => void;
}) {
  if (!status) return null;
  const job = status.job;
  const running = job?.state === 'running';
  const reconnect = status.connection.state === 'reconnect';
  const failed = job?.state === 'failed' || job?.state === 'interrupted';
  const complete = job?.state === 'complete';
  const connected = status.connection.state === 'connected';
  const unseen = status.new_transaction_count || 0;
  const added = job?.added || 0;
  const title = reconnect
    ? 'Gmail access expired — reconnect needed'
    : running
      ? 'Gmail sync in progress'
      : failed
        ? 'Gmail sync stopped before completion'
        : complete
          ? 'Gmail sync completed'
          : connected
            ? 'Gmail connected — waiting to sync'
            : 'Direct Gmail sync is not connected';
  const time = job?.finished_at || status.last_sync;
  const stamp = time
    ? new Date(time).toLocaleString('en-IN', {
        dateStyle: 'medium',
        timeStyle: 'short',
      })
    : '';
  if (complete && !running && !reconnect && !failed)
    return (
      <section
        className="sync-status sync-complete"
        aria-label="Gmail sync status"
      >
        <output className="sync-status-heading" aria-live="polite">
          <CheckCheck size={18} />
          <strong>
            Sync complete ·{' '}
            {added === 0
              ? 'No new transactions'
              : `${added.toLocaleString('en-IN')} transaction${added === 1 ? '' : 's'} added`}
          </strong>
        </output>
        <p>
          {stamp}
          {unseen > 0
            ? ` · ${unseen.toLocaleString('en-IN')} new transaction${unseen === 1 ? '' : 's'} waiting for you to look through.`
            : ''}
        </p>
        <div className="sync-status-actions">
          {unseen > 0 && (
            <Button onClick={onNewTransactions}>
              See new transactions ({unseen.toLocaleString('en-IN')})
            </Button>
          )}
          <Button variant="outline" onClick={onSettings}>
            Sync details
          </Button>
        </div>
      </section>
    );
  return (
    <section
      className={`sync-status ${reconnect || failed ? 'sync-attention' : running ? 'sync-running' : complete ? 'sync-complete' : ''}`}
      aria-label="Gmail sync status"
    >
      <output className="sync-status-heading" aria-live="polite">
        {reconnect || failed ? (
          <CircleAlert size={22} />
        ) : running ? (
          <RefreshCw size={22} className="spin" />
        ) : complete ? (
          <CheckCheck size={22} />
        ) : (
          <RefreshCw size={22} />
        )}
        <strong>{title}</strong>
      </output>
      {running && (
        <>
          <p>{job.phase}</p>
          <p>
            <strong>
              {job.processed.toLocaleString('en-IN')} emails screened
            </strong>{' '}
            · {job.discovered.toLocaleString('en-IN')} discovered so far
            {' · '}
            {added.toLocaleString('en-IN')} transactions added
          </p>
          <div className="sync-activity" aria-hidden="true">
            <span />
          </div>
          <small>
            Still working. More emails may be screened. You can use the app
            while it syncs; spending totals may change.
          </small>
        </>
      )}
      {reconnect && (
        <p>
          Sign in with the same Google account to resume. Your saved history is
          safe.
        </p>
      )}
      {!reconnect && failed && (
        <p>
          {job?.error ||
            'The app stopped during sync. Resume to continue from saved progress.'}
        </p>
      )}
      {!running && !reconnect && !failed && !complete && (
        <p>
          {connected
            ? 'The first sync has not completed yet.'
            : 'Previously imported records remain available. Connect Gmail for automatic updates.'}
        </p>
      )}
      <div className="sync-status-actions">
        {unseen > 0 && (
          <Button onClick={onNewTransactions}>
            See new transactions ({unseen.toLocaleString('en-IN')})
          </Button>
        )}
        <Button variant="outline" onClick={onSettings}>
          {reconnect
            ? 'Reconnect Gmail'
            : failed
              ? 'Resume / view details'
              : 'Sync details'}
        </Button>
      </div>
    </section>
  );
}
