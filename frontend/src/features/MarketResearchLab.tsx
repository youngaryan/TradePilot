import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BarChart3, BrainCircuit, ChevronDown, FileText, History, Layers, Loader2, Play, RefreshCw, ShieldCheck, XCircle } from "lucide-react";

import { getChartData, getMarketResearchJob, getMarketResearchRuntime, listMarketResearchJobs, startMarketResearchJob } from "../api/client";
import type { MarketResearchAgentOutput, MarketResearchJob, MarketResearchReport, MarketResearchRuntimeConfig, MarketResearchSignal, MultiStockReport } from "../api/types";
import { Badge } from "../components/Badge";
import { MetricCard, Panel } from "../components/Cards";
import { decisionTone, formatDateTime, formatNumber, jobDisplayStatus, statusTone } from "../utils/format";
import { DecisionHistoryPanel } from "./DecisionHistoryPanel";
import { ResearchCharts } from "./ResearchCharts";
import { StockUniversePanel } from "./StockUniversePanel";

const DISCLAIMER = "For research and educational purposes only. Not financial advice.";

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

function isMultiStockReport(value: MarketResearchJob["result"]): value is MultiStockReport {
  return Boolean(value && Array.isArray((value as MultiStockReport).reports));
}

function isSingleStockReport(value: MarketResearchJob["result"]): value is MarketResearchReport {
  return Boolean(value && !isMultiStockReport(value) && Array.isArray((value as MarketResearchReport).raw_agent_outputs));
}

function MultiReportView({ report }: { report: MultiStockReport }) {
  const reports = report.reports ?? [];
  const averageConfidence = reports.length
    ? reports.reduce((total, item) => total + item.confidence, 0) / reports.length
    : null;
  const pairMetrics = report.cross_stock_analysis?.pair_metrics && typeof report.cross_stock_analysis.pair_metrics === "object"
    ? report.cross_stock_analysis.pair_metrics as Record<string, unknown>
    : null;

  return (
    <div className="market-research-report multi-stock-report">
      <Panel title="Multi-Stock Committee Summary" subtitle={report.pair ? `Pair ${report.pair.replace(",", " / ")}` : `${report.tickers.length} selected tickers`}>
        <p className="market-research-summary">{report.summary}</p>
        <div className="metric-grid metric-grid--small">
          <MetricCard label="Tickers" value={String(report.tickers.length)} detail={report.tickers.slice(0, 8).join(", ")} />
          <MetricCard label="Reports" value={String(reports.length)} detail={report.horizon} />
          {averageConfidence !== null ? (
            <MetricCard label="Avg Confidence" value={`${formatNumber(averageConfidence, 0)}/100`} tone={averageConfidence >= 70 ? "good" : "info"} />
          ) : null}
          {report.cross_stock_analysis?.divergence ? (
            <MetricCard label="Pair Divergence" value={String(report.cross_stock_analysis.divergence)} tone={report.cross_stock_analysis.divergence === "yes" ? "warn" : "neutral"} />
          ) : null}
        </div>
        {pairMetrics ? (
          <div className="decision-metrics">
            {Object.entries(pairMetrics).map(([key, value]) => (
              <span key={key} className="metric-tag">
                <strong>{key.replace(/_/g, " ")}</strong>
                <span>{typeof value === "number" ? formatNumber(value, 4) : String(value)}</span>
              </span>
            ))}
          </div>
        ) : null}
      </Panel>

      {reports.map((item) => (
        <section key={`${item.ticker}-${item.created_at_utc}`} className="multi-stock-report-section">
          <h3>{item.ticker}</h3>
          <ReportView report={item} />
        </section>
      ))}
    </div>
  );
}

function ResearchResultView({ result }: { result: MarketResearchReport | MultiStockReport }) {
  if (isMultiStockReport(result)) return <MultiReportView report={result} />;
  return <ReportView report={result} />;
}

