import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  CheckCircle2,
  FlaskConical,
  Layers,
  Newspaper,
  Activity,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { Link, useLocation, useSearchParams } from "react-router";

import {
  confirmPasswordReset,
  getPricing,
  login as loginRequest,
  requestPasswordReset,
  signup as signupRequest,
  trackTelemetryEvent,
  verifyEmail,
} from "../api/client";
import type { AuthResponse, PricingPlan } from "../api/types";
import {
  BrandMark,
  BrandWord,
  Button,
  InlineNotice,
  SegmentedControl,
  Tag,
  TextInput,
} from "../ui";

/**
 * Public product surface.
 *
 * Serves three jobs from one module, chosen by pathname:
 *   1. the product overview + sign-in / apply page,
 *   2. the legal and compliance pages, and
 *   3. the email-verification and password-reset utility pages.
 *
 * Plan information is read from the public `/api/billing/pricing` endpoint —
 * nothing about pricing or performance is hardcoded here.
 */

const LANDING_ANON_STORAGE_KEY = "quantops.landing_visitor";
const demoCredentialsEnabled = import.meta.env.DEV || import.meta.env.VITE_ENABLE_DEMO_LOGIN === "true";

const capabilityCards = [
  {
    title: "Strategy definition",
    body: "Start from the built-in library or describe an idea in plain English. The builder returns a reviewable specification with entry rules, exit rules, parameters, and stated limitations.",
    icon: <Layers size={18} />,
  },
  {
    title: "Historical validation",
    body: "Walk-forward backtests with purge and embargo windows, per-fold metrics, trade markers, readiness checks, and a probability-of-backtest-overfitting estimate.",
    icon: <FlaskConical size={18} />,
  },
  {
    title: "Simulated paper trading",
    body: "Promote a validated run to a simulated agent. Track equity, target weights, holdings, and orders forward in time against a paper ledger.",
    icon: <Activity size={18} />,
  },
  {
    title: "Evidence-linked research",
    body: "An informational multi-analyst review of a ticker — bull, bear, technical, and risk perspectives — with source links, freshness, and missing-data indicators.",
    icon: <Sparkles size={18} />,
  },
  {
    title: "News and sentiment datasets",
    body: "Build datasets from RSS, local web search, files, or API providers. Inspect the scored headlines behind every signal and compare them with reported financial events.",
    icon: <Newspaper size={18} />,
  },
  {
    title: "Auditable workspaces",
    body: "Projects, saved experiments, agent records, reports, refresh status, and consent-aware telemetry — with workspace and platform roles kept separate.",
    icon: <ShieldCheck size={18} />,
  },
];

const workedExamples = [
  {
    title: "Trend sleeve on liquid ETFs",
    setup: "Pick a moving-average trend rule, a small ETF universe, and a multi-year window. Run walk-forward folds with realistic cost assumptions.",
    outcome: "You get per-fold metrics, a drawdown profile, readiness checks, and an overfitting estimate to decide whether the sleeve deserves a simulated deployment.",
    metric: "Validation workflow",
  },
  {
    title: "Sentiment-aware event review",
    setup: "Build a news dataset for a watchlist, then line the scored headlines up against reported earnings and filing events in the same window.",
    outcome: "You can read exactly which headlines and events sit behind a score before any strategy consumes them.",
    metric: "Evidence workflow",
  },
  {
    title: "Agent comparison room",
    setup: "Deploy several validated strategies as simulated agents with different symbols and intervals.",
    outcome: "Compare simulated equity, orders, exposure, and warnings side by side. No broker is connected at any point.",
    metric: "Simulation workflow",
  },
];

const journeySteps = [
  "Define the idea — from the library, or written in plain English.",
  "Validate it against history with walk-forward folds and an overfitting check.",
  "Review the evidence: metrics, per-fold behaviour, warnings, and provenance.",
  "Deploy the validated strategy as a simulated paper agent.",
  "Monitor it forward, then iterate with research and sentiment data.",
];

