'use client';
import Image from 'next/image';
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
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
import { ThemeToggle } from '@/components/theme-toggle';
import { SyncStatus } from '@/components/sync-status';
import { AIReview } from '@/components/ai-review';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  api,
  isLocalAccessRequired,
  subscribeLocalAccess,
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
  transactionHistory,
} from '@/lib/transaction-filters';

import type {
  AppStatus,
  Report,
  Transaction,
  TransactionSelection,
} from '@/lib/types';
type FocusFilters = {
  month: string;
  currency: string;
  group_ids: Record<string, string[]>;
  group_totals: Record<string, number>;
  categories: { name: string; transaction_ids: string[] }[];
  fixed_categories: { name: string; transaction_ids: string[] }[];
  unavoidable_categories: { name: string; transaction_ids: string[] }[];
};
const navigation = [
  { id: 'overview', name: 'Overview', icon: LayoutDashboard },
  { id: 'transactions', name: 'Transactions', icon: ArrowLeftRight },
  { id: 'review', name: 'Review', icon: ScanLine },
  { id: 'settings', name: 'Settings', icon: Settings2 },
] as const;

export default function Home() {
  const accessRequired = useSyncExternalStore(
    subscribeLocalAccess,
    isLocalAccessRequired,
    () => false,
  );
  if (accessRequired)
    return (
      <main className="main-content" id="main-content">
        <section className="sync-status sync-attention" role="alert">
          <h1>This Kharcha tab needs to be unlocked</h1>
          <p>
            Restarting Kharcha expires older browser sessions. This tab cannot
            show current sync progress or resume syncing.
          </p>
          <p>
            Use the new tab opened by Kharcha, or double-click{' '}
            <strong>Start Kharcha.command</strong> and use the tab it opens.
            Refreshing this older tab alone will not unlock it.
          </p>
          <p>Your saved ledger and sync progress remain on this Mac.</p>
        </section>
      </main>
    );
  return (
    <SidebarProvider
      style={{ '--sidebar-width': '224px' } as React.CSSProperties}
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
    group,
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
  const hasFilters =
    category !== 'all' || kind !== 'all' || !!search || !!group;
  const clearFilters = () =>
    navigate({ category: 'all', kind: 'all', search: '', group: '' });
  const [status, setStatus] = useState<AppStatus | null>(null),
    [report, setReport] = useState<Report | null>(null),
    [rows, setRows] = useState<Transaction[]>([]),
    [categories, setCategories] = useState<string[]>([]);
  const [focusFilters, setFocusFilters] = useState<FocusFilters | null>(null);
  const [revision, setRevision] = useState(0);
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
      const [s, r, t, c, f] = await Promise.all([
        api<AppStatus>('/status'),
        api<Report>(
          '/report?' +
            new URLSearchParams({ ...(month ? { month } : {}), currency }),
        ),
        api<Transaction[]>('/transactions'),
        api<string[]>('/categories'),
        month
          ? api<FocusFilters>(
              `/spending-focus?${new URLSearchParams({ month, currency })}`,
            ).catch(() => null)
          : Promise.resolve(null),
      ]);
      if (version !== requestVersion.current) return;
      setStatus(s);
      setReport(r);
      setRows(t);
      setCategories(c);
      setFocusFilters(f);
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
    setRevision((r) => r + 1);
    void refresh();
  };
  const drill = (cat = 'all', type = 'all', q = '') => {
    navigate({
      view: 'transactions',
      group: '',
      category: cat,
      kind: type,
      newOnly: false,
      search: q,
      selected: null,
      sourceId: '',
    });
  };
  const showGroup = (value: string) =>
    navigate({
      view: 'transactions',
      group: value,
      category: 'all',
      kind: 'all',
      search: '',
      newOnly: false,
      selected: null,
      sourceId: '',
    });
  const showNewTransactions = () => {
    setOpenMobile(false);
    navigate({
      view: 'transactions',
      newOnly: true,
      group: '',
      category: 'all',
      kind: 'all',
      search: '',
      selected: null,
      sourceId: '',
    });
  };
  const currentFocus =
    focusFilters?.month === month && focusFilters?.currency === currency
      ? focusFilters
      : null;
  const categoryEvidence = currentFocus
    ? [
        ...currentFocus.categories,
        ...currentFocus.fixed_categories,
        ...currentFocus.unavoidable_categories,
      ].filter((g) => g.name === category)
    : [];
  const filtered = filterTransactions(
    rows,
    {
      month,
      currency,
      category: view === 'overview' ? 'all' : category,
      kind: view === 'overview' ? 'all' : kind,
      search: view === 'overview' ? '' : search,
      newOnly: viewingNew,
      group: view === 'transactions' ? group : '',
      categoryIds:
        !viewingNew && categoryEvidence.length
          ? [...new Set(categoryEvidence.flatMap((g) => g.transaction_ids))]
          : undefined,
      groupIds: currentFocus?.group_ids[group] || (group ? [] : undefined),
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
  const history = transactionHistory(
    rows,
    currency,
    report?.months.map((item) => item.month),
  );
  const showHistory = () => {
    setOpenMobile(false);
    navigate({
      view: 'transactions',
      month: history.latestMonth || month,
      group: '',
      currency,
      category: 'all',
      kind: 'all',
      search: '',
      newOnly: false,
      selected: null,
      sourceId: '',
    });
  };
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
          <div className="sidebar-local">
            <ShieldCheck size={16} />
            <span>
              Local on your Mac<small>Your ledger stays with you.</small>
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
            <ThemeToggle />
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
          {(backLabel || view !== 'overview') && (
            <div className="return-navigation">
              <Button variant="ghost" onClick={back}>
                <ArrowLeft size={16} /> Back to {backLabel || 'Overview'}
              </Button>
            </div>
          )}
          <div className="page-heading" key={view}>
            <div>
              <h1 ref={heading} tabIndex={-1}>
                {viewNames[view]}
              </h1>
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
                    <Picker
                      label="Month"
                      value={month}
                      onChange={setMonth}
                      options={history.months.map((value) => ({
                        value,
                        label:
                          monthName(value) +
                          (report.months.some(
                            (item) => item.month === value && item.current,
                          )
                            ? ' · so far'
                            : ''),
                      }))}
                    />
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
          <SyncStatus
            status={status}
            busy={busy}
            onResume={() =>
              void action(() => api('/sync', 'POST', {}), 'Sync resumed')
            }
            onSettings={() => setView('settings')}
            onNewTransactions={showNewTransactions}
          />
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
          {view === 'overview' && month && report && (
            <AIReview
              month={month}
              currency={currency}
              onTransaction={setSelected}
              onSettings={() => setView('settings')}
              report={report}
              onMonth={setMonth}
              onCategory={(cat) => drill(cat)}
              onGroup={showGroup}
              onReview={() => setView('review')}
              revision={revision}
            />
          )}
          {view === 'overview' && history.count > 0 && (
            <button className="saved-history-link" onClick={showHistory}>
              {history.count.toLocaleString('en-IN')} saved transactions ·{' '}
              {monthName(history.firstMonth)} – {monthName(history.latestMonth)}{' '}
              <span>
                Browse history <ChevronRight size={14} />
              </span>
            </button>
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
                  Viewing a new payment clears its New badge; evidence review is
                  separate.
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
                  {group && (
                    <button
                      onClick={() => navigate({ group: '' })}
                      aria-label="Remove spending group filter"
                    >
                      {group[0].toUpperCase() + group.slice(1)} spending ·{' '}
                      {money(focusFilters?.group_totals[group] || 0, currency)}
                      <X size={14} />
                    </button>
                  )}
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
                    {group
                      ? 'personal spending in these payments'
                      : 'personal spending'}
                  </b>
                )}
              </div>
              <TransactionTable
                rows={filtered}
                onSelect={setSelected}
                categories={categories}
                onUpdated={notify}
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
                month={month}
                currency={currency}
                onTransaction={setSelected}
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
                onOverview={() => setView('overview')}
                onClearFilters={() =>
                  navigate({ reviewSearch: '', reviewKind: 'all' })
                }
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
