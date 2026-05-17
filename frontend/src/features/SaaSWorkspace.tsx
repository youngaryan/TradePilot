import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, CreditCard, DatabaseZap, FileText, KeyRound, Loader2, Rocket, ShieldCheck, Workflow } from "lucide-react";

import {
  createApiKeyMetadata,
  createProject,
  getRefreshStatus,
  getWorkspaceExperiment,
  getWorkspacePaperAgent,
  listTelemetryEvents,
  openBillingPortal,
  runDailyRefresh,
  startBillingCheckout
} from "../api/client";
import type {
  ApiKeyCreateRequest,
  AuthResponse,
  ExperimentRecord,
  Organization,
  PaperAgentRecord,
  RefreshStatusPayload,
  TelemetryEventRecord,
  WorkspacePayload
} from "../api/types";
import { Badge } from "../components/Badge";
import {
  BacktestEquityChart,
  StrategyConcentrationBars,
  TelemetryCategoryBars,
  TelemetryConsentBars,
  TelemetryLatencyChart,
  TelemetryTimelineChart,
  TelemetryTopEventsBars
} from "../components/Charts";
import { Explainer, MetricCard, Panel, SectionHeader } from "../components/Cards";
import { DataTable } from "../components/Table";
import { formatCurrency, formatDateTime, formatNumber, formatPercent, pipelineLabel, statusTone, toNumber } from "../utils/format";
import { telemetryIsError, telemetryLatencyMs } from "../utils/telemetry";
import { MarketResearchReports } from "./workspace/MarketResearchReports";

type WorkspaceSection = "onboarding" | "experiments" | "agents" | "reports" | "data" | "operations" | "billing";

function safeString(value: unknown, fallback = "n/a") {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function metric(summary: Record<string, unknown>, key: string) {
  return toNumber(summary[key]);
}

function prettyJson(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2);
}

function readinessTone(score?: number) {
  if (score == null) return "neutral";
  if (score >= 80) return "good";
  if (score >= 50) return "warn";
  return "bad";
}

function equityPoints(experiment: ExperimentRecord | null) {
  return (experiment?.equity_curve_points ?? [])
    .map((point) => ({
      timestamp: safeString(point.timestamp),
      equity: toNumber(point.equity) ?? 1,
      drawdown: toNumber(point.drawdown) ?? 0,
      net_return: toNumber(point.net_return) ?? 0
    }))
    .filter((point) => point.timestamp !== "n/a");
}

