import React, { Suspense, useEffect } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router";
import { Activity, BookOpen, FlaskConical, Newspaper, Sparkles } from "lucide-react";

import type { AppSession } from "../session/useAppSession";
import { LEGACY_HASH_ROUTES, navItemForPath } from "../shell/navigation";
import {
  AccessNotice,
  Button,
  ErrorBoundary,
  InlineNotice,
  LoadingBlock,
  PageHeader,
  Tag,
} from "../ui";
import { OverviewPage } from "../features/overview/OverviewPage";
import { StrategiesPage } from "../features/strategies/StrategiesPage";
import { ManagementPage } from "../features/management/ManagementPage";
import { useWorkspaceData } from "./useWorkspaceData";

const BacktestLab = React.lazy(() => import("../features/BacktestLab").then((module) => ({ default: module.BacktestLab })));
const LiveOps = React.lazy(() => import("../features/LiveOps").then((module) => ({ default: module.LiveOps })));
const MarketResearchLab = React.lazy(() => import("../features/MarketResearchLab").then((module) => ({ default: module.MarketResearchLab })));
const SentimentLab = React.lazy(() => import("../features/SentimentLab").then((module) => ({ default: module.SentimentLab })));
const SaaSWorkspace = React.lazy(() => import("../features/SaaSWorkspace").then((module) => ({ default: module.SaaSWorkspace })));
const AdminDashboard = React.lazy(() => import("../features/AdminDashboard").then((module) => ({ default: module.AdminDashboard })));
const AccountSecurity = React.lazy(() => import("../features/AccountSecurity").then((module) => ({ default: module.AccountSecurity })));
const PricingPage = React.lazy(() => import("../features/PricingPage").then((module) => ({ default: module.PricingPage })));
const SystemGuide = React.lazy(() => import("../features/SystemGuide").then((module) => ({ default: module.SystemGuide })));

/** Keeps the document title in step with the active screen. */
function useDocumentTitle(pathname: string) {
  useEffect(() => {
    const item = navItemForPath(pathname);
    document.title = item ? `${item.label} · Meridian` : "Meridian · Strategy Research Terminal";
  }, [pathname]);
}

