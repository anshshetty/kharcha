import assert from 'node:assert/strict';
import { test } from 'node:test';
const json = (value, status = 200) =>
  new Response(JSON.stringify(value), { status });

test('refreshes a stale token once without replaying successful writes', async () => {
  const { api } = await import('../lib/api.ts?stale');
  let sessions = 0,
    writes = 0;
  const original = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    if (url === '/api/session') return json({ csrf: `token-${++sessions}` });
    writes++;
    if (writes === 1)
      return json({ error: 'Refresh the app before making changes' }, 403);
    assert.equal(init.headers['x-csrf-token'], 'token-2');
    return json({ imported: 5 });
  };
  try {
    assert.deepEqual(await api('/statements/import', 'POST', {}), {
      imported: 5,
    });
    assert.equal(sessions, 2);
    assert.equal(writes, 2);
  } finally {
    globalThis.fetch = original;
  }
});

test('does not replay a write when its response is lost', async () => {
  const { api } = await import('../lib/api.ts?network');
  let writes = 0;
  const original = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (url === '/api/session') return json({ csrf: 'token' });
    writes++;
    throw new TypeError('Failed to fetch');
  };
  try {
    await assert.rejects(
      api('/statements/import', 'POST', {}),
      /save may have completed/,
    );
    assert.equal(writes, 1);
  } finally {
    globalThis.fetch = original;
  }
});

function browser(hash = '') {
  const storage = new Map();
  const state = { marker: 'keep' };
  const location = { pathname: '/', search: '?connected=1', hash };
  return {
    location,
    history: {
      state,
      replaceState(next, _, url) {
        assert.equal(next, state);
        const parsed = new URL(url, 'http://127.0.0.1:8765');
        Object.assign(location, {
          pathname: parsed.pathname,
          search: parsed.search,
          hash: parsed.hash,
        });
      },
    },
    sessionStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
  };
}

test('launcher fragment becomes origin-scoped authorization and leaves history', async () => {
  const previousWindow = globalThis.window,
    previousFetch = globalThis.fetch;
  const credential = 'a'.repeat(43);
  globalThis.window = browser('#access_token=' + credential);
  let calls = 0;
  globalThis.fetch = async (url, init) => {
    calls++;
    assert.equal(url, '/api/status');
    assert.equal(init.headers.Authorization, 'Bearer ' + credential);
    assert.equal(init.credentials, 'omit');
    assert.equal(init.redirect, 'error');
    assert.equal(window.location.hash, '');
    assert.equal(window.location.search, '?connected=1');
    return json({ ok: true });
  };
  try {
    const { api } = await import('../lib/api.ts?access');
    assert.deepEqual(await api('/status'), { ok: true });
    // A new module instance represents a tab reload with session storage kept.
    const reloaded = await import('../lib/api.ts?access-reload');
    assert.deepEqual(await reloaded.api('/status'), { ok: true });
    assert.equal(calls, 2);
  } finally {
    globalThis.window = previousWindow;
    globalThis.fetch = previousFetch;
  }
});

test('fresh tabs and expired sessions require the launcher without replaying writes', async () => {
  const previousWindow = globalThis.window,
    previousFetch = globalThis.fetch;
  globalThis.window = browser();
  let calls = 0;
  globalThis.fetch = async (url) => {
    calls++;
    if (url === '/api/session') return json({ csrf: 'csrf' });
    return json({ error: 'unauthorized' }, 401);
  };
  try {
    const { api } = await import('../lib/api.ts?unauthorized');
    await assert.rejects(api('/status'), /unlock this browser/);
    assert.equal(calls, 0);
    window.location.hash = '#access_token=' + 'b'.repeat(43);
    await assert.rejects(
      api('/transactions', 'POST', {}),
      /unlock this browser/,
    );
    assert.equal(calls, 2);
    assert.equal(window.sessionStorage.getItem('monthlycost.access'), null);
    await assert.rejects(api('/status'), /unlock this browser/);
    assert.equal(calls, 2);
  } finally {
    globalThis.window = previousWindow;
    globalThis.fetch = previousFetch;
  }
});

