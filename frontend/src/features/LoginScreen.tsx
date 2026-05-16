import { useEffect, useState } from "react";
import { ArrowRight, BrainCircuit, CheckCircle2, FlaskConical, Newspaper, Rocket, ShieldCheck, Sparkles } from "lucide-react";

import { confirmPasswordReset, login as loginRequest, requestPasswordReset, signup as signupRequest, trackTelemetryEvent, verifyEmail } from "../api/client";
import type { AuthResponse } from "../api/types";
import { Badge } from "../components/Badge";

const LANDING_ANON_STORAGE_KEY = "quantops.landing_visitor";
const landingFeatureCards = [
  { title: "Backtest", body: "A ready-to-use backtest engine with multi-asset, multi-algorithm, and composable strategy definitions.", icon: <FlaskConical size={20} /> },
  { title: "Paper agents", body: "Configure, preview, and deploy fake-money agents that trade through time on a simulated broker ledger.", icon: <Rocket size={20} /> },
  { title: "Sentiment", body: "Pull news, RSS, local web, or FinBERT signals into your signal pipeline without a separate data pipeline.", icon: <Newspaper size={20} /> },
  { title: "Market research", body: "Describe a question. The research agent scans the knowledge base with the LLM planner and saves artifacts.", icon: <BrainCircuit size={20} /> },
  { title: "Admin", body: "System-level view for workspace metadata, user management, and platform configuration.", icon: <ShieldCheck size={20} /> }
];
const landingExamples = [
  { title: "ETF trend follower", setup: "Backtest a 200-day SMA timing model on SPY/QQQ/IWM, check the PnL and drawdown, then deploy as a paper agent.", metric: "2m backtest", outcome: "Paper agent deployed with ~38% win rate over 3 years." },
  { title: "Sector rotation", setup: "Rank SPY sector ETFs by 6-month momentum then weight top-3 equally. Compare static vs. dynamic weights.", metric: "12m replay", outcome: "Dynamic version beat static by ~4% annualized." },
  { title: "Event-driven mean reversion", setup: "Score stock baskets on RSI + sentiment z-score. Take the most oversold names with positive sentiment drift.", metric: "6m window", outcome: "Win rate near 56% with lower drawdown." },
  { title: "Statistical arbitrage", setup: "Run a pair screen on the full universe. Deploy baskets calibrated on 3-month residual history.", metric: "62 pairs", outcome: "Market-neutral portfolio with 0.08 Sharpe." }
];
const landingPlans = [
  { name: "Free", price: "Free", body: "Ideal for first-time evaluation and inspecting saved examples.", features: ["Saved example backtests & research", "Tutorial sentiment and paper runs", "1 concurrent paper agent", "Standard job queue priority"] },
  { name: "Pro", price: "$79", body: "For self-directed researchers who need recurring refresh and longer history.", features: ["10 concurrent paper agents", "Unlimited backtests & research", "Sentiment & official-event refresh jobs", "Higher job queue priority", "Advanced strategy methods"] }
];
const landingFaqs = [
  { question: "Do I need a credit card to start?", answer: "No. The free tier includes saved examples and tutorial workflows. Paid plans add concurrency and refresh jobs." },
  { question: "What markets are supported?", answer: "Any daily OHLCV data in the price file format. Typical samples include US equities, ETFs, and crypto." },
  { question: "How do I add my own data?", answer: "Upload a file to the configured data directory or mount an S3 prefix. The workspace page has file-upload instructions." },
  { question: "Is this real trading?", answer: "No. QuantOps is fake-money paper trading only. There is no broker connection, no real orders, and no cash movement." }
];
const workflowSteps = [
  "Open a backtest to validate a signal idea against historical data.",
  "Add research to gather context and optional sentiment scores.",
  "Tune parameters and review the decision with a human-readable audit.",
  "Configure a paper agent, choose a date, and deploy fake-money runs.",
  "Watch the ledger change, inspect orders, and repeat the cycle."
];
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

function landingVisitorId() {
  const existing = window.localStorage.getItem(LANDING_ANON_STORAGE_KEY);
  if (existing) return existing;
  const next = `visitor_${Math.random().toString(36).slice(2)}_${Date.now().toString(36)}`;
  window.localStorage.setItem(LANDING_ANON_STORAGE_KEY, next);
  return next;
}

export function LoginScreen({ onLogin }: { onLogin: (auth: AuthResponse) => void }) {
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
