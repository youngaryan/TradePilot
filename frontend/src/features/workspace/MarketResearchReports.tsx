import { useEffect, useMemo, useState } from "react";
import { Download, FileText, Loader2, RefreshCw, Search, Trash2 } from "lucide-react";

import {
  deleteWorkspaceReport,
  exportWorkspaceReport,
  getWorkspaceReport,
  listWorkspaceReports,
  regenerateWorkspaceReport
} from "../../api/client";
import type {
  MarketResearchAuditEvent,
  MarketResearchReportDetail,
  MarketResearchReportSummary,
  MarketResearchSignal
} from "../../api/types";
import { Badge } from "../../components/Badge";
import { MetricCard, Panel } from "../../components/Cards";
import { DataTable } from "../../components/Table";
import { decisionTone, formatDateTime, formatNumber, statusTone, toNumber } from "../../utils/format";

const DISCLAIMER = "For research and educational purposes only. Not financial advice.";

function signalTone(signal: MarketResearchSignal): "good" | "bad" | "warn" | "neutral" {
  if (signal.direction === "bullish") return "good";
  if (signal.direction === "bearish") return "bad";
  if (signal.direction === "mixed") return "warn";
  return "neutral";
}

function asText(value: unknown, fallback = "n/a") {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "yes" : "no";
  return fallback;
}

function confidenceLabel(value: unknown) {
  const number = toNumber(value, NaN);
  return Number.isFinite(number) ? `${formatNumber(number, 0)}/100` : "n/a";
}

function providerLabel(detail: MarketResearchReportDetail | null) {
  const metadata = detail?.provider_metadata ?? detail?.report?.metadata ?? {};
  const provider = asText(metadata.llm_provider, "provider");
  const model = asText(metadata.llm_model, "model");
  return `${provider} / ${model}`;
}

