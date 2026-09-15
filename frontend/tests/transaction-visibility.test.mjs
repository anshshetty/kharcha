import assert from 'node:assert/strict';
import { test } from 'node:test';
import { observeVisibleTransactions } from '../lib/transaction-visibility.ts';

function screen(t, ids, markSeen) {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 0 });
  const oldDocument = globalThis.document;
  const oldObserver = globalThis.IntersectionObserver;
  const doc = new EventTarget();
  doc.visibilityState = 'visible';
  let observer;
  globalThis.document = doc;
  globalThis.IntersectionObserver = class {
    elements = new Set();
    constructor(callback, options) {
      this.callback = callback;
      observer = this;
      assert.equal(options.threshold, 0.5);
    }
    observe(element) {
      this.elements.add(element);
    }
    disconnect() {
      this.elements.clear();
    }
  };
  const elements = ids.map((id) => ({ dataset: { newTransactionId: id } }));
  const stop = observeVisibleTransactions(elements, markSeen);
  t.after(() => {
    stop();
    globalThis.document = oldDocument;
    globalThis.IntersectionObserver = oldObserver;
  });
  return {
    stop,
    intersect(visible, ratio = 1) {
      observer.callback(
        elements
          .filter((e) => observer.elements.has(e))
          .map((target) => ({
            target,
            isIntersecting: visible.includes(target.dataset.newTransactionId),
            intersectionRatio: visible.includes(target.dataset.newTransactionId)
              ? ratio
              : 0,
          })),
      );
    },
    visibility(state) {
      doc.visibilityState = state;
      doc.dispatchEvent(new Event('visibilitychange'));
    },
  };
}

test('acknowledges only exposed rows, batches them, and does not repeat on the same screen', async (t) => {
  const calls = [];
  const view = screen(
    t,
    ['visible', 'also-visible', 'below-fold'],
    async (ids) => calls.push(ids),
  );
  view.intersect(['visible', 'also-visible']);
  t.mock.timers.tick(999);
  assert.deepEqual(calls, []);
  t.mock.timers.tick(1);
  await Promise.resolve();
  assert.deepEqual(calls, [['visible', 'also-visible']]);
  view.intersect(['visible', 'also-visible']);
  t.mock.timers.tick(10000);
  assert.equal(calls.length, 1);
  view.intersect(['below-fold']);
  t.mock.timers.tick(1000);
  await Promise.resolve();
  assert.deepEqual(calls[1], ['below-fold']);
});

test('offscreen rows, brief scrolls, and background tabs do not count as viewing', async (t) => {
  const calls = [];
  const view = screen(t, ['a'], async (ids) => calls.push(ids));
  view.intersect(['a'], 0.2);
  t.mock.timers.tick(2000);
  assert.equal(calls.length, 0);
  view.intersect(['a']);
  t.mock.timers.tick(500);
  view.intersect([]);
  t.mock.timers.tick(1000);
  assert.equal(calls.length, 0);
  view.intersect(['a']);
  t.mock.timers.tick(500);
  view.visibility('hidden');
  t.mock.timers.tick(10000);
  assert.equal(calls.length, 0);
  view.visibility('visible');
  view.intersect(['a']);
  t.mock.timers.tick(1000);
  await Promise.resolve();
  assert.deepEqual(calls, [['a']]);
});

test('a failed save retries without acknowledging hidden or newly arrived rows', async (t) => {
  const calls = [];
  const view = screen(t, ['a', 'not-viewed'], async (ids) => {
    calls.push(ids);
    if (calls.length === 1) throw new Error('Offline');
  });
  view.intersect(['a']);
  t.mock.timers.tick(1000);
  await Promise.resolve();
  t.mock.timers.tick(4999);
  assert.equal(calls.length, 1);
  t.mock.timers.tick(1);
  await Promise.resolve();
  assert.deepEqual(calls, [['a'], ['a']]);
  t.mock.timers.tick(10000);
  assert.equal(calls.length, 2);
});

test('pending writes are not duplicated and leaving the screen cancels further work', async (t) => {
  const calls = [];
  let finish;
  const view = screen(t, ['a', 'b'], (ids) => {
    calls.push(ids);
    return new Promise((resolve) => {
      finish = resolve;
    });
  });
  view.intersect(['a']);
  t.mock.timers.tick(1000);
  view.intersect(['a', 'b']);
  t.mock.timers.tick(500);
  view.stop();
  finish();
  await Promise.resolve();
  t.mock.timers.tick(10000);
  assert.deepEqual(calls, [['a']]);
});

test('leaving before the first exposure finishes makes no write', (t) => {
  const calls = [];
  const view = screen(t, ['a'], async (ids) => calls.push(ids));
  view.intersect(['a']);
  t.mock.timers.tick(500);
  view.stop();
  t.mock.timers.tick(1000);
  assert.deepEqual(calls, []);
});
