import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Ban, CheckCircle2, RefreshCw, RotateCcw, Search, ShieldCheck, Trash2, UserCog, UserX } from "lucide-react";

import { deleteAdminUserStrategy, getAdminOverview, getAdminQuotas, getAdminSystemHealth, getSentimentDatasets, getSentimentEvaluation, getSentimentModels, listAdminAuditLog, listAdminUserStrategies, listAdminUsers, updateAdminQuotas, updateAdminUser, updateAdminUserStrategy } from "../api/client";
import type { AdminAuditEntry, AdminOverviewPayload, AdminQuotasResponse, AdminSystemHealth, AdminUserRecord, AuthResponse, SentimentDatasetInfo, SentimentEvalResult, SentimentModelInfo, UserStrategyRecord } from "../api/types";
import { Badge } from "../components/Badge";
import { MetricCard, Panel, SectionHeader } from "../components/Cards";
import { HorizontalBars, TelemetryTimelineChart } from "../components/Charts";
import { DataTable } from "../components/Table";
import { formatDateTime, formatNumber, formatPercent, statusTone } from "../utils/format";
import {
  ADMIN_SEGMENTS,
  ORG_ROLE_SHORT,
  accountMatchesSegment,
  isAdminRole,
  isWorkspaceManagerRole,
  normalizeOrgRole,
  summarizeSubscription,
  type AdminSegmentId
} from "../access/model";

type UserSort = "created_at_utc" | "email" | "role" | "status" | "last_login_at_utc" | "plan" | "subscription_status";

function userTone(user: AdminUserRecord) {
  if (user.status !== "active") return "bad";
  return isAdminRole(user.role) ? "good" : "neutral";
}

/**
 * Subscription summary for an admin user row, built only from the plan and
 * subscription_status fields the API actually returns.
 */
function accountSubscription(user: AdminUserRecord) {
  return summarizeSubscription(
    user.plan == null && user.subscription_status == null
      ? null
      : ({ plan: String(user.plan ?? "free"), status: String(user.subscription_status ?? "") } as never)
  );
}

