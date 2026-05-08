import { ArrowRight, Database, FlaskConical, RadioTower, ServerCog, ShieldCheck, Waypoints } from "lucide-react";

import type { BacktestJob, HealthResponse, PaperRunJob, SystemMetadata } from "../api/types";
import { Badge } from "../components/Badge";
import { Explainer, MetricCard, Panel } from "../components/Cards";
import { DataTable } from "../components/Table";
import { formatDateTime, formatNumber, statusTone } from "../utils/format";

const backendSteps = [
  {
    title: "Frontend request",
    body: "React sends a typed JSON request through frontend/src/api/client.ts.",
    icon: <RadioTower size={18} />
  },
  {
    title: "FastAPI router",
    body: "The backend route validates the request shape and passes it into a service.",
    icon: <ServerCog size={18} />
  },
  {
    title: "Service layer",
    body: "services.py chooses the pipeline, job runner, metadata store, and artifact paths.",
    icon: <Waypoints size={18} />
  },
  {
    title: "Quant engine",
    body: "Pipelines produce StrategyOutput, optional news/SEC event overlays enrich signals, portfolio/risk/execution layers simulate costs, and results are saved.",
    icon: <FlaskConical size={18} />
  },
  {
    title: "Artifacts and metadata",
    body: "JSON/parquet artifacts hold heavy outputs. SQLite tracks jobs, deployments, and experiments.",
    icon: <Database size={18} />
  }
];

export function SystemGuide({
  health,
  metadata,
  paperJobs,
  backtestJobs
}: {
  health: HealthResponse | null;
  metadata: SystemMetadata | null;
  paperJobs: PaperRunJob[];
  backtestJobs: BacktestJob[];
}) {
  return (
    <div className="page-stack">
      <section className="hero-panel">
        <div>
          <p className="eyebrow">System Guide</p>
          <h2>How this frontend fits the backend without hiding the machinery</h2>
          <span>
            This page is the embedded tutorial. It shows what each backend layer does, which endpoints the UI uses,
            and how to interpret jobs and metadata while the project grows toward production-grade operations.
          </span>
        </div>
        <Badge label={health?.status === "ok" ? "backend online" : "backend unknown"} tone={statusTone(health?.status)} />
      </section>

      <section className="metric-grid">
        <MetricCard label="Jobs" value={formatNumber(metadata?.counts?.jobs ?? 0, 0)} detail="Backtest + paper job metadata" />
        <MetricCard label="Deployments" value={formatNumber(metadata?.counts?.deployment_configs ?? 0, 0)} detail="Inline paper configs" />
        <MetricCard label="Experiments" value={formatNumber(metadata?.counts?.experiment_runs ?? 0, 0)} detail="Backtest and paper runs" />
        <MetricCard label="Paper Jobs" value={formatNumber(paperJobs.length, 0)} detail="Loaded in API runner" />
      </section>

      <Panel title="Backend Flow" subtitle="Request to result">
        <div className="flow-lane">
          {backendSteps.map((step, index) => (
            <div key={step.title} className="flow-node">
              {step.icon}
              <strong>{step.title}</strong>
              <span>{step.body}</span>
              {index < backendSteps.length - 1 ? <ArrowRight size={17} /> : null}
            </div>
          ))}
        </div>
      </Panel>

      <div className="content-grid">
        <Panel title="Endpoint Map" subtitle="What the cockpit calls">
          <div className="endpoint-grid">
            <div><code>GET /api/health</code><span>Backend status for the topbar.</span></div>
            <div><code>GET /api/paper/summary</code><span>Current fake-money ledgers and visual read model.</span></div>
            <div><code>POST /api/paper/run-job</code><span>Launch multi-agent paper execution in a background worker.</span></div>
            <div><code>GET /api/paper/jobs</code><span>Paper job history and progress.</span></div>
            <div><code>POST /api/backtests/run</code><span>Launch a walk-forward research experiment.</span></div>
            <div><code>GET /api/backtests/jobs</code><span>Research job history, progress, and results.</span></div>
            <div><code>GET /api/strategies/catalog</code><span>Strategy explanations and examples.</span></div>
            <div><code>GET /api/system/metadata</code><span>Public runtime metadata.</span></div>
            <div><code>GET /api/system/admin-counts</code><span>Admin-only operational counts.</span></div>
          </div>
        </Panel>

        <Panel title="Design Principles" subtitle="Borrowed from professional quant platforms">
          <div className="principle-list">
            <div>
              <ShieldCheck size={17} />
              <strong>Research and live state in one workflow</strong>
              <span>Backtests, paper execution, and monitoring are adjacent, not separate mystery screens.</span>
            </div>
            <div>
              <ShieldCheck size={17} />
              <strong>Explain every action</strong>
              <span>Operators should understand what a deploy, replay, validation score, and fake order means.</span>
            </div>
            <div>
              <ShieldCheck size={17} />
              <strong>Make risk impossible to miss</strong>
              <span>PnL is shown next to exposure, turnover, costs, and job state.</span>
            </div>
          </div>
        </Panel>
      </div>

      <Panel title="Plain-English Number Guide" subtitle="Use this when the charts feel noisy">
        <section className="explain-grid explain-grid--simple">
          <Explainer title="Equity" body="Total fake account value after cash plus marked positions. This is the main money number." />
          <Explainer title="PnL" body="Profit or loss since the previous saved run. Positive means the fake account made money over that step." />
          <Explainer title="Gross exposure" body="How much capital is deployed before longs and shorts cancel out. High exposure can mean high risk even if net exposure is small." />
          <Explainer title="SEC events" body="Official EDGAR filing dates such as 8-K earnings releases, 10-Q reports, and 10-K reports that can feed the event-driven pipeline." />
          <Explainer title="Sentiment" body="A separate overlay from news text. It can adjust conviction, but it should not replace price/risk validation." />
          <Explainer title="DSR/PBO" body="Backtest overfit guardrails. DSR asks if Sharpe is credible; PBO estimates how likely selection was overfit." />
        </section>
      </Panel>

      <div className="content-grid">
        <Panel title="Paper Jobs" subtitle={`${paperJobs.length} records`}>
          <DataTable
            rows={paperJobs}
            empty="No paper jobs available."
            getKey={(row) => row.id}
            columns={[
              { key: "status", header: "Status", render: (row) => <Badge label={row.status} tone={statusTone(row.status)} /> },
              { key: "stage", header: "Stage", render: (row) => row.stage },
              { key: "updated", header: "Updated", render: (row) => formatDateTime(row.updated_at_utc) }
            ]}
          />
        </Panel>

        <Panel title="Backtest Jobs" subtitle={`${backtestJobs.length} records`}>
          <DataTable
            rows={backtestJobs}
            empty="No backtest jobs available."
            getKey={(row) => row.id}
            columns={[
              { key: "status", header: "Status", render: (row) => <Badge label={row.status} tone={statusTone(row.status)} /> },
              { key: "stage", header: "Stage", render: (row) => row.stage ?? "-" },
              { key: "updated", header: "Updated", render: (row) => formatDateTime(row.updated_at_utc) }
            ]}
          />
        </Panel>
      </div>

      <section className="explain-grid">
        <Explainer title="Where money lives" body="Paper cash, positions, order history, and PnL are saved under artifacts/paper/state and artifacts/paper/runs." />
        <Explainer title="Where experiments live" body="Backtest artifacts are saved under artifacts/backtests/experiments or the configured artifact root." />
          <Explainer title="Where metadata lives" body={`Metadata is served through authenticated workspace APIs. Environment: ${metadata?.app_env ?? "unknown"}.`} />
      </section>
    </div>
  );
}
