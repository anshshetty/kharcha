'use client';

import { useEffect, useState } from 'react';
import Image from 'next/image';
import QRCode from 'qrcode';
import { api, downloadLocalFile } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { SectionTitle } from '@/components/common';

type Status = {
  enabled: boolean;
  ready: boolean;
  url: string | null;
  error: string | null;
  fingerprint: string | null;
  pending: { id: string; name: string; code: string }[];
  devices: { id: string; name: string }[];
};

export function MobileAccess({ active }: { active: boolean }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [invite, setInvite] = useState('');
  const [qr, setQr] = useState('');
  const [expires, setExpires] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    const load = () =>
      api<Status>('/mobile/status')
        .then((value) => {
          if (!stopped) {
            setStatus(value);
            if (!value.ready || value.pending.length) {
              setInvite('');
              setQr('');
            }
          }
        })
        .catch((failure: Error) => {
          if (!stopped) setError(failure.message);
        });
    void load();
    const timer = setInterval(() => {
      void load();
      if (expires && Date.now() >= expires) {
        setInvite('');
        setQr('');
      }
    }, 2000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [active, expires]);
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await action();
      setStatus(await api<Status>('/mobile/status'));
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel mobile-access-panel">
      <SectionTitle
        title="Mobile access"
        detail="Open Kharcha from your phone on the same Wi-Fi"
      />
      <label className="settings-actions">
        <Switch
          checked={status?.enabled ?? true}
          disabled={busy || !status}
          onCheckedChange={(enabled) => {
            void run(async () => {
              await api('/mobile/preferences', 'PUT', { enabled });
              setInvite('');
              setQr('');
            });
          }}
        />{' '}
        Allow access over Wi-Fi
      </label>
      <p className="help-text">
        On by default. Phones need your approval before seeing any spending
        data. Turning this off disconnects all paired phones.
      </p>
      {status?.error && status.enabled && (
        <p className="notice error">{status.error}</p>
      )}
      {status?.enabled && !status.ready && !status.error && (
        <p className="help-text">Preparing the local HTTPS connection…</p>
      )}
      {status?.ready && (
        <>
          <p className="help-text">
            Before pairing for the first time, install the certificate using the
            setup steps below.
          </p>
          <p className="mobile-address">
            <b>Phone address</b>
            <br />
            {status.url}
          </p>
          <details>
            <summary>First-time certificate setup</summary>
            <ol className="mobile-setup">
              <li>
                Download the Kharcha certificate below on this Mac. AirDrop the
                file to your phone.
              </li>
              <li>
                On iPhone, install it under Settings → General → VPN &amp;
                Device Management. Then enable Kharcha under General → About →
                Certificate Trust Settings. On Android, install it as a CA
                certificate in your security settings.
              </li>
              <li>
                Return here, choose Pair a phone, and scan the QR code. Compare
                the approval code on both devices.
              </li>
            </ol>
            <p className="help-text">
              Trust only the certificate transferred from this Mac. Remove it
              from your phone if you stop using mobile access.
            </p>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() =>
                void run(() =>
                  downloadLocalFile(
                    '/mobile/certificate',
                    'Kharcha-Mobile.cer',
                  ),
                )
              }
            >
              Download certificate
            </Button>
            {status.fingerprint && (
              <p className="certificate-fingerprint">
                Certificate SHA-256:{' '}
                {status.fingerprint.match(/.{1,2}/g)?.join(':')}
              </p>
            )}
          </details>
          <div className="settings-actions">
            <Button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const value = await api<{ url: string; expires_in: number }>(
                    '/mobile/invite',
                    'POST',
                    {},
                  );
                  const pixels = await QRCode.toDataURL(value.url, {
                    width: 260,
                    margin: 3,
                    errorCorrectionLevel: 'M',
                  });
                  setInvite(value.url);
                  setQr(pixels);
                  setExpires(Date.now() + value.expires_in * 1000);
                })
              }
            >
              Pair a phone
            </Button>
          </div>
          {invite && qr && (
            <div className="mobile-pairing">
              {/* A locally generated QR contains only a short-lived pairing invitation. */}
              <Image
                unoptimized
                src={qr}
                alt="Scan this QR code on your phone to request access"
                width={260}
                height={260}
              />
              <p className="help-text">
                Scan with your phone’s camera. This link works once and expires
                after five minutes.
              </p>
              <Button
                variant="outline"
                onClick={() =>
                  void run(async () => {
                    await navigator.clipboard.writeText(invite);
                  })
                }
              >
                Copy pairing link
              </Button>
            </div>
          )}
        </>
      )}
      {status?.pending.map((phone) => (
        <div className="mobile-device" key={phone.id}>
          <p>
            <b>{phone.name}</b>
            <br />
            Approval code:{' '}
            <strong className="pairing-code-inline">{phone.code}</strong>
          </p>
          <p className="help-text">
            Approve only if this code matches the one on your phone.
          </p>
          <div className="settings-actions">
            <Button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api('/mobile/approve', 'POST', { id: phone.id });
                })
              }
            >
              Approve phone
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api('/mobile/revoke', 'POST', { id: phone.id });
                })
              }
            >
              Reject
            </Button>
          </div>
        </div>
      ))}
      {status?.devices.map((phone) => (
        <div className="small-row" key={phone.id}>
          <span>
            <b>{phone.name}</b>
            <small>
              Access lasts until Kharcha restarts or you disconnect it
            </small>
          </span>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                await api('/mobile/revoke', 'POST', { id: phone.id });
              })
            }
          >
            Disconnect
          </Button>
        </div>
      ))}
      <p className="help-text">
        Keep this Mac awake with Kharcha running. If its address changes, scan a
        new pairing link. Guest Wi-Fi and firewalls may block connections
        between devices.
      </p>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