/** Canonical path for a legacy `#/app/<view>` hash, if the URL carries one. */
function legacyHashTarget(): string | null {
  const hash = window.location.hash.replace(/^#\/?/, "");
  if (!hash.startsWith("app/")) return null;
  const view = hash.slice("app/".length).split(/[/?]/)[0];
  return LEGACY_HASH_ROUTES[view] ?? null;
}

/**
 * Compatibility: keep watching for later `#/app/<view>` changes. The initial
 * hash is resolved during render (see `AppRoutes`) so it wins over the
 * `/` → `/overview` redirect, which would otherwise drop the hash first.
 */
function useLegacyHashRedirect() {
  const navigate = useNavigate();
  useEffect(() => {
    const apply = () => {
      const target = legacyHashTarget();
      if (!target) return;
      navigate(target, { replace: true });
    };
    window.addEventListener("hashchange", apply);
    return () => window.removeEventListener("hashchange", apply);
  }, [navigate]);
}

function ScreenFallback({ label }: { label: string }) {
  return (
    <div className="ui-stack">
      <LoadingBlock label={label} lines={5} />
    </div>
  );
}

export function AppRoutes({ session }: { session: AppSession }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { access } = session;
  const data = useWorkspaceData(session.activeOrgId, {
    authenticated: access.isAuthenticated,
    isPlatformAdmin: access.isPlatformAdmin,
  });

  useDocumentTitle(location.pathname);
  useLegacyHashRedirect();

  // Resolve an incoming `#/app/<view>` link before any other route matches.
  // `Navigate` owns the history write: touching window.history directly here
  // would desynchronise the router's cached location from the address bar.
  const legacyTarget = legacyHashTarget();
  if (legacyTarget && location.pathname !== legacyTarget) {
    return <Navigate to={legacyTarget} replace />;
  }

  // Plans & billing already explains the billing state in full, so the global
  // banner would only repeat itself there.
  const billingAttention = access.subscription.needsBillingAttention
    && !location.pathname.startsWith("/pricing");

  return (
    <>
      {billingAttention ? (
        <InlineNotice
          tone="warn"
          title={`Billing needs attention — ${access.subscription.planLabel} plan is ${access.subscription.stateLabel.toLowerCase()}`}
          actions={
            <Button variant="primary" size="sm" onClick={() => navigate("/pricing")}>
              Resolve billing
            </Button>
          }
        >
          Premium workflows are paused until the subscription is active again. Your saved research, strategies, agents,
          and workspace role are unchanged.
        </InlineNotice>
      ) : null}

      {session.workspaceError ? (
        <InlineNotice
          tone="bad"
          title="Workspace details unavailable"
          actions={<Button variant="secondary" size="sm" onClick={() => void session.refresh()}>Retry</Button>}
        >
          The workspace payload could not be loaded, so plan and capability information may be incomplete. Screens
          below report their own state independently.
        </InlineNotice>
      ) : null}

      <ErrorBoundary area={navItemForPath(location.pathname)?.label ?? "This screen"}>
        <Suspense fallback={<ScreenFallback label="Loading screen" />}>
          <Routes>
            <Route path="/" element={<Navigate to="/overview" replace />} />
            <Route path="/apollo" element={<Navigate to="/overview" replace />} />
            <Route path="/classic" element={<Navigate to="/overview" replace />} />
            <Route path="/product" element={<Navigate to="/overview" replace />} />

            <Route
              path="/overview"
              element={
                <OverviewPage
                  activeOrgId={session.activeOrgId}
                  access={access}
                  displayName={access.displayName}
                  workspaceLabel={session.activeOrganization?.name}
                  backendOnline={session.backendOnline}
                  onRefreshSession={() => void session.refresh()}
                />
              }
            />

            <Route
              path="/strategies"
              element={
                <StrategiesPage
                  tab="library"
                  access={access}
                  activeOrgId={session.activeOrgId}
                  workspace={session.workspace}
                  catalog={data.catalog}
                  catalogLoading={data.isLoading}
                  catalogError={data.errors.catalog}
                  onCatalogChange={data.setCatalog}
                  onRefresh={() => void data.refresh()}
                />
              }
            />
            <Route
              path="/strategies/builder"
              element={
                <StrategiesPage
                  tab="builder"
                  access={access}
                  activeOrgId={session.activeOrgId}
                  workspace={session.workspace}
                  catalog={data.catalog}
                  catalogLoading={data.isLoading}
                  catalogError={data.errors.catalog}
                  onCatalogChange={data.setCatalog}
                  onRefresh={() => void data.refresh()}
                />
              }
            />
            <Route
              path="/strategies/community"
              element={
                <StrategiesPage
                  tab="community"
                  access={access}
                  activeOrgId={session.activeOrgId}
                  workspace={session.workspace}
                  catalog={data.catalog}
                  catalogLoading={data.isLoading}
                  catalogError={data.errors.catalog}
                  onCatalogChange={data.setCatalog}
                  onRefresh={() => void data.refresh()}
                />
              }
            />

            <Route path="/backtests" element={<BacktestsScreen session={session} data={data} />} />
            <Route path="/paper" element={<PaperScreen session={session} data={data} />} />
            <Route path="/research" element={<ResearchScreen session={session} />} />
            <Route path="/sentiment" element={<SentimentScreen session={session} />} />

            <Route path="/workspace" element={<WorkspaceScreen session={session} onRefresh={() => void data.refresh()} />} />
            <Route
              path="/management"
              element={
                <ManagementPage
                  access={access}
                  workspace={session.workspace}
                  workspaceLoading={session.isRefreshing && !session.workspace}
                  organizations={session.organizations}
                  activeOrgId={session.activeOrgId}
                  onRefresh={() => void session.refresh()}
                />
              }
            />
            <Route path="/admin" element={<AdminScreen session={session} />} />
            <Route path="/account" element={<AccountScreen session={session} />} />
            <Route path="/pricing" element={<PricingScreen session={session} />} />
            <Route path="/learn" element={<LearnScreen session={session} data={data} />} />

            <Route path="*" element={<NotFoundScreen />} />
          </Routes>
        </Suspense>
      </ErrorBoundary>
    </>
  );
}

type WorkspaceData = ReturnType<typeof useWorkspaceData>;

function BacktestsScreen({ session, data }: { session: AppSession; data: WorkspaceData }) {
  const { access } = session;
  const gate = access.runCompute;
  return (
    <>
      <PageHeader
        eyebrow="Backtests"
        title="Historical validation"
        description="Validate a strategy against history with walk-forward folds, purge and embargo windows, per-fold metrics, trade markers, and an overfitting estimate. This is historical research — not a forward simulation and not a forecast."
        meta={
          <>
            <span>{data.backtestJobs.length} saved runs in this workspace</span>
            <Tag tone="info">Historical validation</Tag>
            {gate.allowed ? null : <Tag tone="warn">New runs need a paid plan</Tag>}
          </>
        }
      />
      {gate.allowed ? null : (
        <AccessNotice
          reason={gate.reason ?? "subscription"}
          feature="Starting a new backtest"
          whatItDoes="Runs a walk-forward backtest on the selected strategy and universe, producing equity and drawdown curves, per-fold metrics, trade markers, readiness checks, and a probability-of-backtest-overfitting estimate."
          unlockedBy="An active paid plan for this workspace. The server refuses new runs otherwise, so the launch control below stays disabled."
          alternative="Every saved run in this workspace remains fully readable, including its charts, metrics, validation checks, and warnings. You can also configure a run now and launch it after upgrading."
          actions={<Button variant="primary" onClick={() => { window.location.assign("/pricing"); }}>Compare plans</Button>}
        />
      )}
      {data.errors.templates ? (
        <InlineNotice tone="warn" compact>{data.errors.templates}</InlineNotice>
      ) : null}
      {data.isLoading ? (
        <ScreenFallback label="Loading backtest configuration" />
      ) : (
        <BacktestLab
          catalog={data.catalog ?? []}
          templates={data.templates}
          jobs={data.backtestJobs}
          onJobsChange={data.setBacktestJobs}
          onCatalogChange={data.setCatalog}
          strategyBuilderMode={session.workspace?.capabilities.strategy_builder_mode ?? "rules"}
          runGate={gate}
        />
      )}
    </>
  );
}

function PaperScreen({ session, data }: { session: AppSession; data: WorkspaceData }) {
  const { access } = session;
  const gate = access.deployPaperAgents;
  return (
    <>
      <PageHeader
        eyebrow="Paper trading"
        title="Forward simulation"
        description="Deploy a validated strategy to the paper simulator and monitor it forward in time. Equity, positions, and orders here are simulated by an internal ledger — no broker is connected and no real-money orders are placed."
        meta={
          <>
            <span>{data.paper?.strategies?.length ?? 0} agents · ledger as of {data.paper?.asof_date ?? "no run yet"}</span>
            <Tag tone="info">Forward simulation</Tag>
            {gate.allowed ? null : <Tag tone="warn">Deployment needs a paid plan</Tag>}
          </>
        }
      />
      {gate.allowed ? null : (
        <AccessNotice
          reason={gate.reason ?? "subscription"}
          feature="Deploying a simulated paper agent"
          whatItDoes="Promotes a validated backtest to a simulated agent that trades forward against a paper ledger, tracking simulated equity, target weights, holdings, and orders."
          unlockedBy="An active paid plan for this workspace. The server refuses new deployments otherwise."
          alternative="Existing agents and their full history stay readable, and you can prepare an agent configuration now."
          actions={<Button variant="primary" onClick={() => { window.location.assign("/pricing"); }}>Compare plans</Button>}
        />
      )}
      {data.errors.paper ? (
        <InlineNotice tone="bad" title="Simulated portfolio unavailable">{data.errors.paper}</InlineNotice>
      ) : data.isLoading ? (
        <ScreenFallback label="Loading paper trading" />
      ) : !data.paper ? (
        <InlineNotice tone="neutral" title="No paper state yet">
          No paper run has been recorded for this workspace. Deploy a validated backtest to create the first simulated
          ledger.
        </InlineNotice>
      ) : (
        <LiveOps
          payload={data.paper}
          catalog={data.catalog ?? []}
          paperJobs={data.paperJobs}
          onJobsChange={data.setPaperJobs}
          onPaperPayload={data.setPaper}
          onRefresh={() => void data.refresh()}
          runGate={gate}
        />
      )}
    </>
  );
}

function ResearchScreen({ session }: { session: AppSession }) {
  const gate = session.access.runCompute;
  return (
    <>
      <PageHeader
        eyebrow="AI research"
        title="Evidence-linked market review"
        description="An informational multi-analyst review of a ticker — bull, bear, technical, and risk perspectives — synthesised with a stated confidence, linked sources, data freshness, and missing-data indicators. Informational only; not financial advice."
        meta={
          <>
            <Tag tone="info">Informational only</Tag>
            {gate.allowed ? null : <Tag tone="warn">New reviews need a paid plan</Tag>}
          </>
        }
      />
      {gate.allowed ? null : (
        <AccessNotice
          reason={gate.reason ?? "subscription"}
          feature="Running the research committee"
          whatItDoes="Runs several analyst agents over price, fundamental, news, and risk evidence for one ticker, then synthesises a decision with a confidence score and a full audit trail of what each agent read."
          unlockedBy="An active paid plan for this workspace, because each run consumes model and data-provider capacity."
          alternative="Saved reports in this workspace stay readable in full, including signals, evidence, provenance, and warnings."
          actions={<Button variant="primary" icon={<Sparkles size={14} />} onClick={() => { window.location.assign("/pricing"); }}>Compare plans</Button>}
        />
      )}
      <MarketResearchLab runGate={gate} />
    </>
  );
}

function SentimentScreen({ session }: { session: AppSession }) {
  const gate = session.access.runCompute;
  return (
    <>
      <PageHeader
        eyebrow="Data & sentiment"
        title="News datasets and financial events"
        description="Build the news dataset that sentiment features read from, inspect every scored headline behind a signal, and compare coverage against reported financial events."
        meta={
          <>
            <Tag tone="info">Source-linked</Tag>
            {gate.allowed ? null : <Tag tone="warn">Building datasets needs a paid plan</Tag>}
          </>
        }
      />
      {gate.allowed ? null : (
        <AccessNotice
          reason={gate.reason ?? "subscription"}
          feature="Building a sentiment dataset"
          whatItDoes="Collects headlines from RSS, local web search, files, or API providers, scores them per ticker, and stores the raw and scored rows so any signal can be audited back to its source text."
          unlockedBy="An active paid plan for this workspace, because collection consumes provider capacity."
          alternative="Any dataset already stored for this workspace is fully browsable — headlines, per-source coverage, heatmaps, and stored warnings."
          actions={<Button variant="primary" icon={<Newspaper size={14} />} onClick={() => { window.location.assign("/pricing"); }}>Compare plans</Button>}
        />
      )}
      <SentimentLab runGate={gate} />
    </>
  );
}

function WorkspaceScreen({ session, onRefresh }: { session: AppSession; onRefresh: () => void }) {
  const navigate = useNavigate();
  if (!session.auth) return <Navigate to="/" replace />;
  return (
    <>
      <PageHeader
        eyebrow="Workspace"
        title="Setup and saved records"
        description="Your onboarding checklist, projects, saved experiments, paper-agent records, research reports, data configuration, and operational diagnostics for this workspace."
        meta={
          <>
            <span>Workspace: {session.activeOrganization?.name ?? "Unknown"}</span>
            <span>Your role here: {session.access.orgRoleLabel}</span>
            <Tag tone={session.access.hasPremium ? "good" : "neutral"}>
              {session.access.subscription.planLabel} · {session.access.subscription.stateLabel}
            </Tag>
          </>
        }
        actions={
          session.access.viewManagement.allowed ? (
            <Button variant="secondary" onClick={() => navigate("/management")}>Workspace management</Button>
          ) : null
        }
      />
      <SaaSWorkspace
        auth={session.auth}
        activeOrganizationId={session.activeOrgId}
        workspace={session.workspace}
        organizations={session.organizations}
        onSwitchOrganization={session.switchOrganization}
        onRefresh={async () => {
          await session.refresh();
          onRefresh();
        }}
        onNavigate={(view) => navigate(LEGACY_HASH_ROUTES[String(view)] ?? "/overview")}
      />
    </>
  );
}

function AdminScreen({ session }: { session: AppSession }) {
  const { access } = session;
  if (!access.administerPlatform.allowed) {
    return (
      <>
        <PageHeader
          eyebrow="Administration"
          title="Platform administration"
          description="Deployment-wide accounts, subscriptions, telemetry, audit activity, and system health."
        />
        <AccessNotice
          reason={access.administerPlatform.reason ?? "platform_role"}
          feature="Platform administration"
          whatItDoes="Covers every workspace on this deployment: account and organization oversight, role and status changes, subscription visibility, telemetry, audit activity, and system health."
          unlockedBy="The platform administrator role on your account. It is never granted by a paid plan or by managing a workspace."
          alternative="If you own or manage a workspace, workspace-level oversight is available under Management."
          actions={
            <>
              {access.viewManagement.allowed ? (
                <Button variant="secondary" onClick={() => { window.location.assign("/management"); }}>Open workspace management</Button>
              ) : null}
              <Button variant="ghost" onClick={() => { window.location.assign("/overview"); }}>Back to overview</Button>
            </>
          }
        />
      </>
    );
  }
  if (!session.auth) return <Navigate to="/" replace />;
  return (
    <>
      <PageHeader
        eyebrow="Administration"
        title="Platform administration"
        description="Deployment-wide oversight. Actions here affect other people's workspaces, so subscription segment and organizational role are shown separately and destructive changes are confirmed."
        meta={
          <>
            <Tag tone="elevated">Elevated platform access</Tag>
            <span>Signed in as {access.email}</span>
          </>
        }
      />
      <InlineNotice tone="elevated" title="You are acting with platform-wide authority">
        Changes made here apply across workspaces and are written to the audit log. Platform authority is separate from
        subscription status — a paying customer is not an administrator, and an administrator is not necessarily a
        paying customer.
      </InlineNotice>
      <AdminDashboard auth={session.auth} />
    </>
  );
}

function AccountScreen({ session }: { session: AppSession }) {
  if (!session.auth) return <Navigate to="/" replace />;
  const { access } = session;
  return (
    <>
      <PageHeader
        eyebrow="Account"
        title="Account and security"
        description="Your profile, email verification, password, multi-factor authentication, data export, and account deletion."
        meta={
          <>
            <span>{access.email}</span>
            <Tag tone={access.isPlatformAdmin ? "elevated" : "neutral"}>
              Platform role: {access.isPlatformAdmin ? "Administrator" : "Standard account"}
            </Tag>
            <Tag tone="neutral">Workspace role: {access.orgRoleLabel}</Tag>
            <Tag tone={access.hasPremium ? "good" : "neutral"}>
              Plan: {access.subscription.planLabel} · {access.subscription.stateLabel}
            </Tag>
          </>
        }
      />
      <InlineNotice tone="info" compact>
        Your plan and your roles are independent. Changing the workspace subscription does not change anyone's role, and
        a role change does not alter the subscription.
      </InlineNotice>
      <AccountSecurity auth={session.auth} onDeleted={() => void session.handleLogout()} />
    </>
  );
}

function PricingScreen({ session }: { session: AppSession }) {
  const { access } = session;
  return (
    <>
      <PageHeader
        eyebrow="Plans & billing"
        title="Plans and workspace subscription"
        description="What each plan unlocks, what this workspace currently has, and how to change it. Premium access is enforced by the server, so the plan shown here is the plan in force."
        meta={
          <>
            <Tag tone={access.hasPremium ? "good" : "neutral"}>
              Current: {access.subscription.planLabel} · {access.subscription.stateLabel}
            </Tag>
            {access.premiumViaAdminOverride ? (
              <Tag tone="elevated">Premium via administrator role, not a subscription</Tag>
            ) : null}
          </>
        }
      />
      {access.premiumViaAdminOverride ? (
        <InlineNotice tone="elevated" title="Your premium access comes from your role">
          Your account holds the platform administrator role, which the server treats as premium for feature checks.
          This workspace's own subscription is {access.subscription.planLabel} ({access.subscription.stateLabel.toLowerCase()}).
          Other members of this workspace get access based on the subscription, not your role.
        </InlineNotice>
      ) : null}
      {access.subscription.needsBillingAttention ? (
        <InlineNotice tone="warn" title="This workspace needs billing attention">
          The plan is {access.subscription.planLabel} but its status is {access.subscription.stateLabel.toLowerCase()},
          so the server is refusing premium workflows. Resolving billing restores access. No role or saved data has
          changed.
        </InlineNotice>
      ) : null}
      <InlineNotice tone="info" compact>
        Billing is a workspace-level setting. Any member of this workspace can open checkout or the billing portal — the
        API does not expose a separate billing permission — and paying never grants management or administrative
        authority.
      </InlineNotice>
      <PricingPage
        workspace={session.workspace}
        reason={null}
        isAdminAccess={access.isPlatformAdmin}
        onRefresh={async () => {
          await session.refresh();
        }}
      />
    </>
  );
}

function LearnScreen({ session, data }: { session: AppSession; data: WorkspaceData }) {
  return (
    <>
      <PageHeader
        eyebrow="Learn"
        title="Definitions and how the platform works"
        description="Plain-language explanations of every metric the product reports, how a run moves through the system, and where each number comes from."
        meta={<Tag tone="info"><BookOpen size={12} aria-hidden="true" /> Reference</Tag>}
      />
      <SystemGuide
        health={session.health}
        metadata={data.metadata}
        paperJobs={data.paperJobs}
        backtestJobs={data.backtestJobs}
      />
    </>
  );
}

function NotFoundScreen() {
  const navigate = useNavigate();
  return (
    <>
      <PageHeader eyebrow="Not found" title="That screen does not exist" description="The address you followed is not part of Meridian." />
      <InlineNotice
        tone="warn"
        title="Nothing here"
        actions={
          <>
            <Button variant="primary" icon={<Activity size={14} />} onClick={() => navigate("/overview")}>Go to overview</Button>
            <Button variant="secondary" icon={<FlaskConical size={14} />} onClick={() => navigate("/backtests")}>Open backtests</Button>
          </>
        }
      >
        If you followed a bookmark from an older version of the product, the equivalent screen is reachable from the
        navigation on the left.
      </InlineNotice>
    </>
  );
}

export default AppRoutes;
