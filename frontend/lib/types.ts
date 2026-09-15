export interface ReviewIssue {
  amount_minor?: number | null;
  currency?: string | null;
  counterparty?: string | null;
  date?: string | null;
  derived?: boolean;
  id: string;
  kind: string;
  message: string;
  transaction_id?: string | null;
  source_id?: string | null;
  subject?: string;
  sender?: string;
  status: string;
  created_at: string;
}
export interface SourceEmail {
  id: string;
  sender: string;
  subject: string;
  received_at: string | null;
  status: string;
  reason: string;
  template: string;
  parser_version: string;
  cached?: boolean;
  missing?: number;
  body?: string | null;
  attachments?: string[];
  gmail_id?: string;
}
export interface Evidence extends SourceEmail {
  observation_id: string;
  data: Record<string, unknown>;
}
export interface Allocation {
  type: string;
  amount_minor: number;
  category?: string;
}
export interface Transaction {
  id: string;
  is_new: boolean;
  sync_job_id: string | null;
  date: string;
  amount_minor: number;
  currency: string;
  direction: string;
  kind: string;
  counterparty: string;
  merchant_display?: string;
  counterparty_key?: string | null;
  identity_confirmed?: boolean;
  account: string;
  category: string;
  category_source?: 'manual' | 'rule' | 'automatic' | 'default';
  category_reason?: string;
  spend_minor: number;
  allocations?: Allocation[];
  excluded?: boolean;
  avoidable?: boolean;
  linked_to?: string | null;
  cash_source_id?: string | null;
  notes?: string;
  statement_details?: {
    description: string;
    date: string;
    filename: string;
    reference: string;
  };
  reference?: string | null;
  issues: ReviewIssue[];
  sources?: Evidence[];
  audit?: { action: string; created_at: string }[];
  merged_children?: string[];
}
export interface TransactionSelection {
  id?: string;
  new?: boolean;
  source_id?: string;
  date?: string;
}
export interface TransactionForm {
  date: string;
  direction: string;
  kind: string;
  currency: string;
  counterparty: string;
  account: string;
  category: string;
  excluded: boolean;
  avoidable: boolean;
  linked_to: string | null;
  cash_source_id: string | null;
  notes: string;
}
export interface CategoryRule {
  id: string;
  scope: string;
  counterparty_key?: string;
  merchant: string;
  amount_minor: number;
  currency: string;
  direction: string;
  category: string;
}
export interface RulePreview {
  rule: CategoryRule;
  matches: Transaction[];
  scope: string;
}
export interface Account {
  name: string;
  owned: number;
}
export interface AppStatus {
  configured: boolean;
  ai_advisor_enabled: boolean;
  ai_advisor_consent_version: number;
  connection: { state: string; email?: string };
  last_sync?: string;
  new_transaction_count: number;
  job?: {
    state: string;
    phase: string;
    started_at?: string;
    finished_at?: string;
    processed: number;
    discovered: number;
    added: number;
    error?: string;
  };
  local_export_available: boolean;
  data_directory: string;
}
export interface Totals {
  spend_minor: number;
  gross_minor: number;
  refund_minor: number;
  emi_minor: number;
  people_minor: number;
  movement_minor: number;
  count: number;
  review_count: number;
  review_amount_minor: number;
  source_review_count?: number;
  provisional?: boolean;
}
export interface MonthTotals extends Totals {
  month: string;
  current: boolean;
}
export interface NamedAmount {
  name: string;
  amount_minor: number;
}
export interface Report {
  month: string;
  currency: string;
  months: MonthTotals[];
  totals: Totals;
  previous_totals: Totals;
  categories: NamedAmount[];
  merchants: NamedAmount[];
  recurring: {
    name: string;
    amount_minor: number;
    count: number;
    last_date: string;
  }[];
  growing: { category: string; change_minor: number }[];
  avoidable_minor: number;
  coverage: { open_issues: number; last_sync?: string; scope: string };
  currencies: string[];
}
export type CategoryChoices = {
  fixed_categories: string[];
  unavoidable_categories: string[];
  project_categories: string[];
};
