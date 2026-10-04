import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createAutosave } from '../lib/autosave.ts';
const delay = () => new Promise((resolve) => setTimeout(resolve, 5));

test('serializes requests and coalesces edits made while a save is running', async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const calls = [];
  let active = 0,
    max = 0;
  const saver = createAutosave(0, {
    delay: 100000,
    onState() {},
    onSaved() {},
    persist: async (value, previous) => {
      active++;
      max = Math.max(max, active);
      calls.push([value, previous]);
      if (value === 1) await gate;
      active--;
      return { value, result: value };
    },
  });
  saver.update(1);
  const saving = saver.flush();
  await delay();
  saver.update(2);
  saver.update(3);
  release();
  assert.equal(await saving, true);
  assert.equal(max, 1);
  assert.deepEqual(calls, [
    [1, 0],
    [3, 1],
  ]);
  assert.equal(saver.dirty, false);
  saver.dispose();
});

test('failed saves keep their operation identifier and draft for an explicit retry', async () => {
  const ids = [],
    states = [];
  let fail = true;
  const saver = createAutosave('', {
    delay: 100000,
    onState(state) {
      states.push(state);
    },
    onSaved() {},
    persist: async (value, previous, id) => {
      ids.push(id);
      if (fail) {
        fail = false;
        throw new Error('response lost');
      }
      return { value, result: value };
    },
  });
  saver.update('Groceries');
  assert.equal(await saver.flush(), false);
  assert.equal(saver.dirty, true);
  assert.equal(await saver.retry(), true);
  assert.equal(ids[0], ids[1]);
  assert.equal(saver.dirty, false);
  assert.ok(states.includes('error'));
  saver.dispose();
});

test('incomplete split input pauses saving and prevents close until completed', async () => {
  const calls = [];
  const saver = createAutosave(0, {
    delay: 100000,
    onState() {},
    onSaved() {},
    persist: async (value) => {
      calls.push(value);
      return { value, result: value };
    },
  });
  saver.update(1);
  saver.pause();
  assert.equal(await saver.flush(), false);
  assert.deepEqual(calls, []);
  saver.update(2);
  assert.equal(await saver.flush(), true);
  assert.deepEqual(calls, [2]);
  saver.dispose();
});

test('discard waits for an in-flight save and drops queued unsaved edits', async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const calls = [];
  const saver = createAutosave(0, {
    delay: 100000,
    onState() {},
    onSaved() {},
    persist: async (value) => {
      calls.push(value);
      await gate;
      return { value, result: value };
    },
  });
  saver.update(1);
  const saving = saver.flush();
  await delay();
  saver.update(2);
  const discarding = saver.discard();
  release();
  await Promise.all([saving, discarding]);
  assert.deepEqual(calls, [1]);
  assert.equal(saver.dirty, false);
  saver.dispose();
});
