import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BrainCircuit, ChevronDown, FileText, Loader2, Play, RefreshCw, ShieldCheck, XCircle } from "lucide-react";

import { getMarketResearchJob, getMarketResearchRuntime, listMarketResearchJobs, startMarketResearchJob } from "../api/client";
import type { MarketResearchAgentOutput, MarketResearchJob, MarketResearchReport, MarketResearchRuntimeConfig, MarketResearchSignal } from "../api/types";
import { Badge } from "../components/Badge";
import { MetricCard, Panel } from "../components/Cards";
import { formatDateTime, formatNumber, statusTone } from "../utils/format";

const DISCLAIMER = "For research and educational purposes only. Not financial advice.";

function decisionTone(decision: string) {
  if (decision === "BUY") return "good" as const;
  if (decision === "SELL" || decision === "AVOID") return "bad" as const;
  if (decision === "HOLD") return "info" as const;
  return "neutral" as const;
}

function directionTone(direction: string) {
  if (direction === "bullish") return "good" as const;
  if (direction === "bearish") return "bad" as const;
  if (direction === "mixed") return "warn" as const;
  return "neutral" as const;
}

function normalizeTicker(value: string) {
  return value.trim().toUpperCase().replace(/[^A-Z0-9.^=_-]/g, "");
}

function asText(value: unknown, fallback: string) {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return fallback;
}

function uniqueWarnings(values: Array<string | null | undefined>) {
  return Array.from(new Set(values.map((item) => String(item ?? "").trim()).filter(Boolean)));
}

function progressEventTitle(event: NonNullable<MarketResearchJob["progress_events"]>[number]) {
  const agent = event.display_name ?? event.agent_name ?? "Research worker";
  if (event.event_type === "data_collection_started") return "Collecting context";
  if (event.event_type === "data_collection_completed") return "Context collected";
  if (event.event_type === "agent_started") return `${agent} started`;
  if (event.event_type === "deterministic_baseline_started") return `${agent} baseline started`;
  if (event.event_type === "deterministic_baseline_completed") return `${agent} baseline ready`;
  if (event.event_type === "llm_refinement_started") return `${agent} calling LLM`;
  if (event.event_type === "llm_refinement_completed") return `${agent} LLM completed`;
  if (event.event_type === "llm_refinement_failed") return `${agent} LLM fallback`;
  if (event.event_type === "llm_refinement_skipped") return `${agent} LLM skipped`;
  if (event.event_type === "agent_completed") return `${agent} completed`;
  if (event.event_type === "agent_timeout") return `${agent} timed out`;
  if (event.event_type === "agent_failed") return `${agent} failed`;
  return event.event_type.replaceAll("_", " ");
}

