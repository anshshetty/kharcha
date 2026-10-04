import type { Transaction } from './types';
/** Only a recognized merchant is remembered by default. Other names require choosing a scope. */
export function merchantMatch(t: Transaction): boolean {
  return (
    !t.counterparty_key?.startsWith('person:') &&
    (t.merchant_recognized === true ||
      t.category_source === 'automatic' ||
      (!!t.merchant_display && t.merchant_display !== t.counterparty))
  );
}
