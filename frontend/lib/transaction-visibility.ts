// A short exposure lets the New badge be noticed before it clears.
const EXPOSURE_MS = 1000;
const RETRY_MS = 5000;

export function observeVisibleTransactions(
  targets: Iterable<HTMLElement>,
  markSeen: (ids: string[]) => Promise<void>,
): () => void {
  if (typeof IntersectionObserver === 'undefined') return () => {};
  const elements = new Map<Element, string>();
  for (const element of targets) {
    const id = element.dataset.newTransactionId;
    if (id) elements.set(element, id);
  }
  if (!elements.size) return () => {};

  const visibleSince = new Map<Element, number>();
  const seen = new Set<string>();
  const pending = new Set<string>();
  let stopped = false;
  let retryAfter = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function schedule() {
    clearTimeout(timer);
    if (stopped || document.visibilityState !== 'visible') return;
    let next = Infinity;
    for (const [element, since] of visibleSince) {
      const id = elements.get(element)!;
      if (!seen.has(id) && !pending.has(id))
        next = Math.min(next, since + EXPOSURE_MS);
    }
    if (next !== Infinity) {
      timer = setTimeout(
        () => void acknowledge(),
        Math.max(0, next - Date.now(), retryAfter - Date.now()),
      );
    }
  }

  async function acknowledge() {
    if (stopped || document.visibilityState !== 'visible') return;
    const ids = [
      ...new Set(
        [...visibleSince]
          .filter(([element, since]) => {
            const id = elements.get(element)!;
            return (
              Date.now() - since >= EXPOSURE_MS &&
              !seen.has(id) &&
              !pending.has(id)
            );
          })
          .map(([element]) => elements.get(element)!),
      ),
    ];
    if (!ids.length) {
      schedule();
      return;
    }
    ids.forEach((id) => pending.add(id));
    schedule();
    try {
      await markSeen(ids);
      ids.forEach((id) => seen.add(id));
    } catch {
      // Leave failed acknowledgements new and retry while they remain visible.
      retryAfter = Date.now() + RETRY_MS;
    } finally {
      ids.forEach((id) => pending.delete(id));
      schedule();
    }
  }

  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!elements.has(entry.target)) continue;
        if (
          document.visibilityState === 'visible' &&
          entry.isIntersecting &&
          entry.intersectionRatio >= 0.5
        ) {
          if (!visibleSince.has(entry.target))
            visibleSince.set(entry.target, Date.now());
        } else {
          visibleSince.delete(entry.target);
        }
      }
      schedule();
    },
    { threshold: 0.5 },
  );

  function observe() {
    observer.disconnect();
    visibleSince.clear();
    schedule();
    if (document.visibilityState === 'visible') {
      elements.forEach((_id, element) => observer.observe(element));
    }
  }
  observe();
  document.addEventListener('visibilitychange', observe);
  return () => {
    stopped = true;
    clearTimeout(timer);
    observer.disconnect();
    document.removeEventListener('visibilitychange', observe);
  };
}
