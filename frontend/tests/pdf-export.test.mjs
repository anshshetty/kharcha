import assert from 'node:assert/strict';
import { test } from 'node:test';

const json = (value, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
const pdf = () =>
  new Response('%PDF-1.4\nsynthetic report', {
    headers: { 'Content-Type': 'application/pdf' },
  });
const options = {
  start: '2026-08-01',
  end: '2026-09-30',
  currency: 'INR',
  category: 'Food',
  kind: 'purchase',
  search: 'demo café',
  group: 'discretionary',
  new_only: true,
  visit_ids: ['synthetic-seen-payment'],
  include_transactions: true,
};

function environment(t, phone = false) {
  const storage = new Map([['monthlycost.access', 'a'.repeat(43)]]);
  const actions = [];
  const timers = [];
  const blobs = [];
  const location = {
    pathname: '/',
    search: '',
    hash: '',
    hostname: phone ? '192.168.1.10' : '127.0.0.1',
    protocol: phone ? 'https:' : 'http:',
  };
  const browser = {
    location,
    sessionStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
    dispatchEvent: (event) => actions.push(event.type),
  };
  const anchor = {
    href: '',
    download: '',
    attached: false,
    click() {
      assert.equal(
        this.attached,
        true,
        'download anchor must be in the document',
      );
      actions.push('click');
    },
    remove() {
      this.attached = false;
      actions.push('remove');
    },
  };
  t.mock.method(globalThis, 'setTimeout', (callback, delay) => {
    timers.push({ callback, delay });
    return 1;
  });
  t.mock.method(URL, 'createObjectURL', (blob) => {
    assert.ok(blob instanceof Blob);
    blobs.push(blob);
    actions.push('create-url');
    return 'blob:synthetic-kharcha-report';
  });
  t.mock.method(URL, 'revokeObjectURL', (url) => {
    assert.equal(url, 'blob:synthetic-kharcha-report');
    actions.push('revoke');
  });
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  globalThis.window = browser;
  const mockDocument = {
    createElement(tag) {
      assert.equal(tag, 'a');
      return anchor;
    },
    body: {
      appendChild(link) {
        assert.equal(link, anchor);
        link.attached = true;
        actions.push('append');
      },
    },
  };
  globalThis.document = mockDocument;
  t.after(() => {
    globalThis.window = previousWindow;
    globalThis.document = previousDocument;
  });
  return { actions, timers, blobs, anchor, storage };
}

function assertNoDownload(env) {
  assert.deepEqual(env.actions, []);
  assert.equal(env.blobs.length, 0);
  assert.equal(env.timers.length, 0);
}

for (const phone of [false, true]) {
  test(`${phone ? 'phone' : 'desktop'} PDF export posts every selected filter with local authentication and downloads a blob`, async (t) => {
    const env = environment(t, phone);
    const { downloadSpendingPdf } = await import(`../lib/api.ts?pdf-${phone}`);
    const requests = [];
    t.mock.method(globalThis, 'fetch', async (url, init) => {
      requests.push(url);
      assert.equal(init.headers.Authorization, 'Bearer ' + 'a'.repeat(43));
      assert.equal(init.credentials, 'omit');
      assert.equal(init.redirect, 'error');
      if (url === '/api/session') {
        assert.equal(init.method, 'GET');
        return json({ csrf: 'synthetic-csrf' });
      }
      assert.equal(
        url,
        '/api/report.pdf',
        'report stays on the current origin',
      );
      assert.equal(init.method, 'POST');
      assert.equal(init.headers['x-csrf-token'], 'synthetic-csrf');
      assert.equal(init.headers['content-type'], 'application/json');
      assert.deepEqual(JSON.parse(init.body), options);
      return pdf();
    });
    await downloadSpendingPdf(options, 'kharcha-demo.pdf');
    assert.deepEqual(requests, ['/api/session', '/api/report.pdf']);
    assert.equal(env.anchor.href, 'blob:synthetic-kharcha-report');
    assert.equal(env.anchor.download, 'kharcha-demo.pdf');
    assert.equal(env.anchor.attached, false);
    assert.equal(await env.blobs[0].text(), '%PDF-1.4\nsynthetic report');
    assert.deepEqual(env.actions, ['create-url', 'append', 'click', 'remove']);
    assert.equal(env.timers.length, 1);
    assert.equal(env.timers[0].delay, 60000);
    env.timers[0].callback();
    assert.deepEqual(env.actions, [
      'create-url',
      'append',
      'click',
      'remove',
      'revoke',
    ]);
  });
}

test('PDF export propagates server validation errors without starting a download', async (t) => {
  const env = environment(t);
  const { downloadSpendingPdf } = await import('../lib/api.ts?pdf-validation');
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (url) => {
    requests.push(url);
    return url === '/api/session'
      ? json({ csrf: 'synthetic-csrf' })
      : json(
          { error: 'Report start date must be on or before the end date' },
          400,
        );
  });
  await assert.rejects(
    downloadSpendingPdf({ ...options, start: '2026-10-01' }, 'invalid.pdf'),
    /start date must be on or before the end date/,
  );
  assert.deepEqual(requests, ['/api/session', '/api/report.pdf']);
  assertNoDownload(env);
});

