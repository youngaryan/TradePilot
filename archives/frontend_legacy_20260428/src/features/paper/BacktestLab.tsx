import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  BrainCircuit,
  CheckCircle2,
  ClipboardCheck,
  FlaskConical,
  Info,
  Loader2,
  Play,
  ShieldCheck,
  SlidersHorizontal,
  XCircle
} from "lucide-react";

import { getBacktestJob, listBacktestJobs, startBacktest } from "../../api/client";
import type { BacktestJob, BacktestRunRequest, BacktestTemplate, StrategyCatalogItem } from "../../api/types";
import { MetricTile } from "../../components/MetricTile";
import { StatusBadge } from "../../components/StatusBadge";
import { formatNumber, formatPercent, toneFromValue } from "./format";

const defaultRequest: BacktestRunRequest = {
  pipeline: "time_series_momentum",
  symbols: ["SPY", "QQQ", "TLT", "GLD"],
  start: "2018-01-01",
  end: "2026-04-15",
  interval: "1d",
  experiment_name: "ui_backtest_lab",
  sector_map_path: null,
  event_file: null,
  use_sec_companyfacts: false,
  edgar_user_agent: null,
  train_bars: 252,
  test_bars: 63,
  step_bars: 63,
  bars_per_year: 252,
  purge_bars: 5,
  embargo_bars: 0,
  pbo_partitions: 8,
  parameters: {
    momentum_lookbacks: [21, 63, 126, 252],
    momentum_min_agreement: 0.25
  }
};

const parameterHelp: Record<string, string> = {
  momentum_lookbacks: "Momentum horizons. More horizons reduce noise but react more slowly.",
  momentum_min_agreement: "Minimum average horizon vote before taking a position.",
  trend_window: "Lookback used to define directional trend.",
  volatility_window: "Lookback used to estimate realized volatility.",
  target_volatility: "Annualized volatility target for position sizing.",
  bollinger_window: "Rolling window used for Bollinger band mean and volatility.",
  bollinger_num_std: "How wide the bands are. Wider bands trade less often.",
  z_exit: "Exit when the z-score moves back near the mean.",
  ema_fast_window: "Fast exponential moving-average window.",
  ema_slow_window: "Slow exponential moving-average window.",
  regime_fast_window: "Fast trend window for the regime switcher.",
  regime_slow_window: "Slow trend window for the regime switcher.",
  regime_volatility_quantile: "Training-window volatility threshold for switching regimes."
};

function statusTone(status: string): "positive" | "negative" | "warning" | "neutral" {
  if (status === "completed") return "positive";
  if (status === "failed" || status === "interrupted") return "negative";
  if (status === "running" || status === "queued") return "warning";
  return "neutral";
}

function decisionTone(verdict: string): "positive" | "negative" | "warning" | "neutral" {
  if (verdict === "paper_candidate") return "positive";
  if (verdict === "reject_or_redesign") return "negative";
  if (verdict === "research_more") return "warning";
  return "neutral";
}

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function metricValue(source: Record<string, unknown>, keys: string[], formatter: (value: number) => string) {
  for (const key of keys) {
    const value = asNumber(source[key]);
    if (value !== null) return formatter(value);
  }
  return "-";
}

function metricTone(source: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = asNumber(source[key]);
    if (value !== null) return toneFromValue(value);
  }
  return "neutral";
}

function formatCheckValue(name: string, value: number | null) {
  if (value === null) return "n/a";
  if (["PBO", "Drawdown", "Turnover"].includes(name)) return formatPercent(value);
  if (name === "Folds") return formatNumber(value);
  return formatNumber(value);
}

function labelize(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function toSymbols(value: string) {
  return value
    .split(/[,\s]+/)
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean);
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item)).filter(Boolean);
}

function strategyPaperExample(item: StrategyCatalogItem) {
  return item.paper_config_example as {
    symbols?: unknown;
    sector_map_path?: unknown;
    event_file?: unknown;
    params?: unknown;
  };
}

function parseParameterValue(original: unknown, value: string): unknown {
  if (Array.isArray(original)) {
    return value
      .split(/[,\s]+/)
      .map((item) => item.trim())
      .filter(Boolean)
      .map((item) => {
        const numeric = Number(item);
        return Number.isFinite(numeric) ? numeric : item;
      });
  }
  if (typeof original === "number") {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : original;
  }
  if (typeof original === "boolean") {
    return value === "true";
  }
  return value;
}

