import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  createNavigation,
  initialNavigation,
  navigationHash,
  readNavigation,
} from '../lib/navigation.ts';

function browserAt(hash = '#/overview') {
  let position = 0;
  let entries = [{ hash, state: { framework: 'preserved' } }];
  const browser = {
    location: { hash },
    scrollY: 0,
    history: {
      get state() {
        return entries[position].state;
      },
      get length() {
        return entries.length;
      },
      pushState(state, _title, url) {
        entries = entries.slice(0, position + 1);
        entries.push({
          state: structuredClone(state),
          hash: url || browser.location.hash,
        });
        position++;
        browser.location.hash = entries[position].hash;
      },
      replaceState(state, _title, url) {
        entries[position] = {
          state: structuredClone(state),
          hash: url || browser.location.hash,
        };
        browser.location.hash = entries[position].hash;
      },
      back() {
        assert.ok(
          position > 0,
          'Must not leave the app through its return button',
        );
        browser.location.hash = entries[--position].hash;
      },
      forward() {
        browser.location.hash = entries[++position].hash;
      },
    },
  };
  return browser;
}

test('category drilldown is a single step; Back restores overview and scroll', () => {
  const browser = browserAt();
  const nav = createNavigation(browser);
  nav.navigate({ month: '2026-08' }, true);
  browser.scrollY = 940;
  nav.navigate({ view: 'transactions', category: 'Food & dining' });
  assert.equal(browser.history.length, 2);
  assert.equal(nav.backLabel, 'Overview');
  nav.back();
  assert.equal(nav.restore(), 940);
  assert.equal(nav.state.view, 'overview');
  assert.equal(nav.state.month, '2026-08');
  assert.equal(nav.state.category, 'all');
});

test('details and email each go back one level without losing filters', () => {
  const browser = browserAt(
    '#/transactions?month=2026-08&category=Shopping&search=Zara',
  );
  const nav = createNavigation(browser);
  browser.scrollY = 500;
  nav.navigate({ selected: { id: 'tx-12' } });
  nav.navigate({ sourceId: 'mail-123' });
  nav.back();
  nav.restore();
  assert.equal(nav.state.selected.id, 'tx-12');
  assert.equal(nav.state.sourceId, '');
  nav.back();
  assert.equal(nav.restore(), 500);
  assert.equal(nav.state.selected, null);
  assert.equal(nav.state.category, 'Shopping');
  assert.equal(nav.state.search, 'Zara');
  browser.history.forward();
  nav.restore();
  assert.equal(nav.state.selected.id, 'tx-12');
  browser.history.forward();
  nav.restore();
  assert.equal(nav.state.sourceId, 'mail-123');
});

test('typing replaces the current entry instead of adding a step for each letter', () => {
  const browser = browserAt();
  const nav = createNavigation(browser);
  nav.navigate({ view: 'transactions' });
  for (const search of ['Z', 'Za', 'Zar', 'Zara'])
    nav.navigate({ search }, true);
  assert.equal(browser.history.length, 2);
  nav.navigate({ view: 'settings' });
  nav.back();
  nav.restore();
  assert.equal(nav.state.search, 'Zara');
  nav.back();
  nav.restore();
  assert.equal(nav.state.view, 'overview');
});

test('clear filters is reversible and keeps the selected month and currency', () => {
  const browser = browserAt(
    '#/transactions?month=2026-07&currency=USD&category=Travel&kind=refund&search=Train',
  );
  const nav = createNavigation(browser);
  nav.navigate({ category: 'all', kind: 'all', search: '' });
  assert.equal(nav.state.month, '2026-07');
  assert.equal(nav.state.currency, 'USD');
  nav.back();
  nav.restore();
  assert.equal(nav.state.category, 'Travel');
  assert.equal(nav.state.kind, 'refund');
  assert.equal(nav.state.search, 'Train');
});

