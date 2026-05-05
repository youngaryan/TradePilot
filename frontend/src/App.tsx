import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  ArrowRight,
  BrainCircuit,
  CheckCircle2,
  Database,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  LineChart,
  Monitor,
  Newspaper,
  RefreshCw,
  Rocket,
  ShieldCheck,
  Sparkles,
  SunMoon
} from "lucide-react";

import {
  getBacktestTemplates,
  getCurrentUser,
  getHealth,
  getPaperSummary,
  getStrategyCatalog,
  getSystemMetadata,
  getWorkspace,
  login as loginRequest,
  listBacktestJobs,
  listPaperRunJobs,
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
import { BacktestLab } from "./features/BacktestLab";
import { CommandCenter } from "./features/CommandCenter";
import { LiveOps } from "./features/LiveOps";
import { SaaSWorkspace } from "./features/SaaSWorkspace";
import { SentimentLab } from "./features/SentimentLab";
import { SystemGuide } from "./features/SystemGuide";
import { formatCurrency, formatNumber } from "./utils/format";

type ViewId = "command" | "workspace" | "live" | "sentiment" | "backtests" | "system";
type ThemeMode = "light" | "dark" | "system";
type TelemetryConsent = "granted" | "denied";

const views: Array<{ id: ViewId; label: string; description: string; icon: ReactNode }> = [
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
    id: "backtests",
    label: "Strategy Tests",
    description: "Test strategies with realistic validation before trusting them in paper trading.",
    icon: <FlaskConical size={18} />
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

const viewHeadlines: Record<ViewId, string> = {
  command: "Understand your fake-money book at a glance.",
  workspace: "Set up the SaaS workspace behind the research.",
  live: "Deploy paper agents with clear controls and guardrails.",
  sentiment: "Build the news dataset before agents trade from it.",
  backtests: "Test strategy ideas with validation, not vibes.",
  system: "Learn how the backend, data, and agent flow fit together."
};

function backendTone(health: HealthResponse | null) {
  return health?.status === "ok" ? "good" : "warn";
}

const TOKEN_STORAGE_KEY = "quantops.auth_token";
const ORG_STORAGE_KEY = "quantops.organization_id";
const THEME_STORAGE_KEY = "quantops.theme";
const TELEMETRY_STORAGE_KEY = "quantops.telemetry_consent";

function resolveTheme(mode: ThemeMode) {
  if (mode !== "system") return mode;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function LoginScreen({ onLogin }: { onLogin: (auth: AuthResponse) => void }) {
  const [email, setEmail] = useState("demo@quantops.local");
  const [password, setPassword] = useState("quantops-demo");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  async function submit() {
    setIsLoading(true);
    setError(null);
    try {
      const auth = await loginRequest(email, password);
      onLogin(auth);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Login failed.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="marketing-shell">
      <header className="marketing-nav">
        <a className="marketing-brand" href="#top" aria-label="QuantOps home">
          <BrainCircuit size={24} />
          <span>QuantOps</span>
        </a>
        <nav aria-label="Landing page navigation">
          <a href="#tools">Tools</a>
          <a href="#workflow">Workflow</a>
          <a href="#signin">Demo</a>
        </nav>
        <button type="button" className="nav-cta" onClick={() => void submit()} disabled={isLoading}>
          Enter demo
        </button>
      </header>

      <main id="top">
        <section className="marketing-hero">
          <div className="hero-copy">
            <span className="hero-kicker">
              <Sparkles size={16} />
              AI-powered quant research toolkit
            </span>
            <h1>Research, validate, and paper trade strategies from one premium workspace.</h1>
            <p>
              QuantOps helps you move from idea to audited fake-money deployment with backtests, sentiment data,
              strategy agents, saved artifacts, and clear explanations for every important number.
            </p>
            <div className="hero-actions">
              <button type="button" className="primary-button primary-button--xl" onClick={() => void submit()} disabled={isLoading}>
                {isLoading ? "Opening workspace" : "Start with the demo workspace"}
                <ArrowRight size={18} />
              </button>
              <a className="secondary-link" href="#tools">Browse the toolkit</a>
            </div>
            <div className="hero-stats" aria-label="Product highlights">
              <div><strong>10+</strong><span>Strategy paths</span></div>
              <div><strong>24h</strong><span>Refresh workflow</span></div>
              <div><strong>0%</strong><span>Real capital risk</span></div>
            </div>
          </div>

          <aside className="signin-card" id="signin">
            <div className="signin-card__header">
              <Badge label="local demo" tone="good" />
              <span>No credit card. No broker connection.</span>
            </div>
            <h2>Enter your workspace</h2>
            <p>Use the demo credentials to explore the full SaaS-style control room immediately.</p>
            <label>
              Email
              <input value={email} onChange={(event) => setEmail(event.target.value)} />
            </label>
            <label>
              Password
              <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
            </label>
            {error ? <span className="form-error">{error}</span> : null}
            <button type="button" className="primary-button" onClick={() => void submit()} disabled={isLoading}>
              {isLoading ? "Signing in" : "Enter workspace"}
              <ArrowRight size={17} />
            </button>
            <small>Demo login: demo@quantops.local / quantops-demo</small>
          </aside>
        </section>

        <section className="landing-section" id="tools">
          <div className="section-heading">
            <span className="hero-kicker">Everything in one flow</span>
            <h2>The professional toolkit behind every experiment</h2>
            <p>Designed for a first-time user to understand what to do next without learning the codebase first.</p>
          </div>
          <div className="tool-grid">
            {landingFeatureCards.map((card) => (
              <article className="tool-card" key={card.title}>
                <span>{card.icon}</span>
                <h3>{card.title}</h3>
                <p>{card.body}</p>
                <a href="#signin">Open tool <ArrowRight size={15} /></a>
              </article>
            ))}
          </div>
        </section>

        <section className="workflow-band" id="workflow">
          <div>
            <span className="hero-kicker">From idea to paper book</span>
            <h2>A clear launch path, not a wall of charts</h2>
          </div>
          <div className="workflow-steps">
            {workflowSteps.map((step, index) => (
              <div key={step}>
                <strong>{index + 1}</strong>
                <span>{step}</span>
              </div>
            ))}
          </div>
        </section>
      </main>

      <footer className="marketing-footer">
        <span>QuantOps research cockpit</span>
        <span>Fake-money only until you deliberately add a real broker adapter.</span>
      </footer>
    </div>
  );
}

export default function App() {
  const [activeView, setActiveView] = useState<ViewId>("command");
  const [auth, setAuth] = useState<AuthResponse | null>(null);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [activeOrgId, setActiveOrgId] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<WorkspacePayload | null>(null);
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

  async function refreshAll() {
    setIsLoading(true);
    setError(null);
    try {
      const [
        nextHealth,
        nextPayload,
        nextCatalog,
        nextTemplates,
        nextMetadata,
        nextPaperJobs,
        nextBacktestJobs,
        nextWorkspace
      ] = await Promise.all([
        getHealth(),
        getPaperSummary(),
        getStrategyCatalog(),
        getBacktestTemplates(),
        getSystemMetadata(),
        listPaperRunJobs(),
        listBacktestJobs(),
        auth ? getWorkspace() : Promise.resolve(null)
      ]);
      setHealth(nextHealth);
      setPayload(nextPayload);
      setCatalog(nextCatalog);
      setTemplates(nextTemplates);
      setMetadata(nextMetadata);
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
    const storedToken = window.localStorage.getItem(TOKEN_STORAGE_KEY);
    const storedOrg = window.localStorage.getItem(ORG_STORAGE_KEY);
    if (!storedToken) {
      void refreshAll();
      return;
    }
    setApiAuth(storedToken, storedOrg);
    void (async () => {
      try {
        const me = await getCurrentUser();
        const nextAuth: AuthResponse = {
          access_token: storedToken,
          token_type: "bearer",
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
        window.localStorage.removeItem(TOKEN_STORAGE_KEY);
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

  function handleLogin(nextAuth: AuthResponse) {
    setAuth(nextAuth);
    setOrganizations(nextAuth.organizations);
    setActiveOrgId(nextAuth.active_organization_id);
    setApiAuth(nextAuth.access_token, nextAuth.active_organization_id);
    window.localStorage.setItem(TOKEN_STORAGE_KEY, nextAuth.access_token);
    if (nextAuth.active_organization_id) window.localStorage.setItem(ORG_STORAGE_KEY, nextAuth.active_organization_id);
    setActiveView("workspace");
    void trackTelemetryEvent({
      name: "user_logged_in",
      category: "product",
      properties: { organization_count: nextAuth.organizations.length },
      consent: telemetryConsent
    }).catch(() => undefined);
    void refreshAll();
    void getWorkspace().then(setWorkspace).catch(() => setWorkspace(null));
  }

  function switchOrganization(organizationId: string) {
    setActiveOrgId(organizationId);
    setActiveOrganizationId(organizationId);
    window.localStorage.setItem(ORG_STORAGE_KEY, organizationId);
    void refreshAll();
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
      value: formatNumber(metadata?.counts.experiment_runs ?? 0, 0),
      detail: "Saved validation records"
    },
    {
      label: "Telemetry",
      value: formatNumber(metadata?.counts.telemetry_events ?? 0, 0),
      detail: "Consent-aware events"
    }
  ];

  return (
    <div className="app-shell">
      <header className="app-nav">
        <button type="button" className="app-brand" onClick={() => setActiveView("command")} aria-label="Go to QuantOps home">
          <BrainCircuit size={24} />
          <span>QuantOps</span>
        </button>

        <nav className="app-nav-links" aria-label="Quant cockpit navigation">
          {views.map((view) => (
            <button
              key={view.id}
              type="button"
              className={view.id === activeView ? "app-nav-link app-nav-link--active" : "app-nav-link"}
              onClick={() => setActiveView(view.id)}
            >
              {view.icon}
              <span>{view.label}</span>
            </button>
          ))}
        </nav>

        <div className="app-nav-actions">
          <Badge label={health?.status === "ok" ? "Backend online" : "Backend unknown"} tone={backendTone(health)} />
          {organizations.length > 1 ? (
            <label className="compact-control compact-control--nav">
              <span>Workspace</span>
              <select value={activeOrgId ?? ""} onChange={(event) => switchOrganization(event.target.value)} aria-label="Switch workspace">
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
              <button type="button" className="primary-button primary-button--xl" onClick={() => setActiveView("live")}>
                Launch paper agents
                <ArrowRight size={18} />
              </button>
              <button type="button" className="secondary-button" onClick={() => setActiveView("backtests")}>
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
            <label className="compact-control">
              <SunMoon size={15} />
              <span>Theme</span>
              <select
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
            <label className="compact-control">
              <Monitor size={15} />
              <span>Analytics</span>
              <select
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
          </div>
        </section>

        {error ? (
          <section className="alert-card">
            <AlertTriangle size={19} />
            <div>
              <strong>Backend response problem</strong>
              <span>{error}</span>
            </div>
          </section>
        ) : null}

        {!payload && !error ? (
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
            onNavigate={setActiveView}
          />
        ) : null}

        {activeView === "sentiment" ? <SentimentLab /> : null}

        {activeView === "backtests" ? (
          <BacktestLab
            catalog={catalog}
            templates={templates}
            jobs={backtestJobs}
            onJobsChange={setBacktestJobs}
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
