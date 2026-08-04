/**
 * Access model — subscription state and organizational authority are two
 * independent dimensions.
 *
 * Everything here is derived from **server-provided** data only:
 *
 *  - `AuthUser.role`                  → platform authority (`admin` | `user`)
 *  - `Organization.role`              → workspace authority (`owner` | `admin` | `member`),
 *                                       sourced from `organization_members.role`
 *  - `WorkspacePayload.subscription`  → plan + status for the active workspace
 *  - `BillingStatusPayload.premium`   → authoritative premium entitlement
 *  - `BillingStatusPayload.access`    → *why* premium is granted (`admin` vs `subscription`)
 *  - `WorkspacePayload.capabilities`  → server feature switches
 *
 * Nothing in this file is a security boundary. The API enforces authorization
 * (`require_admin_context`, `require_paid_context`, tenant scoping); this module
 * only decides what the interface should *show* and *explain*.
 */

import type {
  AuthUser,
  BillingStatusPayload,
  Organization,
  SubscriptionRecord,
  WorkspacePayload,
} from "../api/types";

/* ------------------------------------------------------------------------- */
/* Organizational authority                                                  */
/* ------------------------------------------------------------------------- */

/** Workspace-membership roles exposed by `organization_members.role`. */
export type OrgRole = "owner" | "admin" | "member" | "unknown";

/** Platform-level role from the `users.role` column. */
export type PlatformRole = "admin" | "user";

export function normalizeOrgRole(role: unknown): OrgRole {
  const value = String(role ?? "").trim().toLowerCase();
  if (value === "owner" || value === "admin" || value === "member") return value;
  return "unknown";
}

/** Retained for existing callers: platform-admin check on `users.role`. */
export function isAdminRole(role: unknown): boolean {
  return String(role ?? "user").toLowerCase() === "admin";
}

/**
 * A workspace manager is an `owner` or `admin` **member of the active
 * workspace**. This is workspace authority only — it grants nothing at the
 * platform level.
 */
export function isWorkspaceManagerRole(role: OrgRole): boolean {
  return role === "owner" || role === "admin";
}

export const ORG_ROLE_LABEL: Record<OrgRole, string> = {
  owner: "Workspace owner",
  admin: "Workspace manager",
  member: "Member",
  unknown: "Member",
};

export const ORG_ROLE_SHORT: Record<OrgRole, string> = {
  owner: "Owner",
  admin: "Manager",
  member: "Member",
  unknown: "Member",
};

/* ------------------------------------------------------------------------- */
/* Subscription state                                                        */
/* ------------------------------------------------------------------------- */

/**
 * Billing lifecycle states we render distinctly. `none` means the workspace has
 * no subscription record at all (a genuinely free workspace).
 */
export type SubscriptionState =
  | "none"
  | "free"
  | "trialing"
  | "active"
  | "past_due"
  | "canceled"
  | "incomplete"
  | "unpaid"
  | "paused"
  | "unknown";

export interface SubscriptionSummary {
  /** Raw plan identifier from the server (`free`, `pro`, `team`, …). */
  plan: string;
  /** Raw status string from the server. */
  status: string;
  state: SubscriptionState;
  /** True when the plan itself is a paid product (regardless of status). */
  isPaidPlan: boolean;
  /** True when the subscription is a trial. */
  isTrial: boolean;
  /**
   * True when a paid plan exists but its status blocks entitlement — the user
   * needs to fix billing rather than upgrade.
   */
  needsBillingAttention: boolean;
  /** Human label for the plan, e.g. "Pro". */
  planLabel: string;
  /** Human label for the state, e.g. "Payment past due". */
  stateLabel: string;
  currentPeriodEndUtc: string | null;
  record: SubscriptionRecord | null;
}

const PAID_PLANS = new Set(["pro", "team", "enterprise", "pro_trial"]);
const ATTENTION_STATES = new Set<SubscriptionState>([
  "past_due",
  "canceled",
  "incomplete",
  "unpaid",
  "paused",
]);

const STATE_LABEL: Record<SubscriptionState, string> = {
  none: "No subscription",
  free: "Free",
  trialing: "Trial",
  active: "Active",
  past_due: "Payment past due",
  canceled: "Canceled",
  incomplete: "Setup incomplete",
  unpaid: "Unpaid",
  paused: "Paused",
  unknown: "Unknown",
};

