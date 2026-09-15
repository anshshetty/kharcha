import type { TransactionSelection } from './types';

export const viewNames = {
  overview: 'Overview',
  transactions: 'Transactions',
  review: 'Review & coverage',
  settings: 'Connections & rules',
};
export type View = keyof typeof viewNames;
export interface NavigationState {
  view: View;
  month: string;
  currency: string;
  search: string;
  category: string;
  kind: string;
  newOnly: boolean;
  reviewSearch: string;
  reviewKind: string;
  sourceId: string;
  selected: TransactionSelection | null;
}
export const initialNavigation: NavigationState = {
  view: 'overview',
  month: '',
  currency: 'INR',
  search: '',
  category: 'all',
  kind: 'all',
  newOnly: false,
  reviewSearch: '',
  reviewKind: 'all',
  sourceId: '',
  selected: null,
};

// Hash routes work in the local static export without a server-side router.
export function readNavigation(hash: string): NavigationState {
  const [path, query = ''] = hash.replace(/^#\/?/, '').split('?');
  const params = new URLSearchParams(query);
  const tx = params.get('transaction');
  const month = params.get('month') || '';
  const currency = params.get('currency') || 'INR';
  return {
    view: Object.hasOwn(viewNames, path) ? (path as View) : 'overview',
    month: /^\d{4}-(0[1-9]|1[0-2])$/.test(month) ? month : '',
    currency: /^[A-Z]{3}$/.test(currency) ? currency : 'INR',
    search: params.get('search') || '',
    category: params.get('category') || 'all',
    kind: params.get('kind') || 'all',
    newOnly: params.get('new') === '1',
    reviewSearch: params.get('reviewSearch') || '',
    reviewKind: params.get('reviewKind') || 'all',
    sourceId: params.get('email') || '',
    selected: tx
      ? tx === 'new'
        ? {
            new: true,
            ...(params.get('source')
              ? { source_id: params.get('source')! }
              : {}),
            ...(params.get('date') ? { date: params.get('date')! } : {}),
          }
        : { id: tx }
      : null,
  };
}

export function navigationHash(state: NavigationState): string {
  const params = new URLSearchParams();
  for (const key of [
    'month',
    'currency',
    'search',
    'category',
    'kind',
    'reviewSearch',
    'reviewKind',
  ] as const) {
    if (state[key] && state[key] !== initialNavigation[key])
      params.set(key, state[key]);
  }
  if (state.sourceId) params.set('email', state.sourceId);
  if (state.newOnly) params.set('new', '1');
  if (state.selected) {
    params.set('transaction', state.selected.new ? 'new' : state.selected.id!);
    if (state.selected.source_id)
      params.set('source', state.selected.source_id);
    if (state.selected.date) params.set('date', state.selected.date);
  }
  return `#/${state.view}${params.size ? '?' + params : ''}`;
}

interface HistoryEntry {
  depth: number;
  backLabel: string;
  scrollY: number;
}
const historyKey = 'monthlyCostNavigation';

// Small browser adapter also lets regression tests exercise real navigation flows.
export function createNavigation(
  browser: Pick<Window, 'history' | 'location' | 'scrollY'>,
) {
  let current = readNavigation(browser.location.hash);
  const entry = (): HistoryEntry | undefined =>
    browser.history.state?.[historyKey];
  const write = (value: HistoryEntry, hash?: string, replace = true) => {
    browser.history[replace ? 'replaceState' : 'pushState'](
      { ...browser.history.state, [historyKey]: value },
      '',
      hash,
    );
  };
  if (!entry()) write({ depth: 0, backLabel: '', scrollY: browser.scrollY });
  return {
    get state() {
      return current;
    },
    get backLabel() {
      return entry()?.depth ? entry()!.backLabel : '';
    },
    rememberScroll() {
      const value = entry();
      if (value) write({ ...value, scrollY: browser.scrollY });
    },
    navigate(patch: Partial<NavigationState>, replace = false) {
      const next = { ...current, ...patch };
      if (navigationHash(next) === navigationHash(current)) return false;
      const previous = entry() || { depth: 0, backLabel: '', scrollY: 0 };
      const scrollY = next.view === current.view ? browser.scrollY : 0;
      write({ ...previous, scrollY: browser.scrollY });
      write(
        replace
          ? { ...previous, scrollY }
          : {
              depth: previous.depth + 1,
              backLabel: current.sourceId
                ? 'Source email'
                : current.selected
                  ? 'Transaction details'
                  : viewNames[current.view],
              scrollY,
            },
        navigationHash(next),
        replace,
      );
      current = next;
      return true;
    },
    restore() {
      current = readNavigation(browser.location.hash);
      if (!entry()) write({ depth: 0, backLabel: '', scrollY: 0 });
      return entry()?.scrollY || 0;
    },
    back() {
      if (entry()?.depth) {
        browser.history.back();
        return;
      }
      // A freshly opened detail link should return to its list, never leave the app.
      this.navigate(
        current.sourceId
          ? { sourceId: '' }
          : current.selected
            ? { selected: null }
            : { view: 'overview' },
        true,
      );
    },
  };
}
