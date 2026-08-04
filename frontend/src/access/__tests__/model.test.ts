import { describe, expect, it } from "vitest";

import {
  ADMIN_SEGMENTS,
  accountMatchesSegment,
  buildAccessContext,
  isWorkspaceManagerRole,
  normalizeOrgRole,
  summarizeSubscription,
  type AdminSegmentId,
} from "../model";
import type {
  AuthUser,
  BillingStatusPayload,
  Organization,
  SubscriptionRecord,
  WorkspacePayload,
} from "../../api/types";

function user(overrides: Partial<AuthUser> = {}): AuthUser {
  return {
    id: "usr_1",
    email: "person@example.com",
    display_name: "Test Person",
    role: "user",
    status: "active",
    ...overrides,
  };
}

function organization(role: string): Organization {
  return {
    id: "org_1",
    name: "Test Desk",
    slug: "test-desk",
    owner_user_id: "usr_1",
    role,
    created_at_utc: "2026-01-01T00:00:00Z",
    updated_at_utc: "2026-01-01T00:00:00Z",
  };
}

function subscription(plan: string, status: string): SubscriptionRecord {
  return {
    id: "sub_1",
    organization_id: "org_1",
    plan,
    status,
    usage: {},
    created_at_utc: "2026-01-01T00:00:00Z",
    updated_at_utc: "2026-01-01T00:00:00Z",
  };
}

function workspace(record: SubscriptionRecord | null): WorkspacePayload {
  return {
    organization_id: "org_1",
    capabilities: {
      strategy_builder_mode: "rules",
      strategy_builder_provider: "deterministic",
      strategy_builder_model: "",
      market_research_data_mode: "demo",
      marketplace_enabled: true,
      marketplace_creator_credits_enabled: false,
      live_broker_trading_enabled: false,
    },
    projects: [],
    subscription: record,
    datasets: [],
    api_keys: [],
    experiments: [],
    paper_agents: [],
    market_research_reports: [],
    onboarding: { complete_count: 0, total_count: 5, steps: [] },
  };
}

function billing(premium: boolean, access: string, record: SubscriptionRecord | null): BillingStatusPayload {
  return { subscription: record, premium, access, pricing: [] };
}

function context(options: {
  platformRole?: "admin" | "user";
  orgRole?: string;
  plan?: [string, string] | null;
  serverPremium?: boolean;
  serverAccess?: string;
}) {
  const record = options.plan ? subscription(options.plan[0], options.plan[1]) : null;
  const derived = Boolean(record)
    && ["pro", "team", "enterprise", "pro_trial"].includes(record!.plan)
    && ["active", "trialing"].includes(record!.status);
  const premium = options.serverPremium ?? (options.platformRole === "admin" || derived);
  return buildAccessContext({
    user: user({ role: options.platformRole ?? "user" }),
    organization: organization(options.orgRole ?? "member"),
    workspace: workspace(record),
    billing: billing(premium, options.serverAccess ?? (derived ? "subscription" : premium ? "admin" : "none"), record),
    backendOnline: true,
  });
}

describe("subscription summarisation", () => {
  it("treats a workspace with no subscription record as free with no state", () => {
    const summary = summarizeSubscription(null);
    expect(summary.planLabel).toBe("Free");
    expect(summary.state).toBe("none");
    expect(summary.isPaidPlan).toBe(false);
    expect(summary.needsBillingAttention).toBe(false);
  });

  it("labels each lifecycle state distinctly", () => {
    expect(summarizeSubscription(subscription("pro", "active")).stateLabel).toBe("Active");
    expect(summarizeSubscription(subscription("pro", "trialing")).stateLabel).toBe("Trial");
    expect(summarizeSubscription(subscription("pro", "past_due")).stateLabel).toBe("Payment past due");
    expect(summarizeSubscription(subscription("pro", "canceled")).stateLabel).toBe("Canceled");
    expect(summarizeSubscription(subscription("pro", "unpaid")).stateLabel).toBe("Unpaid");
    expect(summarizeSubscription(subscription("free", "free")).stateLabel).toBe("Free");
  });

  it("flags a paid plan whose status blocks entitlement as needing billing attention", () => {
    for (const status of ["past_due", "canceled", "incomplete", "unpaid", "paused"]) {
      expect(summarizeSubscription(subscription("pro", status)).needsBillingAttention).toBe(true);
    }
    expect(summarizeSubscription(subscription("pro", "active")).needsBillingAttention).toBe(false);
    // A free plan is not "billing attention" — there is nothing to fix.
    expect(summarizeSubscription(subscription("free", "free")).needsBillingAttention).toBe(false);
  });
});