function normalizeState(status: string, hasRecord: boolean): SubscriptionState {
  if (!hasRecord) return "none";
  const value = status.trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (!value) return "unknown";
  if (value === "active") return "active";
  if (value === "trialing" || value === "trial") return "trialing";
  if (value === "past_due") return "past_due";
  if (value === "canceled" || value === "cancelled") return "canceled";
  if (value === "incomplete" || value === "incomplete_expired") return "incomplete";
  if (value === "unpaid") return "unpaid";
  if (value === "paused") return "paused";
  if (value === "free" || value === "none") return "free";
  return "unknown";
}

export function planLabel(plan: string): string {
  const value = plan.trim().toLowerCase();
  if (!value || value === "free") return "Free";
  if (value === "pro_trial") return "Pro trial";
  return value.charAt(0).toUpperCase() + value.slice(1).replace(/_/g, " ");
}

export function summarizeSubscription(record: SubscriptionRecord | null | undefined): SubscriptionSummary {
  const plan = String(record?.plan ?? "free").toLowerCase();
  const status = String(record?.status ?? "");
  const state = normalizeState(status, Boolean(record));
  const isPaidPlan = PAID_PLANS.has(plan);
  const isTrial = plan === "pro_trial" || state === "trialing";
  return {
    plan,
    status,
    state,
    isPaidPlan,
    isTrial,
    needsBillingAttention: isPaidPlan && ATTENTION_STATES.has(state),
    planLabel: planLabel(plan),
    stateLabel: STATE_LABEL[state],
    currentPeriodEndUtc: record?.current_period_end_utc ?? null,
    record: record ?? null,
  };
}

/* ------------------------------------------------------------------------- */
/* Reasons an action can be unavailable                                      */
/* ------------------------------------------------------------------------- */

export type DenialReason =
  | "subscription"
  | "billing_attention"
  | "workspace_role"
  | "platform_role"
  | "workspace_membership"
  | "configuration"
  | "data"
  | "backend"
  | "job";

export interface Gate {
  allowed: boolean;
  reason?: DenialReason;
}

const ALLOWED: Gate = { allowed: true };

function denied(reason: DenialReason): Gate {
  return { allowed: false, reason };
}

export const DENIAL_HEADLINE: Record<DenialReason, string> = {
  subscription: "Included with a paid plan",
  billing_attention: "Paused until billing is resolved",
  workspace_role: "Requires workspace manager permission",
  platform_role: "Requires platform administrator permission",
  workspace_membership: "Requires membership of this workspace",
  configuration: "Needs configuration first",
  data: "No data available yet",
  backend: "The service is unavailable",
  job: "Blocked by the current job state",
};

export const DENIAL_DETAIL: Record<DenialReason, string> = {
  subscription:
    "This workflow consumes compute, storage, and external data-provider capacity, so the server requires an active paid plan for the workspace.",
  billing_attention:
    "The workspace has a paid plan, but its billing status currently blocks premium workflows. Resolving billing restores access — your role and saved work are unchanged.",
  workspace_role:
    "Only workspace owners and managers can change shared workspace configuration. Your research, backtests, and saved reports are unaffected.",
  platform_role:
    "Platform administration covers every workspace on the deployment and is limited to accounts with the administrator role.",
  workspace_membership:
    "This record belongs to a workspace you are not a member of. Switch workspace to continue.",
  configuration:
    "A required data source, key, or setting has not been configured for this workspace yet.",
  data: "Nothing has been recorded for this workspace yet, so there is nothing to display.",
  backend:
    "The API or an upstream provider did not respond. Nothing was changed — retry once the service recovers.",
  job: "The current job must finish (or be cancelled) before this action can run.",
};

/* ------------------------------------------------------------------------- */
/* Capability set                                                            */
/* ------------------------------------------------------------------------- */

export interface AccessContext {
  /** Signed in at all. */
  isAuthenticated: boolean;
  user: AuthUser | null;

  /* Identity ---------------------------------------------------------------*/
  displayName: string;
  email: string;
  initials: string;

  /* Platform authority -----------------------------------------------------*/
  platformRole: PlatformRole;
  isPlatformAdmin: boolean;

  /* Workspace authority ----------------------------------------------------*/
  organization: Organization | null;
  orgRole: OrgRole;
  orgRoleLabel: string;
  isWorkspaceManager: boolean;

