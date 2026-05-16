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
  login as loginRequest,
  listBacktestJobs,
  listPaperRunJobs,
  logout as logoutRequest,
  confirmPasswordReset,
  verifyEmail,
  requestPasswordReset,
  setActiveOrganizationId,
  setApiAuth,
  signup as signupRequest,
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
const LANDING_ANON_STORAGE_KEY = "quantops.landing_visitor";
const premiumViews = new Set<ViewId>(["live", "sentiment", "research", "backtests"]);
const legalPages: Record<string, { title: string; body: string[] }> = {
  "/privacy": {
    title: "Privacy Policy",
    body: [
      "QuantOps stores account, workspace, telemetry, and research metadata needed to operate the product. Telemetry is minimized, consent-aware, and redacts sensitive fields such as passwords, API keys, emails, and tokens.",
      "Research artifacts are scoped to your workspace. Do not upload confidential third-party data unless you have the right to use it."
    ]
  },
  "/terms": {
    title: "Terms of Use",
    body: [
      "QuantOps is a research and paper-trading tool. You are responsible for your data sources, strategy decisions, and compliance obligations.",
      "Premium features may require a paid subscription and can be limited by quotas to protect platform reliability."
    ]
  },
  "/risk-disclaimer": {
    title: "Risk Disclaimer",
    body: [
      "Nothing in QuantOps is financial advice. Backtests, sentiment scores, and fake-money paper results are educational and may not predict live trading outcomes.",
      "The product does not place real-money broker orders in this version. Add real trading only after legal, compliance, and operational review."
    ]
  },
  "/compliance": {
    title: "Compliance Boundary",
    body: [
      "QuantOps is limited to research workflows and fake-money paper trading. The product should not be marketed as investment advice, guaranteed performance, signal recommendations, or automated real-money trading.",
      "Commercial deployments must review data-provider terms, maintain risk disclaimers, honor privacy/export/delete requests, and keep billing, telemetry, and user-management audit logs."
    ]
  }
};