test('refresh retains the exact screen and its available back destination', () => {
  const browser = browserAt(
    '#/review?reviewSearch=Amazon&reviewKind=source_audit',
  );
  const nav = createNavigation(browser);
  nav.navigate({ selected: { id: 'tx-5' } });
  const refreshed = createNavigation(browser);
  assert.equal(refreshed.state.selected.id, 'tx-5');
  assert.equal(refreshed.backLabel, 'Review & coverage');
  refreshed.back();
  refreshed.restore();
  assert.equal(refreshed.state.reviewSearch, 'Amazon');
  assert.equal(refreshed.state.reviewKind, 'source_audit');
});

test('new transaction view survives reload and details without changing saved month or currency', () => {
  const browser = browserAt('#/transactions?month=2026-07&currency=USD');
  const nav = createNavigation(browser);
  nav.navigate({ newOnly: true });
  assert.equal(readNavigation(browser.location.hash).newOnly, true);
  nav.navigate({ selected: { id: 'tx-new' } });
  const refreshed = createNavigation(browser);
  refreshed.back();
  refreshed.restore();
  assert.equal(refreshed.state.newOnly, true);
  assert.equal(refreshed.state.selected, null);
  refreshed.back();
  refreshed.restore();
  assert.equal(refreshed.state.newOnly, false);
  assert.equal(refreshed.state.month, '2026-07');
  assert.equal(refreshed.state.currency, 'USD');
});

test('fresh detail and email links have safe in-app return destinations', () => {
  const browser = browserAt('#/transactions?transaction=tx-9&email=source-9');
  const nav = createNavigation(browser);
  nav.back();
  assert.equal(nav.state.sourceId, '');
  assert.equal(nav.state.selected.id, 'tx-9');
  nav.back();
  assert.equal(nav.state.selected, null);
  assert.equal(nav.state.view, 'transactions');
  nav.back();
  assert.equal(nav.state.view, 'overview');
  assert.equal(browser.history.length, 1);
});

test('a new branch after Back discards the old forward destination', () => {
  const browser = browserAt();
  const nav = createNavigation(browser);
  nav.navigate({ view: 'transactions' });
  nav.navigate({ view: 'settings' });
  nav.back();
  nav.restore();
  nav.navigate({ view: 'review' });
  assert.equal(browser.history.length, 3);
  assert.equal(nav.backLabel, 'Transactions');
  nav.back();
  nav.restore();
  assert.equal(nav.state.view, 'transactions');
});

test('creating a transaction from a source returns to that source', () => {
  const browser = browserAt('#/review?reviewSearch=bank');
  const nav = createNavigation(browser);
  nav.navigate({ sourceId: 'mail/a+b' });
  nav.navigate({
    selected: { new: true, source_id: 'mail/a+b', date: '2026-08-21' },
    sourceId: '',
  });
  assert.equal(nav.backLabel, 'Source email');
  assert.deepEqual(
    readNavigation(browser.location.hash).selected,
    nav.state.selected,
  );
  nav.back();
  nav.restore();
  assert.equal(nav.state.selected, null);
  assert.equal(nav.state.sourceId, 'mail/a+b');
  assert.equal(nav.state.reviewSearch, 'bank');
});

test('route encoding preserves special characters; invalid defaults are safe', () => {
  const state = {
    ...initialNavigation,
    view: 'transactions',
    search: 'A & B / café? #1',
    category: 'Food & dining',
    month: '2026-08',
  };
  assert.deepEqual(readNavigation(navigationHash(state)), state);
  assert.deepEqual(
    readNavigation('#/constructor?month=2026-99&currency=bad'),
    initialNavigation,
  );
});

test('reselecting the same destination adds no history; framework metadata survives', () => {
  const browser = browserAt();
  const nav = createNavigation(browser);
  assert.equal(nav.navigate({ view: 'overview' }), false);
  assert.equal(browser.history.length, 1);
  nav.navigate({ view: 'review' });
  assert.equal(browser.history.state.framework, 'preserved');
});

test('remembered scroll survives Back followed by Forward', () => {
  const browser = browserAt();
  const nav = createNavigation(browser);
  nav.navigate({ view: 'review' });
  browser.scrollY = 1100;
  nav.rememberScroll();
  browser.history.back();
  nav.restore();
  browser.history.forward();
  assert.equal(nav.restore(), 1100);
});
