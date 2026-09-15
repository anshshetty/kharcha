'use client';
import { useEffect, useState } from 'react';
import { Search, ExternalLink, LoaderCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { api } from '@/lib/api';
import type { Transaction } from '@/lib/types';

type Match = {
  merchant: string | null;
  category: string | null;
  confidence: 'high' | 'medium' | 'low';
  explanation: string;
  sources: { title: string; url: string; evidence: string }[];
};
type Lookup = {
  state: 'idle' | 'running' | 'complete' | 'failed' | 'accepted' | 'dismissed';
  message: string;
  lookup_id?: string;
  result?: Match;
};

export function MerchantLookup({
  tx,
  disabled,
  onUpdated,
}: {
  tx: Transaction;
  disabled: boolean;
  onUpdated: (tx: Transaction, message: string) => void;
}) {
  const [lookup, setLookup] = useState<Lookup | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [remember, setRemember] = useState(true);
  const path =
    '/transactions/' + encodeURIComponent(tx.id) + '/merchant-lookup';
  const running = lookup?.state === 'running';

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const state = await api<Lookup>(path);
        if (!active) return;
        setLookup(state);
        if (state.state === 'running') timer = setTimeout(refresh, 2000);
      } catch (e) {
        if (active) setError((e as Error).message);
      }
    }
    void refresh();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [path, running]);

  async function run(action: 'search' | 'accept' | 'dismiss' | 'forget') {
    setBusy(true);
    setError('');
    try {
      if (action === 'accept') {
        const completed = lookup?.lookup_id
          ? lookup
          : await api<Lookup>(path, 'POST');
        const updated = await api<Transaction>(path + '/accept', 'POST', {
          lookup_id: completed.lookup_id,
          remember,
        });
        setLookup(await api<Lookup>(path));
        onUpdated(
          updated,
          'Merchant identified. Original bank description retained.',
        );
      } else if (action === 'forget') {
        const updated = await api<Transaction>(path + '/match', 'DELETE');
        setLookup(await api<Lookup>(path));
        onUpdated(
          updated,
          'Match removed here and forgotten for future imports.',
        );
      } else {
        setLookup(
          await api<Lookup>(
            path + (action === 'dismiss' ? '/dismiss' : ''),
            'POST',
          ),
        );
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const result = lookup?.result;
  const accepted = lookup?.state === 'accepted';
  return (
    <section className="merchant-lookup" aria-label="Identify merchant">
      <div className="merchant-lookup-heading">
        <h3>
          {result?.merchant ? result.merchant : 'Don’t recognize this payment?'}
        </h3>
        {result?.merchant && (
          <span className="merchant-confidence">
            {accepted
              ? 'Accepted by you'
              : result.confidence === 'high'
                ? 'Strong match'
                : 'Likely match'}
          </span>
        )}
      </div>
      <p className="merchant-original">Bank description: {tx.counterparty}</p>
      {result && (
        <>
          {result.category && (
            <p className="merchant-category">
              Suggested category: <strong>{result.category}</strong>
            </p>
          )}
          <p>{result.explanation}</p>
          {result.sources.length > 0 && (
            <ul className="merchant-sources">
              {result.sources.map((source) => (
                <li key={source.url}>
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {source.title} <ExternalLink size={12} aria-hidden="true" />
                  </a>
                  <small>{source.evidence}</small>
                </li>
              ))}
            </ul>
          )}
          {result.merchant && !accepted && (
            <>
              {tx.category !== 'Uncategorized' ||
              tx.category_source === 'manual' ||
              tx.category_source === 'rule' ? (
                <p className="help-text">
                  Your current category ({tx.category}) will stay. You can
                  change it in the form below.
                </p>
              ) : (
                <p className="help-text">
                  Accepting will categorize this payment as {result.category}.
                </p>
              )}
              <label className="merchant-remember">
                <Checkbox
                  checked={remember}
                  onCheckedChange={(v) => setRemember(!!v)}
                />{' '}
                Remember for future payments with this exact bank description
                and currency
              </label>
              <div className="merchant-actions">
                <Button
                  disabled={busy || disabled}
                  onClick={() => void run('accept')}
                >
                  Accept merchant match
                </Button>
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => void run('dismiss')}
                >
                  Not this merchant
                </Button>
              </div>
            </>
          )}
        </>
      )}
      {!result?.merchant && (
        <>
          <p className="help-text">
            Sends a sanitized merchant label and the category list to Codex for
            public web search. Payment amounts, dates, account fields,
            transaction references, notes, email bodies, and Gmail credentials
            are not sent. Uses your signed-in Codex allowance only when you
            choose to search.
          </p>
          <Button
            variant="outline"
            disabled={busy || running || disabled}
            onClick={() => void run('search')}
          >
            {running ? (
              <LoaderCircle className="spin" size={14} />
            ) : (
              <Search size={14} />
            )}
            {running ? 'Identifying merchant…' : 'Identify merchant'}
          </Button>
          {running && (
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => void run('dismiss')}
            >
              Cancel
            </Button>
          )}
        </>
      )}
      {accepted && (
        <>
          <p className="help-text">{lookup.message}</p>
          <Button
            variant="ghost"
            disabled={busy || disabled}
            onClick={() => void run('forget')}
          >
            Forget this match
          </Button>
        </>
      )}
      {disabled && (
        <p className="help-text">
          Save your edits before applying a merchant match.
        </p>
      )}
      {lookup && !result && (
        <output className="help-text">{lookup.message}</output>
      )}
      {error && (
        <p role="alert" className="merchant-error">
          {error}
        </p>
      )}
    </section>
  );
}