  /* Subscription -----------------------------------------------------------*/
  subscription: SubscriptionSummary;
  /**
   * Authoritative premium entitlement. Prefers `/api/billing/status.premium`
   * and falls back to a local derivation when billing status is unavailable.
   */
  hasPremium: boolean;
  /** Why premium is granted: an actual subscription, or platform-admin override. */
  premiumSource: "subscription" | "admin" | "none";
  /** True when premium is only present because the user is a platform admin. */
  premiumViaAdminOverride: boolean;

  /* Server feature switches ------------------------------------------------*/
  capabilities: WorkspacePayload["capabilities"] | null;
  marketplaceEnabled: boolean;
  strategyBuilderMode: "rules" | "llm";
  liveBrokerTradingEnabled: false;

  /* Environment ------------------------------------------------------------*/
  backendOnline: boolean;

  /* Gates ------------------------------------------------------------------*/
  /** Start compute-heavy jobs: backtests, sentiment, research, paper runs, refresh. */
  runCompute: Gate;
  /** Deploy simulated paper agents from a validated backtest. */
  deployPaperAgents: Gate;
  /**
   * Use the natural-language strategy builder. The API gates this on
   * authentication (plus a quota in LLM mode), **not** on a paid plan — so free
   * workspaces must not see it presented as premium.
   */
  useStrategyBuilder: Gate;
  /** Publish a strategy to the community marketplace. */
  publishToMarketplace: Gate;
  /** Change shared workspace configuration (projects, data sources, API keys). */
  manageWorkspace: Gate;
  /** Open checkout / the billing portal for the active workspace. */
  manageBilling: Gate;
  /** See the platform-wide administration area. */
  administerPlatform: Gate;
  /** See the workspace management area. */
  viewManagement: Gate;
}

export interface AccessInput {
  user: AuthUser | null;
  organization: Organization | null | undefined;
  workspace: WorkspacePayload | null | undefined;
  billing: BillingStatusPayload | null | undefined;
  backendOnline: boolean;
}

function initialsFor(name: string, email: string): string {
  const source = name.trim() || email.trim();
  if (!source) return "?";
  const parts = source.split(/[\s._@-]+/).filter(Boolean).slice(0, 2);
  const letters = parts.map((part) => part[0]?.toUpperCase() ?? "").join("");
  return letters || source[0]?.toUpperCase() || "?";
}

export function buildAccessContext(input: AccessInput): AccessContext {
  const { user, organization, workspace, billing, backendOnline } = input;
  const isAuthenticated = Boolean(user);
  const platformRole: PlatformRole = isAdminRole(user?.role) ? "admin" : "user";
  const isPlatformAdmin = platformRole === "admin";
  const orgRole = normalizeOrgRole(organization?.role);
  const isWorkspaceManager = isWorkspaceManagerRole(orgRole);

  const subscription = summarizeSubscription(
    billing?.subscription ?? workspace?.subscription ?? null,
  );

  // Prefer the server's own entitlement decision when we have it.
  const serverPremium = typeof billing?.premium === "boolean" ? billing.premium : null;
  const derivedPremium = subscription.isPaidPlan
    && (subscription.state === "active" || subscription.state === "trialing");
  const hasPremium = serverPremium ?? (isPlatformAdmin || derivedPremium);

  // The server reports `access: "admin"` for any administrator, even when the
  // workspace also holds a valid paid plan. Attribute premium to the
  // subscription whenever the subscription alone would grant it, so the
  // administrator override is only called out when it is actually doing the work.
  const serverAccess = String(billing?.access ?? "").toLowerCase();
  let premiumSource: AccessContext["premiumSource"] = "none";
  if (hasPremium) {
    if (derivedPremium || serverAccess === "subscription") premiumSource = "subscription";
    else if (isPlatformAdmin || serverAccess === "admin") premiumSource = "admin";
    else premiumSource = "subscription";
  }

  const capabilities = workspace?.capabilities ?? null;
  const marketplaceEnabled = capabilities?.marketplace_enabled === true;

  const computeGate: Gate = hasPremium
    ? ALLOWED
    : subscription.needsBillingAttention
      ? denied("billing_attention")
      : denied("subscription");

  const displayName = user?.display_name?.trim() || user?.email || "";
  const email = user?.email ?? "";

  return {
    isAuthenticated,
    user: user ?? null,
    displayName,
    email,
    initials: initialsFor(displayName, email),

    platformRole,
    isPlatformAdmin,

    organization: organization ?? null,
    orgRole,
    orgRoleLabel: ORG_ROLE_LABEL[orgRole],
    isWorkspaceManager,

    subscription,
    hasPremium,
    premiumSource,
    premiumViaAdminOverride: hasPremium && premiumSource === "admin",

    capabilities,
    marketplaceEnabled,
    strategyBuilderMode: capabilities?.strategy_builder_mode ?? "rules",
    liveBrokerTradingEnabled: false,

    backendOnline,

    runCompute: computeGate,
    deployPaperAgents: computeGate,

    // `/api/strategies/builder/chat` and `/builder/approve` require a session
    // only — never a paid plan — so the builder stays open to free workspaces.
    useStrategyBuilder: isAuthenticated ? ALLOWED : denied("workspace_membership"),

    // Marketplace publish/subscribe are member-accessible in the API; only the
    // moderation endpoint is admin-gated. The capability switch is the real gate.
    publishToMarketplace: marketplaceEnabled ? ALLOWED : denied("configuration"),

    // Shared workspace configuration is a workspace-authority concern, not a
    // billing one. Platform admins retain it for operational support.
    manageWorkspace:
      isWorkspaceManager || isPlatformAdmin ? ALLOWED : denied("workspace_role"),

    // NOTE: the API exposes billing checkout/portal to any authenticated member
    // of the active workspace (see routers/saas.py). There is no separate
    // "billing permission" contract, so the UI must not invent one — every
    // member sees the action, and the workspace-level nature is explained in
    // the copy instead. See docs/frontend-access-model.md.
    manageBilling: isAuthenticated ? ALLOWED : denied("workspace_membership"),

    administerPlatform: isPlatformAdmin ? ALLOWED : denied("platform_role"),
    viewManagement:
      isWorkspaceManager || isPlatformAdmin ? ALLOWED : denied("workspace_role"),
  };
}