function stringifyParameter(value: unknown) {
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

function ParameterEditor({
  parameters,
  onChange
}: {
  parameters: Record<string, unknown>;
  onChange: (parameters: Record<string, unknown>) => void;
}) {
  const entries = Object.entries(parameters);
  if (!entries.length) {
    return (
      <div className="parameter-empty">
        This agent uses its pipeline defaults. Pick another strategy or template to expose editable parameters.
      </div>
    );
  }

  return (
    <div className="parameter-editor">
      {entries.map(([key, value]) => (
        <label key={key} className="parameter-field">
          <span>{labelize(key)}</span>
          <input
            value={stringifyParameter(value)}
            onChange={(event) =>
              onChange({
                ...parameters,
                [key]: parseParameterValue(value, event.target.value)
              })
            }
          />
          <small>{parameterHelp[key] ?? "Strategy-specific setting passed to the backend agent."}</small>
        </label>
      ))}
    </div>
  );
}

function BacktestEquityChart({
  points
}: {
  points: Array<{ timestamp: string; equity: number; drawdown: number; net_return: number }>;
}) {
  if (!points.length) {
    return <div className="empty-state">No equity curve points returned yet.</div>;
  }

  const width = 760;
  const height = 260;
  const padding = 28;
  const equities = points.map((point) => point.equity);
  const drawdowns = points.map((point) => point.drawdown);
  const minEquity = Math.min(...equities);
  const maxEquity = Math.max(...equities);
  const minDrawdown = Math.min(...drawdowns, -0.01);

  const x = (index: number) => padding + (index / Math.max(points.length - 1, 1)) * (width - padding * 2);
  const yEquity = (value: number) =>
    padding + (1 - (value - minEquity) / Math.max(maxEquity - minEquity, 1e-8)) * (height * 0.62 - padding);
  const yDrawdown = (value: number) => height - padding - (Math.abs(value) / Math.abs(minDrawdown)) * (height * 0.22);

  const equityPath = points.map((point, index) => `${x(index)},${yEquity(point.equity)}`).join(" ");
  const drawdownPath = points.map((point, index) => `${x(index)},${yDrawdown(point.drawdown)}`).join(" ");

  return (
    <svg className="backtest-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Backtest equity and drawdown chart">
      <defs>
        <linearGradient id="equityFill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#1d5fd1" stopOpacity="0.22" />
          <stop offset="100%" stopColor="#1d5fd1" stopOpacity="0" />
        </linearGradient>
      </defs>
      <line x1={padding} x2={width - padding} y1={height - padding} y2={height - padding} className="chart-axis" />
      <line x1={padding} x2={padding} y1={padding} y2={height - padding} className="chart-axis" />
      <polyline points={equityPath} fill="none" className="chart-equity-line" />
      <polyline points={drawdownPath} fill="none" className="chart-drawdown-line" />
      <text x={padding} y={18} className="chart-label">
        Equity {formatNumber(points.at(-1)?.equity ?? 1)}
      </text>
      <text x={width - 190} y={height - 10} className="chart-label">
        Max DD {formatPercent(minDrawdown)}
      </text>
    </svg>
  );
}

function AgentStatusPanel({ job }: { job: BacktestJob | null }) {
  if (!job) {
    return (
      <section className="panel backtest-status">
        <div className="backtest-status__empty">
          <BrainCircuit size={34} />
          <h2>No agent running yet</h2>
          <p>Choose a preset, review the validation settings, then launch. The agent status and results will appear here.</p>
        </div>
      </section>
    );
  }

  const progress = Math.round((job.progress ?? 0) * 100);
  const icon =
    job.status === "completed" ? <CheckCircle2 size={20} /> : job.status === "failed" ? <XCircle size={20} /> : <Loader2 size={20} />;

  return (
    <section className="panel backtest-status">
      <div className="panel__header">
        <h2>Agent Status</h2>
        <StatusBadge label={job.status} tone={statusTone(job.status)} />
      </div>
      <div className="job-status-line">
        {icon}
        <span>{job.stage ?? job.status}</span>
      </div>
      <div className="progress-track" aria-label="Backtest progress">
        <i style={{ width: `${progress}%` }} />
      </div>
      <div className="progress-caption">
        <strong>{progress}%</strong>
        <span>{job.message ?? "Working..."}</span>
      </div>
      <div className="job-meta-grid">
        <span>Job: {job.id}</span>
        <span>Created: {job.created_at_utc}</span>
        <span>Started: {job.started_at_utc ?? "-"}</span>
        <span>Finished: {job.finished_at_utc ?? "-"}</span>
      </div>
      {job.error ? (
        <div className="error-banner error-banner--compact">
          <AlertTriangle size={16} />
          <span>{job.error}</span>
        </div>
      ) : null}
    </section>
  );
}

function ResultPanel({ job }: { job: BacktestJob | null }) {
  if (!job?.result) return null;
  const summary = job.result.summary ?? {};
  const validation = job.result.validation ?? {};
  const decision = job.result.decision;

  return (
    <section className="backtest-results">
      <div className="panel decision-panel">
        <div>
          <p className="eyebrow">Research Decision</p>
          <h2>{decision.headline}</h2>
          <span>
            Passed {decision.passed_checks} of {decision.total_checks} checks. This is a research gate, not permission to trade real money.
          </span>
        </div>
        <StatusBadge label={decision.verdict.replaceAll("_", " ")} tone={decisionTone(decision.verdict)} />
      </div>

      <div className="mini-grid">
        <MetricTile
          label="Annual Return"
          value={metricValue(summary, ["annualized_return"], formatPercent)}
          tone={metricTone(summary, ["annualized_return"])}
        />
        <MetricTile label="Sharpe" value={metricValue(summary, ["sharpe"], formatNumber)} />
        <MetricTile label="DSR" value={metricValue(validation, ["dsr"], formatNumber)} />
        <MetricTile label="PBO" value={metricValue(validation, ["pbo"], formatPercent)} tone={metricTone(validation, ["pbo"])} />
        <MetricTile label="Max Drawdown" value={metricValue(summary, ["max_drawdown"], formatPercent)} tone={metricTone(summary, ["max_drawdown"])} />
        <MetricTile label="Avg Turnover" value={metricValue(summary, ["avg_turnover"], formatPercent)} />
      </div>

      <section className="panel">
        <div className="panel__header">
          <h2>Equity And Drawdown</h2>
          <span>{job.result.equity_curve_points.length} plotted points</span>
        </div>
        <BacktestEquityChart points={job.result.equity_curve_points} />
      </section>

      <section className="panel">
        <div className="panel__header">
          <h2>Validation Checklist</h2>
          <span>Promotion gate</span>
        </div>
        <div className="check-grid">
          {decision.checks.map((check) => (
            <div key={check.name} className={check.passed ? "check-card check-card--pass" : "check-card check-card--fail"}>
              {check.passed ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}
              <strong>{check.name}</strong>
              <span>{formatCheckValue(check.name, check.value)}</span>
              <small>{check.message}</small>
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel__header">
          <h2>Artifacts</h2>
          <span>{job.result.artifact_dir ?? "No artifact directory"}</span>
        </div>
        <pre className="json-panel">{JSON.stringify({ summary, validation, visuals: job.result.visuals }, null, 2)}</pre>
      </section>
    </section>
  );
}

function RecentJobs({ jobs, onSelect }: { jobs: BacktestJob[]; onSelect: (job: BacktestJob) => void }) {
  if (!jobs.length) return <div className="empty-state">No backtest jobs have been launched yet.</div>;

  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            <th>Status</th>
            <th>Pipeline</th>
            <th>Progress</th>
            <th>Message</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => {
            const request = job.request as Partial<BacktestRunRequest>;
            return (
              <tr key={job.id} onClick={() => onSelect(job)} className="clickable-row">
                <td>
                  <StatusBadge label={job.status} tone={statusTone(job.status)} />
                </td>
                <td>{String(request.pipeline ?? "-")}</td>
                <td>{Math.round((job.progress ?? 0) * 100)}%</td>
                <td>{job.message ?? "-"}</td>
                <td>{job.updated_at_utc}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function BacktestLab({
  catalog,
  templates
}: {
  catalog: StrategyCatalogItem[];
  templates: BacktestTemplate[];
}) {
  const [request, setRequest] = useState<BacktestRunRequest>(defaultRequest);
  const [symbolsText, setSymbolsText] = useState(defaultRequest.symbols.join(" "));
  const [activeJob, setActiveJob] = useState<BacktestJob | null>(null);
  const [jobs, setJobs] = useState<BacktestJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const launchableStrategies = useMemo(
    () => catalog.filter((item) => item.family === "Directional" || item.id === "etf_trend" || item.id === "stat_arb" || item.id === "edgar_event"),
    [catalog]
  );
  const selectedStrategy = launchableStrategies.find((item) => item.pipeline === request.pipeline || item.id === request.pipeline);

  async function refreshJobs() {
    try {
      setJobs(await listBacktestJobs());
    } catch {
      // The form stays usable even if history cannot be fetched.
    }
  }

  function applyTemplate(template: BacktestTemplate) {
    const nextRequest = {
      ...defaultRequest,
      pipeline: template.pipeline,
      symbols: template.symbols,
      start: template.start,
      end: template.end,
      experiment_name: template.id,
      parameters: template.parameters
    };
    setRequest(nextRequest);
    setSymbolsText(template.symbols.join(" "));
  }

  function applyStrategy(item: StrategyCatalogItem) {
    const example = strategyPaperExample(item);
    const params = typeof example.params === "object" && example.params !== null ? (example.params as Record<string, unknown>) : {};
    const symbols = readStringArray(example.symbols);
    setRequest((current) => ({
      ...current,
      pipeline: item.pipeline,
      symbols: symbols.length ? symbols : current.symbols,
      sector_map_path: typeof example.sector_map_path === "string" ? example.sector_map_path : current.sector_map_path,
      event_file: typeof example.event_file === "string" ? example.event_file : current.event_file,
      experiment_name: `${item.id}_ui_backtest`,
      parameters: params
    }));
    if (symbols.length) setSymbolsText(symbols.join(" "));
  }

  async function launchBacktest() {
    setIsSubmitting(true);
    setError(null);
    try {
      const symbols = toSymbols(symbolsText);
      if (!symbols.length && request.pipeline !== "stat_arb") {
        throw new Error("Add at least one symbol before launching.");
      }
      const submitted = await startBacktest({
        ...request,
        symbols
      });
      setActiveJob(submitted);
      await refreshJobs();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to launch backtest.");
    } finally {
      setIsSubmitting(false);
    }
  }

  useEffect(() => {
    void refreshJobs();
  }, []);

  useEffect(() => {
    if (!activeJob || !["queued", "running"].includes(activeJob.status)) return;
    const timer = window.setInterval(() => {
      void getBacktestJob(activeJob.id).then((job) => {
        setActiveJob(job);
        void refreshJobs();
      });
    }, 1500);
    return () => window.clearInterval(timer);
  }, [activeJob]);

  return (
    <section className="backtest-lab">
      <div className="panel backtest-hero">
        <div>
          <p className="eyebrow">Interactive Research</p>
          <h2>Backtest agent workbench</h2>
          <p>
            A backtest agent is a controlled research run: it builds the selected pipeline, applies walk-forward validation, saves
            artifacts, and gives a promotion decision before anything reaches paper trading.
          </p>
        </div>
        <FlaskConical size={44} />
      </div>

      <section className="operator-flow">
        <div>
          <BrainCircuit size={18} />
          <strong>1. Choose agent</strong>
          <span>Start from a preset or strategy catalog default.</span>
        </div>
        <div>
          <SlidersHorizontal size={18} />
          <strong>2. Configure</strong>
          <span>Set symbols, dates, validation windows, and parameters.</span>
        </div>
        <div>
          <Play size={18} />
          <strong>3. Run</strong>
          <span>The backend queues a job and reports progress.</span>
        </div>
        <div>
          <ClipboardCheck size={18} />
          <strong>4. Decide</strong>
          <span>Use DSR/PBO, drawdown, and turnover before promotion.</span>
        </div>
      </section>

      <div className="agent-template-grid">
        {templates.map((template) => (
          <button key={template.id} type="button" className="agent-template" onClick={() => applyTemplate(template)}>
            <BrainCircuit size={18} />
            <strong>{template.name}</strong>
            <span>{template.description}</span>
            <small>
              {template.risk_level ?? "Research"} | {template.validation_focus ?? "Standard walk-forward validation"}
            </small>
          </button>
        ))}
      </div>

      <div className="backtest-grid">
        <section className="panel backtest-form">
          <div className="panel__header">
            <h2>Configure Agent</h2>
            <StatusBadge label={request.pipeline} />
          </div>

          {selectedStrategy ? (
            <div className="strategy-context">
              <Info size={17} />
              <span>{selectedStrategy.summary}</span>
            </div>
          ) : null}

          <label>
            Strategy
            <select
              value={request.pipeline}
              onChange={(event) => {
                const next = launchableStrategies.find((item) => item.pipeline === event.target.value || item.id === event.target.value);
                if (next) applyStrategy(next);
              }}
            >
              {launchableStrategies.map((item) => (
                <option key={item.id} value={item.pipeline}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>

          <label>
            Symbols
            <input value={symbolsText} onChange={(event) => setSymbolsText(event.target.value)} placeholder="SPY QQQ TLT GLD" />
            <small>Use spaces or commas. Stat-arb should normally use a sector map instead of a free symbol list.</small>
          </label>

          <div className="form-row">
            <label>
              Start
              <input value={request.start} onChange={(event) => setRequest((current) => ({ ...current, start: event.target.value }))} />
            </label>
            <label>
              End
              <input value={request.end} onChange={(event) => setRequest((current) => ({ ...current, end: event.target.value }))} />
            </label>
          </div>

          <div className="form-row">
            <label>
              Train Bars
              <input
                type="number"
                value={request.train_bars}
                onChange={(event) => setRequest((current) => ({ ...current, train_bars: Number(event.target.value) }))}
              />
              <small>Bars used to fit each fold before testing.</small>
            </label>
            <label>
              Test Bars
              <input
                type="number"
                value={request.test_bars}
                onChange={(event) => setRequest((current) => ({ ...current, test_bars: Number(event.target.value) }))}
              />
              <small>Out-of-sample bars per fold.</small>
            </label>
          </div>

          <div className="form-row">
            <label>
              Purge Bars
              <input
                type="number"
                value={request.purge_bars}
                onChange={(event) => setRequest((current) => ({ ...current, purge_bars: Number(event.target.value) }))}
              />
              <small>Gap between train and test to reduce leakage.</small>
            </label>
            <label>
              PBO Partitions
              <input
                type="number"
                value={request.pbo_partitions}
                onChange={(event) => setRequest((current) => ({ ...current, pbo_partitions: Number(event.target.value) }))}
              />
              <small>Partitions for overfitting diagnostics.</small>
            </label>
          </div>

          <div className="form-row">
            <label>
              Sector Map Path
              <input
                value={request.sector_map_path ?? ""}
                onChange={(event) => setRequest((current) => ({ ...current, sector_map_path: event.target.value || null }))}
                placeholder="examples/sector_map.sample.json"
              />
            </label>
            <label>
              Event File
              <input
                value={request.event_file ?? ""}
                onChange={(event) => setRequest((current) => ({ ...current, event_file: event.target.value || null }))}
                placeholder="examples/events.sample.csv"
              />
            </label>
          </div>

          <div className="parameter-section">
            <div className="panel__header">
              <h2>Agent Parameters</h2>
              <span>{Object.keys(request.parameters).length} editable</span>
            </div>
            <ParameterEditor
              parameters={request.parameters}
              onChange={(parameters) => setRequest((current) => ({ ...current, parameters }))}
            />
          </div>

          {error ? (
            <div className="error-banner error-banner--compact">
              <AlertTriangle size={16} />
              <span>{error}</span>
            </div>
          ) : null}

          <button type="button" className="primary-button launch-button" onClick={() => void launchBacktest()} disabled={isSubmitting}>
            {isSubmitting ? <Loader2 size={17} /> : <Play size={17} />}
            <span>{isSubmitting ? "Launching" : "Launch Backtest Agent"}</span>
          </button>
        </section>

        <div className="backtest-side">
          <AgentStatusPanel job={activeJob} />
          <ResultPanel job={activeJob} />
        </div>
      </div>

      <section className="panel">
        <div className="panel__header">
          <h2>Strategy Library</h2>
          <span>Click to load defaults</span>
        </div>
        <div className="strategy-chip-grid">
          {launchableStrategies.map((item) => (
            <button key={item.id} type="button" className="strategy-chip" onClick={() => applyStrategy(item)}>
              <ShieldCheck size={15} />
              <span>{item.name}</span>
              <small>{item.difficulty}</small>
            </button>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel__header">
          <h2>Recent Jobs</h2>
          <span>{jobs.length} persisted jobs</span>
        </div>
        <RecentJobs jobs={jobs} onSelect={setActiveJob} />
      </section>

      <section className="panel backtest-explainer">
        <div className="panel__header">
          <h2>How To Read This Page</h2>
          <span>Research guardrails</span>
        </div>
        <div className="explain-grid">
          <div>
            <BarChart3 size={17} />
            <strong>Equity curve</strong>
            <span>Shows compounded net returns after simulated costs. Smooth is good; sudden cliffs deserve investigation.</span>
          </div>
          <div>
            <ShieldCheck size={17} />
            <strong>DSR</strong>
            <span>Deflated Sharpe adjusts for multiple testing. Prefer stronger DSR before trusting a high Sharpe.</span>
          </div>
          <div>
            <AlertTriangle size={17} />
            <strong>PBO</strong>
            <span>Probability of backtest overfitting. Lower is better; high PBO means the agent may be curve-fit.</span>
          </div>
          <div>
            <ClipboardCheck size={17} />
            <strong>Decision</strong>
            <span>The decision panel is a promotion gate. Passing it means shadow paper next, not real money.</span>
          </div>
        </div>
      </section>
    </section>
  );
}
