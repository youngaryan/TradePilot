import React, { Suspense, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  ArrowRight,
  BrainCircuit,
  CheckCircle2,
  CreditCard,
  Database,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  LineChart,
  LogOut,
  LockKeyhole,
  Monitor,
  Newspaper,
  RefreshCw,
  Rocket,
  ShieldCheck,
  Sparkles,
  SunMoon,
  UserCircle,
  UserCog
} from "lucide-react";

import {
  getBacktestTemplates,
  getCurrentUser,
  getHealth,
  getPaperSummary,
  getStrategyCatalog,
  getSystemAdminCounts,
  getSystemMetadata,
  getWorkspace,
  listBacktestJobs,
  listPaperRunJobs,
  logout as logoutRequest,
  setActiveOrganizationId,
  setApiAuth,
  trackTelemetryEvent
} from "./api/client";
import type {
  AuthResponse,
  BacktestJob,
  BacktestTemplate,
  HealthResponse,
  Organization,
  PaperDashboardPayload,
  PaperRunJob,
  StrategyCatalogItem,
  SystemMetadata,
  WorkspacePayload
} from "./api/types";
import { Badge } from "./components/Badge";
import { EmptyState } from "./components/Cards";
import { formatCurrency, formatNumber } from "./utils/format";
import { LoginScreen } from "./features/LoginScreen";

const AccountSecurity = React.lazy(() => import("./features/AccountSecurity").then((module) => ({ default: module.AccountSecurity })));
const AdminDashboard = React.lazy(() => import("./features/AdminDashboard").then((module) => ({ default: module.AdminDashboard })));
const BacktestLab = React.lazy(() => import("./features/BacktestLab").then((module) => ({ default: module.BacktestLab })));
const CommandCenter = React.lazy(() => import("./features/CommandCenter").then((module) => ({ default: module.CommandCenter })));
const LiveOps = React.lazy(() => import("./features/LiveOps").then((module) => ({ default: module.LiveOps })));
const PricingPage = React.lazy(() => import("./features/PricingPage").then((module) => ({ default: module.PricingPage })));
const SaaSWorkspace = React.lazy(() => import("./features/SaaSWorkspace").then((module) => ({ default: module.SaaSWorkspace })));
const SentimentLab = React.lazy(() => import("./features/SentimentLab").then((module) => ({ default: module.SentimentLab })));
const MarketResearchLab = React.lazy(() => import("./features/MarketResearchLab").then((module) => ({ default: module.MarketResearchLab })));
const SystemGuide = React.lazy(() => import("./features/SystemGuide").then((module) => ({ default: module.SystemGuide })));

type ViewId = "command" | "workspace" | "account" | "pricing" | "live" | "sentiment" | "research" | "backtests" | "admin" | "system";
type ThemeMode = "light" | "dark" | "system";
type TelemetryConsent = "granted" | "denied";

const views: Array<{ id: ViewId; label: string; description: string; icon: ReactNode; adminOnly?: boolean }> = [
  {
    id: "command",
    label: "Today",
    description: "Start here: plain-English portfolio health, risk, and current fake-money state.",
    icon: <LayoutDashboard size={18} />
  },
  {
    id: "workspace",
    label: "Setup",
    description: "Your workspace, onboarding checklist, billing, refresh status, and saved records.",
    icon: <ShieldCheck size={18} />
  },
  {
    id: "account",
    label: "Account",
    description: "Manage email verification, password reset, MFA, data export, and account deletion.",
    icon: <UserCircle size={18} />
  },
  {
    id: "pricing",
    label: "Pricing",
    description: "Compare plans, understand access, and manage subscription status.",
    icon: <CreditCard size={18} />
  },
  {
    id: "live",
    label: "Paper Trading",
    description: "Launch fake-money agents, compare them, and watch their progress safely.",
    icon: <Rocket size={18} />
  },
  {
    id: "sentiment",
    label: "News & Sentiment",
    description: "Build the news dataset and inspect what the model read before it affects strategies.",
    icon: <Newspaper size={18} />
  },
  {
    id: "research",
    label: "AI Research",
    description: "Run a research-only market committee that creates structured reports without placing trades.",
    icon: <BrainCircuit size={18} />
  },
  {
    id: "backtests",
    label: "Strategy Tests",
    description: "Test strategies with realistic validation before trusting them in paper trading.",
    icon: <FlaskConical size={18} />
  },
  {
    id: "admin",
    label: "Admin",
    description: "Manage users, payments, telemetry, and operational activity.",
    icon: <UserCog size={18} />,
    adminOnly: true
  },
  {
    id: "system",
    label: "Learn",
    description: "Guides, definitions, architecture, and explanations for every important number.",
    icon: <Database size={18} />
  }
];

