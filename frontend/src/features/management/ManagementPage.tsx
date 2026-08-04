import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import {
  Activity,
  Database,
  FolderPlus,
  KeyRound,
  RefreshCw,
  ShieldAlert,
  Users,
} from "lucide-react";

import {
  createApiKeyMetadata,
  createProject,
  getRefreshStatus,
  listTelemetryEvents,
  runDailyRefresh,
} from "../../api/client";
import type {
  Organization,
  RefreshStatusPayload,
  TelemetryEventRecord,
  WorkspacePayload,
} from "../../api/types";
import type { AccessContext } from "../../access/model";
import {
  AccessNotice,
  Button,
  Card,
  DataGrid,
  Disclosure,
  EmptyPanel,
  InlineNotice,
  LoadingBlock,
  Metric,
  MetricGrid,
  PageHeader,
  SelectInput,
  StatusIndicator,
  Tabs,
  Tag,
  TextInput,
} from "../../ui";
import { formatDateTime, formatNumber } from "../../utils/format";

export interface ManagementPageProps {
  access: AccessContext;
  workspace: WorkspacePayload | null;
  workspaceLoading: boolean;
  organizations: Organization[];
  activeOrgId: string | null;
  onRefresh: () => void;
}

type ManagementTab = "oversight" | "data" | "activity" | "members";

/**
 * Workspace management.
 *
 * Scoped deliberately to *this workspace*: shared research oversight, agent
 * records, data configuration, refresh operations, and subscription visibility.
 * It grants nothing platform-wide — platform administration is a separate area
 * with a separate permission.
 */