function resolveTheme(mode: ThemeMode) {
  if (mode !== "system") return mode;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function landingVisitorId() {
  const existing = window.localStorage.getItem(LANDING_ANON_STORAGE_KEY);
  if (existing) return existing;
  const next = `visitor_${Math.random().toString(36).slice(2)}_${Date.now().toString(36)}`;
  window.localStorage.setItem(LANDING_ANON_STORAGE_KEY, next);
  return next;
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

function LoginScreen({ onLogin }: { onLogin: (auth: AuthResponse) => void }) {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("demo@quantops.local");
  const [password, setPassword] = useState("quantops-demo");
  const [newPassword, setNewPassword] = useState("");
  const [displayName, setDisplayName] = useState("Research Lead");
  const [organizationName, setOrganizationName] = useState("Northstar Quant Lab");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const legalPage = legalPages[window.location.pathname];
  const authToken = new URLSearchParams(window.location.search).get("token") ?? "";

  function trackLanding(name: string, properties: Record<string, unknown> = {}) {
    void trackTelemetryEvent({
      name,
      category: "landing",
      properties,
      context: {
        path: window.location.pathname,
        hash: window.location.hash || "#top",
        viewport_width: window.innerWidth,
        page: "landing"
      },
      anonymous_id: landingVisitorId(),
      consent: "granted"
    }).catch(() => undefined);
  }

  useEffect(() => {
    trackLanding("landing_page_view", { section: "top" });
    if (typeof IntersectionObserver === "undefined") return undefined;
    const seen = new Set<string>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const section = entry.target.id;
          if (entry.isIntersecting && section && !seen.has(section)) {
            seen.add(section);
            trackLanding(section === "pricing" ? "pricing_viewed" : "landing_section_view", { section });
          }
        }
      },
      { threshold: 0.42 }
    );
    ["features", "examples", "pricing", "faq", "login", "signup"].forEach((id) => {
      const node = document.getElementById(id);
      if (node) observer.observe(node);
    });
    return () => observer.disconnect();
  }, []);

  function goToSection(section: string, eventName = "landing_nav_clicked", cta?: string) {
    trackLanding(eventName, { section, target_section: section, ...(cta ? { cta } : {}) });
    document.getElementById(section)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function submit() {
    setIsLoading(true);
    setError(null);
    try {
      trackLanding("auth_login_started", { section: "login", mode: email.endsWith("@quantops.local") ? "demo" : "standard" });
      const auth = await loginRequest(email, password);
      trackLanding("auth_login_completed", { section: "login", role: auth.user.role, organization_count: auth.organizations.length });
      onLogin(auth);
    } catch (caught) {
      trackLanding("auth_login_failed", { section: "login" });
      setError(caught instanceof Error ? caught.message : "Login failed.");
    } finally {
      setIsLoading(false);
    }
  }

  async function submitSignup() {
    setIsLoading(true);
    setError(null);
    try {
      trackLanding("auth_signup_started", { section: "signup", plan: "free" });
      const auth = await signupRequest({
        email,
        password,
        display_name: displayName,
        organization_name: organizationName
      });
      trackLanding("auth_signup_completed", { section: "signup", plan: "free" });
      onLogin(auth);
    } catch (caught) {
      trackLanding("auth_signup_failed", { section: "signup" });
      setError(caught instanceof Error ? caught.message : "Signup failed.");
    } finally {
      setIsLoading(false);
    }
  }

  function useDemo(kind: "admin" | "user") {
    setMode("login");
    if (kind === "admin") {
      setEmail("demo@quantops.local");
      setPassword("quantops-demo");
      return;
    }
    setEmail("user@quantops.local");
    setPassword("quantops-user");
  }

  async function submitVerifyEmail() {
    setIsLoading(true);
    setError(null);
    try {
      await verifyEmail(authToken);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Email verification failed.");
    } finally {
      setIsLoading(false);
    }
  }

  async function submitPasswordResetRequest() {
    setIsLoading(true);
    setError(null);
    try {
      await requestPasswordReset(email);
      setError("If the account exists, reset instructions were sent. In development, check artifacts/email_outbox.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Password reset request failed.");
    } finally {
      setIsLoading(false);
    }
  }

  async function submitPasswordResetConfirm() {
    setIsLoading(true);
    setError(null);
    try {
      await confirmPasswordReset(authToken, newPassword);
      setError("Password updated. You can log in now.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Password reset failed.");
    } finally {
      setIsLoading(false);
    }
  }

  if (window.location.pathname === "/verify-email") {
    return (
      <div className="marketing-shell">
        <header className="marketing-nav">
          <a className="marketing-brand" href="/" aria-label="QuantOps home"><BrainCircuit size={24} /><span>QuantOps</span></a>
          <nav aria-label="Auth navigation"><a href="/">Home</a><a href="/terms">Terms</a></nav>
        </header>
        <main className="auth-utility-page">
          <section className="signin-card">
            <Badge label="email verification" tone="good" />
            <h1>Verify your email</h1>
            <p>Use the secure one-time token from your email to activate production login.</p>
            <label htmlFor="app-verify-token">
              Verification token
              <input id="app-verify-token" value={authToken} readOnly />
            </label>
            {error ? <span className="form-error">{error}</span> : null}
            <button type="button" className="primary-button" onClick={() => void submitVerifyEmail()} disabled={isLoading || !authToken}>
              {isLoading ? "Verifying" : "Verify email"}
              <ArrowRight size={17} />
            </button>
            <a className="secondary-link" href="/">Back to login</a>
          </section>
        </main>
      </div>
    );
  }

  if (window.location.pathname === "/password-reset") {
    return (
      <div className="marketing-shell">
        <header className="marketing-nav">
          <a className="marketing-brand" href="/" aria-label="QuantOps home"><BrainCircuit size={24} /><span>QuantOps</span></a>
          <nav aria-label="Auth navigation"><a href="/">Home</a><a href="/terms">Terms</a></nav>
        </header>
        <main className="auth-utility-page">
          <section className="signin-card">
            <Badge label="password reset" tone="warn" />
            <h1>{authToken ? "Choose a new password" : "Request a reset link"}</h1>
            {authToken ? (
              <>
                <label htmlFor="app-reset-token">
                  Reset token
                  <input id="app-reset-token" value={authToken} readOnly />
                </label>
                <label htmlFor="app-new-password">
                  New password
                  <input id="app-new-password" type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} />
                </label>
                <button type="button" className="primary-button" onClick={() => void submitPasswordResetConfirm()} disabled={isLoading || newPassword.length < 8}>
                  {isLoading ? "Updating" : "Update password"}
                  <ArrowRight size={17} />
                </button>
              </>
            ) : (
              <>
                <label htmlFor="app-account-email">
                  Account email
                  <input id="app-account-email" value={email} onChange={(event) => setEmail(event.target.value)} />
                </label>
                <button type="button" className="primary-button" onClick={() => void submitPasswordResetRequest()} disabled={isLoading}>
                  {isLoading ? "Sending" : "Send reset link"}
                  <ArrowRight size={17} />
                </button>
              </>
            )}
            {error ? <span className="form-error">{error}</span> : null}
            <a className="secondary-link" href="/">Back to login</a>
          </section>
        </main>
      </div>
    );
  }

  if (legalPage) {
    return (
      <div className="marketing-shell">
        <header className="marketing-nav">
          <a className="marketing-brand" href="/" aria-label="QuantOps home">
            <BrainCircuit size={24} />
            <span>QuantOps</span>
          </a>
          <nav aria-label="Legal navigation">
            <a href="/privacy">Privacy</a>
            <a href="/terms">Terms</a>
            <a href="/risk-disclaimer">Risk</a>
            <a href="/compliance">Compliance</a>
            <a href="/">Home</a>
          </nav>
        </header>
        <main className="legal-page">
          <span className="hero-kicker">QuantOps policy</span>
          <h1>{legalPage.title}</h1>
          {legalPage.body.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
          <a className="primary-button" href="/">Back to QuantOps</a>
        </main>
      </div>
    );
  }

  return (
    <div className="marketing-shell">
      <header className="marketing-nav">
        <a className="marketing-brand" href="#top" aria-label="QuantOps home">
          <BrainCircuit size={24} />
          <span>QuantOps</span>
        </a>
        <nav aria-label="Landing page navigation">
          <button type="button" onClick={() => goToSection("features")}>Features</button>
          <button type="button" onClick={() => goToSection("examples")}>Examples</button>
          <button type="button" onClick={() => goToSection("pricing")}>Pricing</button>
          <button type="button" onClick={() => goToSection("faq")}>FAQ</button>
          <button type="button" onClick={() => goToSection("login")}>Login</button>
        </nav>
        <button type="button" className="nav-cta" onClick={() => { setMode("signup"); goToSection("signup", "landing_cta_clicked", "nav_start_free"); }} disabled={isLoading}>
          Start free
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
              <button type="button" className="primary-button primary-button--xl" onClick={() => { setMode("signup"); goToSection("signup", "landing_cta_clicked", "hero_create_workspace"); }} disabled={isLoading}>
                Create a free workspace
                <ArrowRight size={18} />
              </button>
              <button type="button" className="secondary-button" onClick={() => { trackLanding("landing_cta_clicked", { cta: "demo_login", section: "hero" }); void submit(); }} disabled={isLoading}>
                Open admin demo
              </button>
              <button type="button" className="ghost-button" onClick={() => goToSection("pricing", "landing_cta_clicked", "hero_view_pricing")}>View pricing</button>
            </div>
            <div className="hero-stats" aria-label="Product highlights">
              <div><strong>10+</strong><span>Strategy paths</span></div>
              <div><strong>24h</strong><span>Refresh workflow</span></div>
              <div><strong>0%</strong><span>Real capital risk</span></div>
            </div>
          </div>

          <section className="signin-card" id="login" aria-labelledby="login-heading">
            <div className="signin-card__header">
              <Badge label={mode === "signup" ? "free signup" : "secure login"} tone="good" />
              <span>No broker connection. Fake-money only.</span>
            </div>
            <h2 id="login-heading">{mode === "signup" ? "Create your workspace" : "Enter your workspace"}</h2>
            <p>{mode === "signup" ? "Start on the free tier, then upgrade only when you need premium compute." : "Use a demo account or your own account to enter the SaaS-style control room."}</p>
            <div className="auth-mode-switch" id="signup">
              <button type="button" className={mode === "login" ? "chip chip--active" : "chip"} onClick={() => { setMode("login"); trackLanding("landing_section_view", { section: "login" }); }}>Login</button>
              <button type="button" className={mode === "signup" ? "chip chip--active" : "chip"} onClick={() => { setMode("signup"); trackLanding("landing_section_view", { section: "signup" }); }}>Sign up</button>
            </div>
            {mode === "signup" ? (
              <>
                <label htmlFor="app-signup-name">
                  Name
                  <input id="app-signup-name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
                </label>
                <label htmlFor="app-signup-org">
                  Organization
                  <input id="app-signup-org" value={organizationName} onChange={(event) => setOrganizationName(event.target.value)} />
                </label>
              </>
            ) : null}
            <label htmlFor="app-email">
              Email
              <input id="app-email" value={email} onChange={(event) => setEmail(event.target.value)} />
            </label>
            <label htmlFor="app-password">
              Password
              <input id="app-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
            </label>
            <div className="button-cluster">
              <button type="button" className="ghost-button" onClick={() => useDemo("admin")}>Use admin demo</button>
              <button type="button" className="ghost-button" onClick={() => useDemo("user")}>Use free user demo</button>
            </div>
            {error ? <span className="form-error">{error}</span> : null}
            <button type="button" className="primary-button" onClick={() => mode === "signup" ? void submitSignup() : void submit()} disabled={isLoading}>
              {isLoading ? "Working" : mode === "signup" ? "Create free workspace" : "Enter workspace"}
              <ArrowRight size={17} />
            </button>
            <small>Admin demo: demo@quantops.local / quantops-demo. Free user demo: user@quantops.local / quantops-user.</small>
          </section>
        </section>

        <section className="landing-section" id="features">
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
                <button type="button" className="link-button" onClick={() => { setMode("signup"); goToSection("signup", "landing_cta_clicked", `feature_${card.title.toLowerCase().replaceAll(" ", "_")}`); }}>Open tool <ArrowRight size={15} /></button>
              </article>
            ))}
          </div>
        </section>

        <section className="landing-section" id="examples">
          <div className="section-heading">
            <span className="hero-kicker">Example workflows</span>
            <h2>Realistic use cases that show what the platform actually does</h2>
            <p>Each example mirrors a workflow already supported by the backend: data ingestion, validation, sentiment, paper deployment, and operational review.</p>
          </div>
          <div className="example-grid">
            {landingExamples.map((example) => (
              <article className="example-card" key={example.title}>
                <Badge label={example.metric} tone="good" />
                <h3>{example.title}</h3>
                <p>{example.setup}</p>
                <strong>{example.outcome}</strong>
              </article>
            ))}
          </div>
        </section>

        <section className="landing-section" id="pricing">
          <div className="section-heading">
            <span className="hero-kicker">Simple pricing</span>
            <h2>Start free, unlock premium workflows when you are ready</h2>
            <p>Free users can learn the product and inspect saved examples. Paid tiers unlock compute-heavy jobs such as backtests, sentiment accumulation, refresh jobs, and paper-agent deployment.</p>
          </div>
          <div className="pricing-grid">
            {landingPlans.map((plan) => (
              <article className={plan.name === "Pro" ? "pricing-card pricing-card--featured" : "pricing-card"} key={plan.name}>
                <div className="pricing-card__top">
                  <Badge label={plan.name === "Pro" ? "Recommended" : plan.name} tone={plan.name === "Pro" ? "warn" : "neutral"} />
                  <span>{plan.name === "Free" ? "Starter" : "Premium"}</span>
                </div>
                <h3>{plan.name}</h3>
                <strong>{plan.price}<small>/month</small></strong>
                <p>{plan.body}</p>
                <ul>
                  {plan.features.map((feature) => <li key={feature}><CheckCircle2 size={16} />{feature}</li>)}
                </ul>
                <button type="button" className={plan.name === "Free" ? "secondary-button" : "primary-button"} onClick={() => { setMode(plan.name === "Free" ? "signup" : "login"); goToSection(plan.name === "Free" ? "signup" : "login", "landing_cta_clicked", `pricing_${plan.name.toLowerCase()}`); }}>
                  {plan.name === "Free" ? "Start free" : "Login to upgrade"}
                </button>
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

        <section className="landing-section" id="faq">
          <div className="section-heading">
            <span className="hero-kicker">FAQ</span>
            <h2>Clear answers before someone creates an account</h2>
            <p>The page is written for first-time visitors who need trust, not jargon.</p>
          </div>
          <div className="faq-grid">
            {landingFaqs.map((faq) => (
              <article className="faq-card" key={faq.question}>
                <h3>{faq.question}</h3>
                <p>{faq.answer}</p>
              </article>
            ))}
          </div>
        </section>
      </main>

      <footer className="marketing-footer">
        <span>QuantOps research cockpit</span>
        <button type="button" className="secondary-link" onClick={() => goToSection("login", "landing_cta_clicked", "footer_login")}>Login</button>
        <button type="button" className="secondary-link" onClick={() => { setMode("signup"); goToSection("signup", "landing_cta_clicked", "footer_signup"); }}>Sign up</button>
        <a className="secondary-link" href="/privacy">Privacy</a>
        <a className="secondary-link" href="/terms">Terms</a>
        <a className="secondary-link" href="/risk-disclaimer">Risk disclaimer</a>
        <a className="secondary-link" href="/compliance">Compliance</a>
      </footer>
    </div>
  );
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