export function MarketResearchLab() {
  const [tickerText, setTickerText] = useState("AAPL");
  const [analysisDate, setAnalysisDate] = useState("");
  const [horizon, setHorizon] = useState("swing");
  const [selectedModel, setSelectedModel] = useState("");
  const [showProgressTrace, setShowProgressTrace] = useState(false);
  const [showUniverse, setShowUniverse] = useState(false);
  const [showDecisions, setShowDecisions] = useState(false);
  const [pairMode, setPairMode] = useState(false);
  const [universeTickers, setUniverseTickers] = useState<string[]>([]);
  const [universePair, setUniversePair] = useState("");
  const [chartData, setChartData] = useState<Record<string, unknown>>({});
  const [showCharts, setShowCharts] = useState(false);
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
    setChartData({});
    setShowCharts(false);
    setError(null);
    setIsRunning(true);

    const [providerOverride, modelOverride] = selectedModel ? selectedModel.split("|", 2) : [null, null];

    try {
      if (pairMode && universeTickers.length === 2) {
        const job = await startMarketResearchJob({
          ticker: universeTickers[0],
          analysis_date: analysisDate || null,
          horizon,
          pair: `${universeTickers[0]},${universeTickers[1]}`,
          provider: providerOverride || null,
          model: modelOverride || null,
          options: {},
        });
        setActiveJob(job);
        setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      } else if (universeTickers.length > 1) {
        const job = await startMarketResearchJob({
          ticker: universeTickers[0],
          analysis_date: analysisDate || null,
          horizon,
          tickers: universeTickers,
          provider: providerOverride || null,
          model: modelOverride || null,
          options: {},
        });
        setActiveJob(job);
        setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      } else {
        const ticker = normalizeTicker(tickerText);
        if (!ticker) {
          setError("Enter a valid ticker symbol or select from universe.");
          setIsRunning(false);
          return;
        }
        setTickerText(ticker);
        const job = await startMarketResearchJob({
          ticker,
          analysis_date: analysisDate || null,
          horizon,
          provider: providerOverride || null,
          model: modelOverride || null,
          options: {},
        });
        setActiveJob(job);
        setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      }
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
      if (activeJob?.status === "completed" && activeJob.id) {
        void getChartData(activeJob.id).then((cd) => {
          setChartData(cd.charts);
          setShowCharts(true);
        }).catch(() => {});
      }
      setIsRunning(false);
      return undefined;
    }
    setIsRunning(true);
    const timer = window.setInterval(() => {
      void getMarketResearchJob(activeJob.id)
        .then((job) => {
          setActiveJob(job);
          setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
          if (!["queued", "running"].includes(job.status)) {
            setIsRunning(false);
            if (job.status === "completed") {
              void getChartData(job.id).then((cd) => {
                setChartData(cd.charts);
                setShowCharts(true);
              }).catch(() => {});
            }
            if (job.status === "failed" || job.status === "interrupted") setError(job.error || job.message || "Market research job failed.");
          }
        })
        .catch((caught) => {
          setIsRunning(false);
          setError(caught instanceof Error ? caught.message : "Unable to read market research job status.");
        });
    }, 1200);
    return () => window.clearInterval(timer);
  }, [activeJob?.id, activeJob?.status]);

  const activeResult = activeJob?.result ?? null;
  const displayResult = activeResult ?? jobs.find((job) => job.result)?.result ?? null;
  const agentData = useMemo(() => {
    const map: Record<string, MarketResearchAgentOutput> = {};
    const outputs: MarketResearchAgentOutput[][] = [];

    if (activeResult) {
      if (isSingleStockReport(activeResult)) {
        outputs.push(activeResult.raw_agent_outputs);
      } else if (isMultiStockReport(activeResult)) {
        for (const r of activeResult.reports) {
          outputs.push(r.raw_agent_outputs);
        }
      }
    }

    for (const job of jobs) {
      const jr = job.result;
      if (!jr || jr === activeResult) continue;
      if (isSingleStockReport(jr)) {
        outputs.push(jr.raw_agent_outputs);
      } else if (isMultiStockReport(jr)) {
        for (const r of jr.reports) {
          outputs.push(r.raw_agent_outputs);
        }
      }
    }

    for (const list of outputs) {
      for (const agent of list) {
        if (!map[agent.agent_name]) {
          map[agent.agent_name] = agent;
        }
      }
    }
    return map;
  }, [activeResult, jobs]);
  const jobProgress = Math.round((activeJob?.progress ?? (isRunning ? 0.04 : 0)) * 100);
  const activeRequest = activeJob?.request as { ticker?: unknown; tickers?: unknown; pair?: unknown; horizon?: unknown; analysis_date?: unknown } | undefined;
  const activeRequestLabel = activeRequest?.pair
    ? String(activeRequest.pair).replace(",", " / ")
    : Array.isArray(activeRequest?.tickers) && activeRequest.tickers.length
      ? activeRequest.tickers.slice(0, 4).map(String).join(", ")
      : String(activeRequest?.ticker ?? "Ticker");
  const jobTitle = activeJob
    ? `${activeRequestLabel} | ${String(activeRequest?.horizon ?? "swing")}`
    : "No research job selected";
  const jobStatus = jobDisplayStatus(activeJob);
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
        <Panel title="Research Committee" subtitle="Ticker, date, horizon, universe, and runtime configuration">
          <div className="market-research-config-grid">
            <label htmlFor="mr-ticker">
              {pairMode ? "Ticker A / Pair First" : "Ticker"}
              <input
                id="mr-ticker"
                value={pairMode && universeTickers.length > 0 ? universeTickers[0] : tickerText}
                onChange={(event) => {
                  if (pairMode && universeTickers.length > 0) {
                    setUniverseTickers([event.target.value.toUpperCase(), universeTickers[1] || ""].filter(Boolean));
                  } else {
                    setTickerText(event.target.value.toUpperCase());
                  }
                }}
                onBlur={() => {
                  if (!pairMode) setTickerText(normalizeTicker(tickerText));
                }}
                placeholder={pairMode ? "First ticker" : "e.g. AAPL"}
              />
            </label>
            {pairMode ? (
              <label htmlFor="mr-ticker-b">
                Ticker B / Pair Second
                <input
                  id="mr-ticker-b"
                  value={universeTickers.length > 1 ? universeTickers[1] : ""}
                  onChange={(event) => {
                    const first = universeTickers[0] || tickerText;
                    setUniverseTickers([first, event.target.value.toUpperCase()]);
                    setUniversePair(`${first},${event.target.value.toUpperCase()}`);
                  }}
                  placeholder="Second ticker"
                />
              </label>
            ) : null}
            <label htmlFor="mr-analysis-date">
              Analysis date
              <input id="mr-analysis-date" value={analysisDate} onChange={(event) => setAnalysisDate(event.target.value)} placeholder="Defaults to today" />
            </label>
            <label htmlFor="mr-horizon">
              Horizon
              <select id="mr-horizon" value={horizon} onChange={(event) => setHorizon(event.target.value)}>
                <option value="intraday">Intraday</option>
                <option value="swing">Swing</option>
                <option value="long-term">Long-term</option>
              </select>
            </label>
            <label htmlFor="mr-llm-model">
              LLM model
              <select id="mr-llm-model" value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)} disabled={!runtime?.model_override_enabled}>
                <option value="">Server default</option>
                {nvidiaModelOptions.map((model) => (
                  <option key={model.model} value={`${model.provider}|${model.model}`}>
                    {model.display_name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="button-row button-row--compact">
            <label className="toggle-label" htmlFor="mr-pair-mode">
              <input id="mr-pair-mode" type="checkbox" checked={pairMode} onChange={(e) => { setPairMode(e.target.checked); if (!e.target.checked) { setUniversePair(""); } }} />
              Pair research mode
            </label>
            <label className="toggle-label" htmlFor="mr-show-universe">
              <input id="mr-show-universe" type="checkbox" checked={showUniverse} onChange={(e) => setShowUniverse(e.target.checked)} />
              <Layers size={14} /> Stock universe
            </label>
            <label className="toggle-label" htmlFor="mr-show-decisions">
              <input id="mr-show-decisions" type="checkbox" checked={showDecisions} onChange={(e) => setShowDecisions(e.target.checked)} />
              <History size={14} /> Decision history
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
          <label className="trace-toggle" htmlFor="mr-show-trace">
            <input id="mr-show-trace" type="checkbox" checked={showProgressTrace} onChange={(event) => setShowProgressTrace(event.target.checked)} />
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
                  <span>{jobLabel(job)}</span>
                  <Badge label={jobDisplayStatus(job)} tone={statusTone(jobDisplayStatus(job))} />
                </button>
              ))}
            </div>
          ) : null}
        </Panel>
      </div>

      {showUniverse ? (
        <div className="content-grid">
          <StockUniversePanel
            selectedTickers={universeTickers}
            onSelectionChange={setUniverseTickers}
            pairMode={pairMode}
            onPairChange={(p) => {
              setUniversePair(p);
              const parts = p.split(",").filter(Boolean);
              if (parts.length >= 2) {
                setUniverseTickers(parts);
              }
            }}
          />
          <Panel title="Quick Stats" subtitle="Universe overview">
            <div className="metric-grid metric-grid--small">
              <MetricCard label="Selected" value={String(universeTickers.length)} detail={pairMode ? "pair mode" : "tickers"} />
              {universeTickers.length >= 2 ? (
                <MetricCard label="Mode" value={pairMode ? "Pair" : "Multi"} detail="Click run to start" tone="info" />
              ) : null}
            </div>
          </Panel>
        </div>
      ) : null}

      {showCharts && Object.keys(chartData).length > 0 ? (
        <ResearchCharts charts={chartData} />
      ) : null}

      {showDecisions ? (
        <DecisionHistoryPanel compact={true} />
      ) : null}

      {displayResult ? <ResearchResultView result={displayResult} /> : null}
    </div>
  );
}

function jobLabel(job: MarketResearchJob) {
  const request = job.request as { ticker?: unknown; tickers?: unknown; pair?: unknown };
  if (request.pair) return String(request.pair).replace(",", " / ");
  if (Array.isArray(request.tickers) && request.tickers.length) return request.tickers.slice(0, 3).map(String).join(", ");
  return String(request.ticker ?? "ticker");
}
