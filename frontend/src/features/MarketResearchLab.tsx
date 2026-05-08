import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BrainCircuit, FileText, Loader2, Play, RefreshCw, ShieldCheck } from "lucide-react";

import { getMarketResearchJob, listMarketResearchJobs, startMarketResearchJob } from "../api/client";
import type { MarketResearchJob, MarketResearchReport, MarketResearchSignal } from "../api/types";
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
          {[...report.data_quality_notes, ...report.warnings].filter(Boolean).map((warning, index) => (
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
  const [error, setError] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);

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
        provider: "mock",
        model: "mock-research-v1",
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

  return (
    <div className="market-research-lab">
      <section className="research-disclaimer">
        <ShieldCheck size={18} />
        <strong>{DISCLAIMER}</strong>
      </section>

      <div className="content-grid">
        <Panel title="Research Committee" subtitle="Ticker, date, horizon, and model metadata">
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
          <div className="button-row">
            <button type="button" className="primary-button" onClick={() => void runResearch()} disabled={isRunning}>
              {isRunning ? <Loader2 size={17} /> : <Play size={17} />}
              {isRunning ? "Running committee" : "Run research committee"}
            </button>
            <button type="button" className="ghost-button" onClick={() => void refreshJobs()}>
              <RefreshCw size={17} />
              Refresh jobs
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
