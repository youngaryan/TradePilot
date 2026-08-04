import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { buildAccessContext } from "../../access/model";
import type {
  AuthResponse,
  AuthUser,
  BillingStatusPayload,
  Organization,
  SubscriptionRecord,
  WorkspacePayload,
} from "../../api/types";
import type { AppSession } from "../../session/useAppSession";
import { AppRoutes } from "../AppRoutes";

vi.mock("../../api/client", () => ({
  getPaperSummary: vi.fn().mockResolvedValue(null),
  getStrategyCatalog: vi.fn().mockResolvedValue([]),
  getBacktestTemplates: vi.fn().mockResolvedValue([]),
  listPaperRunJobs: vi.fn().mockResolvedValue([]),
  listBacktestJobs: vi.fn().mockResolvedValue([]),
  listMarketResearchJobs: vi.fn().mockResolvedValue([]),
  getSystemMetadata: vi.fn().mockResolvedValue({ app_env: "test" }),
  getSystemAdminCounts: vi.fn().mockResolvedValue({ counts: {} }),
  getSentimentDataset: vi.fn().mockResolvedValue({ daily_points: [], headlines: [], scored_headlines: [] }),
  getRefreshStatus: vi.fn().mockResolvedValue({ interval_hours: 24, max_attempts: 3, scheduler_enabled: false, statuses: [], recent_runs: [] }),
  listTelemetryEvents: vi.fn().mockResolvedValue([]),
  createProject: vi.fn(),
  createApiKeyMetadata: vi.fn(),
  runDailyRefresh: vi.fn(),
  trackTelemetryEvent: vi.fn().mockResolvedValue({ stored: false }),
  getAdminOverview: vi.fn().mockRejectedValue(new Error("not reached")),
  listAdminUsers: vi.fn().mockRejectedValue(new Error("not reached")),
  listAdminUserStrategies: vi.fn().mockRejectedValue(new Error("not reached")),
  getAdminSystemHealth: vi.fn().mockRejectedValue(new Error("not reached")),
  listAdminAuditLog: vi.fn().mockRejectedValue(new Error("not reached")),
  getAdminQuotas: vi.fn().mockRejectedValue(new Error("not reached")),
  getSentimentDatasets: vi.fn().mockResolvedValue([]),
  getSentimentModels: vi.fn().mockResolvedValue([]),
}));

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

function workspace(subscription: SubscriptionRecord | null): WorkspacePayload {
  return {
    organization_id: "org_1",
    capabilities: {
      strategy_builder_mode: "rules",
      strategy_builder_provider: "deterministic",
      strategy_builder_model: "",
      market_research_data_mode: "demo",
      marketplace_enabled: false,
      marketplace_creator_credits_enabled: false,
      live_broker_trading_enabled: false,
    },
    projects: [],
    subscription,
    datasets: [],
    api_keys: [],
    experiments: [],
    paper_agents: [],
    market_research_reports: [],
    onboarding: { complete_count: 0, total_count: 5, steps: [] },
  };
}

function makeSession(options: {
  platformRole?: "admin" | "user";
  orgRole?: string;
  paid?: boolean;
}): AppSession {
  const user: AuthUser = {
    id: "usr_1",
    email: "person@example.com",
    display_name: "Test Person",
    role: options.platformRole ?? "user",
    status: "active",
  };
  const subscription: SubscriptionRecord | null = options.paid
    ? {
      id: "sub_1",
      organization_id: "org_1",
      plan: "pro",
      status: "active",
      usage: {},
      created_at_utc: "2026-01-01T00:00:00Z",
      updated_at_utc: "2026-01-01T00:00:00Z",
    }
    : null;
  const org = organization(options.orgRole ?? "member");
  const premium = Boolean(options.paid) || options.platformRole === "admin";
  const billing: BillingStatusPayload = {
    subscription,
    premium,
    access: options.paid ? "subscription" : premium ? "admin" : "none",
    pricing: [],
  };
  const auth: AuthResponse = { user, organizations: [org], active_organization_id: org.id };
  return {
    auth,
    isLoading: false,
    isRefreshing: false,
    organizations: [org],
    activeOrgId: org.id,
    activeOrganization: org,
    workspace: workspace(subscription),
    billing,
    health: { status: "ok", service: "test" },
    backendOnline: true,
    workspaceError: false,
    isAdminAccess: options.platformRole === "admin",
    hasPremiumAccess: premium,
    access: buildAccessContext({
      user,
      organization: org,
      workspace: workspace(subscription),
      billing,
      backendOnline: true,
    }),
    handleLogin: vi.fn(),
    handleLogout: vi.fn(),
    switchOrganization: vi.fn(),
    refresh: vi.fn().mockResolvedValue(undefined),
  };
}

