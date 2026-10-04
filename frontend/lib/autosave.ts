/** Serializes edits, coalesces typing, and retains failed drafts for explicit retry. */
export type SaveState = 'saved' | 'pending' | 'saving' | 'error' | 'invalid';
export function createAutosave<T, R>(
  initial: T,
  options: {
    persist: (
      value: T,
      previous: T,
      operationId: string,
    ) => Promise<{ value: T; result: R }>;
    onState: (state: SaveState, error?: string) => void;
    onSaved: (result: R, value: T) => void;
    delay?: number;
  },
) {
  let baseline = initial,
    latest = initial;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let running: Promise<boolean> | null = null;
  let failed: { value: T; previous: T; id: string } | null = null;
  let paused = false;
  const same = (a: T, b: T) => JSON.stringify(a) === JSON.stringify(b);
  const stopTimer = () => {
    clearTimeout(timer);
    timer = undefined;
  };
  const pump = async (): Promise<boolean> => {
    stopTimer();
    if (running) {
      const ok = await running;
      return ok ? pump() : false;
    }
    if (paused) return false;
    if (!failed && same(latest, baseline)) {
      options.onState('saved');
      return true;
    }
    const attempt = failed || {
      value: latest,
      previous: baseline,
      id: crypto.randomUUID().replaceAll('-', ''),
    };
    options.onState('saving');
    running = (async () => {
      try {
        const saved = await options.persist(
          attempt.value,
          attempt.previous,
          attempt.id,
        );
        baseline = saved.value;
        if (same(latest, attempt.value)) latest = saved.value;
        failed = null;
        options.onSaved(saved.result, attempt.value);
        return true;
      } catch (e) {
        failed = attempt;
        options.onState('error', (e as Error).message);
        return false;
      }
    })();
    const success = await running;
    running = null;
    if (!success) return false;
    if (paused) {
      options.onState('invalid');
      return false;
    }
    return pump();
  };
  return {
    update(value: T) {
      paused = false;
      latest = value;
      stopTimer();
      if (failed) return;
      if (!same(value, baseline)) {
        options.onState('pending');
        timer = setTimeout(() => {
          void pump();
        }, options.delay ?? 650);
      } else if (!running) options.onState('saved');
    },
    pause() {
      paused = true;
      stopTimer();
      options.onState('invalid');
    },
    flush: pump,
    retry() {
      paused = false;
      return pump();
    },
    async discard() {
      stopTimer();
      paused = true;
      if (running) await running;
      latest = baseline;
      failed = null;
      paused = false;
      options.onState('saved');
    },
    replace(value: T) {
      baseline = latest = value;
      failed = null;
      paused = false;
      stopTimer();
      options.onState('saved');
    },
    get dirty() {
      return paused || !!failed || !!running || !same(latest, baseline);
    },
    dispose() {
      stopTimer();
    },
  };
}
