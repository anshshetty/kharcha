'use client';
import Image from 'next/image';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  X,
  LayoutDashboard,
  ArrowLeftRight,
  ScanLine,
  Settings2,
  RefreshCw,
  ShieldCheck,
  Plus,
  ChevronRight,
  Search,
  Mail,
  CheckCheck,
  Download,
  CircleAlert,
} from 'lucide-react';
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from '@/components/ui/sidebar';
import { Button } from '@/components/ui/button';
import { SyncStatus } from '@/components/sync-status';
import { FinancialPlan } from '@/components/financial-plan';
import { AIReview } from '@/components/ai-review';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  api,
  downloadTransactions,
  money,
  monthName,
  kindName,
  kinds,
} from '@/lib/api';
import { Picker } from '@/components/common';
import { TransactionTable } from '@/components/transaction-table';
import { LedgerOverview } from '@/components/ledger-overview';
import { SettingsPanel } from '@/components/settings-panel';
import { ReviewPanel } from '@/components/review-panel';
import { TransactionDetail } from '@/components/transaction-detail';
import { useNavigation } from '@/hooks/use-navigation';
import { viewNames, type View } from '@/lib/navigation';
import {
  filterTransactions,
  newTransactionIds,
  spendingByCurrency,
} from '@/lib/transaction-filters';

import type {
  AppStatus,
  Report,
  Transaction,
  TransactionSelection,
  MonthTotals,
} from '@/lib/types';
const navigation = [
  { id: 'overview', name: 'Overview', icon: LayoutDashboard },
  { id: 'transactions', name: 'Transactions', icon: ArrowLeftRight },
  { id: 'review', name: 'Review & coverage', icon: ScanLine },
  { id: 'settings', name: 'Connections & rules', icon: Settings2 },
] as const;

export default function Home() {
  return (
    <SidebarProvider
      style={{ '--sidebar-width': '244px' } as React.CSSProperties}
    >
      <Workspace />
    </SidebarProvider>
  );
}

