import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  BrainCircuit,
  Database,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  Monitor,
  Newspaper,
  RefreshCw,
  Rocket,
  ShieldCheck,
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
    <div className="login-shell">
      <section className="login-card">
        <div className="brand-mark brand-mark--login">
          <BrainCircuit size={28} />
          <div>
            <strong>QuantOps SaaS</strong>
            <span>Research, validation, and fake-money deployment cockpit</span>
          </div>
        </div>
        <h1>Sign in to your workspace</h1>
        <p>
          The local prototype ships with a demo workspace so you can test authentication, organization switching,
          billing hooks, experiments, datasets, and paper agents immediately.
        </p>
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
        </button>
        <small>Demo login: demo@quantops.local / quantops-demo</small>
      </section>
    </div>
  );
}

export default function App() {
  const [activeView, setActiveView] = useState<ViewId>("command");
  const [auth, setAuth] = useState<AuthResponse | null>(null);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [activeOrgId, setActiveOrgId] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<WorkspacePayload | null>(null);
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => (window.localStorage.getItem(THEME_STORAGE_KEY) as ThemeMode | null) ?? "system");
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

  return (
    <div className="quant-shell">
      <aside className="nav-rail" aria-label="Quant cockpit navigation">
        <div className="brand-mark">
          <BrainCircuit size={24} />
          <div>
            <strong>QuantOps</strong>
            <span>Research to paper</span>
          </div>
        </div>

        <nav className="nav-stack">
          {views.map((view) => (
            <button
              key={view.id}
              type="button"
              className={view.id === activeView ? "nav-item nav-item--active" : "nav-item"}
              onClick={() => setActiveView(view.id)}
            >
              {view.icon}
              <span>{view.label}</span>
            </button>
          ))}
        </nav>

        <div className="nav-footer">
          <Badge label={health?.status === "ok" ? "Backend online" : "Backend unknown"} tone={backendTone(health)} />
          <span>{auth.user.email}</span>
          <span>{metadata?.counts.experiment_runs ?? 0} saved experiments</span>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Professional Quant Control Room</p>
            <h1>{activeMeta.label}</h1>
            <span>{activeMeta.description}</span>
          </div>
          <div className="topbar-actions">
            <Badge label={payload?.run_timestamp_utc ? "state loaded" : "waiting for run"} tone={payload?.run_timestamp_utc ? "good" : "warn"} />
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
        </header>

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