describe("organizational role normalisation", () => {
  it("only accepts roles the server actually stores", () => {
    expect(normalizeOrgRole("owner")).toBe("owner");
    expect(normalizeOrgRole("admin")).toBe("admin");
    expect(normalizeOrgRole("member")).toBe("member");
    expect(normalizeOrgRole("manager")).toBe("unknown");
    expect(normalizeOrgRole(undefined)).toBe("unknown");
  });

  it("treats workspace owners and workspace admins as managers, members as not", () => {
    expect(isWorkspaceManagerRole("owner")).toBe(true);
    expect(isWorkspaceManagerRole("admin")).toBe(true);
    expect(isWorkspaceManagerRole("member")).toBe(false);
    expect(isWorkspaceManagerRole("unknown")).toBe(false);
  });
});

describe("mixed subscription and role states", () => {
  it("free standard member: free features only, no management, no administration", () => {
    const access = context({ orgRole: "member", plan: null });
    expect(access.hasPremium).toBe(false);
    expect(access.runCompute.allowed).toBe(false);
    expect(access.runCompute.reason).toBe("subscription");
    // The strategy builder is authentication-gated by the API, never plan-gated.
    expect(access.useStrategyBuilder.allowed).toBe(true);
    expect(access.viewManagement.allowed).toBe(false);
    expect(access.manageWorkspace.allowed).toBe(false);
    expect(access.administerPlatform.allowed).toBe(false);
    // Billing is member-accessible in the API, so it must not be hidden.
    expect(access.manageBilling.allowed).toBe(true);
  });

  it("paying standard member: premium compute, still no management or administration", () => {
    const access = context({ orgRole: "member", plan: ["pro", "active"] });
    expect(access.hasPremium).toBe(true);
    expect(access.premiumSource).toBe("subscription");
    expect(access.runCompute.allowed).toBe(true);
    expect(access.deployPaperAgents.allowed).toBe(true);
    expect(access.viewManagement.allowed).toBe(false);
    expect(access.manageWorkspace.allowed).toBe(false);
    expect(access.administerPlatform.allowed).toBe(false);
    expect(access.isPlatformAdmin).toBe(false);
  });

  it("free workspace manager: management without premium compute and without administration", () => {
    const access = context({ orgRole: "owner", plan: null });
    expect(access.isWorkspaceManager).toBe(true);
    expect(access.viewManagement.allowed).toBe(true);
    expect(access.manageWorkspace.allowed).toBe(true);
    expect(access.hasPremium).toBe(false);
    expect(access.runCompute.allowed).toBe(false);
    expect(access.administerPlatform.allowed).toBe(false);
  });

  it("paying workspace manager: management plus premium compute, still no administration", () => {
    const access = context({ orgRole: "admin", plan: ["team", "active"] });
    expect(access.isWorkspaceManager).toBe(true);
    expect(access.viewManagement.allowed).toBe(true);
    expect(access.hasPremium).toBe(true);
    expect(access.administerPlatform.allowed).toBe(false);
  });

  it("platform administrator without a paid workspace: administration, and premium attributed to the role", () => {
    const access = context({ platformRole: "admin", orgRole: "member", plan: null, serverAccess: "admin" });
    expect(access.administerPlatform.allowed).toBe(true);
    expect(access.isPlatformAdmin).toBe(true);
    expect(access.hasPremium).toBe(true);
    expect(access.premiumSource).toBe("admin");
    expect(access.premiumViaAdminOverride).toBe(true);
    // The workspace itself is still on the free plan and must say so.
    expect(access.subscription.planLabel).toBe("Free");
    expect(access.subscription.isPaidPlan).toBe(false);
  });

  it("administrator inside a paid workspace attributes premium to the subscription, not the role", () => {
    // The API reports access:"admin" for any administrator even when the
    // workspace holds a valid paid plan; the UI must not blame the role.
    const access = context({ platformRole: "admin", orgRole: "owner", plan: ["pro", "active"], serverAccess: "admin" });
    expect(access.hasPremium).toBe(true);
    expect(access.premiumSource).toBe("subscription");
    expect(access.premiumViaAdminOverride).toBe(false);
  });

  it("past-due paying manager: compute paused for billing, role untouched", () => {
    const access = context({ orgRole: "owner", plan: ["pro", "past_due"] });
    expect(access.hasPremium).toBe(false);
    expect(access.runCompute.allowed).toBe(false);
    // The reason must be "fix billing", not "upgrade".
    expect(access.runCompute.reason).toBe("billing_attention");
    expect(access.subscription.needsBillingAttention).toBe(true);
    // A billing failure never removes workspace authority.
    expect(access.isWorkspaceManager).toBe(true);
    expect(access.viewManagement.allowed).toBe(true);
    expect(access.manageWorkspace.allowed).toBe(true);
  });

  it("a trialing paid plan is entitled and is labelled as a trial", () => {
    const access = context({ orgRole: "member", plan: ["pro_trial", "trialing"] });
    expect(access.hasPremium).toBe(true);
    expect(access.subscription.isTrial).toBe(true);
    expect(access.subscription.stateLabel).toBe("Trial");
  });
});