export function AdminDashboard({ auth }: { auth: AuthResponse }) {
  const [overview, setOverview] = useState<AdminOverviewPayload | null>(null);
  const [users, setUsers] = useState<AdminUserRecord[]>([]);
  const [strategies, setStrategies] = useState<UserStrategyRecord[]>([]);
  const [search, setSearch] = useState("");
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const [strategyStatus, setStrategyStatus] = useState("");
  const [strategyRisk, setStrategyRisk] = useState("");
  const [segment, setSegment] = useState<AdminSegmentId>("all");
  const [sortBy, setSortBy] = useState<UserSort>("created_at_utc");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [landingSearch, setLandingSearch] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [evalResult, setEvalResult] = useState<SentimentEvalResult | null>(null);
  const [evalLoading, setEvalLoading] = useState(false);
  const [evalDatasets, setEvalDatasets] = useState<SentimentDatasetInfo[]>([]);
  const [evalModelInfos, setEvalModelInfos] = useState<SentimentModelInfo[]>([]);
  const [selectedDataset, setSelectedDataset] = useState("financial_phrasebank");
  const [selectedModels, setSelectedModels] = useState<Set<string>>(new Set(["finbert", "vader", "rule_based", "ensemble"]));
  const [systemHealth, setSystemHealth] = useState<AdminSystemHealth | null>(null);
  const [auditEntries, setAuditEntries] = useState<AdminAuditEntry[]>([]);
  const [quotas, setQuotas] = useState<AdminQuotasResponse | null>(null);
  const [operationsLoading, setOperationsLoading] = useState(false);
  const [operationsError, setOperationsError] = useState<string | null>(null);
  const [auditAction, setAuditAction] = useState("");
  const [quotaEdits, setQuotaEdits] = useState<Record<string, Record<string, string>>>({});

  async function load() {
    setIsLoading(true);
    setError(null);
    try {
      const [nextOverview, nextUsers, nextStrategies] = await Promise.all([
        getAdminOverview(),
        listAdminUsers({
          search,
          role: role || undefined,
          status: status || undefined,
          sort_by: sortBy,
          sort_dir: sortDir,
          limit: 300
        }),
        listAdminUserStrategies({
          status: strategyStatus || undefined,
          risk_level: strategyRisk || undefined,
          limit: 300
        })
      ]);
      setOverview(nextOverview);
      setUsers(nextUsers);
      setStrategies(nextStrategies);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load admin dashboard.");
      setOverview(null);
      setUsers([]);
      setStrategies([]);
    } finally {
      setIsLoading(false);
    }
  }

  async function loadOperations() {
    setOperationsLoading(true);
    setOperationsError(null);
    const [healthResult, auditResult, quotaResult] = await Promise.allSettled([
      getAdminSystemHealth(),
      listAdminAuditLog({ limit: 100, action: auditAction || undefined }),
      getAdminQuotas()
    ]);
    const failures: string[] = [];
    if (healthResult.status === "fulfilled") setSystemHealth(healthResult.value);
    else failures.push("health");
    if (auditResult.status === "fulfilled") setAuditEntries(auditResult.value);
    else failures.push("audit log");
    if (quotaResult.status === "fulfilled") setQuotas(quotaResult.value);
    else failures.push("quotas");
    if (failures.length) setOperationsError(`Could not load ${failures.join(", ")}. Other admin data remains available.`);
    setOperationsLoading(false);
  }

  useEffect(() => {
    void load();
    void loadOperations();
    void Promise.all([
      getSentimentDatasets().then(setEvalDatasets).catch(() => {}),
      getSentimentModels().then(setEvalModelInfos).catch(() => {}),
    ]);
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void getAdminSystemHealth().then(setSystemHealth).catch(() => undefined);
    }, 30000);
    return () => window.clearInterval(timer);
  }, []);

  async function saveQuota(organizationId: string) {
    const edits = quotaEdits[organizationId] ?? {};
    const limits = Object.fromEntries(
      Object.entries(edits)
        .filter(([, value]) => value.trim() !== "")
        .map(([key, value]) => [key, Number(value)])
    );
    if (!Object.keys(limits).length) return;
    if (Object.values(limits).some((value) => !Number.isSafeInteger(value) || value < 0)) {
      setOperationsError("Quota limits must be non-negative whole numbers.");
      return;
    }
    if (!window.confirm(`Apply ${Object.keys(limits).length} quota change(s) to this organization?`)) return;
    setOperationsLoading(true);
    setOperationsError(null);
    try {
      await updateAdminQuotas(organizationId, limits);
      setQuotaEdits((current) => ({ ...current, [organizationId]: {} }));
      setNotice("Organization quotas updated and reloaded from the server.");
      setQuotas(await getAdminQuotas());
    } catch (caught) {
      setOperationsError(caught instanceof Error ? caught.message : "Could not update quotas.");
    } finally {
      setOperationsLoading(false);
    }
  }

  const userSummary = useMemo(() => {
    const paid = users.filter((user) => ["active", "trialing"].includes(String(user.subscription_status))).length;
    return { paid, inactive: users.filter((user) => user.status !== "active").length };
  }, [users]);
  const landing = overview?.landing_analytics;
  const landingEvents = useMemo(() => {
    const term = landingSearch.trim().toLowerCase();
    const events = landing?.recent_events ?? [];
    if (!term) return events;
    return events.filter((event) => {
      const section = String(event.properties?.section ?? event.properties?.target_section ?? "");
      const cta = String(event.properties?.cta ?? event.properties?.target ?? "");
      const country = String(event.context?.visitor_country ?? "");
      return [event.name, section, cta, country].some((value) => value.toLowerCase().includes(term));
    });
  }, [landing?.recent_events, landingSearch]);
  const countryRows = Object.entries(landing?.visitors_by_country ?? {})
    .map(([label, value]) => ({ label, value, tone: "neutral" as const }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 8);
  const sectionRows = Object.entries(landing?.section_views ?? {})
    .map(([label, value]) => ({ label, value, tone: label === "pricing" ? "good" as const : "neutral" as const }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 8);
  const ctaRows = Object.entries(landing?.cta_clicks ?? {})
    .map(([label, value]) => ({ label, value, tone: "good" as const }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 8);

  async function updateUser(user: AdminUserRecord, change: { role?: string; status?: string }) {
    const action = change.status === "inactive"
      ? `deactivate ${user.email}`
      : change.status === "active"
        ? `reactivate ${user.email}`
        : `change ${user.email}'s PLATFORM role to ${change.role}`;
    if (!window.confirm(
      `Confirm you want to ${action}.

This affects access immediately across every workspace. `
      + "Platform role is separate from workspace membership and from subscription status: this change does not "
      + "alter any subscription, and it does not alter workspace roles."
    )) return;
    setIsLoading(true);
    setNotice(null);
    setError(null);
    try {
      await updateAdminUser(user.id, change);
      setNotice(`Updated ${user.email}.`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not update the user.");
    } finally {
      setIsLoading(false);
    }
  }

  async function updateStrategy(strategy: UserStrategyRecord, nextStatus: "active" | "disabled") {
    if (!window.confirm(`Confirm ${nextStatus === "disabled" ? "disable" : "reactivate"} ${strategy.name}. This affects backtest availability immediately.`)) return;
    setIsLoading(true);
    setNotice(null);
    setError(null);
    try {
      await updateAdminUserStrategy(strategy.id, nextStatus);
      setNotice(`${strategy.name} is now ${nextStatus}.`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not update the strategy.");
    } finally {
      setIsLoading(false);
    }
  }

  async function runEvaluation() {
    setEvalLoading(true);
    setError(null);
    try {
      const models = Array.from(selectedModels).join(",");
      const result = await getSentimentEvaluation({ dataset: selectedDataset, models, max_samples: 500 });
      setEvalResult(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not run sentiment evaluation.");
    } finally {
      setEvalLoading(false);
    }
  }

  function evalMetricColor(value: number | null): string {
    if (value === null) return "var(--text-muted)";
    if (value >= 0.8) return "var(--positive)";
    if (value >= 0.6) return "var(--warn)";
    return "var(--negative)";
  }

  async function deleteStrategy(strategy: UserStrategyRecord) {
    if (!window.confirm(`Delete ${strategy.name}? This removes it from the owner's allowed strategy list.`)) return;
    setIsLoading(true);
    setNotice(null);
    setError(null);
    try {
      await deleteAdminUserStrategy(strategy.id);
      setNotice(`Deleted ${strategy.name}.`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not delete the strategy.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="admin-dashboard">
      <SectionHeader eyebrow="Admin Control Room" title="Users, payments, telemetry, and system activity">
        <Badge label={auth.user.role === "admin" ? "Admin verified" : "No admin access"} tone={auth.user.role === "admin" ? "good" : "bad"} />
      </SectionHeader>

      {notice ? <section className="alert-card alert-card--good"><CheckCircle2 size={18} /><span>{notice}</span></section> : null}
      {error ? <section className="alert-card"><AlertTriangle size={18} /><span>{error}</span></section> : null}

      <div className="metric-grid">
        <MetricCard label="Registered users" value={formatNumber(overview?.metrics.users_total ?? users.length, 0)} detail={`${formatNumber(overview?.metrics.users_active ?? 0, 0)} active`} icon={<UserCog size={18} />} />
        <MetricCard label="Active admins" value={formatNumber(overview?.metrics.admins_active ?? 0, 0)} detail="Protected from removing last admin" tone="good" icon={<ShieldCheck size={18} />} />
        <MetricCard label="Signups 30d" value={formatNumber(overview?.metrics.signups_30d ?? 0, 0)} detail={`${formatNumber(overview?.metrics.signups_7d ?? 0, 0)} in 7 days`} />
        <MetricCard label="Paid/trialing" value={formatNumber(userSummary.paid, 0)} detail="Subscription status visible per user" tone={userSummary.paid ? "good" : "warn"} />
        <MetricCard label="Inactive users" value={formatNumber(userSummary.inactive, 0)} detail="Sessions revoked on deactivation" tone={userSummary.inactive ? "warn" : "good"} />
      </div>

      <Panel title="Landing page analytics" subtitle="Visitor behavior from the public landing page: section views, CTA clicks, pricing interest, and signup/login conversion.">
        <div className="metric-grid metric-grid--compact">
          <MetricCard label="Landing visits" value={formatNumber(landing?.totals.landing_page_visits ?? 0, 0)} detail="Total public page views" />
          <MetricCard label="Pricing views" value={formatNumber(landing?.totals.pricing_views ?? 0, 0)} detail="Pricing section/page interest" tone={(landing?.totals.pricing_views ?? 0) ? "good" : "neutral"} />
          <MetricCard label="CTA clicks" value={formatNumber(landing?.totals.cta_clicks ?? 0, 0)} detail="Signup, pricing, demo, and login clicks" />
          <MetricCard label="Signup conversion" value={formatPercent(landing?.conversion_rates.signup ?? 0)} detail={`${formatNumber(landing?.totals.signup_completions ?? 0, 0)} created accounts`} tone={(landing?.totals.signup_completions ?? 0) ? "good" : "warn"} />
          <MetricCard label="Login conversion" value={formatPercent(landing?.conversion_rates.login ?? 0)} detail={`${formatNumber(landing?.totals.login_completions ?? 0, 0)} completed logins`} />
        </div>
        <TelemetryTimelineChart events={landing?.recent_events ?? []} />
        <div className="telemetry-chart-grid">
          <article className="telemetry-chart-card">
            <h4>Visitors by country</h4>
            <p>Uses CDN/proxy country headers when available; no IP addresses are stored.</p>
            <HorizontalBars rows={countryRows} valueKind="number" />
          </article>
          <article className="telemetry-chart-card">
            <h4>Section views</h4>
            <p>Shows where visitors spend attention across features, examples, pricing, FAQ, login, and signup.</p>
            <HorizontalBars rows={sectionRows} valueKind="number" />
          </article>
          <article className="telemetry-chart-card telemetry-chart-card--wide">
            <h4>CTA clicks</h4>
            <p>Highlights conversion intent: free signup, pricing, demo, and login actions.</p>
            <HorizontalBars rows={ctaRows} valueKind="number" />
          </article>
        </div>
        <div className="admin-toolbar admin-toolbar--compact">
          <label htmlFor="admin-landing-search">
            Search landing events
            <input id="admin-landing-search" value={landingSearch} onChange={(event) => setLandingSearch(event.target.value)} placeholder="pricing, signup, country, CTA..." />
          </label>
        </div>
        <DataTable
          empty="No landing telemetry events match this filter yet."
          getKey={(event) => event.id}
          columns={[
            { key: "event", header: "Event", render: (event) => event.name },
            { key: "section", header: "Section/CTA", render: (event) => String(event.properties?.section ?? event.properties?.cta ?? event.properties?.target_section ?? "n/a") },
            { key: "country", header: "Country", render: (event) => String(event.context?.visitor_country ?? "Unknown") },
            { key: "time", header: "Time", render: (event) => formatDateTime(event.occurred_at_utc) }
          ]}
          rows={landingEvents.slice(0, 25)}
        />
      </Panel>

      <Panel
        title="Accounts"
        subtitle="Subscription segment and organizational role are separate dimensions. A paying customer is not an administrator, and an administrator is not necessarily paying."
      >
        <div className="section-tabs" role="tablist" aria-label="Account audience segments">
          {ADMIN_SEGMENTS.map((option) => (
            <button
              key={option.id}
              type="button"
              role="tab"
              aria-selected={segment === option.id}
              aria-pressed={segment === option.id}
              title={option.description}
              onClick={() => setSegment(option.id)}
            >
              {option.label}
              {" "}
              <span className="table-count">{users.filter((user) => accountMatchesSegment(user, option.id)).length}</span>
            </button>
          ))}
        </div>
        <p className="chart-subtitle">{ADMIN_SEGMENTS.find((option) => option.id === segment)?.description}</p>
        <div className="admin-toolbar">
          <label htmlFor="admin-user-search">
            Search
            <input id="admin-user-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Email, name, or organization" />
          </label>
          <label htmlFor="admin-user-role">
            Role
            <select id="admin-user-role" value={role} onChange={(event) => setRole(event.target.value)}>
              <option value="">All roles</option>
              <option value="admin">Admins</option>
              <option value="user">Users</option>
            </select>
          </label>
          <label htmlFor="admin-user-status">
            Status
            <select id="admin-user-status" value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="">All statuses</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </label>
          <label htmlFor="admin-user-sort">
            Sort
            <select id="admin-user-sort" value={sortBy} onChange={(event) => setSortBy(event.target.value as UserSort)}>
              <option value="created_at_utc">Created</option>
              <option value="email">Email</option>
              <option value="role">Role</option>
              <option value="status">Status</option>
              <option value="last_login_at_utc">Last login</option>
              <option value="plan">Plan</option>
              <option value="subscription_status">Subscription</option>
            </select>
          </label>
          <label htmlFor="admin-user-dir">
            Direction
            <select id="admin-user-dir" value={sortDir} onChange={(event) => setSortDir(event.target.value as "asc" | "desc")}>
              <option value="desc">Descending</option>
              <option value="asc">Ascending</option>
            </select>
          </label>
          <button type="button" className="primary-button" onClick={() => void load()} disabled={isLoading}>
            {isLoading ? <RefreshCw size={16} className="spin" /> : <Search size={16} />}
            Apply
          </button>
        </div>

        <DataTable
          empty="No users match these filters."
          getKey={(user) => user.id}
          columns={[
            { key: "user", header: "User", render: (user) => <strong>{user.display_name}<br /><small>{user.email}</small></strong> },
            { key: "platform_role", header: "Platform role", render: (user) => <Badge label={isAdminRole(user.role) ? "administrator" : "standard"} tone={userTone(user)} /> },
            {
              key: "workspace_role",
              header: "Workspace role",
              render: (user) => {
                const orgRole = normalizeOrgRole(user.organization_role);
                return (
                  <Badge
                    label={ORG_ROLE_SHORT[orgRole]}
                    tone={isWorkspaceManagerRole(orgRole) ? "info" : "neutral"}
                  />
                );
              }
            },
            { key: "status", header: "Status", render: (user) => <Badge label={user.status} tone={statusTone(user.status)} /> },
            { key: "workspace", header: "Workspace", render: (user) => user.organization_name ?? "No workspace" },
            {
              key: "plan",
              header: "Plan",
              render: (user) => {
                const subscription = accountSubscription(user);
                return (
                  <span className="stacked-cell">
                    <strong>{subscription.planLabel}</strong>
                    <span>{subscription.stateLabel}</span>
                  </span>
                );
              }
            },
            { key: "last_login", header: "Last login", render: (user) => formatDateTime(user.last_login_at_utc) },
            {
              key: "actions",
              header: "Actions",
              render: (user) => (
                <div className="table-actions">
                  <button
                    type="button"
                    className="ghost-button"
                    title="Changes the platform role only. Subscription status and workspace membership are unaffected."
                    onClick={() => void updateUser(user, { role: isAdminRole(user.role) ? "user" : "admin" })}
                    disabled={isLoading || user.id === auth.user.id}
                  >
                    <UserCog size={14} />
                    {isAdminRole(user.role) ? "Revoke platform admin" : "Grant platform admin"}
                  </button>
                  <button type="button" className="ghost-button danger-button" onClick={() => void updateUser(user, { status: user.status === "active" ? "inactive" : "active" })} disabled={isLoading || user.id === auth.user.id}>
                    <UserX size={14} />
                    {user.status === "active" ? "Deactivate" : "Reactivate"}
                  </button>
                </div>
              )
            }
          ]}
          rows={users.filter((user) => accountMatchesSegment(user, segment))}
        />
        <p className="chart-subtitle">
          Showing {users.filter((user) => accountMatchesSegment(user, segment)).length} of {users.length} loaded
          accounts. Segments are computed from the role, status, plan, and subscription-status fields returned by the
          API — never inferred from unrelated data.
        </p>
      </Panel>

      <Panel title="User-created strategies" subtitle="Audit AI-generated specs, ownership, approval history, usage, and safety status.">
        <div className="admin-toolbar admin-toolbar--strategy">
          <label htmlFor="admin-strategy-status">
            Status
            <select id="admin-strategy-status" value={strategyStatus} onChange={(event) => setStrategyStatus(event.target.value)}>
              <option value="">All statuses</option>
              <option value="active">Active</option>
              <option value="disabled">Disabled</option>
            </select>
          </label>
          <label htmlFor="admin-strategy-risk">
            Risk
            <select id="admin-strategy-risk" value={strategyRisk} onChange={(event) => setStrategyRisk(event.target.value)}>
              <option value="">All risk levels</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </label>
          <button type="button" className="primary-button" onClick={() => void load()} disabled={isLoading}>
            {isLoading ? <RefreshCw size={16} className="spin" /> : <Search size={16} />}
            Apply
          </button>
        </div>
        <DataTable
          rows={strategies}
          empty="No user-created strategies match these filters."
          getKey={(strategy) => strategy.id}
          columns={[
            { key: "name", header: "Strategy", render: (strategy) => <strong>{strategy.name}<br /><small>v{strategy.version} · {strategy.id}</small></strong> },
            { key: "owner", header: "Owner", render: (strategy) => strategy.owner_email ?? strategy.owner_user_id },
            { key: "status", header: "Status", render: (strategy) => <Badge label={strategy.status} tone={statusTone(strategy.status)} /> },
            { key: "risk", header: "Risk", render: (strategy) => <Badge label={strategy.risk_level} tone={strategy.risk_level === "high" ? "bad" : strategy.risk_level === "medium" ? "warn" : "good"} /> },
            { key: "created", header: "Created", render: (strategy) => formatDateTime(strategy.created_at_utc) },
            { key: "approved", header: "Approved", render: (strategy) => formatDateTime(strategy.approved_at_utc) },
            { key: "backtests", header: "Backtests", align: "right", render: (strategy) => formatNumber(strategy.backtest_count, 0) },
            {
              key: "spec",
              header: "Audit",
              render: (strategy) => (
                <details className="table-details">
                  <summary>View audit</summary>
                  <pre>{JSON.stringify({ spec: strategy.spec, approval: strategy.approval, validation: strategy.validation }, null, 2)}</pre>
                </details>
              )
            },
            {
              key: "actions",
              header: "Actions",
              render: (strategy) => (
                <div className="table-actions">
                  <button type="button" className="ghost-button" onClick={() => void updateStrategy(strategy, strategy.status === "active" ? "disabled" : "active")} disabled={isLoading}>
                    {strategy.status === "active" ? <Ban size={14} /> : <RotateCcw size={14} />}
                    {strategy.status === "active" ? "Disable" : "Reactivate"}
                  </button>
                  <button type="button" className="ghost-button danger-button" onClick={() => void deleteStrategy(strategy)} disabled={isLoading}>
                    <Trash2 size={14} />
                    Delete
                  </button>
                </div>
              )
            }
          ]}
        />
      </Panel>

      <Panel title="Sentiment model evaluation" subtitle="Benchmark sentiment models against labeled datasets. FinBERT was trained on Financial PhraseBank — scores may be inflated.">
        <div className="admin-toolbar admin-toolbar--wide">
          <label htmlFor="eval-dataset">
            Dataset
            <select id="eval-dataset" value={selectedDataset} onChange={(e) => setSelectedDataset(e.target.value)}>
              {evalDatasets.map((ds) => (
                <option key={ds.key} value={ds.key}>{ds.name}</option>
              ))}
            </select>
          </label>
          <fieldset className="eval-model-checkboxes">
            <legend>Models</legend>
            {evalModelInfos.map((m) => (
              <label key={m.type}>
                <input
                  type="checkbox"
                  checked={selectedModels.has(m.type)}
                  onChange={() => setSelectedModels((prev) => { const next = new Set(prev); if (next.has(m.type)) next.delete(m.type); else next.add(m.type); return next; })}
                />
                {m.name}
              </label>
            ))}
          </fieldset>
          <button type="button" className="primary-button" onClick={() => void runEvaluation()} disabled={evalLoading || selectedModels.size === 0}>
            {evalLoading ? <RefreshCw size={16} className="spin" /> : null}
            {evalLoading ? "Evaluating…" : "Run evaluation"}
          </button>
        </div>
        {evalResult ? (
          <div>
            <div className="metric-grid metric-grid--compact" style={{ marginBottom: "16px" }}>
              <MetricCard label="Dataset" value={evalResult.dataset} detail={`${evalResult.dataset_size} samples`} />
              <MetricCard label="Label distribution" value={JSON.stringify(evalResult.label_distribution)} />
              <MetricCard label="Evaluated at" value={formatDateTime(evalResult.evaluated_at)} />
            </div>
            <table className="eval-matrix-table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Accuracy</th>
                  <th>Precision (macro)</th>
                  <th>Recall (macro)</th>
                  <th>F1 (macro)</th>
                  <th>Per-class F1</th>
                  <th>Timing</th>
                </tr>
              </thead>
              <tbody>
                {evalResult.models.map((model) => (
                  <tr key={model.model_name} className={model.error ? "eval-matrix-row--errored" : ""}>
                    <td><strong>{model.model_name}</strong></td>
                    {model.error ? (
                      <td colSpan={6} style={{ color: "var(--negative)" }}>{model.error}</td>
                    ) : (
                      <>
                        <td style={{ color: evalMetricColor(model.accuracy), fontWeight: 700 }}>
                          {model.accuracy !== null ? formatPercent(model.accuracy) : "—"}
                        </td>
                        <td style={{ color: evalMetricColor(model.macro_precision) }}>
                          {model.macro_precision !== null ? formatPercent(model.macro_precision) : "—"}
                        </td>
                        <td style={{ color: evalMetricColor(model.macro_recall) }}>
                          {model.macro_recall !== null ? formatPercent(model.macro_recall) : "—"}
                        </td>
                        <td style={{ color: evalMetricColor(model.macro_f1), fontWeight: 700 }}>
                          {model.macro_f1 !== null ? formatPercent(model.macro_f1) : "—"}
                        </td>
                        <td>
                          {model.f1 ? (
                            <span className="eval-per-class">
                              {["positive", "negative", "neutral"].map((cls) => (
                                <span key={cls} style={{ color: evalMetricColor(model.f1![cls] ?? null) }}>
                                  {cls}: {formatPercent(model.f1![cls] ?? 0)}
                                </span>
                              ))}
                            </span>
                          ) : "—"}
                        </td>
                        <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                          {model.timing_ms !== null ? `${model.timing_ms.toFixed(0)} ms` : "—"}
                        </td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="eval-detail-section">
              <div>
                {evalResult.models.filter((m) => !m.error).map((model) => (
                  <details key={model.model_name} className="eval-confusion-details">
                    <summary>Confusion matrix — {model.model_name}</summary>
                    {model.confusion_matrix ? (
                      <table className="eval-confusion-table">
                        <thead>
                          <tr>
                            <th>True \ Pred</th>
                            {["positive", "negative", "neutral"].map((cls) => (
                              <th key={cls}>{cls}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {["positive", "negative", "neutral"].map((trueCls) => (
                            <tr key={trueCls}>
                              <td><strong>{trueCls}</strong></td>
                              {["positive", "negative", "neutral"].map((predCls) => (
                                <td key={predCls}>
                                  {model.confusion_matrix![trueCls]?.[predCls] ?? 0}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : null}
                  </details>
                ))}
              </div>
              <div>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => {
                    const text = [
                      `# Sentiment Model Evaluation: ${evalResult.dataset}`,
                      "",
                      `- **Samples**: ${evalResult.dataset_size}`,
                      `- **Label distribution**: ${JSON.stringify(evalResult.label_distribution)}`,
                      `- **Evaluated at**: ${evalResult.evaluated_at}`,
                      "",
                      "| Model | Accuracy | Precision (macro) | Recall (macro) | F1 (macro) | Timing (ms) |",
                      "|-------|----------|-------------------|----------------|------------|-------------|",
                      ...evalResult.models.map((m) =>
                        m.error
                          ? `| ${m.model_name} | ERROR: ${m.error} | — | — | — | — |`
                          : `| ${m.model_name} | ${m.accuracy !== null ? formatPercent(m.accuracy) : "—"} | ${m.macro_precision !== null ? formatPercent(m.macro_precision) : "—"} | ${m.macro_recall !== null ? formatPercent(m.macro_recall) : "—"} | ${m.macro_f1 !== null ? formatPercent(m.macro_f1) : "—"} | ${m.timing_ms !== null ? `${m.timing_ms.toFixed(0)}` : "—"} |`
                      ),
                      "",
                    ].join("\n");
                    const blob = new Blob([text], { type: "text/markdown" });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = `sentiment-eval-${selectedDataset}.md`;
                    a.click();
                    URL.revokeObjectURL(url);
                  }}
                >
                  Download markdown report
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </Panel>

      {operationsError ? <section className="alert-card"><AlertTriangle size={18} /><span>{operationsError}</span></section> : null}

      <Panel title="Operational health" subtitle="Safe dependency state, controller heartbeat, and durable queue visibility.">
        <div className="admin-toolbar admin-toolbar--compact">
          <button type="button" className="ghost-button" onClick={() => void loadOperations()} disabled={operationsLoading}>
            <RefreshCw size={16} /> {operationsLoading ? "Refreshing" : "Refresh operations"}
          </button>
        </div>
        {systemHealth ? (
          <div className="metric-grid metric-grid--compact">
            <MetricCard label="Controller" value={systemHealth.queue.controller.status} detail={systemHealth.queue.controller.age_seconds == null ? "No heartbeat age" : `${formatNumber(systemHealth.queue.controller.age_seconds, 0)}s old`} tone={systemHealth.queue.controller.healthy ? "good" : "warn"} />
            <MetricCard label="Dispatch pending" value={formatNumber(systemHealth.queue.dispatch_pending_count, 0)} detail={systemHealth.queue.oldest_dispatch_pending_age_seconds == null ? "None pending" : `Oldest ${formatNumber(systemHealth.queue.oldest_dispatch_pending_age_seconds, 0)}s`} tone={systemHealth.queue.dispatch_pending_count ? "warn" : "good"} />
            <MetricCard label="Oldest queued" value={systemHealth.queue.oldest_queued_age_seconds == null ? "None" : `${formatNumber(systemHealth.queue.oldest_queued_age_seconds, 0)}s`} detail={Object.entries(systemHealth.queue.queued_count_by_kind).map(([kind, count]) => `${kind}: ${count}`).join(" · ")} />
            <MetricCard label="Storage" value={systemHealth.storage.s3_configured ? "Configured" : "Local/unconfigured"} detail={`Database: ${systemHealth.database.metadata_store}`} tone={systemHealth.storage.s3_configured ? "good" : "neutral"} />
          </div>
        ) : <p>{operationsLoading ? "Loading health…" : "Health data is unavailable."}</p>}
      </Panel>

      <Panel title="Audit log" subtitle="Paginated server audit entries with bounded, secret-redacted metadata.">
        <div className="admin-toolbar admin-toolbar--compact">
          <label htmlFor="admin-audit-action">Action filter
            <input id="admin-audit-action" value={auditAction} onChange={(event) => setAuditAction(event.target.value)} placeholder="strategy_builder.approved" />
          </label>
          <button type="button" className="ghost-button" onClick={() => void loadOperations()} disabled={operationsLoading}>Apply filter</button>
        </div>
        <DataTable
          rows={auditEntries}
          empty={operationsLoading ? "Loading audit entries…" : "No audit entries match the current filter."}
          getKey={(entry) => entry.id}
          columns={[
            { key: "time", header: "Time", render: (entry) => formatDateTime(entry.occurred_at_utc) },
            { key: "action", header: "Action", render: (entry) => entry.action },
            { key: "organization", header: "Organization", render: (entry) => entry.organization_id ?? "system" },
            { key: "target", header: "Target", render: (entry) => [entry.target_type, entry.target_id].filter(Boolean).join(": ") || "—" },
            { key: "metadata", header: "Safe metadata", render: (entry) => <span className="path-cell" title={JSON.stringify(entry.metadata)}>{JSON.stringify(entry.metadata).slice(0, 180)}</span> }
          ]}
        />
      </Panel>

      <Panel title="Organization quotas" subtitle="Edit bounded daily limits. Zero disables the corresponding workflow for non-admin users.">
        {!quotas?.organizations.length ? <p>{operationsLoading ? "Loading quotas…" : "No organizations are available."}</p> : null}
        {quotas?.organizations.map((organization) => (
          <details key={organization.organization_id} className="advanced-details">
            <summary>{organization.organization_name} · {organization.organization_id}</summary>
            <div className="admin-toolbar">
              {Object.entries(quotas.defaults).map(([feature]) => (
                <label key={feature} htmlFor={`quota-${organization.organization_id}-${feature}`}>
                  {feature.replaceAll("_", " ")}
                  <input
                    id={`quota-${organization.organization_id}-${feature}`}
                    type="number"
                    min={0}
                    step={1}
                    value={quotaEdits[organization.organization_id]?.[feature] ?? ""}
                    placeholder={String(organization.effective[feature] ?? quotas.defaults[feature])}
                    onChange={(event) => setQuotaEdits((current) => ({
                      ...current,
                      [organization.organization_id]: {
                        ...(current[organization.organization_id] ?? {}),
                        [feature]: event.target.value
                      }
                    }))}
                  />
                </label>
              ))}
              <button type="button" className="primary-button" disabled={operationsLoading} onClick={() => void saveQuota(organization.organization_id)}>Confirm quota changes</button>
            </div>
          </details>
        ))}
      </Panel>

      <div className="grid-two">
        <Panel title="Usage telemetry" subtitle="Latest events visible to admins for product and operational diagnosis.">
          <DataTable
            empty="No telemetry events have been recorded yet."
            getKey={(event) => event.id}
            columns={[
              { key: "event", header: "Event", render: (event) => event.name },
              { key: "category", header: "Category", render: (event) => event.category },
              { key: "consent", header: "Consent", render: (event) => event.consent },
              { key: "time", header: "Time", render: (event) => formatDateTime(event.occurred_at_utc) }
            ]}
            rows={overview?.telemetry.slice(0, 20) ?? []}
          />
        </Panel>

        <Panel title="System activity" subtitle="Refresh and job activity for debugging operational health.">
          <DataTable
            empty="No refresh runs have been recorded yet."
            getKey={(run) => run.id}
            columns={[
              { key: "run", header: "Run", render: (run) => <span className="path-cell">{run.id}</span> },
              { key: "status", header: "Status", render: (run) => <Badge label={run.status} tone={statusTone(run.status)} /> },
              { key: "attempt", header: "Attempt", align: "right", render: (run) => `${run.attempt}/${run.max_attempts}` },
              { key: "created", header: "Created", render: (run) => formatDateTime(run.created_at_utc) }
            ]}
            rows={overview?.recent_refresh_runs.slice(0, 20) ?? []}
          />
        </Panel>
      </div>
    </div>
  );
}