const landingFeatureCards = [
  {
    title: "Launch fake-money agents",
    body: "Deploy multiple strategy sleeves with symbols, timeframes, sentiment, SEC events, and realistic execution assumptions before risking real capital.",
    icon: <Rocket size={20} />
  },
  {
    title: "Validate before you trust",
    body: "Run backtests with purged validation, experiment artifacts, readiness checks, trades, and lineage so every result has an audit trail.",
    icon: <FlaskConical size={20} />
  },
  {
    title: "Read the news layer",
    body: "Build RSS, local-web, file, and API sentiment datasets, then inspect headlines, scores, heatmaps, and overlays from the same workspace.",
    icon: <Newspaper size={20} />
  },
  {
    title: "Operate like a SaaS product",
    body: "Workspaces, telemetry, refresh status, saved experiments, and paper-agent records are already wired for a future subscription model.",
    icon: <ShieldCheck size={20} />
  }
];

const workflowSteps = [
  "Choose a strategy sleeve",
  "Backtest with validation",
  "Launch paper agents",
  "Review warnings and lineage"
];

const landingExamples = [
  {
    title: "ETF momentum sleeve",
    setup: "SPY, QQQ, TLT, GLD with 63/126/252-day trend agreement and realistic cost assumptions.",
    outcome: "Use the readiness report to decide whether the sleeve deserves fake-money deployment.",
    metric: "Example readiness: 82/100"
  },
  {
    title: "Sentiment-aware event research",
    setup: "RSS, local web search, SEC events, and optional API news providers scored into a daily overlay.",
    outcome: "See exactly which headlines influenced a signal before it reaches a strategy or paper agent.",
    metric: "Example dataset: 274 scored headlines"
  },
  {
    title: "Paper-agent comparison room",
    setup: "Run several fake-money agents with different symbols, date ranges, and strategy methods.",
    outcome: "Compare equity, orders, warnings, exposure, and decision logs without touching real capital.",
    metric: "Example risk: 0% real capital"
  }
];

const landingPlans = [
  {
    name: "Free",
    price: "$0",
    body: "Explore the cockpit, saved examples, strategy catalog, pricing, and learning material.",
    features: ["Read-only workspace", "Example workflows", "No broker required"]
  },
  {
    name: "Pro",
    price: "$49",
    body: "Launch backtests, build sentiment datasets, run refresh jobs, and deploy fake-money agents.",
    features: ["Premium research jobs", "Paper trading agents", "Experiment artifacts"]
  },
  {
    name: "Team",
    price: "$149",
    body: "Operate shared research with admin controls, user management, telemetry, and activity monitoring.",
    features: ["Admin dashboard", "Team visibility", "Operational analytics"]
  }
];

const landingFaqs = [
  {
    question: "Does QuantOps trade real money?",
    answer: "No. The current product is built for research and fake-money paper trading. A real broker adapter should be added only after legal, risk, and production controls are ready."
  },
  {
    question: "What makes this different from a charting app?",
    answer: "The workflow is built around durable experiments, validation reports, data lineage, sentiment overlays, paper-agent decisions, and admin-ready telemetry."
  },
  {
    question: "Can I inspect what the sentiment model used?",
    answer: "Yes. The sentiment lab stores raw headlines, scored rows, source mix, heatmaps, and daily overlays so the user can audit the text behind a score."
  },
  {
    question: "Why is there a payment wall if this is fake-money?",
    answer: "Premium workflows still consume compute, storage, and external data-provider capacity. The backend checks subscription status before launching those jobs."
  }
];

const viewHeadlines: Record<ViewId, string> = {
  command: "Understand your fake-money book at a glance.",
  workspace: "Set up the SaaS workspace behind the research.",
  account: "Manage your account security and data rights.",
  pricing: "Upgrade only when the workflow is worth unlocking.",
  live: "Deploy paper agents with clear controls and guardrails.",
  sentiment: "Build the news dataset before agents trade from it.",
  research: "Run a research-only AI market committee.",
  backtests: "Test strategy ideas with validation, not vibes.",
  admin: "Operate the SaaS layer with real permissions.",
  system: "Learn how the backend, data, and agent flow fit together."
};

function backendTone(health: HealthResponse | null) {
  return health?.status === "ok" ? "good" : "warn";
}