function renderAt(path: string, session: AppSession) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes session={session} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

describe("entering a restricted URL directly", () => {
  it("denies /admin to a free standard member and explains the role requirement", async () => {
    renderAt("/admin", makeSession({}));
    expect(await screen.findByText("Requires platform administrator permission")).toBeInTheDocument();
    expect(
      screen.getByText(/limited to accounts with the administrator role/i),
    ).toBeInTheDocument();
    // No administrative surface leaks in.
    expect(screen.queryByText("Accounts")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /grant platform admin/i })).not.toBeInTheDocument();
  });

  it("denies /admin to a paying standard member — paying is not administering", async () => {
    renderAt("/admin", makeSession({ paid: true }));
    expect(await screen.findByText("Requires platform administrator permission")).toBeInTheDocument();
    expect(screen.queryByText("Accounts")).not.toBeInTheDocument();
  });

  it("denies /admin to a paying workspace manager and points at workspace management instead", async () => {
    renderAt("/admin", makeSession({ paid: true, orgRole: "owner" }));
    expect(await screen.findByText("Requires platform administrator permission")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /open workspace management/i })).toBeInTheDocument();
  });

  it("denies /management to a standard member and explains the workspace-role requirement", async () => {
    renderAt("/management", makeSession({ paid: true }));
    expect(await screen.findByText("Requires workspace manager permission")).toBeInTheDocument();
    expect(screen.getByText(/Only workspace owners and managers/i)).toBeInTheDocument();
    // Management content itself must not render.
    expect(screen.queryByRole("tab", { name: /shared research/i })).not.toBeInTheDocument();
  });

  it("allows /management for a free workspace manager without granting platform tools", async () => {
    renderAt("/management", makeSession({ orgRole: "owner" }));
    expect(await screen.findByRole("tab", { name: /shared research/i })).toBeInTheDocument();
    expect(screen.queryByText("Requires workspace manager permission")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /open platform administration/i })).not.toBeInTheDocument();
  });
});

describe("premium gating on research screens", () => {
  it("explains the paid gate on /backtests for a free member and still shows saved runs", async () => {
    renderAt("/backtests", makeSession({}));
    expect(await screen.findByText("Starting a new backtest")).toBeInTheDocument();
    expect(screen.getByText("Included with a paid plan")).toBeInTheDocument();
    expect(screen.getByText(/remains fully readable/i)).toBeInTheDocument();
  });

  it("does not show a paid gate on /backtests for a paying member", async () => {
    renderAt("/backtests", makeSession({ paid: true }));
    await waitFor(() => {
      expect(screen.queryByText("Starting a new backtest")).not.toBeInTheDocument();
    });
  });

  it("never presents the strategy builder as a premium feature", async () => {
    renderAt("/strategies/builder", makeSession({}));
    expect(await screen.findByText("Describe the idea")).toBeInTheDocument();
    expect(screen.queryByText("Included with a paid plan")).not.toBeInTheDocument();
  });
});

describe("plan and role are displayed independently", () => {
  it("shows plan state and both role dimensions on the account screen", async () => {
    renderAt("/account", makeSession({ paid: true, orgRole: "owner" }));
    expect(await screen.findByText("Platform role: Standard account")).toBeInTheDocument();
    expect(screen.getByText("Workspace role: Workspace owner")).toBeInTheDocument();
    expect(screen.getByText("Plan: Pro · Active")).toBeInTheDocument();
  });

  it("tells an administrator when premium comes from the role rather than a subscription", async () => {
    renderAt("/pricing", makeSession({ platformRole: "admin" }));
    expect(await screen.findByText("Premium via administrator role, not a subscription")).toBeInTheDocument();
  });
});