function ProgressTraceRow({ event, agentData }: { event: NonNullable<MarketResearchJob["progress_events"]>[number]; agentData?: Record<string, MarketResearchAgentOutput> }) {
  const [expandedBadge, setExpandedBadge] = useState<string | null>(null);
  const isError = event.event_type?.includes("fail") || event.event_type?.includes("error") || event.event_type === "agent_timeout";
  const isWarning = event.event_type?.includes("skip") || (typeof event.warning_count === "number" && event.warning_count > 0 && !isError);
  const showEventIcon = (isError || event.event_type === "llm_refinement_skipped") && event.error;
  const matchedAgent = event.agent_name ? agentData?.[event.agent_name] : null;

  function BadgePill({ id, className, children, detail }: { id: string; className: string; children: React.ReactNode; detail: React.ReactNode }) {
    const active = expandedBadge === id;
    return (
      <div className={`badge-pill-wrapper ${active ? "badge-pill-wrapper--active" : ""}`}>
        <button type="button" className={`badge ${className}`} onClick={() => setExpandedBadge(active ? null : id)}>
          {children}
        </button>
        {active ? (
          <div className="badge-pill-detail">{detail}</div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="progress-trace-row">
      <div className="progress-trace__header">
        <span className="progress-trace__time">{formatDateTime(event.timestamp_utc)}</span>
        <strong className="progress-trace__title">{progressEventTitle(event)}</strong>
      </div>
      <div className="progress-trace__meta">
        {event.provider && event.model ? (
          <BadgePill id="provider" className="badge--neutral" detail={<span>{event.provider} / {event.model}</span>}>
            {event.provider}/{event.model}
          </BadgePill>
        ) : null}
        {typeof event.latency_ms === "number" ? (
          <BadgePill id="latency" className="badge--neutral" detail={<span>{event.latency_ms} ms</span>}>
            {event.latency_ms} ms
          </BadgePill>
        ) : null}
        {typeof event.confidence === "number" ? (
          <BadgePill id="confidence" className="badge--good" detail={<span>{event.confidence}/100 - {progressEventTitle(event)}</span>}>
            confidence {event.confidence}
          </BadgePill>
        ) : null}
        {typeof event.signal_count === "number" ? (
          <BadgePill id="signals" className="badge--info" detail={
            <div>
              {matchedAgent?.signals?.length ? (
                <div className="badge-detail-tags">
                  {matchedAgent.signals.map((s) => (
                    <span key={s.label} className="badge-detail-tag badge-detail-tag--signal">{s.label.replaceAll("_", " ")}</span>
                  ))}
                </div>
              ) : <span>{event.signal_count} signal(s)</span>}
            </div>
          }>
            {event.signal_count} signal(s)
          </BadgePill>
        ) : null}
        {typeof event.warning_count === "number" && event.warning_count > 0 ? (
          <BadgePill id="warnings" className="badge--warn" detail={
            <div>
              {matchedAgent?.warnings?.length ? (
                <div className="badge-detail-tags">
                  {matchedAgent.warnings.map((w, i) => (
                    <span key={i} className="badge-detail-tag badge-detail-tag--warning">{w}</span>
                  ))}
                </div>
              ) : event.error ? (
                <span>{event.error}</span>
              ) : <span>{event.warning_count} warning(s)</span>}
            </div>
          }>
            {event.warning_count} warning(s)
          </BadgePill>
        ) : null}
        {typeof event.price_bar_count === "number" ? (
          <BadgePill id="bars" className="badge--neutral" detail={<span>{event.price_bar_count} price bars</span>}>
            {event.price_bar_count} price bars
          </BadgePill>
        ) : null}
        {typeof event.news_count === "number" ? (
          <BadgePill id="news" className="badge--neutral" detail={<span>{event.news_count} news rows</span>}>
            {event.news_count} news rows
          </BadgePill>
        ) : null}
        {typeof event.financial_event_count === "number" ? (
          <BadgePill id="finance" className="badge--neutral" detail={<span>{event.financial_event_count} financial events</span>}>
            {event.financial_event_count} financial event(s)
          </BadgePill>
        ) : null}
      </div>
      {showEventIcon && event.error ? (
        <div className={isError ? "inline-error" : "inline-warning"}>
          {isError ? <XCircle size={16} /> : <AlertTriangle size={16} />}
          <span>{event.error}</span>
        </div>
      ) : null}
    </div>
  );
}

function reportMetadata(report: MarketResearchReport) {
  const metadata = report.metadata ?? {};
  const providerMetadata = metadata.provider_metadata && typeof metadata.provider_metadata === "object"
    ? (metadata.provider_metadata as Record<string, unknown>)
    : {};
  return {
    llmProvider: asText(metadata.llm_provider, "unknown"),
    llmModel: asText(metadata.llm_model, "unknown"),
    dataProvider: asText(providerMetadata.backend_data_provider ?? providerMetadata.provider, "unknown"),
    fallbackAgents: Array.isArray(metadata.llm_fallback_agents) ? metadata.llm_fallback_agents.map((item) => String(item)) : []
  };
}

function runtimeTone(runtime: MarketResearchRuntimeConfig | null) {
  if (!runtime) return "neutral" as const;
  if (runtime.llm_provider === "ollama") return runtime.ollama?.reachable && runtime.ollama?.model_available ? "good" as const : "bad" as const;
  if (runtime.llm_provider === "mock" || runtime.llm_provider === "disabled") return "warn" as const;
  return "good" as const;
}

function SignalList({ signals, empty }: { signals: MarketResearchSignal[]; empty: string }) {
  if (!signals.length) return <div className="empty-state">{empty}</div>;
  return (
    <div className="market-research-signal-list">
      {signals.map((signal) => (
        <ExpandableSignal key={`${signal.label}-${signal.rationale}`} signal={signal} />
      ))}
    </div>
  );
}

function ExpandableSignal({ signal }: { signal: MarketResearchSignal }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <article className={`market-research-signal ${expanded ? "market-research-signal--expanded" : ""}`}>
      <button type="button" className="signal-header" onClick={() => setExpanded((prev) => !prev)}>
        <div>
          <strong>{signal.label.replaceAll("_", " ")}</strong>
          <span>{signal.rationale}</span>
        </div>
        <div className="signal-header-right">
          <div className="signal-score">
            <Badge label={signal.direction} tone={directionTone(signal.direction)} />
            <strong>{formatNumber(signal.strength, 0)}</strong>
          </div>
          <ChevronDown size={16} className={`signal-chevron ${expanded ? "signal-chevron--open" : ""}`} />
        </div>
      </button>
      {expanded ? (
        <div className="signal-details">
          {signal.evidence.length ? (
            <div className="signal-detail-section">
              <h4>Evidence</h4>
              <ul>
                {signal.evidence.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          ) : null}
          {signal.provenance.length ? (
            <div className="signal-detail-section">
              <h4>Sources</h4>
              <ul>
                {signal.provenance.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function WarningList({ warnings }: { warnings: string[] }) {
  if (!warnings.length) return null;
  return (
    <div className="warning-list warning-list--compact">
      {warnings.map((warning, index) => (
        <ExpandableWarning key={`${index}-${warning}`} warning={warning} />
      ))}
    </div>
  );
}

function ExpandableWarning({ warning }: { warning: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className={`warning-item ${expanded ? "warning-item--expanded" : ""}`}>
      <button type="button" className="warning-item-header" onClick={() => setExpanded((prev) => !prev)}>
        <AlertTriangle size={14} />
        <span>{warning}</span>
        <ChevronDown size={14} className={`warning-chevron ${expanded ? "warning-chevron--open" : ""}`} />
      </button>
      {expanded ? (
        <div className="warning-item-detail">
          <p>{warning}</p>
        </div>
      ) : null}
    </div>
  );
}

function ConfidenceCard({ report }: { report: MarketResearchReport }) {
  const [expanded, setExpanded] = useState(false);
  const mainTone = report.confidence >= 70 ? "good" as const : report.confidence >= 40 ? "info" as const : "bad" as const;
  return (
    <div className={`confidence-card ${expanded ? "confidence-card--expanded" : ""}`}>
      <button type="button" className={`confidence-card-header metric-card metric-card--${mainTone}`} onClick={() => setExpanded((prev) => !prev)}>
        <span className="metric-card__top">
          <span>Confidence</span>
        </span>
        <strong>{formatNumber(report.confidence, 0)}/100</strong>
        <small>Click for breakdown</small>
      </button>
      {expanded ? (
        <div className="confidence-breakdown">
          {report.raw_agent_outputs.length ? (
            <>
              <h4>Agent Confidence Breakdown</h4>
              <div className="confidence-agent-list">
                {report.raw_agent_outputs.map((agent) => (
                  <div key={agent.agent_name} className="confidence-agent-row">
                    <span className="confidence-agent-name">{agent.display_name}</span>
                    <div className="confidence-agent-bar-track">
                      <div className="confidence-agent-bar" style={{ width: `${clampPercent(agent.confidence)}%` }} />
                    </div>
                    <span className="confidence-agent-value">{agent.confidence}</span>
                  </div>
                ))}
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function clampPercent(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

function ReportView({ report }: { report: MarketResearchReport }) {
  const auditCompleted = report.audit_trail.filter((item) => item.status === "completed").length;
  const metadata = reportMetadata(report);
  const warningItems = uniqueWarnings([...report.data_quality_notes, ...report.warnings]);
  return (
    <div className="market-research-report">
      <section className="research-disclaimer">
        <ShieldCheck size={18} />
        <strong>{report.disclaimer || DISCLAIMER}</strong>
      </section>

      <div className="metric-grid">
        <MetricCard label="Decision" value={report.decision} detail={report.time_horizon} tone={decisionTone(report.decision)} icon={<BrainCircuit size={18} />} />
        <ConfidenceCard report={report} />
        <MetricCard label="Ticker" value={report.ticker} detail={`Analysis date ${report.analysis_date}`} />
        <MetricCard label="Agents" value={`${auditCompleted}/${report.audit_trail.length}`} detail="Completed committee roles" tone={auditCompleted === report.audit_trail.length ? "good" : "warn"} />
        <MetricCard label="LLM" value={metadata.llmProvider} detail={metadata.llmModel} tone={metadata.llmProvider === "mock" ? "warn" : "info"} />
        <MetricCard label="Data" value={metadata.dataProvider} detail={metadata.fallbackAgents.length ? `${metadata.fallbackAgents.length} fallback agent(s)` : "research context"} tone={metadata.dataProvider === "demo" ? "warn" : "neutral"} />
      </div>

      <Panel title="Committee Summary" subtitle="Final simulated research decision">
        <p className="market-research-summary">{report.summary}</p>
      </Panel>

      <div className="content-grid">
        <Panel title="Bull Thesis" subtitle="Strongest constructive case">
          <p className="market-research-thesis">{report.bull_thesis}</p>
        </Panel>
        <Panel title="Bear Thesis" subtitle="Strongest cautious case">
          <p className="market-research-thesis">{report.bear_thesis}</p>
        </Panel>
      </div>

      <div className="content-grid">
        <Panel title="Technical Signals" subtitle="Trend, indicators, volatility, support/resistance">
          <SignalList signals={report.technical_signals} empty="No technical signals were produced." />
        </Panel>
        <Panel title="Fundamental Signals" subtitle="Financial events and valuation coverage">
          <SignalList signals={report.fundamental_signals} empty="No fundamental signals were produced." />
        </Panel>
      </div>

      <div className="content-grid">
        <Panel title="News/Sentiment Signals" subtitle="Catalysts, source text, and sentiment">
          <SignalList signals={report.news_sentiment_signals} empty="No news or sentiment signals were produced." />
        </Panel>
        <Panel title="Risk Assessment" subtitle={report.risk_assessment.display_name}>
          <p className="market-research-thesis">{report.risk_assessment.summary}</p>
          <SignalList signals={report.risk_assessment.signals} empty="No risk signals were produced." />
        </Panel>
      </div>

      <Panel title="Data Quality And Warnings" subtitle="Provider coverage, missing data, and audit caveats">
        <WarningList warnings={warningItems} />
      </Panel>

      <div className="content-grid">
        <Panel title="Agent Audit Trail" subtitle="Role execution status and timing">
          <div className="market-research-audit">
            {report.audit_trail.map((event) => (
              <div key={`${event.agent_name}-${event.started_at_utc}`} className="audit-row">
                <div>
                  <strong>{event.display_name}</strong>
                  <span>{event.duration_ms} ms</span>
                </div>
                <Badge label={event.status} tone={event.status === "completed" ? "good" : "bad"} />
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Source Provenance" subtitle="Data sources recorded with the report">
          <div className="market-research-audit">
            {report.provenance.map((item) => (
              <div key={`${item.source}-${item.provider}-${item.detail}`} className="audit-row">
                <div>
                  <strong>{item.source.replaceAll("_", " ")}</strong>
                  <span>{item.detail}</span>
                </div>
                <Badge label={item.provider} tone="neutral" />
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}

export function MarketResearchLab() {
  const [tickerText, setTickerText] = useState("AAPL");
  const [analysisDate, setAnalysisDate] = useState("");
  const [horizon, setHorizon] = useState("swing");
  const [selectedModel, setSelectedModel] = useState("");
  const [showProgressTrace, setShowProgressTrace] = useState(false);
  const [jobs, setJobs] = useState<MarketResearchJob[]>([]);
  const [activeJob, setActiveJob] = useState<MarketResearchJob | null>(null);
  const [runtime, setRuntime] = useState<MarketResearchRuntimeConfig | null>(null);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  async function refreshRuntime() {
    try {
      setRuntime(await getMarketResearchRuntime());
      setRuntimeError(null);
    } catch (caught) {
      setRuntimeError(caught instanceof Error ? caught.message : "Unable to load market research runtime config.");
    }
  }

  async function refreshJobs() {
    setError(null);
    try {
      const nextJobs = await listMarketResearchJobs();
      setJobs(nextJobs);
      if (!activeJob && nextJobs[0]) setActiveJob(nextJobs[0]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load market research jobs.");
    }
  }

  async function runResearch() {
    const ticker = normalizeTicker(tickerText);
    if (!ticker) {
      setError("Enter a valid ticker symbol.");
      return;
    }
    setTickerText(ticker);
    setError(null);
    setIsRunning(true);
    const [providerOverride, modelOverride] = selectedModel ? selectedModel.split("|", 2) : [null, null];
    try {
      const job = await startMarketResearchJob({
        ticker,
        analysis_date: analysisDate || null,
        horizon,
        provider: providerOverride || null,
        model: modelOverride || null,
        options: {}
      });
      setActiveJob(job);
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to start market research.");
      setIsRunning(false);
    }
  }

  useEffect(() => {
    void refreshRuntime();
    void refreshJobs();
  }, []);

  useEffect(() => {
    if (!activeJob || !["queued", "running"].includes(activeJob.status)) {
      setIsRunning(false);
      return undefined;
    }
    setIsRunning(true);
    const timer = window.setInterval(() => {
      void getMarketResearchJob(activeJob.id)
        .then((job) => {
          setActiveJob(job);
          setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
          if (!["queued", "running"].includes(job.status)) setIsRunning(false);
          if (job.status === "failed" || job.status === "interrupted") setError(job.error || job.message || "Market research job failed.");
        })
        .catch((caught) => {
          setIsRunning(false);
          setError(caught instanceof Error ? caught.message : "Unable to read market research job status.");
        });
    }, 1200);
    return () => window.clearInterval(timer);
  }, [activeJob?.id, activeJob?.status]);

  const activeReport = activeJob?.result ?? null;
  const report = activeReport ?? jobs.find((job) => job.result)?.result ?? null;
  const agentData = useMemo(() => {
    if (!activeReport?.raw_agent_outputs) return {};
    const map: Record<string, MarketResearchAgentOutput> = {};
    for (const agent of activeReport.raw_agent_outputs) {
      map[agent.agent_name] = agent;
    }
    return map;
  }, [activeReport]);
  const jobProgress = Math.round((activeJob?.progress ?? (isRunning ? 0.04 : 0)) * 100);
  const activeRequest = activeJob?.request as { ticker?: unknown; horizon?: unknown; analysis_date?: unknown } | undefined;
  const jobTitle = activeJob
    ? `${String(activeRequest?.ticker ?? "Ticker")} | ${String(activeRequest?.horizon ?? "swing")}`
    : "No research job selected";
  const jobStatus = activeJob?.status ?? "idle";
  const progressEvents = activeJob?.progress_events ?? [];
  const latestJobs = useMemo(() => jobs.slice(0, 8), [jobs]);
  const nvidiaModelOptions = useMemo(
    () => (runtime?.nvidia?.market_research_models ?? []).filter((model) => model.market_research_compatible),
    [runtime?.nvidia?.market_research_models]
  );
  const selectedModelDetail = selectedModel
    ? nvidiaModelOptions.find((model) => selectedModel === `${model.provider}|${model.model}`)
    : null;
  const nvidiaCaveats = runtime?.llm_provider === "nvidia" || selectedModel ? runtime?.nvidia?.caveats ?? [] : [];
  const nvidiaKeyWarning = selectedModel && runtime?.nvidia?.api_key_configured === false
    ? "NVIDIA_API_KEY is not visible to the backend; NVIDIA model overrides will fail preflight."
    : null;
  const runtimeWarnings = uniqueWarnings([...(runtime?.warnings ?? []), ...nvidiaCaveats, nvidiaKeyWarning, runtime?.ollama?.error ?? null]);

  return (
    <div className="market-research-lab">
      <section className="research-disclaimer">
        <ShieldCheck size={18} />
        <strong>{DISCLAIMER}</strong>
      </section>

      <div className="content-grid">
        <Panel title="Research Committee" subtitle="Ticker, date, horizon, and runtime configuration">
          <div className="market-research-config-grid">
            <label>
              Ticker
              <input value={tickerText} onChange={(event) => setTickerText(event.target.value.toUpperCase())} onBlur={() => setTickerText(normalizeTicker(tickerText))} />
            </label>
            <label>
              Analysis date
              <input value={analysisDate} onChange={(event) => setAnalysisDate(event.target.value)} placeholder="Defaults to today" />
            </label>
            <label>
              Horizon
              <select value={horizon} onChange={(event) => setHorizon(event.target.value)}>
                <option value="intraday">Intraday</option>
                <option value="swing">Swing</option>
                <option value="long-term">Long-term</option>
              </select>
            </label>
            <label>
              LLM model
              <select value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)} disabled={!runtime?.model_override_enabled}>
                <option value="">Server default</option>
                {nvidiaModelOptions.map((model) => (
                  <option key={model.model} value={`${model.provider}|${model.model}`}>
                    {model.display_name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="runtime-diagnostics">
            <div>
              <strong>Configured LLM</strong>
              <span>{runtime ? `${runtime.llm_provider} / ${runtime.llm_model}` : "Loading runtime config"}</span>
            </div>
            <div>
              <strong>Data provider</strong>
              <span>{runtime?.data_provider ?? "unknown"}</span>
            </div>
            <div>
              <strong>Timeouts</strong>
              <span>{runtime ? `${formatNumber(runtime.agent_timeout_seconds, 0)}s agent / ${formatNumber(runtime.llm_timeout_seconds, 0)}s LLM` : "n/a"}</span>
            </div>
            <div>
              <strong>Selected model</strong>
              <span>{selectedModelDetail?.display_name ?? "Server default"}</span>
            </div>
            <div>
              <strong>Hosted guardrail</strong>
              <span>
                {runtime
                  ? `${formatNumber(runtime.free_endpoint_timeout_cap_seconds ?? runtime.llm_timeout_seconds, 0)}s cap / fail-fast ${runtime.llm_fail_fast_after_failures ?? 1}`
                  : "n/a"}
              </span>
            </div>
            <Badge label={runtime?.llm_provider === "ollama" ? (runtime.ollama?.model_available ? "ollama ready" : "ollama needs attention") : runtime?.llm_provider ?? "runtime"} tone={runtimeTone(runtime)} />
          </div>
          {runtimeError ? (
            <div className="inline-error">
              <AlertTriangle size={16} />
              {runtimeError}
            </div>
          ) : null}
          {runtimeWarnings.length ? (
            <div className="warning-list warning-list--compact">
              {runtimeWarnings.map((warning) => <span key={warning}>{warning}</span>)}
            </div>
          ) : null}
          <div className="button-row">
            <button type="button" className="primary-button" onClick={() => void runResearch()} disabled={isRunning}>
              {isRunning ? <Loader2 size={17} /> : <Play size={17} />}
              {isRunning ? "Running committee" : "Run research committee"}
            </button>
            <button type="button" className="ghost-button" onClick={() => { void refreshRuntime(); void refreshJobs(); }}>
              <RefreshCw size={17} />
              Refresh
            </button>
          </div>
          {error ? (
            <div className="inline-error">
              <AlertTriangle size={16} />
              {error}
            </div>
          ) : null}
        </Panel>

        <Panel title="Job Status" subtitle={jobTitle}>
          <div className={`job-progress-card ${jobStatus === "completed" ? "job-progress-card--done" : ""} ${jobStatus === "failed" || jobStatus === "interrupted" ? "job-progress-card--failed" : ""}`}>
            <div className="progress-card__top">
              <div>
                <strong>{activeJob?.message ?? "Start a job to generate a committee report."}</strong>
                <span>{activeJob?.stage?.replaceAll("_", " ") ?? "idle"}</span>
              </div>
              <Badge label={jobStatus} tone={statusTone(jobStatus)} />
            </div>
            <div className="progress-track" role="progressbar" aria-valuenow={jobProgress} aria-valuemin={0} aria-valuemax={100}>
              <i style={{ width: `${jobProgress}%` }} />
            </div>
            <small>{formatNumber(jobProgress, 0)}% complete</small>
          </div>
          <label className="trace-toggle">
            <input type="checkbox" checked={showProgressTrace} onChange={(event) => setShowProgressTrace(event.target.checked)} />
            Show progress trace
          </label>
          {showProgressTrace ? (
            <div className="progress-trace-list">
              {progressEvents.length ? progressEvents.slice(-24).map((event, index) => (
                <ProgressTraceRow key={`${event.timestamp_utc}-${event.event_type}-${index}`} event={event} agentData={agentData} />
              )) : <div className="empty-state">No progress trace events have been emitted yet.</div>}
            </div>
          ) : null}
          {latestJobs.length ? (
            <div className="market-research-job-list">
              {latestJobs.map((job) => (
                <button key={job.id} type="button" className={job.id === activeJob?.id ? "job-history-row job-history-row--active" : "job-history-row"} onClick={() => setActiveJob(job)}>
                  <FileText size={15} />
                  <span>{String((job.request as { ticker?: unknown }).ticker ?? "ticker")}</span>
                  <Badge label={job.status} tone={statusTone(job.status)} />
                </button>
              ))}
            </div>
          ) : null}
        </Panel>
      </div>

      {report ? <ReportView report={report} /> : null}
    </div>
  );
}