export function ManagementPage({
  access,
  workspace,
  workspaceLoading,
  organizations,
  activeOrgId,
  onRefresh,
}: ManagementPageProps) {
  const navigate = useNavigate();
  const [tab, setTab] = useState<ManagementTab>("oversight");
  const [refreshStatus, setRefreshStatus] = useState<RefreshStatusPayload | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [telemetry, setTelemetry] = useState<TelemetryEventRecord[] | null>(null);
  const [telemetryError, setTelemetryError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [projectName, setProjectName] = useState("");
  const [keyName, setKeyName] = useState("");
  const [keyProvider, setKeyProvider] = useState("newsapi");
  const version = useRef(0);

  const load = useCallback(async () => {
    const current = ++version.current;
    const [statusResult, telemetryResult] = await Promise.allSettled([
      getRefreshStatus(),
      listTelemetryEvents(200),
    ]);
    if (current !== version.current) return;
    if (statusResult.status === "fulfilled") {
      setRefreshStatus(statusResult.value);
      setRefreshError(null);
    } else {
      setRefreshStatus(null);
      setRefreshError(statusResult.reason instanceof Error ? statusResult.reason.message : "Refresh status is unavailable.");
    }
    if (telemetryResult.status === "fulfilled") {
      setTelemetry(telemetryResult.value);
      setTelemetryError(null);
    } else {
      setTelemetry(null);
      setTelemetryError(telemetryResult.reason instanceof Error ? telemetryResult.reason.message : "Workspace activity is unavailable.");
    }
  }, []);

  useEffect(() => {
    version.current += 1;
    setRefreshStatus(null);
    setTelemetry(null);
    void load();
    return () => {
      version.current += 1;
    };
  }, [activeOrgId, load]);

  if (!access.viewManagement.allowed && access.viewManagement.reason) {
    return (
      <>
        <PageHeader
          eyebrow="Management"
          title="Workspace management"
          description="Shared configuration and oversight for this workspace."
        />
        <AccessNotice
          reason={access.viewManagement.reason}
          feature="Workspace management"
          whatItDoes="Gives workspace owners and managers oversight of shared research: projects, experiments, paper agents, saved reports, data sources, refresh operations, and the workspace subscription."
          unlockedBy="The owner or manager role on this workspace, assigned by an existing owner. Your subscription is unrelated to this permission."
          alternative="Your own research, backtests, agents, and saved reports remain fully available under Workspace."
          actions={<Button variant="secondary" onClick={() => navigate("/workspace")}>Open my workspace</Button>}
        />
      </>
    );
  }

  const managerOnlyGate = access.manageWorkspace;

  async function run(key: string, operation: () => Promise<unknown>, successText: string) {
    setBusy(key);
    setActionNotice(null);
    try {
      await operation();
      setActionNotice({ tone: "good", text: successText });
      onRefresh();
      await load();
    } catch (caught) {
      setActionNotice({
        tone: "bad",
        text: caught instanceof Error ? caught.message : "The request failed. Nothing was changed.",
      });
    } finally {
      setBusy(null);
    }
  }

  const projects = workspace?.projects ?? [];
  const experiments = workspace?.experiments ?? [];
  const agents = workspace?.paper_agents ?? [];
  const datasets = workspace?.datasets ?? [];
  const apiKeys = workspace?.api_keys ?? [];
  const reports = workspace?.market_research_reports ?? [];

  return (
    <>
      <PageHeader
        eyebrow="Management"
        title="Workspace management"
        description="Oversight and shared configuration for this workspace only. Platform-wide administration is a separate area with a separate permission."
        meta={
          <>
            <span>Workspace: {access.organization?.name ?? "Unknown"}</span>
            <span>Your role here: {access.orgRoleLabel}</span>
            <Tag tone={access.hasPremium ? "good" : "neutral"}>
              {access.subscription.planLabel} · {access.subscription.stateLabel}
            </Tag>
            {access.isPlatformAdmin ? <Tag tone="elevated">Platform administrator</Tag> : null}
          </>
        }
        actions={
          <Button variant="secondary" icon={<RefreshCw size={14} />} onClick={() => { onRefresh(); void load(); }}>
            Refresh
          </Button>
        }
      />

      {access.isPlatformAdmin && !access.isWorkspaceManager ? (
        <InlineNotice tone="elevated" title="Viewing as a platform administrator">
          You are not an owner or manager of this workspace. You can see it because you hold the platform
          administrator role — treat any change here as an operational-support action.
        </InlineNotice>
      ) : null}

      {actionNotice ? (
        <InlineNotice tone={actionNotice.tone} role={actionNotice.tone === "bad" ? "alert" : "status"}>
          {actionNotice.text}
        </InlineNotice>
      ) : null}

      <Tabs
        label="Management sections"
        value={tab}
        onChange={setTab}
        options={[
          { value: "oversight", label: "Shared research", count: experiments.length + agents.length },
          { value: "data", label: "Data & keys", count: datasets.length + apiKeys.length },
          { value: "activity", label: "Operations" },
          { value: "members", label: "Members & roles" },
        ]}
      />

      {workspaceLoading ? (
        <LoadingBlock label="Loading workspace management data" lines={5} />
      ) : !workspace ? (
        <InlineNotice
          tone="bad"
          title="Workspace data unavailable"
          actions={<Button variant="secondary" size="sm" onClick={onRefresh}>Retry</Button>}
        >
          The workspace payload could not be loaded, so shared research and configuration cannot be shown. Nothing has
          been changed.
        </InlineNotice>
      ) : (
        <>
          {tab === "oversight" ? (
            <div className="ui-stack">
              <MetricGrid>
                <Metric label="Projects" value={formatNumber(projects.length, 0)} footnote="Shared research containers" />
                <Metric label="Saved experiments" value={formatNumber(experiments.length, 0)} footnote="Validated backtest records" />
                <Metric label="Paper agents" value={formatNumber(agents.length, 0)} footnote="Simulated deployments" />
                <Metric label="Saved reports" value={formatNumber(reports.length, 0)} footnote="AI research output" />
              </MetricGrid>

              <Card
                title="Projects"
                subtitle="Shared containers for the workspace's research."
                actions={
                  managerOnlyGate.allowed ? (
                    <div className="button-row--compact">
                      <TextInput label="New project name" value={projectName} onChange={setProjectName} />
                      <Button
                        variant="primary"
                        size="sm"
                        icon={<FolderPlus size={13} />}
                        disabled={busy === "project" || !projectName.trim()}
                        onClick={() => void run("project", () => createProject({ name: projectName.trim() }), `Project “${projectName.trim()}” created.`).then(() => setProjectName(""))}
                      >
                        Create
                      </Button>
                    </div>
                  ) : null
                }
              >
                {projects.length === 0 ? (
                  <EmptyPanel
                    icon={<FolderPlus size={18} />}
                    title="No projects yet"
                    body="Projects group experiments and agents for a shared research theme. One is enough to get started."
                  />
                ) : (
                  <DataGrid
                    rows={projects}
                    columns={[
                      { key: "name", header: "Project", render: (project) => <strong>{project.name}</strong> },
                      { key: "slug", header: "Slug", render: (project) => <code>{project.slug}</code> },
                      { key: "description", header: "Description", render: (project) => project.description || "Not described" },
                      { key: "created", header: "Created", align: "right", render: (project) => formatDateTime(project.created_at_utc) },
                    ]}
                    caption="Workspace projects with slug, description, and creation time"
                    getKey={(project) => project.id}
                  />
                )}
              </Card>

              <Card title="Paper agents in this workspace" subtitle="Every simulated deployment, whoever created it.">
                {agents.length === 0 ? (
                  <EmptyPanel
                    icon={<Activity size={18} />}
                    title="No paper agents deployed"
                    body="Agents appear here once a validated backtest has been promoted to the paper simulator."
                  />
                ) : (
                  <DataGrid
                    rows={agents}
                    columns={[
                      {
                        key: "name",
                        header: "Agent",
                        render: (agent) => (
                          <span className="stacked-cell">
                            <strong>{agent.name}</strong>
                            <span>{agent.pipeline}</span>
                          </span>
                        ),
                      },
                      {
                        key: "status",
                        header: "Status",
                        render: (agent) => (
                          <StatusIndicator tone={agent.status === "active" ? "good" : "neutral"}>{agent.status}</StatusIndicator>
                        ),
                      },
                      {
                        key: "warnings",
                        header: "Warnings",
                        align: "right",
                        render: (agent) => (agent.warnings?.length ? `${agent.warnings.length}` : "None"),
                      },
                      { key: "updated", header: "Updated", align: "right", render: (agent) => formatDateTime(agent.updated_at_utc) },
                    ]}
                    caption="Paper agents with status, warning count, and last update"
                    getKey={(agent) => agent.id}
                    summary="Simulated capital only. These agents do not place real-money orders."
                  />
                )}
              </Card>

              <Card title="Saved experiments" subtitle="The validation record behind anything deployed.">
                {experiments.length === 0 ? (
                  <EmptyPanel
                    icon={<Database size={18} />}
                    title="No experiments recorded"
                    body="Completed backtests are stored as experiments with readiness checks, validation output, and lineage."
                  />
                ) : (
                  <DataGrid
                    rows={experiments.slice(0, 20)}
                    columns={[
                      {
                        key: "name",
                        header: "Experiment",
                        render: (experiment) => (
                          <span className="stacked-cell">
                            <strong>{experiment.name}</strong>
                            <span>{experiment.pipeline}</span>
                          </span>
                        ),
                      },
                      {
                        key: "readiness",
                        header: "Readiness",
                        render: (experiment) => (
                          experiment.readiness?.score == null
                            ? <span className="ui-table__muted">Not scored</span>
                            : (
                              <span className="stacked-cell">
                                <span className="ui-num">{experiment.readiness.score}/100</span>
                                <span>{experiment.readiness.verdict ?? "no verdict"}</span>
                              </span>
                            )
                        ),
                      },
                      { key: "status", header: "Status", render: (experiment) => experiment.status },
                      { key: "created", header: "Created", align: "right", render: (experiment) => formatDateTime(experiment.created_at_utc) },
                    ]}
                    caption="Saved experiments with readiness score, status, and creation time"
                    getKey={(experiment) => experiment.id}
                    summary={experiments.length > 20 ? `Showing the 20 most recent of ${experiments.length} experiments.` : undefined}
                  />
                )}
              </Card>
            </div>
          ) : null}

          {tab === "data" ? (
            <div className="ui-stack">
              <Card title="Registered datasets" subtitle="What the workspace's strategies and research can read.">
                {datasets.length === 0 ? (
                  <EmptyPanel
                    icon={<Database size={18} />}
                    title="No datasets registered"
                    body="Datasets are registered automatically when a sentiment or refresh job publishes output for this workspace."
                  />
                ) : (
                  <DataGrid
                    rows={datasets}
                    columns={[
                      { key: "name", header: "Dataset", render: (dataset) => <strong>{dataset.name}</strong> },
                      { key: "kind", header: "Kind", render: (dataset) => dataset.kind },
                      { key: "rows", header: "Rows", align: "right", render: (dataset) => formatNumber(dataset.row_count, 0) },
                      { key: "updated", header: "Updated", align: "right", render: (dataset) => formatDateTime(dataset.updated_at_utc) },
                    ]}
                    caption="Registered datasets with kind, row count, and last update"
                    getKey={(dataset) => dataset.id}
                  />
                )}
              </Card>

              <Card
                title="API key metadata"
                subtitle="Provider credentials are stored as references. Secrets are never displayed after creation."
              >
                {!managerOnlyGate.allowed ? (
                  <InlineNotice tone="warn" compact>
                    Only workspace owners and managers can register provider credentials. Existing keys are listed
                    below for transparency.
                  </InlineNotice>
                ) : (
                  <div className="form-row">
                    <TextInput label="Key name" value={keyName} onChange={setKeyName} />
                    <SelectInput label="Provider" value={keyProvider} onChange={setKeyProvider}>
                      <option value="newsapi">newsapi</option>
                      <option value="alphavantage">alphavantage</option>
                      <option value="benzinga">benzinga</option>
                      <option value="stocktwits">stocktwits</option>
                      <option value="machine">machine (scoped API key)</option>
                    </SelectInput>
                    <Button
                      variant="primary"
                      icon={<KeyRound size={13} />}
                      disabled={busy === "key" || !keyName.trim()}
                      onClick={() => void run(
                        "key",
                        () => createApiKeyMetadata({ name: keyName.trim(), provider: keyProvider, scopes: ["read"] }),
                        "Key metadata registered. If a token was generated it is shown once in the response.",
                      ).then(() => setKeyName(""))}
                    >
                      Register
                    </Button>
                  </div>
                )}
                {apiKeys.length === 0 ? (
                  <EmptyPanel icon={<KeyRound size={18} />} title="No API keys registered" body="No provider credentials have been registered for this workspace." />
                ) : (
                  <DataGrid
                    rows={apiKeys}
                    columns={[
                      { key: "name", header: "Name", render: (key) => <strong>{key.name}</strong> },
                      { key: "provider", header: "Provider", render: (key) => key.provider },
                      { key: "masked", header: "Value", render: (key) => <code>{key.masked_value}</code> },
                      { key: "scopes", header: "Scopes", render: (key) => (key.scopes?.length ? key.scopes.join(", ") : "read") },
                      { key: "status", header: "Status", render: (key) => <Tag tone={key.status === "active" ? "good" : "neutral"}>{key.status}</Tag> },
                    ]}
                    caption="Registered API keys with provider, masked value, scopes, and status"
                    getKey={(key) => key.id}
                  />
                )}
              </Card>
            </div>
          ) : null}

          {tab === "activity" ? (
            <div className="ui-stack">
              <Card
                title="Data refresh"
                subtitle="The scheduled job that keeps this workspace's market and news caches current."
                actions={
                  <Button
                    variant="primary"
                    size="sm"
                    icon={<RefreshCw size={13} />}
                    disabled={busy === "refresh" || !access.runCompute.allowed}
                    onClick={() => void run("refresh", () => runDailyRefresh(false), "Refresh requested. Progress appears in the run history below.")}
                  >
                    Run refresh now
                  </Button>
                }
              >
                {!access.runCompute.allowed ? (
                  <InlineNotice tone="warn" compact>
                    Starting a refresh is a paid workflow because it consumes provider capacity. Refresh status stays
                    visible so you can see when the workspace data was last updated.
                  </InlineNotice>
                ) : null}
                {refreshError ? (
                  <InlineNotice tone="bad" compact>{refreshError}</InlineNotice>
                ) : refreshStatus == null ? (
                  <LoadingBlock label="Loading refresh status" lines={2} />
                ) : (
                  <>
                    <MetricGrid>
                      <Metric label="Interval" value={`${refreshStatus.interval_hours}h`} footnote="Between scheduled attempts" />
                      <Metric label="Max attempts" value={formatNumber(refreshStatus.max_attempts, 0)} footnote="Per due window" />
                      <Metric
                        label="Scheduler"
                        value={refreshStatus.scheduler_enabled ? "Enabled" : "Disabled"}
                        footnote={refreshStatus.scheduler_enabled ? "Runs automatically" : "Manual runs only"}
                      />
                      <Metric label="Recent runs" value={formatNumber(refreshStatus.recent_runs.length, 0)} />
                    </MetricGrid>
                    {refreshStatus.recent_runs.length === 0 ? (
                      <InlineNotice tone="neutral" compact>No refresh has run for this workspace yet.</InlineNotice>
                    ) : (
                      <DataGrid
                        rows={refreshStatus.recent_runs.slice(0, 10)}
                        columns={[
                          {
                            key: "status",
                            header: "Status",
                            render: (record) => (
                              <StatusIndicator
                                tone={record.status === "succeeded" ? "good" : record.status === "failed" ? "bad" : "info"}
                                busy={record.status === "running"}
                              >
                                {record.status}
                              </StatusIndicator>
                            ),
                          },
                          { key: "attempt", header: "Attempt", align: "right", render: (record) => `${record.attempt}/${record.max_attempts}` },
                          { key: "started", header: "Started", align: "right", render: (record) => formatDateTime(record.started_at_utc) },
                          { key: "finished", header: "Finished", align: "right", render: (record) => formatDateTime(record.finished_at_utc) },
                          { key: "error", header: "Error", render: (record) => record.error || "None" },
                        ]}
                        caption="Recent data-refresh runs with status, attempt, timing, and error"
                        getKey={(record) => record.id}
                      />
                    )}
                  </>
                )}
              </Card>

              <Card title="Workspace activity" subtitle="Consent-aware product telemetry scoped to this workspace.">
                {telemetryError ? (
                  <InlineNotice tone="bad" compact>{telemetryError}</InlineNotice>
                ) : telemetry == null ? (
                  <LoadingBlock label="Loading workspace activity" lines={3} />
                ) : telemetry.length === 0 ? (
                  <EmptyPanel
                    icon={<Activity size={18} />}
                    title="No activity recorded"
                    body="No telemetry events have been stored for this workspace. Members may have analytics turned off."
                  />
                ) : (
                  <DataGrid
                    rows={telemetry.slice(0, 25)}
                    columns={[
                      { key: "name", header: "Event", render: (event) => <strong>{event.name}</strong> },
                      { key: "category", header: "Category", render: (event) => event.category },
                      { key: "consent", header: "Consent", render: (event) => <Tag tone={event.consent === "granted" ? "good" : "neutral"}>{event.consent}</Tag> },
                      { key: "when", header: "Recorded", align: "right", render: (event) => formatDateTime(event.occurred_at_utc) },
                    ]}
                    caption="Recent workspace telemetry events with category, consent state, and time"
                    getKey={(event) => event.id}
                    summary={`Showing the 25 most recent of ${telemetry.length} loaded events.`}
                  />
                )}
              </Card>
            </div>
          ) : null}

          {tab === "members" ? (
            <div className="ui-stack">
              <Card title="Your role in each workspace" subtitle="Roles are per workspace and are returned by the server with your session.">
                <DataGrid
                  rows={organizations}
                  columns={[
                    {
                      key: "name",
                      header: "Workspace",
                      render: (organization) => (
                        <span className="stacked-cell">
                          <strong>{organization.name}</strong>
                          <span>{organization.id === activeOrgId ? "Currently active" : "Available to switch to"}</span>
                        </span>
                      ),
                    },
                    {
                      key: "role",
                      header: "Your role",
                      render: (organization) => (
                        <Tag tone={organization.role === "owner" || organization.role === "admin" ? "brand" : "neutral"}>
                          {organization.role ?? "member"}
                        </Tag>
                      ),
                    },
                    {
                      key: "billing",
                      header: "Billing email",
                      render: (organization) => organization.billing_email || "Not set",
                    },
                    { key: "created", header: "Created", align: "right", render: (organization) => formatDateTime(organization.created_at_utc) },
                  ]}
                  caption="Workspaces you belong to and your role in each"
                  getKey={(organization) => organization.id}
                />
              </Card>

              <Card title="Subscription visibility" subtitle="Workspace-level entitlement. Independent of your role.">
                <MetricGrid>
                  <Metric label="Plan" value={access.subscription.planLabel} />
                  <Metric label="Status" value={access.subscription.stateLabel} tone={access.subscription.needsBillingAttention ? "bad" : undefined} />
                  <Metric
                    label="Renews / ends"
                    value={access.subscription.currentPeriodEndUtc ? formatDateTime(access.subscription.currentPeriodEndUtc) : undefined}
                    unavailable={access.subscription.currentPeriodEndUtc ? undefined : "Not provided"}
                  />
                  <Metric
                    label="Premium workflows"
                    value={access.hasPremium ? "Available" : "Not available"}
                    footnote={access.premiumViaAdminOverride ? "Granted by the platform administrator role" : undefined}
                  />
                </MetricGrid>
                <InlineNotice tone="info" compact>
                  Billing checkout and the billing portal are available to any member of this workspace — the API does
                  not expose a separate billing permission. Changing the plan never changes anyone's role.
                </InlineNotice>
                <Button variant="secondary" size="sm" onClick={() => navigate("/pricing")}>Open plans &amp; billing</Button>
              </Card>

              <Card title="Member directory and invitations">
                <InlineNotice tone="warn" title="Not exposed by the API yet">
                  This deployment's API returns your own membership and role, but has no endpoint that lists a
                  workspace's members, sends invitations, or changes another member's workspace role. Those controls
                  are intentionally absent rather than shown as buttons that would fail.
                </InlineNotice>
                <Disclosure summary="What is missing, precisely">
                  <ul className="principle-list">
                    <li>
                      <strong>Read:</strong> no <code>GET /api/workspaces/members</code>. Membership rows exist in
                      <code> organization_members</code> (with <code>role</code> of owner, admin, or member) and are
                      only surfaced for your own account via <code>/api/auth/me</code>.
                    </li>
                    <li>
                      <strong>Write:</strong> no invitation or role-assignment endpoint. Platform administrators can
                      change a user's <em>platform</em> role via <code>PATCH /api/admin/users/:id</code>, which is a
                      different dimension and does not touch workspace membership.
                    </li>
                    <li>
                      <strong>Consequence:</strong> the manager experience here covers everything the API does expose.
                      When member endpoints are added, this tab is where they belong — the layout and permission gate
                      are already in place.
                    </li>
                  </ul>
                </Disclosure>
                {access.isPlatformAdmin ? (
                  <Button variant="secondary" size="sm" icon={<ShieldAlert size={13} />} onClick={() => navigate("/admin")}>
                    Open platform administration
                  </Button>
                ) : null}
              </Card>

              <Card title="What management does and does not include" inset>
                <ul className="principle-list">
                  <li><Users size={13} aria-hidden="true" /> <span><strong>Manage this workspace:</strong> shared projects, data sources, provider keys, refresh operations, and oversight of shared research. That is this page.</span></li>
                  <li><Database size={13} aria-hidden="true" /> <span><strong>Manage billing:</strong> plan comparison, checkout, and the billing portal. Available to workspace members under Plans &amp; billing.</span></li>
                  <li><ShieldAlert size={13} aria-hidden="true" /> <span><strong>Administer the platform:</strong> every workspace, account, and subscription on the deployment. Requires the platform administrator role and is never granted by a subscription or by workspace management.</span></li>
                </ul>
              </Card>
            </div>
          ) : null}
        </>
      )}
    </>
  );
}

export default ManagementPage;