const ORG_STORAGE_KEY = "quantops.organization_id";
const THEME_STORAGE_KEY = "quantops.theme";
const TELEMETRY_STORAGE_KEY = "quantops.telemetry_consent";
const premiumViews = new Set<ViewId>(["live", "sentiment", "research", "backtests"]);

function resolveTheme(mode: ThemeMode) {
  if (mode !== "system") return mode;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function appViewHash(view: ViewId) {
  return `#/app/${view}`;
}

function viewFromLocationHash(): ViewId | null {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const candidate = hash.startsWith("app/") ? hash.slice("app/".length) : "";
  return views.some((view) => view.id === candidate) ? candidate as ViewId : null;
}

function isAdminRole(role: unknown) {
  return String(role ?? "user").toLowerCase() === "admin";
}

export default function App() {
  const [activeView, setActiveView] = useState<ViewId>(() => viewFromLocationHash() ?? "command");
  const [auth, setAuth] = useState<AuthResponse | null>(null);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [activeOrgId, setActiveOrgId] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<WorkspacePayload | null>(null);
  const [paymentWallReason, setPaymentWallReason] = useState<string | null>(null);
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => (window.localStorage.getItem(THEME_STORAGE_KEY) as ThemeMode | null) ?? "light");
  const [telemetryConsent, setTelemetryConsent] = useState<TelemetryConsent>(() => (window.localStorage.getItem(TELEMETRY_STORAGE_KEY) as TelemetryConsent | null) ?? "granted");
  const [payload, setPayload] = useState<PaperDashboardPayload | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [metadata, setMetadata] = useState<SystemMetadata | null>(null);
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [templates, setTemplates] = useState<BacktestTemplate[]>([]);
  const [paperJobs, setPaperJobs] = useState<PaperRunJob[]>([]);
  const [backtestJobs, setBacktestJobs] = useState<BacktestJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  async function refreshAll(authOverride: AuthResponse | null = auth) {
    setIsLoading(true);
    setError(null);
    try {
      const shouldLoadAdminCounts = isAdminRole(authOverride?.user.role);
      const [
        nextHealth,
        nextPayload,
        nextCatalog,
        nextTemplates,
        nextMetadata,
        nextAdminCounts,
        nextPaperJobs,
        nextBacktestJobs,
        nextWorkspace
      ] = await Promise.all([
        getHealth(),
        getPaperSummary(),
        getStrategyCatalog(),
        getBacktestTemplates(),
        getSystemMetadata(),
        shouldLoadAdminCounts ? getSystemAdminCounts().catch(() => null) : Promise.resolve(null),
        listPaperRunJobs(),
        listBacktestJobs(),
        authOverride ? getWorkspace() : Promise.resolve(null)
      ]);
      setHealth(nextHealth);
      setPayload(nextPayload);
      setCatalog(nextCatalog);
      setTemplates(nextTemplates);
      setMetadata(nextAdminCounts ? { ...nextMetadata, counts: nextAdminCounts.counts } : nextMetadata);
      setPaperJobs(nextPaperJobs);
      setBacktestJobs(nextBacktestJobs);
      setWorkspace(nextWorkspace);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The backend did not return a usable response.");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    const handleHashChange = () => {
      const nextView = viewFromLocationHash();
      if (nextView) setActiveView(nextView);
    };
    window.addEventListener("hashchange", handleHashChange);
    handleHashChange();
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  useEffect(() => {
    const storedOrg = window.localStorage.getItem(ORG_STORAGE_KEY);
    setApiAuth(null, storedOrg);
    void (async () => {
      try {
        const me = await getCurrentUser();
        const nextAuth: AuthResponse = {
          user: me.user,
          organizations: me.organizations,
          active_organization_id: me.active_organization_id
        };
        setAuth(nextAuth);
        setOrganizations(me.organizations);
        setActiveOrgId(me.active_organization_id);
        setActiveOrganizationId(me.active_organization_id);
        setWorkspace(await getWorkspace());
      } catch {
        window.localStorage.removeItem(ORG_STORAGE_KEY);
        setApiAuth(null, null);
      } finally {
        void refreshAll();
      }
    })();
  }, []);

  useEffect(() => {
    const applyTheme = () => {
      const resolved = resolveTheme(themeMode);
      document.documentElement.dataset.theme = resolved;
      document.documentElement.dataset.themeMode = themeMode;
      document.documentElement.style.colorScheme = resolved;
    };
    applyTheme();
    window.localStorage.setItem(THEME_STORAGE_KEY, themeMode);
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    media?.addEventListener("change", applyTheme);
    return () => media?.removeEventListener("change", applyTheme);
  }, [themeMode]);

  useEffect(() => {
    window.localStorage.setItem(TELEMETRY_STORAGE_KEY, telemetryConsent);
  }, [telemetryConsent]);

  const activeMeta = useMemo(() => views.find((view) => view.id === activeView) ?? views[0], [activeView]);
  const isAdminAccess = isAdminRole(auth?.user.role);
  const hasPremiumAccess = useMemo(() => {
    if (isAdminAccess) return true;
    const subscription = workspace?.subscription;
    const plan = String(subscription?.plan ?? "free");
    const status = String(subscription?.status ?? "");
    return plan !== "free" && status === "active";
  }, [workspace?.subscription, isAdminAccess]);
  const visibleViews = useMemo(
    () => views.filter((view) => !view.adminOnly || isAdminAccess),
    [isAdminAccess]
  );

  function premiumReason(view: ViewId) {
    const label = views.find((item) => item.id === view)?.label ?? "This feature";
    return `${label} is a premium workflow. Free users can explore the workspace and saved records, then upgrade before launching compute or paper agents.`;
  }

  function setRoutedView(view: ViewId, options: { replace?: boolean } = {}) {
    setActiveView(view);
    const nextHash = appViewHash(view);
    if (window.location.hash !== nextHash) {
      if (options.replace) {
        window.history.replaceState(null, "", nextHash);
      } else {
        window.history.pushState(null, "", nextHash);
      }
    }
  }

  function navigateTo(view: ViewId) {
    if (view === "admin" && !isAdminAccess) {
      setPaymentWallReason("Admin dashboard access requires an admin account.");
      setRoutedView("pricing");
      return;
    }
    if (premiumViews.has(view) && !hasPremiumAccess) {
      setPaymentWallReason(premiumReason(view));
      setRoutedView("pricing");
      return;
    }
    setPaymentWallReason(null);
    setRoutedView(view);
  }

  function track(name: string, properties: Record<string, unknown> = {}) {
    void trackTelemetryEvent({
      name,
      category: "product",
      properties,
      context: {
        view: activeView,
        theme_mode: themeMode,
        resolved_theme: document.documentElement.dataset.theme
      },
      consent: telemetryConsent
    }).catch(() => undefined);
  }

  useEffect(() => {
    if (auth) track("view_opened", { view: activeView });
  }, [activeView]);

  useEffect(() => {
    if (auth && hasPremiumAccess) {
      setPaymentWallReason(null);
    }
    if (auth && premiumViews.has(activeView) && workspace && !hasPremiumAccess) {
      setPaymentWallReason(premiumReason(activeView));
      setRoutedView("pricing", { replace: true });
    }
    if (auth && activeView === "admin" && !isAdminAccess) {
      setPaymentWallReason("Admin dashboard access requires an admin account.");
      setRoutedView("pricing", { replace: true });
    }
  }, [isAdminAccess, activeView, hasPremiumAccess, workspace?.organization_id]);

  function handleLogin(nextAuth: AuthResponse) {
    setAuth(nextAuth);
    setOrganizations(nextAuth.organizations);
    setActiveOrgId(nextAuth.active_organization_id);
    setApiAuth(null, nextAuth.active_organization_id);
    setPaymentWallReason(null);
    if (nextAuth.active_organization_id) window.localStorage.setItem(ORG_STORAGE_KEY, nextAuth.active_organization_id);
    setRoutedView("workspace", { replace: true });
    void trackTelemetryEvent({
      name: "user_logged_in",
      category: "product",
      properties: { organization_count: nextAuth.organizations.length },
      consent: telemetryConsent
    }).catch(() => undefined);
    void refreshAll(nextAuth);
    void getWorkspace().then(setWorkspace).catch(() => setWorkspace(null));
  }

  function switchOrganization(organizationId: string) {
    setActiveOrgId(organizationId);
    setActiveOrganizationId(organizationId);
    window.localStorage.setItem(ORG_STORAGE_KEY, organizationId);
    void refreshAll();
  }

  async function handleLogout() {
    try {
      await logoutRequest();
    } catch {
      // Local logout should still clear client state if the server session already expired.
    }
    setAuth(null);
    setWorkspace(null);
    setOrganizations([]);
    setActiveOrgId(null);
    setActiveView("command");
    setPaymentWallReason(null);
    window.history.replaceState(null, "", window.location.pathname);
    setApiAuth(null, null);
    window.localStorage.removeItem(ORG_STORAGE_KEY);
  }

  if (!auth) {
    return <LoginScreen onLogin={handleLogin} />;
  }

  const activeOrganization = organizations.find((organization) => organization.id === activeOrgId) ?? organizations[0];
  const cockpitStats = [
    {
      label: "Paper equity",
      value: payload ? formatCurrency(payload.totals.equity) : "Not loaded",
      detail: payload?.asof_date ? `As of ${payload.asof_date}` : "Waiting for a run"
    },
    {
      label: "Strategies",
      value: formatNumber(payload?.strategies.length ?? 0, 0),
      detail: "Configured fake-money sleeves"
    },
    {
      label: "Experiments",
      value: formatNumber(metadata?.counts?.experiment_runs ?? 0, 0),
      detail: "Saved validation records"
    },
    {
      label: "Telemetry",
      value: formatNumber(metadata?.counts?.telemetry_events ?? 0, 0),
      detail: "Consent-aware events"
    }
  ];

  return (
    <div className="app-shell">
      <header className="app-nav">
        <button type="button" className="app-brand" onClick={() => navigateTo("command")} aria-label="Go to QuantOps home">
          <BrainCircuit size={24} />
          <span>QuantOps</span>
        </button>

        <nav className="app-nav-links" aria-label="Quant cockpit navigation">
          {visibleViews.map((view) => (
            <button
              key={view.id}
              type="button"
              className={view.id === activeView ? "app-nav-link app-nav-link--active" : "app-nav-link"}
              onClick={() => navigateTo(view.id)}
            >
              {view.icon}
              <span>{view.label}</span>
            </button>
          ))}
        </nav>

        <div className="app-nav-actions">
          <Badge label={health?.status === "ok" ? "Backend online" : "Backend unknown"} tone={backendTone(health)} />
          <Badge label={hasPremiumAccess ? "Premium" : "Free"} tone={hasPremiumAccess ? "good" : "warn"} />
          {organizations.length > 1 ? (
            <label className="compact-control compact-control--nav" htmlFor="app-workspace">
              <span>Workspace</span>
              <select id="app-workspace" value={activeOrgId ?? ""} onChange={(event) => switchOrganization(event.target.value)} aria-label="Switch workspace">
                {organizations.map((organization) => (
                  <option key={organization.id} value={organization.id}>{organization.name}</option>
                ))}
              </select>
            </label>
          ) : (
            <span className="workspace-pill">{activeOrganization?.name ?? auth.user.email}</span>
          )}
        </div>
      </header>

      <main className="app-main">
        <section className="app-hero">
          <div>
            <span className="hero-kicker">
              <Sparkles size={16} />
              Professional Quant Control Room
            </span>
            <h1>{viewHeadlines[activeView]}</h1>
            <p>{activeMeta.description}</p>
            <div className="hero-actions">
              <button type="button" className="primary-button primary-button--xl" onClick={() => navigateTo("live")}>
                Launch paper agents
                <ArrowRight size={18} />
              </button>
              <button type="button" className="secondary-button" onClick={() => navigateTo("backtests")}>
                Run a backtest
              </button>
            </div>
          </div>

          <aside className="hero-product-card">
            <div className="hero-product-card__top">
              <LineChart size={22} />
              <Badge label={payload?.run_timestamp_utc ? "state loaded" : "waiting for run"} tone={payload?.run_timestamp_utc ? "good" : "warn"} />
            </div>
            <strong>{payload ? formatCurrency(payload.totals.equity) : "No paper ledger yet"}</strong>
            <span>Current fake-money equity across all saved paper agents.</span>
            <div className="mini-checklist">
              {["Backtest", "Sentiment", "Paper run"].map((item) => (
                <div key={item}><CheckCircle2 size={15} />{item}</div>
              ))}
            </div>
          </aside>
        </section>

        <section className="stat-strip">
          {cockpitStats.map((stat) => (
            <article key={stat.label} className="stat-card">
              <span>{stat.label}</span>
              <strong>{stat.value}</strong>
              <small>{stat.detail}</small>
            </article>
          ))}
        </section>

        <section className="control-strip">
          <div>
            <strong>{activeMeta.label}</strong>
            <span>{auth.user.email}</span>
          </div>
          <div className="topbar-actions">
            <label className="compact-control" htmlFor="app-theme">
              <SunMoon size={15} />
              <span>Theme</span>
              <select
                id="app-theme"
                value={themeMode}
                onChange={(event) => {
                  const next = event.target.value as ThemeMode;
                  setThemeMode(next);
                  track("theme_changed", { theme_mode: next });
                }}
                aria-label="Theme"
              >
                <option value="system">System</option>
                <option value="light">Light</option>
                <option value="dark">Dark</option>
              </select>
            </label>
            <label className="compact-control" htmlFor="app-analytics">
              <Monitor size={15} />
              <span>Analytics</span>
              <select
                id="app-analytics"
                value={telemetryConsent}
                onChange={(event) => {
                  const next = event.target.value as TelemetryConsent;
                  setTelemetryConsent(next);
                  void trackTelemetryEvent({
                    name: "telemetry_consent_changed",
                    category: "product",
                    properties: { consent: next },
                    consent: next
                  }).catch(() => undefined);
                }}
                aria-label="Analytics consent"
              >
                <option value="granted">On</option>
                <option value="denied">Off</option>
              </select>
            </label>
            <button type="button" className="ghost-button" onClick={() => void refreshAll()} disabled={isLoading}>
              <RefreshCw size={17} />
              <span>{isLoading ? "Refreshing" : "Refresh"}</span>
            </button>
            <button type="button" className="ghost-button" onClick={() => void handleLogout()}>
              <LogOut size={17} />
              <span>Logout</span>
            </button>
          </div>
        </section>

        {!hasPremiumAccess && activeView !== "pricing" ? (
          <section className="payment-wall-banner">
            <LockKeyhole size={20} />
            <div>
              <strong>Free workspace active</strong>
              <span>Premium runs are locked until this workspace has an active paid subscription.</span>
            </div>
            <button type="button" className="primary-button" onClick={() => navigateTo("pricing")}>View pricing</button>
          </section>
        ) : null}

        {error ? (
          <section className="alert-card">
            <AlertTriangle size={19} />
            <div>
              <strong>Backend response problem</strong>
              <span>{error}</span>
            </div>
          </section>
        ) : null}

        <Suspense
          fallback={
            <EmptyState
              icon={<Gauge size={34} />}
              title="Loading view"
              body="The requested workspace module is loading."
            />
          }
        >
          {!payload && !error && activeView === "command" ? (
            <EmptyState
              icon={<Gauge size={34} />}
              title={isLoading ? "Loading quant cockpit" : "No paper state found"}
              body="The cockpit reads from the backend API. Start the backend, then run or replay a paper deployment to populate ledgers."
            />
          ) : null}

          {payload && activeView === "command" ? (
            <CommandCenter
              payload={payload}
              health={health}
              metadata={metadata}
              paperJobs={paperJobs}
              backtestJobs={backtestJobs}
            />
          ) : null}

          {payload && activeView === "live" ? (
            <LiveOps
              payload={payload}
              catalog={catalog}
              paperJobs={paperJobs}
              onJobsChange={setPaperJobs}
              onPaperPayload={setPayload}
              onRefresh={() => void refreshAll()}
            />
          ) : null}

          {activeView === "workspace" ? (
            <SaaSWorkspace
              auth={auth}
              activeOrganizationId={activeOrgId}
              workspace={workspace}
              organizations={organizations}
              onSwitchOrganization={switchOrganization}
              onRefresh={refreshAll}
              onNavigate={navigateTo}
            />
          ) : null}

          {activeView === "account" ? <AccountSecurity auth={auth} onDeleted={() => void handleLogout()} /> : null}

          {activeView === "pricing" ? (
            <PricingPage workspace={workspace} reason={paymentWallReason} isAdminAccess={isAdminAccess} onRefresh={refreshAll} />
          ) : null}

          {activeView === "sentiment" ? <SentimentLab /> : null}

          {activeView === "research" ? <MarketResearchLab /> : null}

          {activeView === "backtests" ? (
            <BacktestLab
              catalog={catalog}
              templates={templates}
              jobs={backtestJobs}
              onJobsChange={setBacktestJobs}
              onCatalogChange={setCatalog}
            />
          ) : null}

          {activeView === "system" ? (
            <SystemGuide
              health={health}
              metadata={metadata}
              paperJobs={paperJobs}
              backtestJobs={backtestJobs}
            />
          ) : null}

          {activeView === "admin" && isAdminAccess ? <AdminDashboard auth={auth} /> : null}
        </Suspense>

        <section className="research-note">
          <Gauge size={16} />
          <span>
            Everything here is fake-money research until a real broker adapter is deliberately added. Start with Home, then Run Paper, then Backtest.
          </span>
        </section>
      </main>
    </div>
  );
}