function Workspace() {
  const {
    view,
    month,
    currency,
    search,
    category,
    kind,
    newOnly,
    selected,
    reviewSearch,
    reviewKind,
    sourceId,
    ready,
    navigate,
    back,
    backLabel,
  } = useNavigation();
  const { setOpenMobile } = useSidebar();
  const heading = useRef<HTMLHeadingElement>(null);
  const previousView = useRef(view);
  const opener = useRef<HTMLElement | null>(null);
  const hadSelection = useRef(false);
  const requestVersion = useRef(0);
  const [visitedViews, setVisitedViews] = useState<View[]>([]);
  if (!visitedViews.includes(view)) setVisitedViews([...visitedViews, view]);
  const invalidateRequests = useCallback(() => {
    requestVersion.current++;
  }, []);
  useEffect(() => {
    if (previousView.current !== view)
      heading.current?.focus({ preventScroll: true });
    previousView.current = view;
    if (hadSelection.current && !selected)
      opener.current?.focus({ preventScroll: true });
    hadSelection.current = !!selected;
  }, [view, selected]);
  const setView = (next: View) => {
    setOpenMobile(false);
    navigate({ view: next, selected: null, sourceId: '' });
  };
  const setMonth = (value: string) => navigate({ month: value });
  const setCurrency = (value: string) => navigate({ currency: value });
  const setCategory = (value: string) => navigate({ category: value });
  const setKind = (value: string) => navigate({ kind: value });
  const setSearch = (value: string) => navigate({ search: value }, true);
  const setSelected = (value: TransactionSelection) => {
    opener.current = document.activeElement as HTMLElement;
    navigate({ selected: value, sourceId: '' });
  };
  const hasFilters = category !== 'all' || kind !== 'all' || !!search;
  const clearFilters = () =>
    navigate({ category: 'all', kind: 'all', search: '' });
  const [status, setStatus] = useState<AppStatus | null>(null),
    [report, setReport] = useState<Report | null>(null),
    [rows, setRows] = useState<Transaction[]>([]),
    [categories, setCategories] = useState<string[]>([]);
  const [message, setMessage] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const [newVisit, setNewVisit] = useState({
    active: false,
    ids: new Set<string>(),
  });
  const viewingNew = view === 'transactions' && newOnly;
  const visitAdditions = viewingNew
    ? newTransactionIds(rows).filter((id) => !newVisit.ids.has(id))
    : [];
  if (viewingNew !== newVisit.active || visitAdditions.length > 0) {
    setNewVisit({
      active: viewingNew,
      ids: viewingNew
        ? new Set([...newVisit.ids, ...visitAdditions])
        : new Set(),
    });
  }
  const refresh = useCallback(async () => {
    if (!ready) return;
    const version = ++requestVersion.current;
    try {
      const [s, r, t, c] = await Promise.all([
        api<AppStatus>('/status'),
        api<Report>(
          '/report?' +
            new URLSearchParams({ ...(month ? { month } : {}), currency }),
        ),
        api<Transaction[]>('/transactions'),
        api<string[]>('/categories'),
      ]);
      if (version !== requestVersion.current) return;
      setStatus(s);
      setReport(r);
      setRows(t);
      setCategories(c);
      if (!month && r.month) navigate({ month: r.month }, true);
    } catch (e) {
      if (version === requestVersion.current) setError((e as Error).message);
    }
  }, [month, currency, ready, navigate]);
  useEffect(() => {
    const initial = setTimeout(() => {
      void refresh();
    }, 0);
    const timer = setInterval(refresh, 10000);
    return () => {
      invalidateRequests();
      clearTimeout(initial);
      clearInterval(timer);
    };
  }, [refresh, invalidateRequests]);
  const action = async (fn: () => Promise<unknown>, success?: string) => {
    setBusy(true);
    setError('');
    try {
      const result = await fn();
      if (success) setMessage(success);
      await refresh();
      return result;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const notify = (value: string) => {
    setMessage(value);
    void refresh();
  };
  const drill = (cat = 'all', type = 'all', q = '') => {
    navigate({
      view: 'transactions',
      category: cat,
      kind: type,
      newOnly: false,
      search: q,
      selected: null,
      sourceId: '',
    });
  };
  const showNewTransactions = () => {
    setOpenMobile(false);
    navigate({
      view: 'transactions',
      newOnly: true,
      category: 'all',
      kind: 'all',
      search: '',
      selected: null,
      sourceId: '',
    });
  };
  const filtered = filterTransactions(
    rows,
    {
      month,
      currency,
      category: view === 'overview' ? 'all' : category,
      kind: view === 'overview' ? 'all' : kind,
      search: view === 'overview' ? '' : search,
      newOnly: viewingNew,
    },
    newVisit.ids,
  );
  const newCount = newTransactionIds(rows).length;
  const shownSpending = spendingByCurrency(filtered);
  const markVisibleAsSeen = useCallback(
    async (ids: string[]) => {
      await api('/sync/seen', 'POST', {
        transaction_ids: ids,
      });
      await refresh();
    },
    [refresh],
  );
  const running = status?.job?.state === 'running';
  const connected = status?.connection?.state === 'connected';
  return (
    <>
      <a
        href="#main-content"
        className="skip-link"
        onClick={(event) => {
          event.preventDefault();
          heading.current?.focus();
          heading.current?.scrollIntoView({ block: 'start' });
        }}
      >
        Skip to content
      </a>
      <Sidebar className="app-sidebar">
        <SidebarHeader>
          <div className="brand">
            <span className="brand-icon">
              <Image
                src="/kharcha-icon.png"
                alt=""
                width={42}
                height={42}
                unoptimized
              />
            </span>
            <span>
              Kharcha<span className="brand-sub">PERSONAL SPENDING</span>
            </span>
          </div>
        </SidebarHeader>
        <SidebarContent>
          <div className="nav-label">YOUR MONEY</div>
          <SidebarMenu>
            {navigation.map((item) => (
              <SidebarMenuItem key={item.id}>
                <SidebarMenuButton
                  isActive={view === item.id}
                  aria-current={view === item.id ? 'page' : undefined}
                  onClick={() => setView(item.id)}
                  className="nav-item"
                >
                  <item.icon size={18} />
                  <span>{item.name}</span>
                  {item.id === 'transactions' && newCount > 0 && (
                    <span
                      className="nav-count"
                      aria-label={`${newCount} new transactions`}
                    >
                      {newCount} new
                    </span>
                  )}
                  {item.id === 'review' &&
                    (report?.coverage.open_issues || 0) > 0 && (
                      <span className="nav-count">
                        {report?.coverage.open_issues}
                      </span>
                    )}
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
          <div className="sidebar-note">
            <ShieldCheck size={21} />
            <h3>At home on your Mac.</h3>
            <p>Your ledger is stored on this Mac. Gmail access is read-only.</p>
            <span>
              <i />
              LOCAL STORAGE
            </span>
          </div>
        </SidebarContent>
        <SidebarFooter>
          <button
            className="connection-footer"
            onClick={() => setView('settings')}
          >
            <span className="avatar">
              {connected ? status.connection.email?.[0]?.toUpperCase() : 'M'}
            </span>
            <span>
              <b>{connected ? 'Gmail connected' : 'Connect your Gmail'}</b>
              <small>
                {connected
                  ? status.connection.email
                  : 'Bring your spending into view'}
              </small>
            </span>
            <ChevronRight size={16} />
          </button>
        </SidebarFooter>
      </Sidebar>
      <SidebarInset className="workspace">
        <header className="topbar">
          <div>
            <SidebarTrigger className="mobile-toggle" />
            <nav className="breadcrumbs" aria-label="Breadcrumb">
              {view === 'overview' ? (
                <span aria-current="page">Overview</span>
              ) : (
                <>
                  <button onClick={() => setView('overview')}>Overview</button>
                  <ChevronRight size={14} aria-hidden="true" />
                  <span aria-current="page">{viewNames[view]}</span>
                </>
              )}
            </nav>
            <span className="local-pill">
              <i />
              Stored on this Mac
            </span>
          </div>
          <div>
            <span className="last-sync">
              {running
                ? status?.job?.phase
                : status?.last_sync
                  ? 'Synced ' +
                    new Date(status.last_sync).toLocaleTimeString('en-IN', {
                      hour: '2-digit',
                      minute: '2-digit',
                    })
                  : 'No Gmail sync yet'}
            </span>
            <Button
              variant="outline"
              disabled={busy || running}
              onClick={() => {
                if (!connected) setView('settings');
                else void action(() => api('/sync', 'POST', {}));
              }}
            >
              {connected ? (
                <RefreshCw size={15} className={running ? 'spin' : ''} />
              ) : (
                <Mail size={15} />
              )}
              {running
                ? 'Syncing'
                : connected
                  ? 'Sync Gmail'
                  : status?.connection?.state === 'reconnect'
                    ? 'Reconnect Gmail'
                    : 'Connect Gmail'}
            </Button>
          </div>
        </header>
        <main id="main-content" className="main-content">
          <SyncStatus
            status={status}
            onSettings={() => setView('settings')}
            onNewTransactions={showNewTransactions}
          />
          {(backLabel || view !== 'overview') && (
            <div className="return-navigation">
              <Button variant="ghost" onClick={back}>
                <ArrowLeft size={16} /> Back to {backLabel || 'Overview'}
              </Button>
            </div>
          )}
          <div className="page-heading" key={view}>
            <div>
              <div className="eyebrow">
                {view === 'overview'
                  ? 'YOUR SPENDING THIS MONTH'
                  : view === 'transactions'
                    ? 'FOLLOW THE DETAILS'
                    : view === 'review'
                      ? 'CONFIDENCE IN YOUR NUMBERS'
                      : 'YOUR LOCAL WORKSPACE'}
              </div>
              <h1 ref={heading} tabIndex={-1}>
                {viewNames[view]}
              </h1>
              <p>
                {view === 'overview'
                  ? 'Understand your spending, with categories and priorities you choose.'
                  : view === 'transactions'
                    ? 'Every payment, with its story and supporting evidence.'
                    : view === 'review'
                      ? 'Resolve uncertain details and understand what your emails cover.'
                      : 'Connect your email and make the app work the way you do.'}
              </p>
            </div>
            <div className="heading-actions">
              {['overview', 'transactions'].includes(view) &&
                report &&
                !(view === 'transactions' && newOnly) && (
                  <>
                    <Picker
                      label="Currency"
                      value={currency}
                      onChange={setCurrency}
                      options={report.currencies}
                    />
                    {view === 'transactions' && (
                      <Picker
                        label="Month"
                        value={month}
                        onChange={setMonth}
                        options={report.months.map((m: MonthTotals) => ({
                          value: m.month,
                          label:
                            monthName(m.month) + (m.current ? ' · so far' : ''),
                        }))}
                      />
                    )}
                  </>
                )}
              {view === 'transactions' && (
                <Button onClick={() => setSelected({ new: true })}>
                  <Plus size={16} />
                  Add transaction
                </Button>
              )}
            </div>
          </div>
          {error && (
            <div className="notice error" role="alert">
              <CircleAlert size={19} />
              <span>{error}</span>
              <button onClick={() => setError('')} aria-label="Dismiss error">
                ×
              </button>
            </div>
          )}
          {message && (
            <div className="notice success" aria-live="polite">
              <CheckCheck size={19} />
              <span>{message}</span>
              <button
                onClick={() => setMessage('')}
                aria-label="Dismiss notification"
              >
                ×
              </button>
            </div>
          )}
          {status?.job?.state === 'failed' && (
            <div className="notice warning">
              <CircleAlert size={19} />
              <span>{status.job.error}</span>
            </div>
          )}
          {!status && !error && (
            <div className="stat-grid">
              {[1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-40 rounded-2xl" />
              ))}
            </div>
          )}
          {view === 'overview' && (
            <AIReview
              currency={currency}
              onTransaction={setSelected}
              onSettings={() => setView('settings')}
            />
          )}
          {view === 'settings' && month && (
            <details className="panel">
              <summary>Optional corrections to your financial context</summary>
              <FinancialPlan
                month={month}
                currency={currency}
                onTransaction={setSelected}
              />
            </details>
          )}
          {view === 'overview' && report && (
            <LedgerOverview
              report={report}
              month={month}
              currency={currency}
              connected={connected}
              rows={rows}
              filtered={filtered}
              setMonth={setMonth}
              setView={setView}
              setSelected={setSelected}
              drill={drill}
              onVisible={markVisibleAsSeen}
              visibilityEnabled={!selected}
            />
          )}
          {view === 'transactions' && (
            <section className="panel transaction-panel">
              <div className="transaction-view-controls">
                <fieldset
                  className="transaction-view-buttons"
                  aria-label="Transaction view"
                >
                  <Button
                    variant={newOnly ? 'outline' : 'default'}
                    aria-pressed={!newOnly}
                    onClick={() => navigate({ newOnly: false })}
                  >
                    All transactions
                  </Button>
                  <Button
                    variant={newOnly ? 'default' : 'outline'}
                    aria-pressed={newOnly}
                    onClick={showNewTransactions}
                  >
                    New transactions ({newCount})
                  </Button>
                </fieldset>
                <p>
                  {newOnly
                    ? 'Showing new Gmail transactions across all months and currencies. '
                    : ''}
                  New badges clear automatically after a transaction appears on
                  screen.
                </p>
              </div>
              <div className="table-filters">
                <div className="search-input">
                  <Search size={17} />
                  <Input
                    aria-label="Search transactions"
                    placeholder="Search a merchant, person, or account…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
                <Picker
                  label="Category"
                  value={category}
                  onChange={setCategory}
                  options={[
                    { value: 'all', label: 'All categories' },
                    ...categories,
                  ]}
                />
                <Picker
                  label="Transaction type"
                  value={kind}
                  onChange={setKind}
                  options={[
                    { value: 'all', label: 'All types' },
                    ...kinds.map((k) => ({ value: k, label: kindName(k) })),
                  ]}
                />
                <button
                  type="button"
                  className="export-link"
                  onClick={() => void action(downloadTransactions)}
                >
                  <Download size={16} />
                  CSV
                </button>
              </div>
              {hasFilters && (
                <div
                  className="active-filters"
                  aria-label="Active transaction filters"
                >
                  <span>Filtered by</span>
                  {category !== 'all' && (
                    <button
                      onClick={() => setCategory('all')}
                      aria-label={`Remove category filter: ${category}`}
                    >
                      {category}
                      <X size={14} />
                    </button>
                  )}
                  {kind !== 'all' && (
                    <button
                      onClick={() => setKind('all')}
                      aria-label={`Remove type filter: ${kindName(kind)}`}
                    >
                      {kindName(kind)}
                      <X size={14} />
                    </button>
                  )}
                  {search && (
                    <button
                      onClick={() => setSearch('')}
                      aria-label="Remove search filter"
                    >
                      Search: {search}
                      <X size={14} />
                    </button>
                  )}
                  <Button variant="ghost" onClick={clearFilters}>
                    Clear all filters
                  </Button>
                </div>
              )}
              <div className="table-summary">
                <span aria-live="polite">
                  {filtered.length} transactions
                  {newOnly
                    ? ' · All months · All currencies'
                    : month
                      ? ` · ${monthName(month)}`
                      : ''}
                </span>
                {(shownSpending.length > 0 || !newOnly) && (
                  <b className="transaction-spending-totals">
                    {(shownSpending.length
                      ? shownSpending
                      : [[currency, 0] as [string, number]]
                    ).map(([code, amount]) => (
                      <span key={code}>
                        {money(amount, code)}
                        {newOnly ? ` ${code}` : ''}
                      </span>
                    ))}{' '}
                    personal spending
                  </b>
                )}
              </div>
              <TransactionTable
                rows={filtered}
                onSelect={setSelected}
                newOnly={newOnly}
                onVisible={markVisibleAsSeen}
                visibilityEnabled={!selected}
              />
              {!filtered.length && hasFilters && (
                <div className="empty-filter-action">
                  <Button variant="outline" onClick={clearFilters}>
                    Clear filters and show transactions
                  </Button>
                </div>
              )}
            </section>
          )}
          {(view === 'settings' || visitedViews.includes('settings')) && (
            <div hidden={view !== 'settings'}>
              <SettingsPanel
                active={view === 'settings'}
                status={status}
                categories={categories}
                notify={notify}
                onError={setError}
                refresh={refresh}
              />
            </div>
          )}
          {(view === 'review' || visitedViews.includes('review')) && (
            <div hidden={view !== 'review'}>
              <ReviewPanel
                active={view === 'review'}
                report={report}
                notify={notify}
                onError={setError}
                onTransaction={setSelected}
                sourceId={view === 'review' && !selected ? sourceId : ''}
                onSource={(value) => navigate({ sourceId: value })}
                onBack={back}
                query={reviewSearch}
                filter={reviewKind}
                onQueryChange={(value) =>
                  navigate({ reviewSearch: value }, true)
                }
                onFilterChange={(value) => navigate({ reviewKind: value })}
              />
            </div>
          )}
          <footer className="app-footer">
            <ShieldCheck size={14} />
            Stored on your Mac
            <span>
              Amounts reflect available evidence, not a complete bank ledger.
            </span>
          </footer>
        </main>
      </SidebarInset>
      <nav className="mobile-navigation" aria-label="Main navigation">
        {navigation.map((item) => (
          <button
            key={item.id}
            aria-current={view === item.id ? 'page' : undefined}
            onClick={() => setView(item.id)}
          >
            <item.icon size={20} />
            <span>
              {item.id === 'settings'
                ? 'Settings'
                : item.id === 'review'
                  ? 'Review'
                  : item.name}
            </span>
          </button>
        ))}
      </nav>
      {selected && (
        <TransactionDetail
          key={selected.id || selected.source_id || 'new'}
          selected={selected}
          returnLabel={backLabel || viewNames[view]}
          sourceId={sourceId}
          onSource={(value) => navigate({ sourceId: value })}
          rows={rows}
          categories={categories}
          onClose={back}
          onSaved={notify}
          onError={setError}
          onVisible={markVisibleAsSeen}
        />
      )}
    </>
  );
}
