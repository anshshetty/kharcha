'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { isMobileClient, pairingRequest, savePhoneAccess } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

type Pending = { id: string; poll_token: string; code: string };

export function PhoneAccess({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<'loading' | 'open' | 'pair'>('loading');
  const [secret, setSecret] = useState('');
  const [name, setName] = useState('My phone');
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const initialize = setTimeout(() => {
      if (!isMobileClient()) {
        setMode('open');
        return;
      }
      const fragment = new URLSearchParams(window.location.hash.slice(1));
      const incoming = fragment.get('pair');
      if (incoming !== null) {
        fragment.delete('pair');
        const rest = fragment.toString();
        window.history.replaceState(
          window.history.state,
          '',
          window.location.pathname +
            window.location.search +
            (rest ? '#' + rest : ''),
        );
        window.sessionStorage.removeItem('monthlycost.pairing');
        if (/^[A-Za-z0-9_-]{43}$/.test(incoming)) {
          window.sessionStorage.removeItem('monthlycost.access');
          window.sessionStorage.setItem('monthlycost.pair-secret', incoming);
        }
      }
      setSecret(window.sessionStorage.getItem('monthlycost.pair-secret') || '');
      try {
        const saved = window.sessionStorage.getItem('monthlycost.pairing');
        if (saved) setPending(JSON.parse(saved) as Pending);
      } catch {
        window.sessionStorage.removeItem('monthlycost.pairing');
      }
      setMode(
        window.sessionStorage.getItem('monthlycost.access') ? 'open' : 'pair',
      );
    }, 0);
    const lock = () => {
      setMode('pair');
      setError('This phone session ended. Scan a new pairing link on the Mac.');
    };
    window.addEventListener('kharcha-phone-locked', lock);
    return () => {
      clearTimeout(initialize);
      window.removeEventListener('kharcha-phone-locked', lock);
    };
  }, []);

  useEffect(() => {
    if (!pending || mode !== 'pair') return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await pairingRequest<{ state: string; token?: string }>(
          'pair-status',
          pending,
        );
        if (stopped) return;
        if (result.state === 'approved' && result.token) {
          savePhoneAccess(result.token);
          window.sessionStorage.removeItem('monthlycost.pairing');
          window.sessionStorage.removeItem('monthlycost.pair-secret');
          setPending(null);
          setSecret('');
          setError('');
          setMode('open');
          return;
        }
        setError('');
        timer = setTimeout(poll, 2000);
      } catch (failure) {
        if (stopped) return;
        setError((failure as Error).message);
        if (failure instanceof TypeError) timer = setTimeout(poll, 3000);
        else {
          window.sessionStorage.removeItem('monthlycost.pairing');
          setPending(null);
          setSecret('');
        }
      }
    };
    timer = setTimeout(poll, 500);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [pending, mode]);

  if (mode === 'open') return children;
  if (mode === 'loading')
    return (
      <main className="phone-access">
        <p>Opening Kharcha…</p>
      </main>
    );
  return (
    <main className="phone-access">
      <section className="panel">
        <h1>Kharcha on your phone</h1>
        <p className="help-text">
          Keep your Mac awake with Kharcha running, and connect both devices to
          the same Wi-Fi.
        </p>
        {pending ? (
          <>
            <h2>Approve this phone on the Mac</h2>
            <p>Check that the Mac shows this same code before approving:</p>
            <p className="pairing-code">{pending.code}</p>
            <p className="help-text">
              Waiting for approval. This request expires after five minutes.
            </p>
          </>
        ) : secret ? (
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              setError('');
              try {
                const request = await pairingRequest<Pending>('pair', {
                  secret,
                  name,
                });
                window.sessionStorage.setItem(
                  'monthlycost.pairing',
                  JSON.stringify(request),
                );
                window.sessionStorage.removeItem('monthlycost.pair-secret');
                setPending(request);
                setSecret('');
              } catch (failure) {
                setError((failure as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <label className="form-label">
              Device name
              <Input
                value={name}
                maxLength={60}
                onChange={(event) => setName(event.target.value)}
                autoComplete="off"
              />
            </label>
            <Button disabled={busy || !name.trim()} type="submit">
              {busy ? 'Requesting…' : 'Request access'}
            </Button>
          </form>
        ) : (
          <p>
            On the Mac, open <b>Settings → Mobile access</b>, choose{' '}
            <b>Pair a phone</b>, and scan its QR code.
          </p>
        )}
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}
      </section>
    </main>
  );
}