const faqs = [
  {
    question: "Does Meridian place real trades?",
    answer:
      "No. There is no broker connection and no real-money order path. Paper equity, positions, and profit and loss are simulated by an internal ledger.",
  },
  {
    question: "What can I do without paying?",
    answer:
      "Free workspaces can browse the strategy library, use the strategy builder, read saved experiments, reports, and agent records, and work through the learning material. Compute-heavy jobs require a paid plan because they consume provider and compute capacity.",
  },
  {
    question: "Is the research output financial advice?",
    answer:
      "No. Research output is informational, is generated from the sources it links to, and states its own confidence, freshness, and missing data. It is not a recommendation.",
  },
  {
    question: "Do backtests predict future performance?",
    answer:
      "No. A backtest describes how a rule would have behaved on historical data. Meridian reports an overfitting estimate precisely because strong historical results are easy to manufacture by accident.",
  },
];

const legalPages: Record<string, { title: string; body: string[] }> = {
  "/privacy": {
    title: "Privacy policy",
    body: [
      "Meridian stores the account, workspace, telemetry, and research metadata needed to operate the product. Telemetry is minimised, consent-aware, and redacts sensitive fields such as passwords, API keys, email addresses, and tokens.",
      "Research artifacts are scoped to your workspace. Do not upload confidential third-party data unless you have the right to use it.",
      "You can export your account data and request deletion from Account & security inside the application.",
    ],
  },
  "/terms": {
    title: "Terms of use",
    body: [
      "Meridian is a research and simulated paper-trading tool. You are responsible for your data sources, your strategy decisions, and your own compliance obligations.",
      "Premium workflows may require a paid subscription and can be limited by quotas to protect platform reliability.",
      "Access can be suspended where use breaches data-provider terms or platform policy.",
    ],
  },
  "/risk-disclaimer": {
    title: "Risk disclaimer",
    body: [
      "Nothing in Meridian is financial advice. Backtests, sentiment scores, and simulated paper results are educational and may not resemble live trading outcomes.",
      "Historical validation describes the past. It does not predict future results, and a strong backtest can be the product of overfitting rather than a durable edge.",
      "The product does not place real-money broker orders in this version. Real trading should only be added after legal, compliance, and operational review.",
    ],
  },
  "/compliance": {
    title: "Compliance boundary",
    body: [
      "Meridian is limited to research workflows and simulated paper trading. The product must not be marketed as investment advice, guaranteed performance, signal recommendations, or automated real-money trading.",
      "Commercial deployments must review data-provider terms, maintain risk disclaimers, honour privacy, export and deletion requests, and retain billing, telemetry, and user-management audit logs.",
      "Platform administration and workspace management are separate authorities, and subscription status never grants either.",
    ],
  },
};

function landingVisitorId() {
  const existing = window.localStorage.getItem(LANDING_ANON_STORAGE_KEY);
  if (existing) return existing;
  const next = `visitor_${Math.random().toString(36).slice(2)}_${Date.now().toString(36)}`;
  window.localStorage.setItem(LANDING_ANON_STORAGE_KEY, next);
  return next;
}

function PublicChrome({ children, nav }: { children: React.ReactNode; nav?: React.ReactNode }) {
  return (
    <div className="marketing-shell">
      <header className="marketing-nav">
        <Link to="/" className="marketing-brand" aria-label="Meridian home">
          <BrandMark size={24} />
          <BrandWord descriptor={null} />
        </Link>
        {nav}
      </header>
      {children}
      <footer className="marketing-footer">
        <span>© Meridian · research and simulated paper trading only</span>
        <span className="shell-footer__links">
          <Link className="secondary-link" to="/">Sign in</Link>
          <a className="secondary-link" href="/privacy">Privacy</a>
          <a className="secondary-link" href="/terms">Terms</a>
          <a className="secondary-link" href="/risk-disclaimer">Risk disclaimer</a>
          <a className="secondary-link" href="/compliance">Compliance</a>
        </span>
      </footer>
    </div>
  );
}

