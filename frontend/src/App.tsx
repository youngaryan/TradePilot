import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import {
  BookOpen,
  BrainCircuit,
  CreditCard,
  FlaskConical,
  LayoutDashboard,
  Newspaper,
  Rocket,
  ShieldCheck,
  UserCircle,
  UserCog,
} from "lucide-react";

import { logout as logoutRequest, setApiAuth, trackTelemetryEvent } from "./api/client";
import type { AuthResponse } from "./api/types";
import { isAdminRole } from "./access/model";
import { LoginScreen } from "./features/LoginScreen";
import { useAppSession } from "./session/useAppSession";
import { THEME_STORAGE_KEY, TELEMETRY_STORAGE_KEY } from "./shell/preferences";
import { BrandMark, BrandWord, InlineNotice } from "./ui";

/**
 * Legacy console adapter (`/classic`).
 *
 * The classic QuantOps console and the newer Apollo dashboard have been unified
 * into a single product shell (see `app/AppRoot`, `shell/AppShell`). Nothing was
 * dropped: every capability the classic console exposed — account, pricing,
 * billing, setup, operations, security, administration, and the learning guides —
 * now lives in the unified navigation.
 *
 * This module remains the compatibility entry point for old addresses. It owns
 * the legacy view registry and translates `#/app/<view>` deep links onto the
 * canonical routes, so bookmarks and shared links keep working. It renders the
 * public sign-in surface when signed out.
 */

type ViewId =
  | "command"
  | "workspace"
  | "account"
  | "pricing"
  | "live"
  | "sentiment"
  | "research"
  | "backtests"
  | "admin"
  | "system";

type ThemeMode = "light" | "dark" | "system";
type TelemetryConsent = "granted" | "denied";

/**
 * Legacy view registry. `route` is where each classic view now lives in the
 * unified information architecture.
 */
const views: Array<{
  id: ViewId;
  label: string;
  description: string;
  route: string;
  icon: ReactNode;
  adminOnly?: boolean;
}> = [
  {
    id: "command",
    label: "Overview",
    description: "Simulated portfolio, validation activity, research output, and data freshness.",
    route: "/overview",
    icon: <LayoutDashboard size={16} />,
  },
  {
    id: "workspace",
    label: "Workspace",
    description: "Onboarding checklist, projects, saved records, data configuration, and operations.",
    route: "/workspace",
    icon: <ShieldCheck size={16} />,
  },
  {
    id: "account",
    label: "Account",
    description: "Email verification, password, MFA, data export, and account deletion.",
    route: "/account",
    icon: <UserCircle size={16} />,
  },
  {
    id: "pricing",
    label: "Plans & billing",
    description: "Compare plans, understand premium access, and manage the workspace subscription.",
    route: "/pricing",
    icon: <CreditCard size={16} />,
  },
  {
    id: "live",
    label: "Paper trading",
    description: "Deploy validated strategies as simulated agents and monitor them forward.",
    route: "/paper",
    icon: <Rocket size={16} />,
  },
  {
    id: "sentiment",
    label: "Data & sentiment",
    description: "Build the news dataset and inspect the headlines behind every score.",
    route: "/sentiment",
    icon: <Newspaper size={16} />,
  },
  {
    id: "research",
    label: "AI research",
    description: "Informational multi-analyst reviews with linked evidence and provenance.",
    route: "/research",
    icon: <BrainCircuit size={16} />,
  },
  {
    id: "backtests",
    label: "Backtests",
    description: "Validate strategies against history with walk-forward folds and an overfitting check.",
    route: "/backtests",
    icon: <FlaskConical size={16} />,
  },
  {
    id: "admin",
    label: "Administration",
    description: "Deployment-wide accounts, subscriptions, telemetry, and operational activity.",
    route: "/admin",
    icon: <UserCog size={16} />,
    adminOnly: true,
  },
  {
    id: "system",
    label: "Learn",
    description: "Definitions, architecture, and explanations for every important number.",
    route: "/learn",
    icon: <BookOpen size={16} />,
  },
];

/** Views the server gates behind an active paid plan for compute-heavy jobs. */
const premiumViews = new Set<ViewId>(["live", "sentiment", "research", "backtests"]);

/**
 * Destination module for each legacy view, used to warm the code-split chunk
 * before redirecting so the unified screen paints without a second wait.
 */
const LEGACY_VIEW_PREFETCH: Partial<Record<ViewId, () => Promise<unknown>>> = {
  // Overview ships in the main bundle, so only the code-split screens are listed.
  workspace: () => import("./features/SaaSWorkspace"),
  account: () => import("./features/AccountSecurity"),
  pricing: () => import("./features/PricingPage"),
  live: () => import("./features/LiveOps"),
  sentiment: () => import("./features/SentimentLab"),
  research: () => import("./features/MarketResearchLab"),
  backtests: () => import("./features/BacktestLab"),
  admin: () => import("./features/AdminDashboard"),
  system: () => import("./features/SystemGuide"),
};

