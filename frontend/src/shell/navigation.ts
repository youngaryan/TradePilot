/**
 * Information architecture.
 *
 * One product, one navigation tree. Sections are grouped by *what the user is
 * doing* — research the idea, validate it, run it, then operate the workspace —
 * with workspace management and platform administration kept visibly separate.
 *
 * Visibility is driven by server-provided capabilities via `requires`. Hiding an
 * item is presentation only; the API remains the authorization boundary and
 * every route also re-checks before rendering.
 */

export type NavGroupId = "research" | "workspace" | "platform" | "account";

export type NavRequirement = "viewManagement" | "administerPlatform";

export interface NavChild {
  id: string;
  label: string;
  path: string;
  description: string;
}

export interface NavItem {
  id: string;
  label: string;
  path: string;
  group: NavGroupId;
  /** One-line answer to "what is this screen for?". Shown in search + headers. */
  description: string;
  /** Icon name resolved by the shell (keeps this module free of JSX). */
  icon: IconName;
  requires?: NavRequirement;
  children?: NavChild[];
  /** Legacy `#/app/<view>` identifier this screen replaces. */
  legacyView?: string;
}

export type IconName =
  | "overview"
  | "strategies"
  | "backtests"
  | "paper"
  | "research"
  | "sentiment"
  | "workspace"
  | "management"
  | "admin"
  | "account"
  | "plans"
  | "learn";

export const NAV_GROUPS: Array<{ id: NavGroupId; label: string }> = [
  { id: "research", label: "Research" },
  { id: "workspace", label: "Workspace" },
  { id: "platform", label: "Platform" },
  { id: "account", label: "Account" },
];

export const NAV_ITEMS: NavItem[] = [
  {
    id: "overview",
    label: "Overview",
    path: "/overview",
    group: "research",
    icon: "overview",
    legacyView: "command",
    description: "Simulated portfolio, open paper agents, recent validation runs, and data freshness.",
  },
  {
    id: "strategies",
    label: "Strategies",
    path: "/strategies",
    group: "research",
    icon: "strategies",
    description: "Browse the strategy library, describe a new idea in plain English, and review specifications.",
    children: [
      { id: "strategies-library", label: "Library", path: "/strategies", description: "Built-in, benchmark, workspace, and community strategies." },
      { id: "strategies-builder", label: "Builder", path: "/strategies/builder", description: "Turn a written idea into a validated, backtestable specification." },
      { id: "strategies-community", label: "Community", path: "/strategies/community", description: "Published workspace strategies and your subscriptions." },
    ],
  },
  {
    id: "backtests",
    label: "Backtests",
    path: "/backtests",
    group: "research",
    icon: "backtests",
    legacyView: "backtests",
    description: "Validate a strategy against history with walk-forward folds, evidence, and an overfitting check.",
  },
  {
    id: "paper",
    label: "Paper trading",
    path: "/paper",
    group: "research",
    icon: "paper",
    legacyView: "live",
    description: "Deploy validated strategies as simulated agents and monitor them forward in time.",
  },
  {
    id: "research",
    label: "AI research",
    path: "/research",
    group: "research",
    icon: "research",
    legacyView: "research",
    description: "Run an informational multi-analyst review of a ticker with linked evidence and provenance.",
  },
  {
    id: "sentiment",
    label: "Data & sentiment",
    path: "/sentiment",
    group: "research",
    icon: "sentiment",
    legacyView: "sentiment",
    description: "Build the news dataset, inspect scored headlines, and compare against financial events.",
  },
  {
    id: "workspace",
    label: "Workspace",
    path: "/workspace",
    group: "workspace",
    icon: "workspace",
    legacyView: "workspace",
    description: "Setup checklist, projects, saved experiments, paper-agent records, reports, and data sources.",
  },
  {
    id: "management",
    label: "Management",
    path: "/management",
    group: "workspace",
    icon: "management",
    requires: "viewManagement",
    description: "Workspace-level oversight: shared research, agents, data configuration, and subscription state.",
  },
  {
    id: "admin",
    label: "Administration",
    path: "/admin",
    group: "platform",
    icon: "admin",
    requires: "administerPlatform",
    legacyView: "admin",
    description: "Platform-wide accounts, subscriptions, telemetry, audit activity, and system health.",
  },
  {
    id: "account",
    label: "Account",
    path: "/account",
    group: "account",
    icon: "account",
    legacyView: "account",
    description: "Profile, email verification, password, MFA, data export, and account deletion.",
  },
  {
    id: "plans",
    label: "Plans & billing",
    path: "/pricing",
    group: "account",
    icon: "plans",
    legacyView: "pricing",
    description: "Compare plans, understand premium access, and manage the workspace subscription.",
  },
  {
    id: "learn",
    label: "Learn",
    path: "/learn",
    group: "account",
    icon: "learn",
    legacyView: "system",
    description: "Definitions, architecture, and plain-language explanations for every metric in the product.",
  },
];

/** Legacy `#/app/<view>` identifiers mapped onto canonical paths. */
export const LEGACY_HASH_ROUTES: Record<string, string> = NAV_ITEMS.reduce<Record<string, string>>(
  (accumulator, item) => {
    if (item.legacyView) accumulator[item.legacyView] = item.path;
    return accumulator;
  },
  {},
);

export function navItemForPath(pathname: string): NavItem | undefined {
  const path = pathname.replace(/\/+$/, "") || "/";
  // Longest match first so `/strategies/builder` resolves to Strategies.
  return [...NAV_ITEMS]
    .sort((a, b) => b.path.length - a.path.length)
    .find((item) => path === item.path || path.startsWith(`${item.path}/`));
}

export function visibleNavItems(capabilities: { viewManagement: boolean; administerPlatform: boolean }): NavItem[] {
  return NAV_ITEMS.filter((item) => {
    if (item.requires === "viewManagement") return capabilities.viewManagement;
    if (item.requires === "administerPlatform") return capabilities.administerPlatform;
    return true;
  });
}