export function LoginScreen({ onLogin }: { onLogin: (auth: AuthResponse) => void }) {
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [plans, setPlans] = useState<PricingPlan[] | null>(null);
  const [plansError, setPlansError] = useState<string | null>(null);
  const legalPage = legalPages[location.pathname];
  const authToken = searchParams.get("token") ?? "";
  const isUtilityRoute = location.pathname === "/verify-email" || location.pathname === "/password-reset";

  function trackLanding(name: string, properties: Record<string, unknown> = {}) {
    void trackTelemetryEvent({
      name,
      category: "landing",
      properties,
      context: {
        path: window.location.pathname,
        hash: window.location.hash || "#top",
        viewport_width: window.innerWidth,
        page: "landing",
      },
      anonymous_id: landingVisitorId(),
      consent: "granted",
    }).catch(() => undefined);
  }

  useEffect(() => {
    if (legalPage || isUtilityRoute) return undefined;
    trackLanding("landing_page_view", { section: "top" });
    let cancelled = false;
    void getPricing()
      .then((payload) => {
        if (!cancelled) setPlans(payload.plans ?? []);
      })
      .catch((caught) => {
        if (!cancelled) setPlansError(caught instanceof Error ? caught.message : "Plan details are unavailable.");
      });
    if (typeof IntersectionObserver === "undefined") return () => { cancelled = true; };
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
      { threshold: 0.42 },
    );
    ["features", "examples", "pricing", "faq", "login", "signup"].forEach((id) => {
      const node = document.getElementById(id);
      if (node) observer.observe(node);
    });
    return () => {
      cancelled = true;
      observer.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);

  function goToSection(section: string, eventName = "landing_nav_clicked", cta?: string) {
    trackLanding(eventName, { section, target_section: section, ...(cta ? { cta } : {}) });
    document.getElementById(section)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function submit() {
    setIsLoading(true);
    setError(null);
    try {
      trackLanding("auth_login_started", { section: "login" });
      const auth = await loginRequest(email, password);
      trackLanding("auth_login_completed", { section: "login", role: auth.user.role, organization_count: auth.organizations.length });
      onLogin(auth);
    } catch (caught) {
      trackLanding("auth_login_failed", { section: "login" });
      setError(caught instanceof Error ? caught.message : "Sign-in failed.");
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
        display_name: displayName || email.split("@")[0],
        organization_name: organizationName || "Northstar Quant Lab",
      });
      trackLanding("auth_signup_completed", { section: "signup", plan: "free" });
      onLogin(auth);
    } catch (caught) {
      trackLanding("auth_signup_failed", { section: "signup" });
      setError(caught instanceof Error ? caught.message : "Could not create the workspace.");
    } finally {
      setIsLoading(false);
    }
  }

  /** Development helper: prefills demo credentials, never signs in silently. */
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
    setNotice(null);
    try {
      await verifyEmail(authToken);
      setNotice("Email verified. You can sign in now.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Email verification failed.");
    } finally {
      setIsLoading(false);
    }
  }

  async function submitPasswordResetRequest() {
    setIsLoading(true);
    setError(null);
    setNotice(null);
    try {
      await requestPasswordReset(email);
      setNotice("If the account exists, reset instructions were sent. In development they are written to artifacts/email_outbox.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Password reset request failed.");
    } finally {
      setIsLoading(false);
    }
  }

  async function submitPasswordResetConfirm() {
    setIsLoading(true);
    setError(null);
    setNotice(null);
    try {
      await confirmPasswordReset(authToken, newPassword);
      setNotice("Password updated. You can sign in now.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Password reset failed.");
    } finally {
      setIsLoading(false);
    }
  }

  const resolvedPlans = useMemo(() => plans ?? [], [plans]);

  if (location.pathname === "/verify-email") {
    return (
      <PublicChrome
        nav={
          <nav aria-label="Auth navigation">
            <Link to="/">Sign in</Link>
            <a href="/terms">Terms</a>
          </nav>
        }
      >
        <main className="auth-utility-page">
          <section className="signin-card">
            <Tag tone="info">Email verification</Tag>
            <h1>Verify your email</h1>
            <p>Use the single-use token from your verification email to activate sign-in.</p>
            <TextInput label="Verification token" value={authToken} onChange={() => undefined} readOnly mono />
            {error ? <InlineNotice tone="bad" role="alert">{error}</InlineNotice> : null}
            {notice ? <InlineNotice tone="good" role="status">{notice}</InlineNotice> : null}
            <Button
              variant="primary"
              block
              iconEnd={<ArrowRight size={16} />}
              onClick={() => void submitVerifyEmail()}
              disabled={isLoading || !authToken}
            >
              {isLoading ? "Verifying…" : "Verify email"}
            </Button>
            <Link className="secondary-link" to="/">Back to sign in</Link>
          </section>
        </main>
      </PublicChrome>
    );
  }

  if (location.pathname === "/password-reset") {
    return (
      <PublicChrome
        nav={
          <nav aria-label="Auth navigation">
            <Link to="/">Sign in</Link>
            <a href="/terms">Terms</a>
          </nav>
        }
      >
        <main className="auth-utility-page">
          <section className="signin-card">
            <Tag tone="warn">Password reset</Tag>
            <h1>{authToken ? "Choose a new password" : "Request a reset link"}</h1>
            {authToken ? (
              <>
                <TextInput label="Reset token" value={authToken} onChange={() => undefined} readOnly mono />
                <TextInput
                  label="New password"
                  type="password"
                  value={newPassword}
                  onChange={setNewPassword}
                  autoComplete="new-password"
                  hint="At least 8 characters."
                />
                <Button
                  variant="primary"
                  block
                  iconEnd={<ArrowRight size={16} />}
                  onClick={() => void submitPasswordResetConfirm()}
                  disabled={isLoading || newPassword.length < 8}
                >
                  {isLoading ? "Updating…" : "Update password"}
                </Button>
              </>
            ) : (
              <>
                <TextInput label="Account email" type="email" value={email} onChange={setEmail} autoComplete="email" />
                <Button
                  variant="primary"
                  block
                  iconEnd={<ArrowRight size={16} />}
                  onClick={() => void submitPasswordResetRequest()}
                  disabled={isLoading}
                >
                  {isLoading ? "Sending…" : "Send reset link"}
                </Button>
              </>
            )}
            {error ? <InlineNotice tone="bad" role="alert">{error}</InlineNotice> : null}
            {notice ? <InlineNotice tone="good" role="status">{notice}</InlineNotice> : null}
            <Link className="secondary-link" to="/">Back to sign in</Link>
          </section>
        </main>
      </PublicChrome>
    );
  }

  if (legalPage) {
    return (
      <PublicChrome
        nav={
          <nav aria-label="Legal navigation">
            <a href="/privacy">Privacy</a>
            <a href="/terms">Terms</a>
            <a href="/risk-disclaimer">Risk</a>
            <a href="/compliance">Compliance</a>
            <Link to="/">Sign in</Link>
          </nav>
        }
      >
        <main className="legal-page">
          <span className="hero-kicker">Meridian policy</span>
          <h1>{legalPage.title}</h1>
          <div className="legal-toc">
            <a href="/privacy">Privacy policy</a>
            <a href="/terms">Terms of use</a>
            <a href="/risk-disclaimer">Risk disclaimer</a>
            <a href="/compliance">Compliance boundary</a>
          </div>
          {legalPage.body.map((paragraph) => (
            <p key={paragraph}>{paragraph}</p>
          ))}
          <Link className="primary-button" to="/">Back to Meridian</Link>
        </main>
      </PublicChrome>
    );
  }

  return (
    <PublicChrome
      nav={
        <>
          <nav aria-label="Product navigation">
            <button type="button" onClick={() => goToSection("features")}>Capabilities</button>
            <button type="button" onClick={() => goToSection("examples")}>Examples</button>
            <button type="button" onClick={() => goToSection("pricing")}>Plans</button>
            <button type="button" onClick={() => goToSection("faq")}>FAQ</button>
            <button type="button" onClick={() => goToSection("login")}>Sign in</button>
          </nav>
          <button
            type="button"
            className="nav-cta"
            disabled={isLoading}
            onClick={() => {
              setMode("signup");
              goToSection("signup", "landing_cta_clicked", "nav_start_free");
            }}
          >
            Create a free workspace
          </button>
        </>
      }
    >
      <main id="top">
        <section className="marketing-hero">
          <div className="hero-copy">
            <span className="hero-kicker">
              <Sparkles size={14} aria-hidden="true" />
              Strategy research terminal
            </span>
            <h1>Take a strategy from idea to evidence before any capital is at risk.</h1>
            <p>
              Meridian is a research workspace for defining strategies, validating them against history with
              walk-forward testing and an overfitting check, and then running them forward as simulated paper
              agents. Every number is traceable to the run that produced it.
            </p>
            <div className="hero-actions">
              <Button
                variant="primary"
                size="lg"
                iconEnd={<ArrowRight size={17} />}
                disabled={isLoading}
                onClick={() => {
                  setMode("signup");
                  goToSection("signup", "landing_cta_clicked", "hero_create_workspace");
                }}
              >
                Create a free workspace
              </Button>
              <Button variant="secondary" size="lg" onClick={() => goToSection("pricing", "landing_cta_clicked", "hero_view_pricing")}>
                Compare plans
              </Button>
              <Link className="ui-link" to="/">Already have an account? Sign in</Link>
            </div>
            <div className="hero-stats" aria-label="Product boundaries">
              <div>
                <strong>None</strong>
                <span>Broker connections</span>
              </div>
              <div>
                <strong>None</strong>
                <span>Real-money orders</span>
              </div>
              <div>
                <strong>100%</strong>
                <span>Simulated capital</span>
              </div>
            </div>
          </div>

          <section className="signin-card" id="login" aria-labelledby="login-heading">
            <div className="signin-card__header">
              <Tag tone="good">Secure session</Tag>
              <span>No broker connection</span>
            </div>
            <h2 id="login-heading">{mode === "signup" ? "Create your workspace" : "Sign in to your workspace"}</h2>
            <p>
              {mode === "signup"
                ? "Start on the free plan. Upgrade only when you need compute-heavy research jobs."
                : "Use your account credentials to enter the research workspace."}
            </p>
            <div id="signup">
              <SegmentedControl
                label="Authentication mode"
                value={mode}
                onChange={(next) => {
                  setMode(next);
                  trackLanding("landing_section_view", { section: next });
                }}
                options={[
                  { value: "login", label: "Sign in" },
                  { value: "signup", label: "Sign up" },
                ]}
              />
            </div>
            {mode === "signup" ? (
              <>
                <TextInput label="Your name" value={displayName} onChange={setDisplayName} autoComplete="name" />
                <TextInput
                  label="Workspace name"
                  value={organizationName}
                  onChange={setOrganizationName}
                  placeholder="Northstar Quant Lab"
                  hint="Shown in the workspace switcher. You can belong to more than one."
                />
              </>
            ) : null}
            <TextInput label="Email" type="email" value={email} onChange={setEmail} autoComplete="email" />
            <TextInput
              label="Password"
              type="password"
              value={password}
              onChange={setPassword}
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
            />
            {demoCredentialsEnabled ? (
              <div className="button-cluster">
                <Button variant="ghost" size="sm" onClick={() => useDemo("admin")}>Use admin demo</Button>
                <Button variant="ghost" size="sm" onClick={() => useDemo("user")}>Use free user demo</Button>
              </div>
            ) : null}
            {error ? <InlineNotice tone="bad" role="alert">{error}</InlineNotice> : null}
            <Button
              variant="primary"
              block
              iconEnd={<ArrowRight size={16} />}
              disabled={isLoading}
              onClick={() => (mode === "signup" ? void submitSignup() : void submit())}
            >
              {isLoading ? "Working…" : mode === "signup" ? "Create a free workspace" : "Sign in"}
            </Button>
            <Link className="secondary-link" to="/password-reset">Forgot your password?</Link>
          </section>
        </section>

        <section className="landing-section" id="features">
          <div className="section-heading">
            <span className="hero-kicker">Capabilities</span>
            <h2>One workspace for the whole research cycle</h2>
            <p>
              Each stage produces a saved, inspectable record — so a result can always be traced back to the
              configuration, data, and validation behind it.
            </p>
          </div>
          <div className="tool-grid">
            {capabilityCards.map((card) => (
              <article className="tool-card" key={card.title}>
                <span>{card.icon}</span>
                <h3>{card.title}</h3>
                <p>{card.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="landing-section" id="examples">
          <div className="section-heading">
            <span className="hero-kicker">Worked examples</span>
            <h2>What a session actually looks like</h2>
            <p>
              These describe workflows the product supports today. They deliberately do not quote performance
              figures — the only numbers worth trusting are the ones your own run produces.
            </p>
          </div>
          <div className="example-grid">
            {workedExamples.map((example) => (
              <article className="example-card" key={example.title}>
                <Tag tone="info">{example.metric}</Tag>
                <h3>{example.title}</h3>
                <p>{example.setup}</p>
                <strong>{example.outcome}</strong>
              </article>
            ))}
          </div>
        </section>

        <section className="landing-section" id="pricing">
          <div className="section-heading">
            <span className="hero-kicker">Plans</span>
            <h2>Free to learn the product, paid for compute</h2>
            <p>
              Free workspaces get the strategy library, the builder, saved records, and the learning material.
              Paid plans unlock the jobs that consume compute and external data capacity.
            </p>
          </div>
          {plansError ? (
            <InlineNotice tone="warn" title="Plan details are unavailable">
              {plansError} Plan comparison is also available inside the application under Plans &amp; billing.
            </InlineNotice>
          ) : plans == null ? (
            <InlineNotice tone="neutral" role="status">Loading plan details…</InlineNotice>
          ) : resolvedPlans.length === 0 ? (
            <InlineNotice tone="neutral">
              This deployment does not publish a plan catalogue. Sign in to see the access your workspace has.
            </InlineNotice>
          ) : (
            <div className="pricing-grid">
              {resolvedPlans.map((plan) => (
                <article
                  className={plan.recommended ? "pricing-card pricing-card--featured" : "pricing-card"}
                  key={plan.id}
                >
                  <div className="pricing-card__top">
                    <Tag tone={plan.recommended ? "brand" : "neutral"}>
                      {plan.recommended ? "Recommended" : plan.premium ? "Premium" : "Starter"}
                    </Tag>
                  </div>
                  <h3>{plan.name}</h3>
                  <strong>
                    {plan.price_monthly === 0
                      ? "Free"
                      : `${plan.currency === "usd" || plan.currency === "USD" ? "$" : ""}${plan.price_monthly}`}
                    {plan.price_monthly === 0 ? null : <small>/month</small>}
                  </strong>
                  <p>{plan.description}</p>
                  <ul>
                    {plan.features.map((feature) => (
                      <li key={feature}>
                        <CheckCircle2 size={14} aria-hidden="true" />
                        {feature}
                      </li>
                    ))}
                  </ul>
                  <Button
                    variant={plan.premium ? "primary" : "secondary"}
                    onClick={() => {
                      setMode(plan.premium ? "login" : "signup");
                      goToSection(plan.premium ? "login" : "signup", "landing_cta_clicked", `pricing_${plan.id}`);
                    }}
                  >
                    {plan.premium ? "Sign in to upgrade" : plan.cta || "Create a free workspace"}
                  </Button>
                </article>
              ))}
            </div>
          )}
        </section>

        <section className="workflow-band" id="workflow">
          <div>
            <span className="hero-kicker">The journey</span>
            <h2>Idea → validation → review → simulation</h2>
          </div>
          <div className="workflow-steps">
            {journeySteps.map((step, index) => (
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
            <h2>The important answers up front</h2>
            <p>Written for someone deciding whether this product is honest about what it does.</p>
          </div>
          <div className="faq-grid">
            {faqs.map((faq) => (
              <article className="faq-card" key={faq.question}>
                <h3>{faq.question}</h3>
                <p>{faq.answer}</p>
              </article>
            ))}
          </div>
          <InlineNotice tone="info" title="Research and simulation only">
            No broker is connected, no real-money orders are placed, and simulated paper equity is not an account
            balance. Research output is informational and is not financial advice.
          </InlineNotice>
          <Link className="ui-link" to="/">
            <BookOpen size={14} aria-hidden="true" />
            Read the risk disclaimer and compliance boundary after signing in
          </Link>
        </section>
      </main>
    </PublicChrome>
  );
}

export default LoginScreen;