test('expired access invalidates the workspace instead of leaving stale sync controls usable', async () => {
  const previousWindow = globalThis.window,
    previousFetch = globalThis.fetch;
  globalThis.window = browser('#access_token=' + 'c'.repeat(43));
  let authorized = true;
  let writes = 0;
  globalThis.fetch = async (url, init) => {
    if (init.method === 'POST') writes++;
    if (!authorized) return json({ error: 'unauthorized' }, 401);
    if (url === '/api/session') return json({ csrf: 'synthetic-csrf' });
    return json({ job: { state: 'running' } });
  };
  try {
    const { api, isLocalAccessRequired, subscribeLocalAccess } =
      await import('../lib/api.ts?expired-workspace');
    const states = [];
    const unsubscribe = subscribeLocalAccess(() =>
      states.push(isLocalAccessRequired()),
    );
    await api('/status');
    assert.equal(isLocalAccessRequired(), false);
    authorized = false;
    await assert.rejects(api('/status'), /unlock this browser/);
    assert.equal(isLocalAccessRequired(), true);
    await assert.rejects(api('/sync', 'POST', {}), /unlock this browser/);
    assert.equal(writes, 0);
    assert.deepEqual(states, [true]);
    authorized = true;
    window.location.hash = '#access_token=' + 'd'.repeat(43);
    await api('/status');
    assert.equal(isLocalAccessRequired(), false);
    assert.deepEqual(states, [true, false]);
    unsubscribe();
    authorized = false;
    await assert.rejects(api('/status'), /unlock this browser/);
    assert.deepEqual(states, [true, false]);
  } finally {
    globalThis.window = previousWindow;
    globalThis.fetch = previousFetch;
  }
});

test('phone sessions unlock normal API requests and lock after revocation', async () => {
  const previousWindow = globalThis.window,
    previousFetch = globalThis.fetch;
  globalThis.window = browser();
  window.location.hostname = '192.168.1.10';
  window.location.protocol = 'https:';
  let locked = 0;
  window.dispatchEvent = (event) => {
    assert.equal(event.type, 'kharcha-phone-locked');
    locked++;
  };
  globalThis.fetch = async () => json({ error: 'Session revoked' }, 401);
  try {
    const { api, isMobileClient, savePhoneAccess, isLocalAccessRequired } =
      await import('../lib/api.ts?phone');
    assert.equal(isMobileClient(), true);
    assert.throws(() => savePhoneAccess('invalid'), /Invalid phone session/);
    savePhoneAccess('c'.repeat(43));
    await assert.rejects(api('/status'), /Pair this phone/);
    assert.equal(locked, 1);
    assert.equal(isLocalAccessRequired(), false);
    assert.equal(window.sessionStorage.getItem('monthlycost.access'), null);
    savePhoneAccess('d'.repeat(43));
    assert.equal(isLocalAccessRequired(), false);
  } finally {
    globalThis.window = previousWindow;
    globalThis.fetch = previousFetch;
  }
});

test('pairing requests use only JSON and do not replay a failed request', async () => {
  const previousFetch = globalThis.fetch;
  let requests = 0;
  globalThis.fetch = async (url, init) => {
    requests++;
    assert.equal(url, '/api/mobile/pair');
    assert.equal(init.method, 'POST');
    assert.equal(init.credentials, 'omit');
    assert.equal(init.redirect, 'error');
    assert.equal(init.headers.Authorization, undefined);
    assert.deepEqual(JSON.parse(init.body), {
      secret: 'invitation',
      name: 'Phone',
    });
    return json({ error: 'This pairing link has expired' }, 400);
  };
  try {
    const { pairingRequest } = await import('../lib/api.ts?pairing');
    await assert.rejects(
      pairingRequest('pair', { secret: 'invitation', name: 'Phone' }),
      /expired/,
    );
    assert.equal(requests, 1);
  } finally {
    globalThis.fetch = previousFetch;
  }
});