describe("independence of the two dimensions", () => {
  it("subscription changes never change the reported role", () => {
    const free = context({ orgRole: "owner", plan: null });
    const paid = context({ orgRole: "owner", plan: ["pro", "active"] });
    const pastDue = context({ orgRole: "owner", plan: ["pro", "past_due"] });
    for (const access of [free, paid, pastDue]) {
      expect(access.orgRole).toBe("owner");
      expect(access.isWorkspaceManager).toBe(true);
      expect(access.isPlatformAdmin).toBe(false);
      expect(access.administerPlatform.allowed).toBe(false);
    }
  });

  it("role changes never change the reported subscription", () => {
    const member = context({ orgRole: "member", plan: ["pro", "active"] });
    const manager = context({ orgRole: "owner", plan: ["pro", "active"] });
    const admin = context({ platformRole: "admin", orgRole: "member", plan: ["pro", "active"], serverAccess: "admin" });
    for (const access of [member, manager, admin]) {
      expect(access.subscription.plan).toBe("pro");
      expect(access.subscription.stateLabel).toBe("Active");
    }
  });

  it("paying never implies management or administration for any workspace role", () => {
    for (const orgRole of ["member", "unknown"]) {
      const access = context({ orgRole, plan: ["enterprise", "active"] });
      expect(access.hasPremium).toBe(true);
      expect(access.viewManagement.allowed).toBe(false);
      expect(access.administerPlatform.allowed).toBe(false);
    }
  });

  it("managing a workspace never implies platform administration", () => {
    for (const orgRole of ["owner", "admin"]) {
      const access = context({ orgRole, plan: ["pro", "active"] });
      expect(access.manageWorkspace.allowed).toBe(true);
      expect(access.administerPlatform.allowed).toBe(false);
      expect(access.isPlatformAdmin).toBe(false);
    }
  });

  it("prefers the server's own premium decision over local derivation", () => {
    // Server says no premium even though the record looks entitled: trust the server.
    const access = context({ orgRole: "member", plan: ["pro", "active"], serverPremium: false });
    expect(access.hasPremium).toBe(false);
    expect(access.runCompute.allowed).toBe(false);
  });
});