test('a failed session prevents the report request and propagates its message', async (t) => {
  const env = environment(t);
  const { downloadSpendingPdf } =
    await import('../lib/api.ts?pdf-session-error');
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (url) => {
    requests.push(url);
    return json({ error: 'Could not refresh the local session' }, 503);
  });
  await assert.rejects(
    downloadSpendingPdf(options, 'unavailable.pdf'),
    /Could not refresh the local session/,
  );
  assert.deepEqual(requests, ['/api/session']);
  assertNoDownload(env);
});

test('PDF export requires an unlocked browser before making any request', async (t) => {
  const env = environment(t);
  env.storage.clear();
  const { downloadSpendingPdf, isLocalAccessRequired } =
    await import('../lib/api.ts?pdf-locked');
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => {
    calls++;
    return pdf();
  });
  await assert.rejects(
    downloadSpendingPdf(options, 'locked.pdf'),
    /unlock this browser/,
  );
  assert.equal(calls, 0);
  assert.equal(isLocalAccessRequired(), true);
  assertNoDownload(env);
});

for (const phone of [false, true]) {
  test(`revoked ${phone ? 'phone' : 'desktop'} access never downloads an unauthorized report`, async (t) => {
    const env = environment(t, phone);
    const { downloadSpendingPdf, isLocalAccessRequired } = await import(
      `../lib/api.ts?pdf-revoked-${phone}`
    );
    const requests = [];
    t.mock.method(globalThis, 'fetch', async (url) => {
      requests.push(url);
      return url === '/api/session'
        ? json({ csrf: 'synthetic-csrf' })
        : json({ error: 'Session revoked' }, 401);
    });
    await assert.rejects(
      downloadSpendingPdf(options, 'revoked.pdf'),
      phone ? /Pair this phone/ : /unlock this browser/,
    );
    assert.equal(env.storage.get('monthlycost.access'), undefined);
    assert.equal(isLocalAccessRequired(), !phone);
    assert.deepEqual(env.actions, phone ? ['kharcha-phone-locked'] : []);
    assert.equal(env.blobs.length, 0);
    assert.equal(env.timers.length, 0);
    await assert.rejects(downloadSpendingPdf(options, 'still-locked.pdf'));
    assert.deepEqual(requests, ['/api/session', '/api/report.pdf']);
  });
}

test('an interrupted PDF request is reported once without retry or false download', async (t) => {
  const env = environment(t);
  const { downloadSpendingPdf } = await import('../lib/api.ts?pdf-interrupted');
  let reports = 0;
  t.mock.method(globalThis, 'fetch', async (url) => {
    if (url === '/api/session') return json({ csrf: 'synthetic-csrf' });
    reports++;
    throw new TypeError('Failed to fetch');
  });
  await assert.rejects(
    downloadSpendingPdf(options, 'interrupted.pdf'),
    /Connection to Kharcha was interrupted/,
  );
  assert.equal(reports, 1);
  assertNoDownload(env);
});

test('a PDF body read failure rejects before creating a download URL', async (t) => {
  const env = environment(t);
  const { downloadSpendingPdf } = await import('../lib/api.ts?pdf-broken-body');
  t.mock.method(globalThis, 'fetch', async (url) => {
    if (url === '/api/session') return json({ csrf: 'synthetic-csrf' });
    const response = pdf();
    response.blob = async () => {
      throw new Error('Report transfer interrupted');
    };
    return response;
  });
  await assert.rejects(
    downloadSpendingPdf(options, 'broken.pdf'),
    /Report transfer interrupted/,
  );
  assertNoDownload(env);
});

test('a malformed session cannot trigger a PDF POST with invalid CSRF', async (t) => {
  const env = environment(t);
  const { downloadSpendingPdf } =
    await import('../lib/api.ts?pdf-malformed-session');
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (url) => {
    requests.push(url);
    return url === '/api/session' ? json({}) : pdf();
  });
  await assert.rejects(
    downloadSpendingPdf(options, 'malformed.pdf'),
    /session/i,
  );
  assert.deepEqual(requests, ['/api/session']);
  assertNoDownload(env);
});

test('a non-JSON PDF error has a readable fallback and never starts a download', async (t) => {
  const env = environment(t);
  const { downloadSpendingPdf } = await import('../lib/api.ts?pdf-html-error');
  t.mock.method(globalThis, 'fetch', async (url) =>
    url === '/api/session'
      ? json({ csrf: 'synthetic-csrf' })
      : new Response('<html>Service unavailable</html>', { status: 503 }),
  );
  await assert.rejects(
    downloadSpendingPdf(options, 'unavailable.pdf'),
    /Could not.*(download|report|PDF)/i,
  );
  assertNoDownload(env);
});

test('a successful non-PDF response is rejected instead of claiming a PDF download', async (t) => {
  const env = environment(t);
  const { downloadSpendingPdf } =
    await import('../lib/api.ts?pdf-unexpected-response');
  t.mock.method(globalThis, 'fetch', async (url) =>
    url === '/api/session'
      ? json({ csrf: 'synthetic-csrf' })
      : json({ error: 'Unexpected response' }),
  );
  await assert.rejects(
    downloadSpendingPdf(options, 'not-a-pdf.pdf'),
    /PDF|report|download/i,
  );
  assertNoDownload(env);
});
