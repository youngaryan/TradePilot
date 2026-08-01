import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CalendarDays,
  CheckCircle2,
  Clock,
  Copy,
  Gauge,
  Loader2,
  Newspaper,
  Play,
  Plus,
  RefreshCw,
  Route,
  ShieldCheck,
  Trash2,
  Zap
} from "lucide-react";

import { getPaperRunJob, listPaperRunJobs, startPaperRunJob } from "../../api/client";
import type {
  PaperAgentConfig,
  PaperDashboardPayload,
  PaperExecutionConfig,
  PaperRunJob,
  PaperRunRequest,
  StrategyCatalogItem
} from "../../api/types";
import { MetricTile } from "../../components/MetricTile";
import { StatusBadge } from "../../components/StatusBadge";
import {
  AgentSymbolCoverageChart,
  DeploymentAgentChart,
  ExecutionCostChart,
  ExposureBars,
  OrderNotionalChart,
  PortfolioEquityChart,
  SentimentCoverageChart,
  StrategyRiskReturnChart
} from "./PaperCharts";
import { OrdersTable } from "./OrdersPage";
import { formatCurrency, formatNumber, formatPercent, toneFromValue } from "./format";
import { getAllOrders } from "./paperUtils";

type DateMode = "single" | "range";

const defaultExecution: PaperExecutionConfig = {
  initial_cash: 100_000,
  commission_bps: 0.5,
  slippage_bps: 1.0,
  min_trade_notional: 100,
  weight_tolerance: 0.0025
};

const fallbackLaunchCatalog: StrategyCatalogItem[] = [
  {
    id: "etf_trend",
    name: "ETF Trend / Momentum",
    family: "Directional",
    difficulty: "Core",
    pipeline: "etf_trend",
    summary: "Liquid ETF rotation sleeve with trend and cross-asset momentum filters.",
    how_it_works: "Ranks ETFs by multi-window momentum, filters weak trends, and allocates to the strongest candidates.",
    best_for: "A first serious sleeve because it is liquid, explainable, and cheap to run.",
    watch_out: "Can underperform in sharp mean-reverting markets.",
    key_parameters: ["top_n", "trend_window", "rebalance_bars"],
    example_cli: "",
    paper_config_example: {
      symbols: ["SPY", "QQQ", "IWM", "TLT", "GLD", "XLK"],
      lookback_bars: 800,
      params: { top_n: 3, trend_window: 200, rebalance_bars: 21 }
    }
  },
  {
    id: "volatility_target_trend",
    name: "Volatility Target Trend",
    family: "Directional",
    difficulty: "Intermediate",
    pipeline: "volatility_target_trend",
    summary: "Trend following with dynamic position scaling based on recent volatility.",
    how_it_works: "Uses a trend window for direction and a volatility window to resize exposure.",
    best_for: "Comparing raw trend to risk-scaled trend.",
    watch_out: "Vol targeting can reduce upside during persistent low-volatility rallies.",
    key_parameters: ["trend_window", "volatility_window", "target_volatility"],
    example_cli: "",
    paper_config_example: {
      symbols: ["SPY", "QQQ", "TLT", "GLD"],
      lookback_bars: 360,
      params: { trend_window: 120, volatility_window: 20, target_volatility: 0.15, max_strategy_weight: 0.25 }
    }
  },
  {
    id: "stat_arb",
    name: "Residual Stat-Arb",
    family: "Statistical Arbitrage",
    difficulty: "Advanced",
    pipeline: "stat_arb",
    summary: "Sector-constrained residual book with optional classic pairs and sentiment overlay.",
    how_it_works: "Builds sector-neutral residual spreads, ranks candidates, and allocates to synthetic components.",
    best_for: "A market-neutral research sleeve once data and validation are strong.",
    watch_out: "Sensitive to borrow, spread, break detection, and execution assumptions.",
    key_parameters: ["include_residual_book", "top_n_pairs", "residual_lookback"],
    example_cli: "",
    paper_config_example: {
      symbols: [],
      sector_map_path: "examples/sector_map.sample.json",
      lookback_bars: 620,
      params: { include_residual_book: true, include_classic_pairs: true, top_n_pairs: 3, residual_lookback: 60 }
    }
  },
  {
    id: "edgar_event",
    name: "EDGAR Event",
    family: "Event",
    difficulty: "Advanced",
    pipeline: "edgar_event",
    summary: "Event-driven paper agent for filings, fundamentals, or supplied event files.",
    how_it_works: "Combines event timestamps with price history and applies holding-period event weights.",
    best_for: "Testing filing/event overlays separately from pure price signals.",
    watch_out: "Needs clean event timestamps and careful avoidance of lookahead bias.",
    key_parameters: ["holding_period_bars", "entry_threshold", "min_events"],
    example_cli: "",
    paper_config_example: {
      symbols: ["AAPL", "MSFT", "NVDA"],
      event_file: "examples/events.sample.csv",
      lookback_bars: 520,
      params: { holding_period_bars: 5, entry_threshold: 0.15, min_events: 1 }
    }
  }
];

