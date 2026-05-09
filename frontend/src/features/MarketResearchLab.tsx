import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BrainCircuit, FileText, Loader2, Play, RefreshCw, ShieldCheck } from "lucide-react";

import { getMarketResearchJob, getMarketResearchRuntime, listMarketResearchJobs, startMarketResearchJob } from "../api/client";
import type { MarketResearchJob, MarketResearchReport, MarketResearchRuntimeConfig, MarketResearchSignal } from "../api/types";
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
        <article key={`${signal.label}-${signal.rationale}`} className="market-research-signal">
          <div>
            <strong>{signal.label.replaceAll("_", " ")}</strong>
            <span>{signal.rationale}</span>
          </div>
          <div className="signal-score">
            <Badge label={signal.direction} tone={directionTone(signal.direction)} />
            <strong>{formatNumber(signal.strength, 0)}</strong>
          </div>
          {signal.evidence.length ? (
            <ul>
              {signal.evidence.slice(0, 3).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : null}
        </article>
      ))}
    </div>
  );
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
        <MetricCard label="Confidence" value={`${formatNumber(report.confidence, 0)}/100`} detail={`Created ${formatDateTime(report.created_at_utc)}`} tone="info" />
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
        <div className="warning-list warning-list--compact">
          {warningItems.map((warning, index) => (
            <span key={`${index}-${warning}`}>{warning}</span>
          ))}
        </div>
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
    try {
      const job = await startMarketResearchJob({
        ticker,
        analysis_date: analysisDate || null,
        horizon,
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

  const report = activeJob?.result ?? jobs.find((job) => job.result)?.result ?? null;
  const jobProgress = Math.round((activeJob?.progress ?? (isRunning ? 0.04 : 0)) * 100);
  const activeRequest = activeJob?.request as { ticker?: unknown; horizon?: unknown; analysis_date?: unknown } | undefined;
  const jobTitle = activeJob
    ? `${String(activeRequest?.ticker ?? "Ticker")} | ${String(activeRequest?.horizon ?? "swing")}`
    : "No research job selected";
  const jobStatus = activeJob?.status ?? "idle";
  const latestJobs = useMemo(() => jobs.slice(0, 8), [jobs]);
  const runtimeWarnings = uniqueWarnings([...(runtime?.warnings ?? []), runtime?.ollama?.error ?? null]);

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