export function SaaSWorkspace({
  auth,
  activeOrganizationId,
  workspace,
  organizations,
  onSwitchOrganization,
  onRefresh,
  onNavigate
}: {
  auth: AuthResponse;
  activeOrganizationId: string | null;
  workspace: WorkspacePayload | null;
  organizations: Organization[];
  onSwitchOrganization: (organizationId: string) => void;
  onRefresh: () => Promise<void>;
  onNavigate: (view: "live" | "sentiment" | "backtests") => void;
}) {
  const [section, setSection] = useState<WorkspaceSection>("onboarding");
  const [activeExperiment, setActiveExperiment] = useState<ExperimentRecord | null>(workspace?.experiments[0] ?? null);
  const [activeAgent, setActiveAgent] = useState<PaperAgentRecord | null>(workspace?.paper_agents[0] ?? null);
  const [projectName, setProjectName] = useState("ETF validation lab");
  const [apiKeyForm, setApiKeyForm] = useState<ApiKeyCreateRequest>({
    name: "Worker machine key",
    provider: "machine",
    secret_ref: null,
    scopes: ["read", "backtests:run", "sentiment:run", "paper:run"]
  });
  const [isBusy, setIsBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshStatus, setRefreshStatus] = useState<RefreshStatusPayload | null>(null);
  const [telemetryEvents, setTelemetryEvents] = useState<TelemetryEventRecord[]>([]);

  useEffect(() => {
    setActiveExperiment(workspace?.experiments[0] ?? null);
    setActiveAgent(workspace?.paper_agents[0] ?? null);
  }, [workspace?.organization_id]);

  useEffect(() => {
    if (!workspace) return;
    void loadOperations();
  }, [workspace?.organization_id]);

  const activeOrg = useMemo(
    () => organizations.find((organization) => organization.id === activeOrganizationId) ?? organizations[0],
    [activeOrganizationId, organizations]
  );

  async function refreshWorkspace() {
    setError(null);
    await onRefresh();
    await loadOperations();
  }

  async function loadOperations() {
    try {
      const [nextRefreshStatus, nextEvents] = await Promise.all([getRefreshStatus(), listTelemetryEvents(200)]);
      setRefreshStatus(nextRefreshStatus);
      setTelemetryEvents(nextEvents);
    } catch {
      setRefreshStatus(null);
      setTelemetryEvents([]);
    }
  }

  async function handleCreateProject() {
    setIsBusy(true);
    setError(null);
    setNotice(null);
    try {
      await createProject({ name: projectName, description: "Created from the first-strategy onboarding wizard." });
      setNotice("Project created. The workspace is ready to store experiments under a durable project boundary.");
      await refreshWorkspace();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create the project.");
    } finally {
      setIsBusy(false);
    }
  }

  async function handleCreateApiKey() {
    setIsBusy(true);
    setError(null);
    setNotice(null);
    try {
      const created = await createApiKeyMetadata(apiKeyForm);
      setNotice(
        created.token
          ? `Machine API key created. Store it now: ${created.token}`
          : "API key metadata saved. Production accepts secret references and scoped machine keys, not raw browser-visible tokens."
      );
      await refreshWorkspace();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save API key metadata.");
    } finally {
      setIsBusy(false);
    }
  }

  async function handleCheckout() {
    setIsBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await startBillingCheckout({ plan: "pro" });
      setNotice(response.message ?? `Billing flow ready in ${response.mode} mode.`);
      const url = response.checkout_url;
      if (url) window.open(url, "_blank", "noopener,noreferrer");
      await refreshWorkspace();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not start checkout.");
    } finally {
      setIsBusy(false);
    }
  }

  async function handlePortal() {
    setIsBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await openBillingPortal(window.location.href);
      setNotice(response.message ?? `Billing portal ready in ${response.mode} mode.`);
      const url = response.portal_url;
      if (url) window.open(url, "_blank", "noopener,noreferrer");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not open billing portal.");
    } finally {
      setIsBusy(false);
    }
  }

  async function handleManualRefresh(force = false) {
    setIsBusy(true);
    setError(null);
    setNotice(null);
    try {
      const run = await runDailyRefresh(force);
      setNotice(
        run.status === "skipped_not_due"
          ? "Daily refresh is not due yet. Use Force refresh only when debugging data freshness."
          : `Refresh ${run.status}. Check Operations for run details and retry history.`
      );
      await loadOperations();
      await onRefresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not run the daily data refresh.");
    } finally {
      setIsBusy(false);
    }
  }

  async function selectExperiment(experiment: ExperimentRecord) {
    setActiveExperiment(experiment);
    setSection("experiments");
    try {
      setActiveExperiment(await getWorkspaceExperiment(experiment.id));
    } catch {
      setActiveExperiment(experiment);
    }
  }

  async function selectAgent(agent: PaperAgentRecord) {
    setActiveAgent(agent);
    setSection("agents");
    try {
      setActiveAgent(await getWorkspacePaperAgent(agent.id));
    } catch {
      setActiveAgent(agent);
    }
  }

  if (!workspace) {
    return (
      <Panel title="Workspace loading" subtitle="The SaaS layer is reading organizations, projects, and durable records.">
        <div className="empty-state chart-empty">No workspace payload has loaded yet.</div>
      </Panel>
    );
  }

  const onboarding = workspace.onboarding;
  const subscription = workspace.subscription;
  const telemetrySummary = {
    total: telemetryEvents.length,
    errors: telemetryEvents.filter(telemetryIsError).length,
    refresh: telemetryEvents.filter((event) => event.category.toLowerCase() === "refresh").length,
    uniqueEvents: new Set(telemetryEvents.map((event) => event.name)).size,
    latest: telemetryEvents[0],
    latencies: telemetryEvents.map(telemetryLatencyMs).filter((value): value is number => value !== null)
  };
  const averageTelemetryLatency = telemetrySummary.latencies.length
    ? telemetrySummary.latencies.reduce((sum, value) => sum + value, 0) / telemetrySummary.latencies.length
    : null;

  return (
    <div className="saas-workspace">
      <SectionHeader eyebrow="SaaS Operating Layer" title="Workspace, billing, experiments, and paper agents">
        <div className="workspace-switcher">
          <span>{auth.user.display_name}</span>
          <select
            value={activeOrganizationId ?? ""}
            onChange={(event) => onSwitchOrganization(event.target.value)}
            aria-label="Active organization"
          >
            {organizations.map((organization) => (
              <option key={organization.id} value={organization.id}>
                {organization.name}
              </option>
            ))}
          </select>
        </div>
      </SectionHeader>

      <div className="metric-grid">
        <MetricCard label="Workspace" value={activeOrg?.name ?? "Unknown"} detail="Multi-tenant organization boundary" tone="good" icon={<ShieldCheck size={18} />} />
        <MetricCard label="Plan" value={subscription?.plan ?? "free"} detail={subscription?.status ?? "not configured"} tone="neutral" icon={<CreditCard size={18} />} />
        <MetricCard label="Experiments" value={formatNumber(workspace.experiments.length)} detail="Saved research records" tone="neutral" icon={<Workflow size={18} />} />
        <MetricCard label="Paper agents" value={formatNumber(workspace.paper_agents.length)} detail="Fake-money deployment records" tone="neutral" icon={<Rocket size={18} />} />
        <MetricCard label="Reports" value={formatNumber(workspace.market_research_reports?.length ?? 0)} detail="AI committee history" tone="neutral" icon={<FileText size={18} />} />
      </div>

      {notice ? (
        <section className="alert-card alert-card--good">
          <CheckCircle2 size={18} />
          <span>{notice}</span>
        </section>
      ) : null}
      {error ? (
        <section className="alert-card">
          <AlertTriangle size={18} />
          <span>{error}</span>
        </section>
      ) : null}

      <div className="section-tabs">
        {(["onboarding", "experiments", "agents", "reports", "data", "operations", "billing"] as WorkspaceSection[]).map((item) => (
          <button key={item} type="button" className={section === item ? "chip chip--active" : "chip"} onClick={() => setSection(item)}>
            {item === "onboarding" ? "Launch wizard" : item === "reports" ? "Reports" : item}
          </button>
        ))}
      </div>

      {section === "onboarding" ? (
        <div className="grid-two">
          <Panel title="Launch first strategy wizard" subtitle="A guided path from empty SaaS account to a monitored paper agent.">
            <div className="wizard-steps">
              {onboarding.steps.map((step, index) => (
                <div key={step.id} className={step.complete ? "wizard-step wizard-step--done" : "wizard-step"}>
                  <strong>{index + 1}</strong>
                  <span>{step.label}</span>
                  <Badge label={step.complete ? "done" : "next"} tone={step.complete ? "good" : "warn"} />
                </div>
              ))}
            </div>
            <div className="form-row">
              <label htmlFor="saas-project-name">
                Project name
                <input id="saas-project-name" value={projectName} onChange={(event) => setProjectName(event.target.value)} />
              </label>
              <button type="button" className="primary-button" onClick={() => void handleCreateProject()} disabled={isBusy}>
                {isBusy ? <Loader2 size={16} className="spin" /> : <CheckCircle2 size={16} />}
                Create project
              </button>
            </div>
            <div className="button-cluster">
              <button type="button" className="ghost-button" onClick={() => onNavigate("sentiment")}>Build sentiment data</button>
              <button type="button" className="ghost-button" onClick={() => onNavigate("backtests")}>Run first backtest</button>
              <button type="button" className="ghost-button" onClick={() => onNavigate("live")}>Deploy fake-money agent</button>
            </div>
          </Panel>

          <Panel title="Why this matters" subtitle="The SaaS version must sell trust, not mystery.">
            <Explainer
              icon={<ShieldCheck size={17} />}
              title="Production-grade workflow"
              body="Every serious quant SaaS needs durable records: who ran what, with which data, which assumptions, which billing plan, and what happened after deployment."
              items={[
                "Experiments store lineage, validation, artifacts, and readiness score.",
                "Paper agents store live fake-money state, decisions, orders, reconciliation, and warnings.",
                "Datasets and API-key metadata make external data dependencies visible."
              ]}
            />
          </Panel>
        </div>
      ) : null}

      {section === "experiments" ? (
        <div className="grid-two grid-two--wide-left">
          <Panel title="Saved experiments" subtitle="Durable backtest records synced from completed jobs and artifacts.">
            <DataTable
              empty="Run a backtest to create the first experiment."
              getKey={(experiment) => experiment.id}
              columns={[
                { key: "name", header: "Name", render: (experiment) => <button type="button" className="link-button" onClick={() => void selectExperiment(experiment)}>{experiment.name}</button> },
                { key: "pipeline", header: "Pipeline", render: (experiment) => pipelineLabel(experiment.pipeline) },
                { key: "readiness", header: "Readiness", render: (experiment) => <Badge label={`${experiment.readiness.score ?? 0}/100`} tone={readinessTone(experiment.readiness.score)} /> },
                { key: "sharpe", header: "Sharpe", align: "right", render: (experiment) => formatNumber(metric(experiment.summary, "sharpe")) },
                { key: "dsr", header: "DSR", align: "right", render: (experiment) => formatNumber(metric(experiment.validation, "dsr") || metric(experiment.summary, "dsr")) }
              ]}
              rows={workspace.experiments}
            />
          </Panel>

          <Panel title={activeExperiment ? activeExperiment.name : "Experiment detail"} subtitle="Lineage, validation, sentiment, trades, and artifact trail.">
            {activeExperiment ? (
              <div className="detail-stack">
                <div className="metric-grid metric-grid--compact">
                  <MetricCard label="Readiness" value={`${activeExperiment.readiness.score ?? 0}/100`} detail={activeExperiment.readiness.verdict ?? "unscored"} tone={readinessTone(activeExperiment.readiness.score)} />
                  <MetricCard label="Sharpe" value={formatNumber(metric(activeExperiment.summary, "sharpe"))} detail="after modeled costs" />
                  <MetricCard label="PBO" value={formatNumber(metric(activeExperiment.validation, "pbo") ?? metric(activeExperiment.summary, "pbo"))} detail="overfit probability" />
                  <MetricCard label="Drawdown" value={formatPercent(metric(activeExperiment.summary, "max_drawdown"))} detail="max historical dip" />
                </div>
                <BacktestEquityChart points={equityPoints(activeExperiment)} />
                <DataTable
                  empty="No readiness checks were attached to this experiment."
                  getKey={(check) => check.name}
                  columns={[
                    { key: "check", header: "Check", render: (check) => check.name },
                    { key: "target", header: "Target", render: (check) => check.target },
                    { key: "value", header: "Value", render: (check) => String(check.value ?? "n/a") },
                    { key: "status", header: "Status", render: (check) => <Badge label={check.passed ? "pass" : "review"} tone={check.passed ? "good" : "warn"} /> }
                  ]}
                  rows={activeExperiment.readiness.checks ?? []}
                />
                <div className="code-split">
                  <pre>{prettyJson(activeExperiment.lineage)}</pre>
                  <pre>{prettyJson(activeExperiment.sentiment)}</pre>
                </div>
                <small>{activeExperiment.artifact_dir ? `Artifacts: ${activeExperiment.artifact_dir}` : "No artifact directory attached yet."}</small>
              </div>
            ) : (
              <div className="empty-state chart-empty">Run a backtest to create the first durable experiment record.</div>
            )}
          </Panel>
        </div>
      ) : null}

      {section === "agents" ? (
        <div className="grid-two grid-two--wide-left">
          <Panel title="Paper agents" subtitle="Fake-money deployment records synchronized from the paper dashboard.">
            <DataTable
              empty="Run a paper deployment to create the first paper agent."
              getKey={(agent) => agent.id}
              columns={[
                { key: "agent", header: "Agent", render: (agent) => <button type="button" className="link-button" onClick={() => void selectAgent(agent)}>{agent.name}</button> },
                { key: "pipeline", header: "Pipeline", render: (agent) => pipelineLabel(agent.pipeline) },
                { key: "status", header: "Status", render: (agent) => <Badge label={agent.status} tone={statusTone(agent.status)} /> },
                { key: "cash", header: "Cash", align: "right", render: (agent) => formatCurrency(agent.fake_cash) },
                { key: "warnings", header: "Warnings", align: "right", render: (agent) => formatNumber(agent.warnings.length) }
              ]}
              rows={workspace.paper_agents}
            />
          </Panel>

          <Panel title={activeAgent ? activeAgent.name : "Paper agent detail"} subtitle="Live fake-money state, orders, decisions, reconciliation, and warnings.">
            {activeAgent ? (
              <div className="detail-stack">
                <div className="metric-grid metric-grid--compact">
                  <MetricCard label="Equity" value={formatCurrency(toNumber(activeAgent.latest_payload.equity))} detail="fake capital" tone="neutral" />
                  <MetricCard label="Daily PnL" value={formatCurrency(toNumber(activeAgent.latest_payload.daily_pnl))} detail="latest paper run" tone={toNumber(activeAgent.latest_payload.daily_pnl) && toNumber(activeAgent.latest_payload.daily_pnl)! >= 0 ? "good" : "warn"} />
                  <MetricCard label="Gross exposure" value={formatPercent(toNumber(activeAgent.latest_payload.gross_exposure_ratio))} detail="risk footprint" />
                  <MetricCard label="Trades" value={formatNumber(toNumber(activeAgent.latest_payload.trade_count))} detail="latest rebalance" />
                </div>
                <StrategyConcentrationBars
                  strategy={{ target_weights: activeAgent.latest_payload.target_weights ?? {} } as never}
                />
                {activeAgent.warnings.length ? (
                  <div className="warning-list">
                    {activeAgent.warnings.map((warning) => <span key={warning}>{warning}</span>)}
                  </div>
                ) : (
                  <Badge label="No current warnings" tone="good" />
                )}
                <pre>{prettyJson(activeAgent.latest_payload.diagnostics ?? activeAgent.latest_payload)}</pre>
              </div>
            ) : (
              <div className="empty-state chart-empty">Run a paper deployment to create the first paper-agent record.</div>
            )}
          </Panel>
        </div>
      ) : null}

      {section === "reports" ? (
        <MarketResearchReports initialReports={workspace.market_research_reports ?? []} onRefreshWorkspace={refreshWorkspace} />
      ) : null}

      {section === "data" ? (
        <div className="grid-two">
          <Panel title="Datasets" subtitle="Visible data dependencies for reproducible research.">
            <DataTable
              empty="No datasets have been indexed yet. Build sentiment data or run a backtest."
              getKey={(dataset) => dataset.id}
              columns={[
                { key: "name", header: "Name", render: (dataset) => dataset.name },
                { key: "kind", header: "Kind", render: (dataset) => dataset.kind },
                { key: "rows", header: "Rows/files", align: "right", render: (dataset) => formatNumber(dataset.row_count) },
                { key: "path", header: "Path", render: (dataset) => <span className="path-cell">{dataset.path}</span> }
              ]}
              rows={workspace.datasets}
            />
          </Panel>
          <Panel title="Scoped API keys" subtitle="Users stay on HttpOnly cookies. Machines use separately scoped API keys. Leave secret fields blank to generate one.">
            <div className="form-grid">
              <label htmlFor="saas-api-name">
                Name
                <input id="saas-api-name" value={apiKeyForm.name} onChange={(event) => setApiKeyForm({ ...apiKeyForm, name: event.target.value })} />
              </label>
              <label htmlFor="saas-api-provider">
                Provider
                <input id="saas-api-provider" value={apiKeyForm.provider} onChange={(event) => setApiKeyForm({ ...apiKeyForm, provider: event.target.value })} />
              </label>
              <label htmlFor="saas-api-secret-ref">
                Secret reference
                <input id="saas-api-secret-ref" value={apiKeyForm.secret_ref ?? ""} onChange={(event) => setApiKeyForm({ ...apiKeyForm, secret_ref: event.target.value, secret: null })} />
              </label>
              <label htmlFor="saas-api-secret-val">
                Secret value for masking only
                <input id="saas-api-secret-val" value={apiKeyForm.secret ?? ""} onChange={(event) => setApiKeyForm({ ...apiKeyForm, secret: event.target.value, secret_ref: null })} />
              </label>
              <label htmlFor="saas-api-scopes">
                Machine scopes
                <input id="saas-api-scopes" value={(apiKeyForm.scopes ?? []).join(" ")} onChange={(event) => setApiKeyForm({ ...apiKeyForm, scopes: event.target.value.split(/\s+/).filter(Boolean) })} />
              </label>
            </div>
            <button type="button" className="primary-button" onClick={() => void handleCreateApiKey()} disabled={isBusy}>
              <KeyRound size={16} />
              Save or generate API key
            </button>
            <DataTable
              empty="No API key metadata has been saved yet."
              getKey={(key) => key.id}
              columns={[
                { key: "name", header: "Name", render: (key) => key.name },
                { key: "provider", header: "Provider", render: (key) => key.provider },
                { key: "scopes", header: "Scopes", render: (key) => (key.scopes ?? []).join(", ") || "metadata only" },
                { key: "masked", header: "Masked", render: (key) => key.masked_value },
                { key: "status", header: "Status", render: (key) => <Badge label={key.status} tone="good" /> }
              ]}
              rows={workspace.api_keys}
            />
          </Panel>
        </div>
      ) : null}

      {section === "operations" ? (
        <div className="grid-two grid-two--wide-left">
          <Panel title="24-hour data refresh" subtitle="Per-user refresh status, idempotency, retries, and recent outcomes.">
            <div className="metric-grid metric-grid--compact">
              <MetricCard label="Interval" value={`${refreshStatus?.interval_hours ?? 24}h`} detail="per user" />
              <MetricCard label="Max retries" value={formatNumber(refreshStatus?.max_attempts ?? 3, 0)} detail="before failed status" />
              <MetricCard label="Scheduler" value={refreshStatus?.scheduler_enabled ? "Enabled" : "Manual/local"} detail="worker-safe implementation" tone={refreshStatus?.scheduler_enabled ? "good" : "warn"} />
            </div>
            <div className="button-cluster">
              <button type="button" className="primary-button" onClick={() => void handleManualRefresh(false)} disabled={isBusy}>
                <DatabaseZap size={16} />
                Run if due
              </button>
              <button type="button" className="ghost-button" onClick={() => void handleManualRefresh(true)} disabled={isBusy}>
                Force refresh
              </button>
            </div>
            <DataTable
              empty="No refresh status has been recorded yet."
              getKey={(status) => status.user_id}
              columns={[
                { key: "status", header: "Status", render: (status) => <Badge label={status.status} tone={statusTone(status.status)} /> },
                { key: "last_success", header: "Last success", render: (status) => status.last_success_at_utc ?? "Not yet" },
                { key: "next_due", header: "Next due", render: (status) => status.next_due_at_utc },
                { key: "error", header: "Last error", render: (status) => status.last_error ?? "None" }
              ]}
              rows={refreshStatus?.statuses ?? []}
            />
            <DataTable
              empty="No refresh runs yet."
              getKey={(run) => run.id}
              columns={[
                { key: "run", header: "Run", render: (run) => <span className="path-cell">{run.id}</span> },
                { key: "status", header: "Status", render: (run) => <Badge label={run.status} tone={statusTone(run.status)} /> },
                { key: "attempt", header: "Attempt", align: "right", render: (run) => `${run.attempt}/${run.max_attempts}` },
                { key: "created", header: "Created", render: (run) => run.created_at_utc }
              ]}
              rows={refreshStatus?.recent_runs ?? []}
            />
          </Panel>

          <Panel title="Telemetry dashboard" subtitle="Privacy-aware product and operational events for debugging and analytics.">
            <div className="metric-grid metric-grid--compact">
              <MetricCard label="Visible events" value={formatNumber(telemetrySummary.total, 0)} detail="latest 200 for this workspace" />
              <MetricCard label="Error events" value={formatNumber(telemetrySummary.errors, 0)} detail="failed/error/security signals" tone={telemetrySummary.errors ? "bad" : "good"} />
              <MetricCard label="Refresh events" value={formatNumber(telemetrySummary.refresh, 0)} detail="scheduler and data-sync trail" tone="good" />
              <MetricCard label="Event types" value={formatNumber(telemetrySummary.uniqueEvents, 0)} detail="unique event names" />
              <MetricCard
                label="Avg latency"
                value={averageTelemetryLatency == null ? "Not tracked" : `${formatNumber(averageTelemetryLatency, 0)}ms`}
                detail="from latency/duration fields"
                tone={averageTelemetryLatency != null && averageTelemetryLatency > 2000 ? "warn" : "neutral"}
              />
              <MetricCard label="Latest event" value={telemetrySummary.latest?.name ?? "None yet"} detail={formatDateTime(telemetrySummary.latest?.occurred_at_utc)} />
            </div>
            <TelemetryTimelineChart events={telemetryEvents} />
            <div className="telemetry-chart-grid">
              <article className="telemetry-chart-card">
                <h4>Category mix</h4>
                <p>Shows whether usage is mostly product, refresh, engineering, billing, or error activity.</p>
                <TelemetryCategoryBars events={telemetryEvents} />
              </article>
              <article className="telemetry-chart-card">
                <h4>Consent mix</h4>
                <p>Separates user-consented product analytics from system events needed for operations.</p>
                <TelemetryConsentBars events={telemetryEvents} />
              </article>
              <article className="telemetry-chart-card telemetry-chart-card--wide">
                <h4>Most common events</h4>
                <p>Useful for spotting feature adoption, noisy events, and funnel drop-off points.</p>
                <TelemetryTopEventsBars events={telemetryEvents} />
              </article>
            </div>
            <TelemetryLatencyChart events={telemetryEvents} />
            <Explainer
              icon={<ShieldCheck size={17} />}
              title="Privacy guardrail"
              body="The backend redacts sensitive-looking keys such as email, token, password, secret, and API key before storage. Product telemetry is skipped when analytics consent is off."
            />
            <DataTable
              empty="No telemetry events have been recorded for this workspace yet."
              getKey={(event) => event.id}
              columns={[
                { key: "name", header: "Event", render: (event) => event.name },
                { key: "category", header: "Category", render: (event) => event.category },
                { key: "consent", header: "Consent", render: (event) => event.consent },
                { key: "time", header: "Time", render: (event) => event.occurred_at_utc }
              ]}
              rows={telemetryEvents.slice(0, 25)}
            />
          </Panel>
        </div>
      ) : null}

      {section === "billing" ? (
        <div className="grid-two">
          <Panel title="Stripe billing hooks" subtitle="Hosted Checkout and Customer Portal, with demo mode until Stripe env vars are set.">
            <div className="billing-card">
              <CreditCard size={24} />
              <div>
                <strong>{subscription?.plan ?? "free"} / {subscription?.status ?? "not configured"}</strong>
                  <span>Set STRIPE_SECRET_KEY and STRIPE_PRICE_PRO_MONTHLY to create real Checkout sessions.</span>
              </div>
            </div>
            <div className="button-cluster">
              <button type="button" className="primary-button" onClick={() => void handleCheckout()} disabled={isBusy}>
                <CreditCard size={16} />
                Start Pro checkout
              </button>
              <button type="button" className="ghost-button" onClick={() => void handlePortal()} disabled={isBusy}>
                Manage subscription
              </button>
            </div>
          </Panel>
          <Panel title="SaaS readiness notes" subtitle="The next hard step is secure production deployment.">
            <Explainer
              icon={<DatabaseZap size={17} />}
              title="What is production vs prototype?"
              body="This screen gives the product skeleton: tenants, subscriptions, data lineage, and durable operational records. Production still needs real secret vaulting, webhooks, monitoring, and legal review before live-money trading."
            />
          </Panel>
        </div>
      ) : null}
    </div>
  );
}