function defaultAgents(): PaperAgentConfig[] {
  return [
    {
      id: crypto.randomUUID(),
      name: "etf_trend_core",
      pipeline: "etf_trend",
      symbols: ["SPY", "QQQ", "IWM", "TLT", "GLD", "XLK"],
      interval: "1d",
      lookback_bars: 800,
      params: { top_n: 3, trend_window: 200, rebalance_bars: 21 }
    },
    {
      id: crypto.randomUUID(),
      name: "vol_target_trend_shadow",
      pipeline: "volatility_target_trend",
      symbols: ["SPY", "QQQ", "TLT", "GLD"],
      interval: "1d",
      lookback_bars: 360,
      params: { trend_window: 120, volatility_window: 20, target_volatility: 0.15, max_strategy_weight: 0.25 }
    },
    {
      id: crypto.randomUUID(),
      name: "residual_stat_arb_shadow",
      pipeline: "stat_arb",
      symbols: [],
      interval: "1d",
      lookback_bars: 620,
      sector_map_path: "examples/sector_map.sample.json",
      use_finbert: false,
      news_provider_names: [],
      params: { include_residual_book: true, include_classic_pairs: true, top_n_pairs: 3, residual_lookback: 60 }
    }
  ];
}

function statusTone(status: string): "positive" | "negative" | "warning" | "neutral" {
  if (status === "completed") return "positive";
  if (status === "failed" || status === "interrupted") return "negative";
  if (status === "running" || status === "queued") return "warning";
  return "neutral";
}