export function viewFromLocationHash(): ViewId | null {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const candidate = hash.startsWith("app/") ? hash.slice("app/".length).split(/[/?]/)[0] : "";
  return views.some((view) => view.id === candidate) ? (candidate as ViewId) : null;
}

export function routeForLegacyView(view: ViewId): string {
  return views.find((item) => item.id === view)?.route ?? "/overview";
}

/** Legacy hash for a view, retained so old links can still be generated. */
export function appViewHash(view: ViewId): string {
  return `#/app/${view}`;
}

export default function App() {
  const session = useAppSession();
  const navigate = useNavigate();
  const [paymentWallReason, setPaymentWallReason] = useState<string | null>(null);
  const [themeMode] = useState<ThemeMode>(
    () => (window.localStorage.getItem(THEME_STORAGE_KEY) as ThemeMode | null) ?? "light",
  );
  const [telemetryConsent] = useState<TelemetryConsent>(
    () => (window.localStorage.getItem(TELEMETRY_STORAGE_KEY) as TelemetryConsent | null) ?? "granted",
  );

  const isAdminAccess = isAdminRole(session.auth?.user.role);
  const hasPremiumAccess = session.hasPremiumAccess;

  // Keep the resolved theme applied on this compatibility entry point too, so a
  // legacy address never flashes the wrong colour scheme before redirecting.
  useEffect(() => {
    const resolved = themeMode === "system"
      ? (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : themeMode;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.style.colorScheme = resolved;
  }, [themeMode]);

  const requestedView = useMemo(() => viewFromLocationHash(), []);

  // Translate legacy `#/app/<view>` deep links onto the canonical route. A bare
  // `/classic` with no view in the hash shows the short "where things moved"
  // summary instead of silently redirecting, so a returning user can see how the
  // classic console maps onto the unified navigation.
  useEffect(() => {
    if (!session.auth) return undefined;

    const resolve = () => {
      const view = viewFromLocationHash();
      if (!view) return;
      const meta = views.find((item) => item.id === view);
      if (meta?.adminOnly && !isAdminAccess) {
        setPaymentWallReason(
          "Platform administration requires the administrator role on your account. It is not granted by a paid plan.",
        );
        navigate("/pricing", { replace: true });
        return;
      }
      if (premiumViews.has(view) && !hasPremiumAccess) {
        // The destination screen explains the gate in place, so send the user
        // there rather than diverting them away from what they asked for.
        setPaymentWallReason(null);
      }
      void LEGACY_VIEW_PREFETCH[view]?.().catch(() => undefined);
      navigate(routeForLegacyView(view), { replace: true });
    };

    resolve();
    window.addEventListener("hashchange", resolve);
    return () => window.removeEventListener("hashchange", resolve);
  }, [session.auth, isAdminAccess, hasPremiumAccess, navigate]);

  useEffect(() => {
    if (!session.auth) return;
    void trackTelemetryEvent({
      name: "legacy_console_redirect",
      category: "product",
      properties: { requested_view: requestedView ?? "command" },
      context: { path: "/classic", resolved_theme: document.documentElement.dataset.theme },
      consent: telemetryConsent,
    }).catch(() => undefined);
  }, [session.auth, requestedView, telemetryConsent]);

  function handleLogin(nextAuth: AuthResponse) {
    setApiAuth(null, nextAuth.active_organization_id);
    session.handleLogin(nextAuth);
  }

  /** Retained for parity with the previous console's sign-out behaviour. */
  async function handleLogout() {
    try {
      await logoutRequest();
    } catch {
      // Clearing local state still matters if the server session already expired.
    }
    await session.handleLogout();
  }

  if (!session.auth) {
    return <LoginScreen onLogin={handleLogin} />;
  }

  return (
    <div className="marketing-shell">
      <header className="marketing-nav">
        <span className="marketing-brand">
          <BrandMark size={24} />
          <BrandWord descriptor={null} />
        </span>
      </header>
      <main className="auth-utility-page">
        <div className="signin-card">
          <h1>Taking you to the unified workspace</h1>
          <p>
            The classic console and the Apollo dashboard are now one product. Every capability that lived here —
            account, plans and billing, setup, operations, security, administration, and the learning guides — is in the
            main navigation.
          </p>
          {paymentWallReason ? (
            <InlineNotice tone="elevated" title="Access note">{paymentWallReason}</InlineNotice>
          ) : null}
          <ul className="principle-list">
            {views
              .filter((view) => !view.adminOnly || isAdminAccess)
              .map((view) => (
                <li key={view.id}>
                  {view.icon}
                  <span>
                    <strong style={{ color: "var(--text-primary)" }}>{view.label}</strong>
                    <br />
                    {view.description}
                  </span>
                </li>
              ))}
          </ul>
          <div className="button-row">
            <button type="button" className="primary-button" onClick={() => navigate("/overview")}>
              Continue to Meridian
            </button>
            <button type="button" className="ghost-button" onClick={() => void handleLogout()}>
              Sign out
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