export function MarketResearchReports({
  initialReports,
  onRefreshWorkspace
}: {
  initialReports: MarketResearchReportSummary[];
  onRefreshWorkspace?: () => Promise<void>;
}) {
  const [reports, setReports] = useState<MarketResearchReportSummary[]>(initialReports);
  const [activeId, setActiveId] = useState<string | null>(initialReports[0]?.id ?? null);
  const [detail, setDetail] = useState<MarketResearchReportDetail | null>(null);
  const [search, setSearch] = useState("");
  const [ticker, setTicker] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);
  const [isBusy, setIsBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setReports(initialReports);
    setActiveId((current) => current ?? initialReports[0]?.id ?? null);
  }, [initialReports]);

  useEffect(() => {
    if (!activeId) {
      setDetail(null);
      return;
    }
    void loadDetail(activeId);
  }, [activeId]);

  const sortedReports = useMemo(
    () => [...reports].sort((left, right) => String(right.created_at_utc).localeCompare(String(left.created_at_utc))),
    [reports]
  );
  const pagedReports = sortedReports.slice(page * 10, page * 10 + 10);
  const activeReport = detail?.report;

  async function loadReports(nextPage = page) {
    setIsBusy(true);
    setError(null);
    try {
      const next = await listWorkspaceReports({
        search: search || undefined,
        ticker: ticker || undefined,
        status: status || undefined,
        limit: 10,
        offset: nextPage * 10
      });
      setReports(next);
      setPage(nextPage);
      if (next.length && !next.some((report) => report.id === activeId)) setActiveId(next[0].id);
      if (!next.length) setActiveId(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load reports.");
    } finally {
      setIsBusy(false);
    }
  }

  async function loadDetail(reportId: string) {
    setError(null);
    try {
      setDetail(await getWorkspaceReport(reportId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load report detail.");
      setDetail(null);
    }
  }

  async function handleRegenerate(reportId: string) {
    setIsBusy(true);
    setNotice(null);
    setError(null);
    try {
      const job = await regenerateWorkspaceReport(reportId);
      setNotice(`Regeneration queued for report ${job.report_id ?? job.id}.`);
      await loadReports(0);
      await onRefreshWorkspace?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not regenerate report.");
    } finally {
      setIsBusy(false);
    }
  }

  async function handleDelete(reportId: string) {
    if (!window.confirm("Delete this saved market research report?")) return;
    setIsBusy(true);
    setNotice(null);
    setError(null);
    try {
      await deleteWorkspaceReport(reportId);
      setNotice("Report deleted.");
      await loadReports(0);
      await onRefreshWorkspace?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not delete report.");
    } finally {
      setIsBusy(false);
    }
  }

  async function handleExport(reportId: string) {
    setIsBusy(true);
    setNotice(null);
    setError(null);
    try {
      const payload = await exportWorkspaceReport(reportId);
      const blob = new Blob([JSON.stringify(payload.report, null, 2)], { type: "application/json" });
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = `${payload.report.ticker}-${payload.report.analysis_date}-market-research.json`;
      link.click();
      URL.revokeObjectURL(href);
      setNotice("Report export prepared.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not export report.");
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <div className="reports-workspace">
      <section className="alert-card alert-card--info reports-disclaimer">
        <FileText size={18} />
        <span>{DISCLAIMER}</span>
      </section>

      {notice ? (
        <section className="alert-card alert-card--good">
          <span>{notice}</span>
        </section>
      ) : null}
      {error ? (
        <section className="alert-card">
          <span>{error}</span>
        </section>
      ) : null}

      <div className="report-toolbar">
        <label htmlFor="reports-search">
          Search
          <input id="reports-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Ticker, title, summary" />
        </label>
        <label htmlFor="reports-ticker">
          Ticker
          <input id="reports-ticker" value={ticker} onChange={(event) => setTicker(event.target.value.toUpperCase())} placeholder="AAPL" />
        </label>
        <label htmlFor="reports-status">
          Status
          <select id="reports-status" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All</option>
            <option value="queued">Queued</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
        </label>
        <button type="button" className="primary-button" onClick={() => void loadReports(0)} disabled={isBusy}>
          {isBusy ? <Loader2 size={16} className="spin" /> : <Search size={16} />}
          Search reports
        </button>
      </div>

      <div className="grid-two grid-two--wide-left reports-layout">
        <Panel title="Report history" subtitle="Saved, user-scoped AI committee research reports.">
          <DataTable
            empty="No market research reports have been saved yet."
            getKey={(report) => report.id}
            columns={[
              {
                key: "title",
                header: "Report",
                render: (report) => (
                  <button type="button" className="link-button report-title-button" onClick={() => setActiveId(report.id)}>
                    {report.title}
                  </button>
                )
              },
              { key: "ticker", header: "Ticker", render: (report) => report.ticker },
              { key: "decision", header: "Decision", render: (report) => <Badge label={report.decision ?? "pending"} tone={decisionTone(report.decision)} /> },
              { key: "confidence", header: "Confidence", align: "right", render: (report) => confidenceLabel(report.confidence) },
              { key: "status", header: "Status", render: (report) => <Badge label={report.status} tone={statusTone(report.status)} /> },
              { key: "created", header: "Created", render: (report) => formatDateTime(report.created_at_utc) },
              {
                key: "actions",
                header: "Actions",
                render: (report) => (
                  <div className="report-row-actions">
                    <button type="button" className="icon-button" aria-label={`Regenerate ${report.title}`} onClick={() => void handleRegenerate(report.id)}>
                      <RefreshCw size={15} />
                    </button>
                    <button type="button" className="icon-button" aria-label={`Export ${report.title}`} onClick={() => void handleExport(report.id)}>
                      <Download size={15} />
                    </button>
                    <button type="button" className="icon-button icon-button--danger" aria-label={`Delete ${report.title}`} onClick={() => void handleDelete(report.id)}>
                      <Trash2 size={15} />
                    </button>
                  </div>
                )
              }
            ]}
            rows={pagedReports}
          />
          <div className="pagination-row">
            <button type="button" className="ghost-button" disabled={page === 0 || isBusy} onClick={() => void loadReports(Math.max(0, page - 1))}>
              Previous
            </button>
            <span>Page {page + 1}</span>
            <button type="button" className="ghost-button" disabled={reports.length < 10 || isBusy} onClick={() => void loadReports(page + 1)}>
              Next
            </button>
          </div>
        </Panel>

        <Panel title={detail?.title ?? "Report detail"} subtitle={detail ? providerLabel(detail) : "Select a report to view its full research trail."}>
          {detail && activeReport ? (
            <div className="detail-stack report-detail">
              <div className="metric-grid metric-grid--compact">
                <MetricCard label="Decision" value={detail.decision ?? "Pending"} detail={detail.horizon} tone={decisionTone(detail.decision)} />
                <MetricCard label="Confidence" value={confidenceLabel(detail.confidence)} detail={detail.status} tone={decisionTone(detail.decision)} />
                <MetricCard label="Sources" value={formatNumber(detail.source_references.length, 0)} detail="references" />
                <MetricCard label="Warnings" value={formatNumber(detail.warnings.length, 0)} detail="data and agent caveats" tone={detail.warnings.length ? "warn" : "good"} />
              </div>

              <section className="report-summary-box">
                <h4>Executive Summary</h4>
                <p>{activeReport.summary}</p>
                <span>{activeReport.disclaimer}</span>
              </section>

              <div className="grid-two">
                <section className="report-text-section">
                  <h4>Bull Thesis</h4>
                  <p>{activeReport.bull_thesis}</p>
                </section>
                <section className="report-text-section">
                  <h4>Bear Thesis</h4>
                  <p>{activeReport.bear_thesis}</p>
                </section>
              </div>

              <DataTable
                empty="No committee signals were returned."
                getKey={(signal, index) => `${signal.label}-${index}`}
                columns={[
                  { key: "signal", header: "Signal", render: (signal) => signal.label },
                  { key: "direction", header: "Direction", render: (signal) => <Badge label={signal.direction} tone={signalTone(signal)} /> },
                  { key: "strength", header: "Strength", align: "right", render: (signal) => formatNumber(signal.strength, 0) },
                  { key: "rationale", header: "Rationale", render: (signal) => signal.rationale }
                ]}
                rows={[...(activeReport.technical_signals ?? []), ...(activeReport.fundamental_signals ?? []), ...(activeReport.news_sentiment_signals ?? [])]}
              />

              <DataTable
                empty="No sentiment matrix rows are attached to this report."
                getKey={(row, index) => `${asText(row.date)}-${index}`}
                columns={[
                  { key: "date", header: "Date", render: (row) => asText(row.date) },
                  { key: "sentiment", header: "Sentiment", align: "right", render: (row) => formatNumber(row.sentiment_score ?? row.score, 3) },
                  { key: "confidence", header: "Confidence", align: "right", render: (row) => formatNumber(row.confidence, 2) },
                  { key: "articles", header: "Articles", align: "right", render: (row) => formatNumber(row.article_count ?? row.headline_count, 0) }
                ]}
                rows={(activeReport.sentiment_matrix ?? []).slice(-25)}
              />

              <DataTable
                empty="No financial-event rows are attached to this report."
                getKey={(row, index) => `${asText(row.id)}-${index}`}
                columns={[
                  { key: "date", header: "Date", render: (row) => asText(row.date) },
                  { key: "event", header: "Event", render: (row) => asText(row.event_type_label ?? row.event_type) },
                  { key: "direction", header: "Direction", render: (row) => asText(row.event_direction) },
                  { key: "confidence", header: "Confidence", align: "right", render: (row) => formatNumber(row.confidence, 2) },
                  { key: "summary", header: "Summary", render: (row) => asText(row.summary ?? row.event_title) }
                ]}
                rows={(activeReport.financial_events_matrix ?? []).slice(0, 25)}
              />

              <DataTable
                empty="No source references were attached."
                getKey={(source) => source.id}
                columns={[
                  { key: "source", header: "Source", render: (source) => source.source },
                  { key: "provider", header: "Provider", render: (source) => source.provider },
                  { key: "verified", header: "Verified", render: (source) => <Badge label={source.verified ? "verified" : "context"} tone={source.verified ? "good" : "neutral"} /> },
                  { key: "title", header: "Title", render: (source) => source.url ? <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> : source.title }
                ]}
                rows={detail.source_references.slice(0, 40)}
              />

              <DataTable
                empty="No audit trail was persisted."
                getKey={(event: MarketResearchAuditEvent) => event.agent_name}
                columns={[
                  { key: "agent", header: "Agent", render: (event) => event.display_name },
                  { key: "status", header: "Status", render: (event) => <Badge label={event.status} tone={statusTone(event.status)} /> },
                  { key: "duration", header: "Duration", align: "right", render: (event) => `${formatNumber(event.duration_ms, 0)}ms` },
                  { key: "warnings", header: "Warnings", align: "right", render: (event) => formatNumber(event.warnings.length, 0) }
                ]}
                rows={activeReport.audit_trail ?? []}
              />

              <div className="code-split">
                <pre>{JSON.stringify(activeReport.data_freshness ?? {}, null, 2)}</pre>
                <pre>{JSON.stringify(detail.provider_metadata ?? {}, null, 2)}</pre>
              </div>
            </div>
          ) : (
            <div className="empty-state chart-empty">Select a saved report to inspect the committee output, matrices, provenance, and audit trail.</div>
          )}
        </Panel>
      </div>
    </div>
  );
}