function splitList(value: string) {
  return value
    .split(/[,\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function splitSymbols(value: string) {
  return splitList(value).map((item) => item.toUpperCase());
}

function businessDayCount(start: string, end: string) {
  if (!start || !end) return 0;
  const startDate = new Date(`${start}T00:00:00Z`);
  const endDate = new Date(`${end}T00:00:00Z`);
  if (Number.isNaN(startDate.getTime()) || Number.isNaN(endDate.getTime()) || startDate > endDate) return 0;
  let count = 0;
  const cursor = new Date(startDate);
  while (cursor <= endDate) {
    const day = cursor.getUTCDay();
    if (day !== 0 && day !== 6) count += 1;
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return count;
}

function agentHasSentiment(agent: PaperAgentConfig) {
  return Boolean(
    agent.use_finbert ||
      agent.daily_sentiment_file ||
      agent.news_provider_names?.length ||
      agent.news_files?.length ||
      agent.news_topics?.length
  );
}

function parseParams(text: string, agentName: string) {
  try {
    const parsed = JSON.parse(text || "{}");
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new Error("parameters must be a JSON object");
    }
    return parsed as Record<string, unknown>;
  } catch (caught) {
    throw new Error(`${agentName} parameters are invalid JSON: ${caught instanceof Error ? caught.message : "unknown error"}`);
  }
}

function agentToStrategySpec(agent: PaperAgentConfig, params: Record<string, unknown>) {
  return {
    name: agent.name,
    pipeline: agent.pipeline,
    symbols: agent.symbols,
    sector_map_path: agent.sector_map_path || undefined,
    daily_sentiment_file: agent.daily_sentiment_file || undefined,
    news_provider_names: agent.news_provider_names ?? [],
    news_files: agent.news_files ?? [],
    use_finbert: Boolean(agent.use_finbert),
    local_finbert_only: Boolean(agent.local_finbert_only),
    news_topics: agent.news_topics ?? [],
    event_file: agent.event_file || undefined,
    use_sec_companyfacts: Boolean(agent.use_sec_companyfacts),
    edgar_user_agent: agent.edgar_user_agent || undefined,
    interval: agent.interval,
    lookback_bars: Number(agent.lookback_bars),
    params
  };
}

function LiveRunProgress({ job }: { job: PaperRunJob | null }) {
  const progress = Math.round((job?.progress ?? 0) * 100);
  const steps = [
    { id: "loading_config", label: "Load Config", body: "Read execution settings and all agent specs." },
    { id: "building_signals", label: "Build Signals", body: "Fetch data, build features, sentiment overlays, and target weights." },
    { id: "simulating_orders", label: "Simulate Orders", body: "Apply risk, slippage, commissions, and fake fills." },
    { id: "saving_ledgers", label: "Save Ledgers", body: "Persist cash, positions, orders, history, and dashboard payloads." }
  ];
  const activeStage = job?.stage ?? "waiting";

  return (
    <section className="panel live-progress-panel">
      <div className="panel__header">
        <h2>Execution Progress</h2>
        <StatusBadge label={job?.status ?? "idle"} tone={job ? statusTone(job.status) : "neutral"} />
      </div>
      <div className="progress-track progress-track--large">
        <i style={{ width: `${progress}%` }} />
      </div>
      <div className="progress-caption">
        <strong>{progress}%</strong>
        <span>{job?.message ?? "Configure agents, then execute a paper live run."}</span>
      </div>
      <div className="live-stepper">
        {steps.map((step) => {
          const stepIndex = steps.findIndex((item) => item.id === step.id);
          const isActive = activeStage === step.id;
          const isDone = (job?.progress ?? 0) >= (stepIndex + 1) / steps.length;
          return (
            <div key={step.id} className={isActive ? "live-step live-step--active" : isDone ? "live-step live-step--done" : "live-step"}>
              {isDone ? <CheckCircle2 size={17} /> : isActive ? <Loader2 size={17} /> : <Clock size={17} />}
              <strong>{step.label}</strong>
              <span>{step.body}</span>
            </div>
          );
        })}
      </div>
      {job?.result?.run_sequence ? (
        <div className="run-sequence-note">
          Ran {job.result.run_sequence.count} date(s). Deployment saved at {job.result.run_sequence.deployment_config_path}.
        </div>
      ) : null}
      {job?.error ? (
        <div className="error-banner error-banner--compact">
          <AlertCircle size={16} />
          <span>{job.error}</span>
        </div>
      ) : null}
    </section>
  );
}

function AgentCard({
  agent,
  catalog,
  paramsText,
  onChange,
  onParamsTextChange,
  onClone,
  onRemove
}: {
  agent: PaperAgentConfig;
  catalog: StrategyCatalogItem[];
  paramsText: string;
  onChange: (agent: PaperAgentConfig) => void;
  onParamsTextChange: (value: string) => void;
  onClone: () => void;
  onRemove: () => void;
}) {
  const strategy = catalog.find((item) => item.pipeline === agent.pipeline || item.id === agent.pipeline);
  const symbolsText = agent.symbols.join(" ");
  const sentimentEnabled = agentHasSentiment(agent);

  function applyCatalogStrategy(pipeline: string) {
    const item = catalog.find((entry) => entry.pipeline === pipeline || entry.id === pipeline);
    const example = item?.paper_config_example as {
      symbols?: string[];
      params?: Record<string, unknown>;
      sector_map_path?: string;
      event_file?: string;
      lookback_bars?: number;
    } | undefined;
    onChange({
      ...agent,
      pipeline,
      symbols: Array.isArray(example?.symbols) ? example.symbols : agent.symbols,
      sector_map_path: example?.sector_map_path ?? agent.sector_map_path,
      event_file: example?.event_file ?? agent.event_file,
      lookback_bars: example?.lookback_bars ?? agent.lookback_bars,
      params: example?.params ?? agent.params
    });
    onParamsTextChange(JSON.stringify(example?.params ?? agent.params, null, 2));
  }

  function setSentimentEnabled(enabled: boolean) {
    if (enabled) {
      onChange({
        ...agent,
        use_finbert: true,
        news_provider_names: agent.news_provider_names?.length ? agent.news_provider_names : ["local"],
        news_topics: agent.news_topics?.length ? agent.news_topics : ["earnings", "macro"]
      });
      return;
    }
    onChange({
      ...agent,
      use_finbert: false,
      local_finbert_only: false,
      daily_sentiment_file: null,
      news_provider_names: [],
      news_files: [],
      news_topics: []
    });
  }

  return (
    <article className="agent-config-card">
      <div className="agent-config-card__header">
        <div>
          <strong>{agent.name}</strong>
          <span>{strategy?.summary ?? "Custom paper agent"}</span>
        </div>
        <div className="agent-card-badges">
          <StatusBadge label={agent.interval} />
          <StatusBadge label={sentimentEnabled ? "sentiment on" : "price only"} tone={sentimentEnabled ? "positive" : "neutral"} />
        </div>
      </div>

      <div className="agent-form-grid">
        <label>
          Agent name
          <input value={agent.name} onChange={(event) => onChange({ ...agent, name: event.target.value })} />
        </label>
        <label>
          Method
          <select value={agent.pipeline} onChange={(event) => applyCatalogStrategy(event.target.value)}>
            {catalog.map((item) => (
              <option key={item.id} value={item.pipeline}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Timeframe
          <select value={agent.interval} onChange={(event) => onChange({ ...agent, interval: event.target.value })}>
            <option value="1d">Daily bars</option>
            <option value="1wk">Weekly bars</option>
            <option value="1h">Hourly bars</option>
            <option value="30m">30 minute bars</option>
          </select>
        </label>
        <label>
          Lookback bars
          <input type="number" value={agent.lookback_bars} onChange={(event) => onChange({ ...agent, lookback_bars: Number(event.target.value) })} />
        </label>
      </div>

      <label className="agent-wide-field">
        Symbols
        <input value={symbolsText} onChange={(event) => onChange({ ...agent, symbols: splitSymbols(event.target.value) })} placeholder="SPY QQQ TLT GLD" />
        <small>Directional, ETF, and event agents use symbols directly. Stat-arb usually uses the sector map instead.</small>
      </label>

      <div className="agent-form-grid">
        <label>
          Sector map
          <input
            value={agent.sector_map_path ?? ""}
            onChange={(event) => onChange({ ...agent, sector_map_path: event.target.value || null })}
            placeholder="examples/sector_map.sample.json"
          />
        </label>
        <label>
          Event file
          <input
            value={agent.event_file ?? ""}
            onChange={(event) => onChange({ ...agent, event_file: event.target.value || null })}
            placeholder="examples/events.sample.csv"
          />
        </label>
      </div>

      <div className="sentiment-box">
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={sentimentEnabled}
            onChange={(event) => setSentimentEnabled(event.target.checked)}
          />
          Use sentiment/news overlay
        </label>
        <small>
          The backend passes these settings into news-aware pipelines. Stat-arb currently consumes the daily sentiment/news overlay.
        </small>
        {sentimentEnabled ? (
          <div className="agent-form-grid">
            <label>
              Daily sentiment file
              <input
                value={agent.daily_sentiment_file ?? ""}
                onChange={(event) => onChange({ ...agent, daily_sentiment_file: event.target.value || null })}
                placeholder="data/sentiment/daily.parquet"
              />
            </label>
            <label>
              News providers
              <input
                value={(agent.news_provider_names ?? []).join(" ")}
                onChange={(event) => onChange({ ...agent, news_provider_names: splitList(event.target.value) })}
                placeholder="local alphavantage benzinga"
              />
            </label>
            <label>
              News files
              <input
                value={(agent.news_files ?? []).join(" ")}
                onChange={(event) => onChange({ ...agent, news_files: splitList(event.target.value) })}
                placeholder="data/news/headlines.csv"
              />
            </label>
            <label>
              News topics
              <input
                value={(agent.news_topics ?? []).join(" ")}
                onChange={(event) => onChange({ ...agent, news_topics: splitList(event.target.value) })}
                placeholder="earnings economy"
              />
            </label>
            <label className="checkbox-field checkbox-field--inline">
              <input
                type="checkbox"
                checked={Boolean(agent.use_finbert)}
                onChange={(event) => onChange({ ...agent, use_finbert: event.target.checked })}
              />
              Score with FinBERT when available
            </label>
            <label className="checkbox-field checkbox-field--inline">
              <input
                type="checkbox"
                checked={Boolean(agent.local_finbert_only)}
                onChange={(event) => onChange({ ...agent, local_finbert_only: event.target.checked })}
              />
              Local FinBERT only
            </label>
          </div>
        ) : null}
      </div>

      <label className="agent-wide-field">
        Method parameters JSON
        <textarea value={paramsText} onChange={(event) => onParamsTextChange(event.target.value)} rows={7} spellCheck={false} />
      </label>

      <div className="agent-card-actions">
        <button type="button" className="icon-button" onClick={onClone}>
          <Copy size={16} />
          <span>Clone</span>
        </button>
        <button type="button" className="icon-button icon-button--danger" onClick={onRemove}>
          <Trash2 size={16} />
          <span>Remove</span>
        </button>
      </div>
    </article>
  );
}

function RecentPaperJobs({ jobs, onSelect }: { jobs: PaperRunJob[]; onSelect: (job: PaperRunJob) => void }) {
  if (!jobs.length) return <div className="empty-state">No paper deployment jobs have been launched yet.</div>;

  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            <th>Status</th>
            <th>Progress</th>
            <th>Stage</th>
            <th>Message</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id} className="clickable-row" onClick={() => onSelect(job)}>
              <td>
                <StatusBadge label={job.status} tone={statusTone(job.status)} />
              </td>
              <td>{Math.round(job.progress * 100)}%</td>
              <td>{job.stage}</td>
              <td>{job.message}</td>
              <td>{job.updated_at_utc}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function LiveTradingPage({
  payload,
  catalog,
  onRunComplete,
  onRefresh
}: {
  payload: PaperDashboardPayload;
  catalog: StrategyCatalogItem[];
  onRunComplete: (payload: PaperDashboardPayload) => void;
  onRefresh: () => void;
}) {
  const [initialAgents] = useState<PaperAgentConfig[]>(() => defaultAgents());
  const [execution, setExecution] = useState<PaperExecutionConfig>(defaultExecution);
  const [agents, setAgents] = useState<PaperAgentConfig[]>(() => initialAgents);
  const [paramsDrafts, setParamsDrafts] = useState<Record<string, string>>(() =>
    Object.fromEntries(initialAgents.map((agent) => [agent.id, JSON.stringify(agent.params, null, 2)]))
  );
  const [dateMode, setDateMode] = useState<DateMode>("single");
  const [asofDate, setAsofDate] = useState(payload.asof_date ?? "");
  const [asofStart, setAsofStart] = useState(payload.asof_date ?? "");
  const [asofEnd, setAsofEnd] = useState(payload.asof_date ?? "");
  const [activeJob, setActiveJob] = useState<PaperRunJob | null>(null);
  const [jobs, setJobs] = useState<PaperRunJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLaunching, setIsLaunching] = useState(false);
  const orders = useMemo(() => getAllOrders(payload.strategies), [payload.strategies]);
  const replayDayCount = dateMode === "range" ? businessDayCount(asofStart, asofEnd) : 1;
  const sentimentAgentCount = agents.filter(agentHasSentiment).length;
  const totalConfiguredSymbols = agents.reduce((sum, agent) => sum + agent.symbols.length, 0);

  const launchableCatalog = useMemo(
    () => {
      const filtered = catalog.filter((item) => item.family === "Directional" || ["etf_trend", "stat_arb", "edgar_event"].includes(item.id));
      return filtered.length ? filtered : fallbackLaunchCatalog;
    },
    [catalog]
  );

  function updateAgent(nextAgent: PaperAgentConfig) {
    setAgents((current) => current.map((agent) => (agent.id === nextAgent.id ? nextAgent : agent)));
  }

  function addAgentFromCatalog(selected?: StrategyCatalogItem) {
    const item = selected ?? launchableCatalog[0] ?? fallbackLaunchCatalog[0];
    const example = item?.paper_config_example as {
      symbols?: string[];
      params?: Record<string, unknown>;
      sector_map_path?: string;
      event_file?: string;
      lookback_bars?: number;
    } | undefined;
    const agent: PaperAgentConfig = {
      id: crypto.randomUUID(),
      name: `${item?.id ?? "custom"}_${agents.length + 1}`,
      pipeline: item?.pipeline ?? "etf_trend",
      symbols: example?.symbols ?? ["SPY", "QQQ"],
      interval: "1d",
      lookback_bars: example?.lookback_bars ?? 360,
      sector_map_path: example?.sector_map_path,
      event_file: example?.event_file,
      params: example?.params ?? {}
    };
    setAgents((current) => [...current, agent]);
    setParamsDrafts((current) => ({ ...current, [agent.id]: JSON.stringify(agent.params, null, 2) }));
  }

  function cloneAgent(agent: PaperAgentConfig) {
    const cloned = { ...agent, id: crypto.randomUUID(), name: `${agent.name}_copy` };
    setAgents((current) => [...current, cloned]);
    setParamsDrafts((current) => ({ ...current, [cloned.id]: current[agent.id] ?? JSON.stringify(agent.params, null, 2) }));
  }

  function removeAgent(agentId: string) {
    setAgents((current) => current.filter((agent) => agent.id !== agentId));
    setParamsDrafts((current) => {
      const next = { ...current };
      delete next[agentId];
      return next;
    });
  }

  function buildRunRequest(): PaperRunRequest {
    if (!agents.length) throw new Error("Add at least one agent before launching.");
    const names = agents.map((agent) => agent.name.trim()).filter(Boolean);
    if (names.length !== agents.length) throw new Error("Every agent needs a name before launch.");
    if (new Set(names).size !== names.length) throw new Error("Agent names must be unique because each one gets its own ledger.");
    if (dateMode === "range") {
      if (!asofStart || !asofEnd) throw new Error("Choose both a start and end date for a replay range.");
      if (businessDayCount(asofStart, asofEnd) < 1) throw new Error("The replay range must contain at least one business day.");
    }
    const strategies = agents.map((agent) =>
      agentToStrategySpec(agent, parseParams(paramsDrafts[agent.id] ?? JSON.stringify(agent.params), agent.name))
    );
    return {
      deployment_config: {
        execution,
        strategies
      },
      asof_date: dateMode === "single" ? asofDate || null : null,
      asof_start: dateMode === "range" ? asofStart || null : null,
      asof_end: dateMode === "range" ? asofEnd || null : null
    };
  }

  async function refreshJobs() {
    try {
      setJobs(await listPaperRunJobs());
    } catch {
      // The control room still works even if job history is temporarily unavailable.
    }
  }

  async function launchPaperRun() {
    setError(null);
    setIsLaunching(true);
    try {
      const job = await startPaperRunJob(buildRunRequest());
      setActiveJob(job);
      await refreshJobs();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to launch live paper run.");
    } finally {
      setIsLaunching(false);
    }
  }

  useEffect(() => {
    void refreshJobs();
  }, []);

  useEffect(() => {
    if (!activeJob || !["queued", "running"].includes(activeJob.status)) return;
    const timer = window.setInterval(() => {
      void getPaperRunJob(activeJob.id).then((job) => {
        setActiveJob(job);
        void refreshJobs();
        if (job.status === "completed" && job.result) {
          onRunComplete(job.result);
        }
      });
    }, 1400);
    return () => window.clearInterval(timer);
  }, [activeJob, onRunComplete]);

  return (
    <div className="live-page">
      <section className="panel live-hero">
        <div>
          <p className="eyebrow">Shadow Live Trading</p>
          <h2>Deploy multiple fake-money agents with visible execution progress</h2>
          <span>
            Configure methods, symbols, timeframes, sentiment overlays, and date ranges. The backend builds an inline deployment
            config, simulates execution, and updates persistent paper ledgers.
          </span>
        </div>
        <StatusBadge label="paper mode" tone="warning" />
      </section>

      <section className="metrics-grid">
        <MetricTile label="Equity" value={formatCurrency(payload.totals.equity)} tone={toneFromValue(payload.totals.daily_pnl)} />
        <MetricTile label="Daily PnL" value={formatCurrency(payload.totals.daily_pnl)} tone={toneFromValue(payload.totals.daily_pnl)} />
        <MetricTile label="Cash" value={formatCurrency(payload.totals.cash)} />
        <MetricTile label="Gross Exposure" value={formatPercent(payload.totals.gross_exposure_ratio)} />
        <MetricTile label="Configured Agents" value={formatNumber(agents.length)} icon={<Gauge size={16} />} />
        <MetricTile label="Symbols / Universe" value={formatNumber(totalConfiguredSymbols)} detail="Sector maps counted in chart" />
        <MetricTile label="Replay Days" value={formatNumber(replayDayCount)} detail={dateMode === "range" ? "Business-day range" : "Single as-of run"} icon={<CalendarDays size={16} />} />
        <MetricTile label="Sentiment Overlays" value={formatNumber(sentimentAgentCount)} detail="News-aware agents" icon={<Newspaper size={16} />} />
      </section>

      <div className="live-control-grid">
        <section className="panel live-launch-panel">
          <div className="panel__header">
            <h2>Deployment Control</h2>
            <span>{payload.run_timestamp_utc ?? "No run loaded"}</span>
          </div>

          <div className="execution-grid">
            <label>
              Initial cash
              <input type="number" value={execution.initial_cash} onChange={(event) => setExecution({ ...execution, initial_cash: Number(event.target.value) })} />
            </label>
            <label>
              Commission bps
              <input type="number" value={execution.commission_bps} onChange={(event) => setExecution({ ...execution, commission_bps: Number(event.target.value) })} />
            </label>
            <label>
              Slippage bps
              <input type="number" value={execution.slippage_bps} onChange={(event) => setExecution({ ...execution, slippage_bps: Number(event.target.value) })} />
            </label>
            <label>
              Min trade
              <input type="number" value={execution.min_trade_notional} onChange={(event) => setExecution({ ...execution, min_trade_notional: Number(event.target.value) })} />
            </label>
          </div>

          <div className="date-mode-toggle">
            <button type="button" className={dateMode === "single" ? "filter-pill filter-pill--active" : "filter-pill"} onClick={() => setDateMode("single")}>
              Single date
            </button>
            <button type="button" className={dateMode === "range" ? "filter-pill filter-pill--active" : "filter-pill"} onClick={() => setDateMode("range")}>
              Date range replay
            </button>
          </div>

          {dateMode === "single" ? (
            <label className="run-date-field">
              As-of date
              <input value={asofDate} onChange={(event) => setAsofDate(event.target.value)} placeholder="YYYY-MM-DD or blank for today" />
              <small>Leave blank to use today's UTC date.</small>
            </label>
          ) : (
            <div className="execution-grid">
              <label>
                Replay start
                <input value={asofStart} onChange={(event) => setAsofStart(event.target.value)} placeholder="YYYY-MM-DD" />
              </label>
              <label>
                Replay end
                <input value={asofEnd} onChange={(event) => setAsofEnd(event.target.value)} placeholder="YYYY-MM-DD" />
              </label>
            </div>
          )}

          <div className="run-window-preview">
            <CalendarDays size={17} />
            <div>
              <strong>{dateMode === "range" ? `${replayDayCount} business-day replay` : "Single paper execution"}</strong>
              <span>
                {dateMode === "range"
                  ? `${agents.length} agent(s) will be run sequentially for every business day in the selected window.`
                  : `${agents.length} agent(s) will run once for the selected as-of date.`}
              </span>
            </div>
          </div>

          <div className="live-actions">
            <button type="button" className="primary-button" onClick={() => void launchPaperRun()} disabled={isLaunching}>
              {isLaunching ? <Loader2 size={17} /> : <Play size={17} />}
              <span>{isLaunching ? "Launching" : "Deploy Agents"}</span>
            </button>
            <button type="button" className="icon-button" onClick={onRefresh}>
              <RefreshCw size={17} />
              <span>Refresh State</span>
            </button>
          </div>
          {error ? (
            <div className="error-banner error-banner--compact">
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
          ) : null}
        </section>

        <LiveRunProgress job={activeJob} />
      </div>

      <div className="analytics-grid">
        <section className="panel">
          <div className="panel__header">
            <h2>Deployment Methods</h2>
            <span>Agents and timeframes</span>
          </div>
          <DeploymentAgentChart agents={agents} />
        </section>
        <section className="panel">
          <div className="panel__header">
            <h2>Sentiment Coverage</h2>
            <span>Overlay usage</span>
          </div>
          <SentimentCoverageChart agents={agents} />
        </section>
        <section className="panel">
          <div className="panel__header">
            <h2>Symbol Coverage</h2>
            <span>Configured universes</span>
          </div>
          <AgentSymbolCoverageChart agents={agents} />
        </section>
        <section className="panel">
          <div className="panel__header">
            <h2>Execution Assumptions</h2>
            <span>Fake broker model</span>
          </div>
          <ExecutionCostChart execution={execution} />
        </section>
      </div>

      <section className="panel">
        <div className="panel__header">
          <h2>Agent Deployment Builder</h2>
          <button type="button" className="icon-button" onClick={() => addAgentFromCatalog()}>
            <Plus size={16} />
            <span>Add Agent</span>
          </button>
        </div>
        <div className="deployment-template-grid">
          {launchableCatalog.slice(0, 4).map((item) => (
            <button key={item.id} type="button" className="agent-template agent-template--compact" onClick={() => addAgentFromCatalog(item)}>
              <Plus size={16} />
              <strong>{item.name}</strong>
              <span>{item.summary}</span>
              <small>Add {item.pipeline}</small>
            </button>
          ))}
        </div>
        <div className="agent-config-grid">
          {agents.map((agent) => (
            <AgentCard
              key={agent.id}
              agent={agent}
              catalog={launchableCatalog}
              paramsText={paramsDrafts[agent.id] ?? JSON.stringify(agent.params, null, 2)}
              onChange={updateAgent}
              onParamsTextChange={(value) => setParamsDrafts((current) => ({ ...current, [agent.id]: value }))}
              onClone={() => cloneAgent(agent)}
              onRemove={() => removeAgent(agent.id)}
            />
          ))}
        </div>
      </section>

      <div className="analytics-grid">
        <section className="panel analytics-grid__wide">
          <div className="panel__header">
            <h2>Live Equity Trail</h2>
            <span>Across saved paper runs</span>
          </div>
          <PortfolioEquityChart strategies={payload.strategies} />
        </section>
        <section className="panel">
          <div className="panel__header">
            <h2>Risk Footprint</h2>
            <span>Gross exposure</span>
          </div>
          <ExposureBars strategies={payload.strategies} />
        </section>
        <section className="panel">
          <div className="panel__header">
            <h2>Order Notional</h2>
            <span>Latest run</span>
          </div>
          <OrderNotionalChart orders={orders} />
        </section>
        <section className="panel analytics-grid__wide">
          <div className="panel__header">
            <h2>Risk / Return Map</h2>
            <span>Exposure, PnL, and trade count</span>
          </div>
          <StrategyRiskReturnChart strategies={payload.strategies} />
        </section>
      </div>

      <section className="panel live-explainer">
        <div className="panel__header">
          <h2>How The Paper Live Run Works</h2>
          <span>Execution path</span>
        </div>
        <div className="explain-grid">
          <div>
            <Route size={17} />
            <strong>Signal path</strong>
            <span>Each configured agent receives its symbols, method, timeframe, lookback, and optional sentiment settings.</span>
          </div>
          <div>
            <ShieldCheck size={17} />
            <strong>Risk path</strong>
            <span>The simulated broker applies cash, minimum trade size, slippage, commissions, and target-weight tolerance.</span>
          </div>
          <div>
            <Zap size={17} />
            <strong>Date range path</strong>
            <span>Range replay runs each business day sequentially, updating ledgers as if the system operated over that period.</span>
          </div>
          <div>
            <CheckCircle2 size={17} />
            <strong>Sentiment path</strong>
            <span>Sentiment settings are passed into strategy specs for pipelines that consume news or daily sentiment overlays.</span>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel__header">
          <h2>Recent Paper Jobs</h2>
          <span>{jobs.length} persisted jobs</span>
        </div>
        <RecentPaperJobs jobs={jobs} onSelect={setActiveJob} />
      </section>

      <OrdersTable orders={orders} />
    </div>
  );
}