/* ------------------------------------------------------------------------- */
/* Admin user segmentation                                                   */
/* ------------------------------------------------------------------------- */

export type AdminSegmentId =
  | "all"
  | "free"
  | "paid"
  | "managers"
  | "admins"
  | "billing_attention"
  | "restricted";

export interface AdminSegment {
  id: AdminSegmentId;
  label: string;
  description: string;
}

export const ADMIN_SEGMENTS: AdminSegment[] = [
  { id: "all", label: "All accounts", description: "Every account visible to this deployment." },
  { id: "free", label: "Free", description: "No paid plan on the account's workspace." },
  { id: "paid", label: "Paying", description: "Active or trialing paid plan." },
  { id: "managers", label: "Managers", description: "Workspace owners and managers (organization role)." },
  { id: "admins", label: "Administrators", description: "Accounts with the platform administrator role." },
  { id: "billing_attention", label: "Billing attention", description: "Paid plan with a status that blocks entitlement." },
  { id: "restricted", label: "Restricted", description: "Accounts that are not active." },
];

export interface SegmentableAccount {
  role?: string | null;
  status?: string | null;
  organization_role?: string | null;
  plan?: string | null;
  subscription_status?: string | null;
}

/**
 * Classify an admin user record into audience segments using only real role and
 * subscription fields returned by `/api/admin/users`. No heuristics on unrelated
 * fields, and no invented classifications.
 */
export function accountMatchesSegment(account: SegmentableAccount, segment: AdminSegmentId): boolean {
  const platformAdmin = isAdminRole(account.role);
  const orgRole = normalizeOrgRole(account.organization_role);
  const subscription = summarizeSubscription(
    account.plan == null && account.subscription_status == null
      ? null
      : ({
        plan: String(account.plan ?? "free"),
        status: String(account.subscription_status ?? ""),
      } as SubscriptionRecord),
  );
  const entitled = subscription.isPaidPlan
    && (subscription.state === "active" || subscription.state === "trialing");

  switch (segment) {
    case "all":
      return true;
    case "free":
      return !entitled && !subscription.needsBillingAttention;
    case "paid":
      return entitled;
    case "managers":
      return isWorkspaceManagerRole(orgRole);
    case "admins":
      return platformAdmin;
    case "billing_attention":
      return subscription.needsBillingAttention;
    case "restricted":
      return String(account.status ?? "active").toLowerCase() !== "active";
    default:
      return true;
  }
}