describe("marketplace capability", () => {
  it("gates publishing on the deployment capability switch, not on the plan", () => {
    const free = context({ orgRole: "member", plan: null });
    expect(free.marketplaceEnabled).toBe(true);
    expect(free.publishToMarketplace.allowed).toBe(true);

    const disabled = buildAccessContext({
      user: user(),
      organization: organization("member"),
      workspace: {
        ...workspace(null),
        capabilities: { ...workspace(null).capabilities, marketplace_enabled: false },
      },
      billing: billing(false, "none", null),
      backendOnline: true,
    });
    expect(disabled.publishToMarketplace.allowed).toBe(false);
    expect(disabled.publishToMarketplace.reason).toBe("configuration");
  });
});

describe("administrator account segmentation", () => {
  const accounts = [
    { id: "free-member", role: "user", status: "active", organization_role: "member", plan: "free", subscription_status: "free" },
    { id: "paid-member", role: "user", status: "active", organization_role: "member", plan: "pro", subscription_status: "active" },
    { id: "free-manager", role: "user", status: "active", organization_role: "owner", plan: "free", subscription_status: "free" },
    { id: "paid-manager", role: "user", status: "active", organization_role: "admin", plan: "team", subscription_status: "active" },
    { id: "admin-free", role: "admin", status: "active", organization_role: "member", plan: "free", subscription_status: "free" },
    { id: "past-due", role: "user", status: "active", organization_role: "owner", plan: "pro", subscription_status: "past_due" },
    { id: "disabled", role: "user", status: "inactive", organization_role: "member", plan: "free", subscription_status: "free" },
  ];

  function idsIn(segment: AdminSegmentId) {
    return accounts.filter((account) => accountMatchesSegment(account, segment)).map((account) => account.id);
  }

  it("exposes exactly the documented segments", () => {
    expect(ADMIN_SEGMENTS.map((segment) => segment.id)).toEqual([
      "all",
      "free",
      "paid",
      "managers",
      "admins",
      "billing_attention",
      "restricted",
    ]);
  });

  it("classifies free accounts by entitlement, not by role", () => {
    expect(idsIn("free")).toEqual(["free-member", "free-manager", "admin-free", "disabled"]);
  });

  it("classifies paying accounts by entitlement, not by role", () => {
    expect(idsIn("paid")).toEqual(["paid-member", "paid-manager"]);
  });

  it("classifies managers by organizational role, independent of plan", () => {
    expect(idsIn("managers")).toEqual(["free-manager", "paid-manager", "past-due"]);
  });

  it("classifies administrators by platform role only", () => {
    expect(idsIn("admins")).toEqual(["admin-free"]);
  });

  it("separates billing-attention accounts from free accounts", () => {
    expect(idsIn("billing_attention")).toEqual(["past-due"]);
    expect(idsIn("free")).not.toContain("past-due");
  });

  it("classifies restricted accounts by status", () => {
    expect(idsIn("restricted")).toEqual(["disabled"]);
  });

  it("never infers a classification when the API omits plan information", () => {
    const unknown = { id: "unknown", role: "user", status: "active", organization_role: null, plan: null, subscription_status: null };
    expect(accountMatchesSegment(unknown, "paid")).toBe(false);
    expect(accountMatchesSegment(unknown, "managers")).toBe(false);
    expect(accountMatchesSegment(unknown, "admins")).toBe(false);
    expect(accountMatchesSegment(unknown, "billing_attention")).toBe(false);
    expect(accountMatchesSegment(unknown, "free")).toBe(true);
  });
});
