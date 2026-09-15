let token = '';
const staleSession = 'Refresh the app before making changes';
const accessKey = 'monthlycost.access';
const unlockMessage =
  'Open Kharcha using Start Kharcha.command to unlock this browser.';

export function localAuthorization(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const incoming = fragment.get('access_token');
  if (incoming !== null) {
    // Remove credentials from browser history immediately, before any requests.
    fragment.delete('access_token');
    const rest = fragment.toString();
    window.history.replaceState(
      window.history.state,
      '',
      window.location.pathname +
        window.location.search +
        (rest ? '#' + rest : ''),
    );
    if (/^[A-Za-z0-9_-]{43}$/.test(incoming)) {
      window.sessionStorage.setItem(accessKey, incoming);
      token = '';
    }
  }
  const access = window.sessionStorage.getItem(accessKey);
  if (!access) throw new Error(unlockMessage);
  return { Authorization: 'Bearer ' + access };
}

async function localFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = {
    ...Object.fromEntries(new Headers(init?.headers)),
    ...localAuthorization(),
  };
  let response: Response;
  try {
    response = await fetch('/api' + path, {
      ...init,
      headers,
      credentials: 'omit',
      redirect: 'error',
    });
  } catch {
    throw new Error(
      'Connection to Kharcha was interrupted. Keep the Kharcha window running, then try again. If you were saving, check your transactions first; the save may have completed.',
    );
  }
  if (response.status === 401) {
    if (typeof window !== 'undefined')
      window.sessionStorage.removeItem(accessKey);
    token = '';
    throw new Error(unlockMessage);
  }
  return response;
}

export async function downloadTransactions(): Promise<void> {
  const response = await localFetch('/export.csv');
  if (!response.ok) throw new Error('Could not export transactions.');
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = 'monthlycost-transactions.csv';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
export async function api<T = unknown>(
  path: string,
  method = 'GET',
  body?: unknown,
): Promise<T> {
  for (let attempt = 0; attempt < 2; attempt++) {
    if (method !== 'GET' && !token) {
      const response = await localFetch('/session');
      const session = await response.json();
      if (
        !response.ok ||
        typeof session !== 'object' ||
        session === null ||
        !('csrf' in session) ||
        typeof session.csrf !== 'string'
      )
        throw new Error(
          'Could not refresh the local session. Refresh Kharcha and try again.',
        );
      token = session.csrf;
    }
    const response = await localFetch(path, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(method !== 'GET' ? { 'X-CSRF-Token': token } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const result: unknown = await response.json().catch(() => {
      throw new Error(
        'Kharcha returned an unreadable response. If you were saving, check your transactions before trying again.',
      );
    });
    const message =
      typeof result === 'object' && result !== null && 'error' in result
        ? String(result.error)
        : 'Could not complete this action';
    if (!response.ok) {
      // This specific rejection happens before any mutation. Never replay a
      // write after an uncertain network error or a general permission failure.
      if (
        method !== 'GET' &&
        response.status === 403 &&
        message === staleSession
      ) {
        token = '';
        if (attempt === 0) continue;
      }
      throw new Error(message);
    }
    return result as T;
  }
  throw new Error(staleSession);
}
export const money = (value: number = 0, currency = 'INR', compact = false) =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency,
    maximumFractionDigits: compact ? 0 : 2,
    minimumFractionDigits: 0,
    ...(compact && Math.abs(value) >= 10000000
      ? { notation: 'compact' as const }
      : {}),
  }).format((value || 0) / 100);
export const monthName = (value: string, short = false) =>
  new Date(value + '-01T12:00:00').toLocaleDateString('en-IN', {
    month: short ? 'short' : 'long',
    ...(short ? {} : { year: 'numeric' as const }),
  });
export const kindName = (kind: string) =>
  kind
    .split('_')
    .map((x) => x[0].toUpperCase() + x.slice(1))
    .join(' ');
export const kinds = [
  'purchase',
  'person_payment',
  'emi',
  'fee',
  'refund',
  'income',
  'card_repayment',
  'own_transfer',
  'investment',
  'wallet_funding',
  'cash_withdrawal',
  'financed_purchase',
  'financing_adjustment',
  'reimbursement',
  'lending',
];
