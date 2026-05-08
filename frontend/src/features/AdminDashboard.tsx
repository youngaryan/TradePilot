import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Ban, CheckCircle2, RefreshCw, RotateCcw, Search, ShieldCheck, Trash2, UserCog, UserX } from "lucide-react";

import { deleteAdminUserStrategy, getAdminOverview, listAdminUserStrategies, listAdminUsers, updateAdminUser, updateAdminUserStrategy } from "../api/client";
import type { AdminOverviewPayload, AdminUserRecord, AuthResponse, UserStrategyRecord } from "../api/types";
import { Badge } from "../components/Badge";
import { MetricCard, Panel, SectionHeader } from "../components/Cards";
import { HorizontalBars, TelemetryTimelineChart } from "../components/Charts";
import { DataTable } from "../components/Table";
import { formatDateTime, formatNumber, formatPercent, statusTone } from "../utils/format";

type UserSort = "created_at_utc" | "email" | "role" | "status" | "last_login_at_utc" | "plan" | "subscription_status";

function userTone(user: AdminUserRecord) {
  if (user.status !== "active") return "bad";
  return user.role === "admin" ? "good" : "neutral";
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
  const [sortBy, setSortBy] = useState<UserSort>("created_at_utc");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [landingSearch, setLandingSearch] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  useEffect(() => {
    void load();
  }, []);

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
        : `change ${user.email}'s role to ${change.role}`;
    if (!window.confirm(`Confirm you want to ${action}. This affects access immediately.`)) return;
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
          <label>
            Search landing events
            <input value={landingSearch} onChange={(event) => setLandingSearch(event.target.value)} placeholder="pricing, signup, country, CTA..." />
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

      <Panel title="Search and manage users" subtitle="Filter, sort, deactivate, reactivate, and change roles with confirmation prompts.">
        <div className="admin-toolbar">
          <label>
            Search
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Email, name, or organization" />
          </label>
          <label>
            Role
            <select value={role} onChange={(event) => setRole(event.target.value)}>
              <option value="">All roles</option>
              <option value="admin">Admins</option>
              <option value="user">Users</option>
            </select>
          </label>
          <label>
            Status
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="">All statuses</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </label>
          <label>
            Sort
            <select value={sortBy} onChange={(event) => setSortBy(event.target.value as UserSort)}>
              <option value="created_at_utc">Created</option>
              <option value="email">Email</option>
              <option value="role">Role</option>
              <option value="status">Status</option>
              <option value="last_login_at_utc">Last login</option>
              <option value="plan">Plan</option>
              <option value="subscription_status">Subscription</option>
            </select>
          </label>
          <label>
            Direction
            <select value={sortDir} onChange={(event) => setSortDir(event.target.value as "asc" | "desc")}>
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
            { key: "role", header: "Role", render: (user) => <Badge label={user.role} tone={userTone(user)} /> },
            { key: "status", header: "Status", render: (user) => <Badge label={user.status} tone={statusTone(user.status)} /> },
            { key: "workspace", header: "Workspace", render: (user) => user.organization_name ?? "No workspace" },
            { key: "subscription", header: "Subscription", render: (user) => `${user.plan ?? "free"} / ${user.subscription_status ?? "unknown"}` },
            { key: "last_login", header: "Last login", render: (user) => formatDateTime(user.last_login_at_utc) },
            {
              key: "actions",
              header: "Actions",
              render: (user) => (
                <div className="table-actions">
                  <button type="button" className="ghost-button" onClick={() => void updateUser(user, { role: user.role === "admin" ? "user" : "admin" })} disabled={isLoading || user.id === auth.user.id}>
                    <UserCog size={14} />
                    {user.role === "admin" ? "Make user" : "Make admin"}
                  </button>
                  <button type="button" className="ghost-button danger-button" onClick={() => void updateUser(user, { status: user.status === "active" ? "inactive" : "active" })} disabled={isLoading || user.id === auth.user.id}>
                    <UserX size={14} />
                    {user.status === "active" ? "Deactivate" : "Reactivate"}
                  </button>
                </div>
              )
            }
          ]}
          rows={users}
        />
      </Panel>

      <Panel title="User-created strategies" subtitle="Audit AI-generated specs, ownership, approval history, usage, and safety status.">
        <div className="admin-toolbar admin-toolbar--strategy">
          <label>
            Status
            <select value={strategyStatus} onChange={(event) => setStrategyStatus(event.target.value)}>
              <option value="">All statuses</option>
              <option value="active">Active</option>
              <option value="disabled">Disabled</option>
            </select>
          </label>
          <label>
            Risk
            <select value={strategyRisk} onChange={(event) => setStrategyRisk(event.target.value)}>
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
