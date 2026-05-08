import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BrainCircuit, DatabaseZap, FileText, Loader2, Newspaper, Play, RefreshCw } from "lucide-react";

import { getFinancialEvents, getSentimentAccumulationJob, getSentimentDataset, startSentimentAccumulationJob } from "../api/client";
import type { FinancialEventRecord, FinancialEventsPayload, SentimentAccumulationJob, SentimentAccumulationRequest, SentimentDailyPoint, SentimentDatasetPayload } from "../api/types";
import { Badge } from "../components/Badge";
import { SentimentHeatmapChart, SentimentSourceBars, SentimentTickerBars, SentimentTimelineChart } from "../components/Charts";
import { Explainer, MetricCard, Panel } from "../components/Cards";
import { DataTable } from "../components/Table";
import { formatDateTime, formatNumber, formatPercent, splitList, splitSymbols, statusTone, toNumber } from "../utils/format";

const DEFAULT_NEWS_FILE = "examples/news_headlines.sample.csv";
const TABLE_PAGE_SIZE = 12;
const SOURCE_OPTIONS = [
  { id: "rss", label: "RSS firehose", detail: "Free ticker RSS feeds; default source for live headline flow." },
  { id: "local_web", label: "Local web search", detail: "Cached RSS/page index; avoids hosted search API rate limits." },
  { id: "web", label: "GDELT web research", detail: "Broad web discovery; optional because public search APIs can throttle." },
  { id: "local", label: "Local files", detail: "CSV/parquet headline files; useful for samples and accumulated dumps." },
  { id: "newsapi", label: "NewsAPI", detail: "Optional API-key supplement for broader publisher coverage." },
  { id: "alphavantage", label: "Alpha Vantage", detail: "Optional free-tier news sentiment source for top-priority tickers." },
  { id: "benzinga", label: "Benzinga", detail: "Optional market-news API source when you have a key." }
];

function isoDate(daysFromToday = 0) {
  const date = new Date();
  date.setDate(date.getDate() + daysFromToday);
  return date.toISOString().slice(0, 10);
}

function liveRssDateRange(days = 14) {
  return { start: isoDate(-days), end: isoDate(0) };
}

function defaultRequest(): SentimentAccumulationRequest {
  const liveDates = liveRssDateRange();
  return {
    symbols: ["AAPL", "MSFT", "NVDA", "GLD"],
    start: liveDates.start,
    end: liveDates.end,
    providers: ["rss", "local_web", "local"],
    rss_feed_urls: [],
    local_web_search_urls: [],
    local_web_refresh_minutes: 60,
    local_web_max_pages_per_source: 30,
    web_research_urls: [],
    web_research_domains: [],
    web_research_query_terms: "",
    web_research_max_articles: 4,
    web_research_fetch_article_text: true,
    news_files: [DEFAULT_NEWS_FILE],
    newsapi_api_key: null,
    alphavantage_api_key: null,
    benzinga_api_key: null,
    use_finbert: false,
    local_finbert_only: true
  };
}

function toggleValue(values: string[], value: string, enabled: boolean) {
  if (enabled) return Array.from(new Set([...values, value]));
  return values.filter((item) => item !== value);
}

function dateKey(value: unknown) {
  return String(value ?? "").slice(0, 10);
}

function rowMatchesFilters(
  row: Record<string, unknown>,
  filters: {
    tickers: string[];
    start: string;
    end: string;
    source: string;
    text: string;
  }
) {
  const ticker = String(row.ticker ?? "").toUpperCase();
  const source = String(row.source ?? row.provider_name ?? "");
  const rowDate = dateKey(row.timestamp ?? row.date);
  const text = [row.headline, row.title, row.summary, row.label].map((value) => String(value ?? "")).join(" ").toLowerCase();

  if (filters.tickers.length && !filters.tickers.includes(ticker)) return false;
  if (filters.start && rowDate && rowDate < filters.start) return false;
  if (filters.end && rowDate && rowDate > filters.end) return false;
  if (filters.source !== "ALL" && source !== filters.source) return false;
  if (filters.text.trim() && !text.includes(filters.text.trim().toLowerCase())) return false;
  return true;
}

function paginate<T>(rows: T[], page: number) {
  const pageCount = Math.max(1, Math.ceil(rows.length / TABLE_PAGE_SIZE));
  const safePage = Math.min(Math.max(page, 1), pageCount);
  const start = (safePage - 1) * TABLE_PAGE_SIZE;
  return {
    page: safePage,
    pageCount,
    pageRows: rows.slice(start, start + TABLE_PAGE_SIZE)
  };
}

function metadataList(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean).join(" ");
  if (typeof value === "string") return value;
  return "";
}

function PaginationControls({
  page,
  pageCount,
  totalRows,
  onPageChange
}: {
  page: number;
  pageCount: number;
  totalRows: number;
  onPageChange: (page: number) => void;
}) {
  if (totalRows <= TABLE_PAGE_SIZE) return <span className="table-count">{formatNumber(totalRows, 0)} rows</span>;
  return (
    <div className="pagination-controls">
      <span>
        Page {page} of {pageCount} | {formatNumber(totalRows, 0)} rows
      </span>
      <button type="button" className="ghost-button" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
        Previous
      </button>
      <button type="button" className="ghost-button" disabled={page >= pageCount} onClick={() => onPageChange(page + 1)}>
        Next
      </button>
    </div>
  );
}

function optionalText(value: string | null | undefined, fallback = "Unavailable") {
  const trimmed = String(value ?? "").trim();
  return trimmed || fallback;
}

function beatMissTone(status: string) {
  if (status === "beat") return "good" as const;
  if (status === "miss") return "bad" as const;
  if (status === "inline") return "info" as const;
  return "neutral" as const;
}

function directionTone(direction: string) {
  if (direction === "positive") return "good" as const;
  if (direction === "negative") return "bad" as const;
  return "neutral" as const;
}

function completenessTone(level: string) {
  if (level === "high") return "good" as const;
  if (level === "medium") return "info" as const;
  if (level === "low") return "warn" as const;
  return "neutral" as const;
}

function FinancialEventCard({ row }: { row: FinancialEventRecord }) {
  return (
    <article className="financial-event-card">
      <div className="financial-event-card__top">
        <div className="financial-event-date">
          <strong>{row.ticker}</strong>
          <span>{row.date}</span>
        </div>
        <span className={`financial-event-type financial-event-type--${directionTone(row.event_direction)}`}>
          {row.event_type_label}
        </span>
      </div>

      <div className="financial-event-card__body">
        <strong>{row.event_title}</strong>
        <p>{row.summary}</p>
      </div>

      <div className="financial-event-field-grid">
        <div className="financial-event-field">
          <span>Reported</span>
          <strong>{optionalText(row.reported_result)}</strong>
        </div>
        <div className="financial-event-field">
          <span>Expected</span>
          <strong>{optionalText(row.expected_result)}</strong>
        </div>
        <div className="financial-event-field">
          <span>Status</span>
          <Badge label={row.beat_miss === "not_available" ? "no consensus" : row.beat_miss} tone={beatMissTone(row.beat_miss)} />
        </div>
        <div className="financial-event-field">
          <span>Market</span>
          <strong>{optionalText(row.market_reaction)}</strong>
        </div>
      </div>

      <div className="financial-event-card__footer">
        <span>{row.form ?? row.source}</span>
        {row.source_url ? (
          <a className="source-link" href={row.source_url} target="_blank" rel="noreferrer">
            Source
          </a>
        ) : (
          <span>{row.source}</span>
        )}
        <Badge label={`${row.data_completeness} ${formatPercent(row.confidence, 0)}`} tone={completenessTone(row.data_completeness)} />
      </div>
    </article>
  );
}

function FinancialEventsMatrix({
  payload,
  isLoading,
  error,
  page,
  onPageChange
}: {
  payload: FinancialEventsPayload | null;
  isLoading: boolean;
  error: string | null;
  page: number;
  onPageChange: (page: number) => void;
}) {
  const rows = payload?.events ?? [];
  const pagination = paginate(rows, page);
  return (
    <div className="financial-events-matrix">
      <div className="table-toolbar">
        <strong>
          {payload?.request.symbols.length ? payload.request.symbols.join(" + ") : "No company selected"}
        </strong>
        <PaginationControls page={pagination.page} pageCount={pagination.pageCount} totalRows={rows.length} onPageChange={onPageChange} />
      </div>
      {isLoading ? (
        <div className="inline-loading">
          <Loader2 size={16} />
          Loading verified financial events
        </div>
      ) : null}
      {error ? (
        <div className="inline-error">
          <AlertTriangle size={16} />
          {error}
        </div>
      ) : null}
      {pagination.pageRows.length ? (
        <div className="financial-event-list">
          {pagination.pageRows.map((row) => (
            <FinancialEventCard key={row.id} row={row} />
          ))}
        </div>
      ) : (
        <div className="empty-state financial-events-empty">
          {isLoading ? "Loading financial events." : "No verified financial events were found for the selected symbols and dates."}
        </div>
      )}
    </div>
  );
}

function sentimentAverage(points: SentimentDailyPoint[], tickers: string[]) {
  const tickerSet = new Set(tickers.map((ticker) => ticker.toUpperCase()));
  const relevant = tickerSet.size ? points.filter((point) => tickerSet.has(String(point.ticker).toUpperCase())) : points;
  const weighted = relevant.reduce(
    (state, point) => {
      const weight = Math.max(1, toNumber(point.article_count, 1));
      state.score += toNumber(point.sentiment_score) * weight;
      state.weight += weight;
      return state;
    },
    { score: 0, weight: 0 }
  );
  return weighted.weight ? weighted.score / weighted.weight : null;
}

function financialDirectionSummary(events: FinancialEventRecord[]) {
  return events.reduce(
    (state, event) => {
      const direction = event.event_direction === "positive" || event.event_direction === "negative" ? event.event_direction : "neutral";
      state[direction] += 1;
      return state;
    },
    { positive: 0, negative: 0, neutral: 0 }
  );
}

function financialSentimentComparison(payload: FinancialEventsPayload | null, points: SentimentDailyPoint[], tickers: string[]) {
  if (!payload?.events.length) return ["Inferred: no financial-event comparison is possible until verified event rows are available."];
  const average = sentimentAverage(points, tickers);
  if (average === null) return ["Inferred: verified financial events are available, but no matching sentiment rows are loaded for the current ticker filter."];
  const directions = financialDirectionSummary(payload.events);
  const financialSkew = directions.positive > directions.negative ? "positive" : directions.negative > directions.positive ? "negative" : "mixed";
  const sentimentSkew = average > 0.05 ? "positive" : average < -0.05 ? "negative" : "neutral";
  if (financialSkew === "mixed" || sentimentSkew === "neutral") {
    return [`Inferred: sentiment averages ${formatNumber(average)} while verified financial events are ${financialSkew}; the evidence is not strongly aligned or contradictory.`];
  }
  return [
    `Inferred: sentiment averages ${formatNumber(average)} and financial events skew ${financialSkew}; this ${financialSkew === sentimentSkew ? "supports" : "contradicts"} the current sentiment read.`
  ];
}

function AnalysisList({ title, rows, empty }: { title: string; rows: string[]; empty: string }) {
  return (
    <div className="analysis-list">
      <strong>{title}</strong>
      {rows.length ? (
        <ul>
          {rows.map((row) => (
            <li key={row}>{row}</li>
          ))}
        </ul>
      ) : (
        <span>{empty}</span>
      )}
    </div>
  );
}

export function SentimentLab() {
  const [request, setRequest] = useState<SentimentAccumulationRequest>(() => defaultRequest());
  const [symbolsText, setSymbolsText] = useState(() => defaultRequest().symbols.join(" "));
  const [dataset, setDataset] = useState<SentimentDatasetPayload | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [activeJob, setActiveJob] = useState<SentimentAccumulationJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedTickers, setSelectedTickers] = useState<string[]>([]);
  const [tickerSearch, setTickerSearch] = useState("");
  const [selectedSource, setSelectedSource] = useState("ALL");
  const [chartStart, setChartStart] = useState("");
  const [chartEnd, setChartEnd] = useState("");
  const [headlineSearch, setHeadlineSearch] = useState("");
  const [headlinePage, setHeadlinePage] = useState(1);
  const [scoredPage, setScoredPage] = useState(1);
  const [financialEvents, setFinancialEvents] = useState<FinancialEventsPayload | null>(null);
  const [financialEventsLoading, setFinancialEventsLoading] = useState(false);
  const [financialEventsError, setFinancialEventsError] = useState<string | null>(null);
  const [financialEventsPage, setFinancialEventsPage] = useState(1);

  async function refreshDataset(datasetId?: string | null) {
    setError(null);
    try {
      setDataset(await getSentimentDataset(datasetId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load sentiment dataset.");
    }
  }

  async function runAccumulator() {
    setError(null);
    const finalSymbols = splitSymbols(symbolsText);
    if (!finalSymbols.length) {
      setError("Add at least one symbol before accumulating sentiment.");
      return;
    }
    if (!request.providers.length) {
      setError("Select at least one sentiment source.");
      return;
    }
    if (request.providers.includes("local") && !request.news_files.length) {
      setError("Local sentiment source needs at least one news file, for example examples/news_headlines.sample.csv.");
      return;
    }
    setIsRunning(true);
    try {
      const finalRequest = { ...request, symbols: finalSymbols };
      setRequest(finalRequest);
      setSymbolsText(finalSymbols.join(" "));
      const job = await startSentimentAccumulationJob(finalRequest);
      setActiveJob(job);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to accumulate sentiment.");
      setIsRunning(false);
      setActiveJob(null);
    }
  }

  function updateSymbolsText(value: string) {
    setSymbolsText(value);
    setRequest((current) => ({ ...current, symbols: splitSymbols(value) }));
  }

  function commitSymbolsText() {
    const nextSymbols = splitSymbols(symbolsText);
    setSymbolsText(nextSymbols.join(" "));
    setRequest((current) => ({ ...current, symbols: nextSymbols }));
  }

  useEffect(() => {
    if (!activeJob || !["queued", "running"].includes(activeJob.status)) return;
    setIsRunning(true);
    const timer = window.setInterval(() => {
      void getSentimentAccumulationJob(activeJob.id)
        .then((job) => {
          setActiveJob(job);
          if (job.status === "completed") {
            setIsRunning(false);
            if (job.result) {
              setDataset(job.result);
            } else {
              void refreshDataset();
            }
          }
          if (job.status === "failed" || job.status === "interrupted") {
            setIsRunning(false);
            setError(job.error || job.message || "Sentiment accumulation failed.");
          }
        })
        .catch((caught) => {
          setIsRunning(false);
          setError(caught instanceof Error ? caught.message : "Unable to read sentiment job status.");
        });
    }, 1200);
    return () => window.clearInterval(timer);
  }, [activeJob]);

  useEffect(() => {
    if (activeJob?.status === "completed" || activeJob?.status === "failed" || activeJob?.status === "interrupted") {
      setIsRunning(false);
    }
  }, [activeJob]);

  const jobIsRunning = activeJob ? ["queued", "running"].includes(activeJob.status) : isRunning;
  const jobProgress = Math.round((activeJob?.progress ?? (isRunning ? 0.04 : 0)) * 100);
  const jobStage = activeJob?.stage?.replaceAll("_", " ") ?? "queued";
  const jobWarnings = Array.isArray(activeJob?.warnings) ? activeJob.warnings.filter(Boolean) : [];
  const jobRequest = activeJob?.request as { symbols?: unknown; providers?: unknown } | undefined;
  const jobSymbols = Array.isArray(jobRequest?.symbols) ? jobRequest.symbols.map(String).join(" ") : request.symbols.join(" ");
  const jobProviders = Array.isArray(jobRequest?.providers) ? jobRequest.providers.map(String).join(" + ") : request.providers.join(" + ");
  const jobDatasetId = activeJob?.result?.dataset_id ?? dataset?.dataset_id ?? "latest tenant sentiment dataset";
  const jobStarted = activeJob?.started_at_utc ?? activeJob?.created_at_utc;
  const jobFinished = activeJob?.finished_at_utc;
  const jobRuntime = jobStarted ? (jobFinished ? `${formatDateTime(jobStarted)} to ${formatDateTime(jobFinished)}` : `Started ${formatDateTime(jobStarted)}`) : "Not started yet";
  const jobHasWarnings = jobWarnings.length > 0;
  const jobHeadline = activeJob?.status === "completed"
    ? jobHasWarnings
      ? "Sentiment dataset updated with warnings"
      : "Sentiment dataset updated"
    : activeJob?.status === "failed" || activeJob?.status === "interrupted"
      ? "Sentiment run needs attention"
      : "Sentiment run in progress";
  const jobResultPath = activeJob?.result?.daily_sentiment_path ?? jobDatasetId;
  const jobSummary = activeJob?.result?.summary
    ? `${formatNumber(activeJob.result.summary.headline_count ?? 0, 0)} headlines, ${formatNumber(activeJob.result.summary.scored_headline_count ?? 0, 0)} scored rows, ${formatNumber(activeJob.result.summary.daily_rows ?? 0, 0)} daily rows.`
    : `Writing to ${jobResultPath}`;
  const jobSteps = ["preparing", "loading", "fetching", "deduplicating", "scoring", "saving"];

  async function refreshWithJobContext() {
    if (activeJob?.status === "completed" && activeJob.result) {
      setDataset(activeJob.result);
      return;
    }
    await refreshDataset();
  }

  function jobStepState(step: string) {
    if (activeJob?.status === "completed") return "done";
    if (activeJob?.status === "failed" || activeJob?.status === "interrupted") return jobStage.includes(step) ? "failed" : "pending";
    return jobStage.includes(step) ? "active" : "pending";
  }

  function sentimentJobProgressCard() {
    if (!activeJob && !isRunning) return null;
    return (
      <div className={`job-progress-card ${activeJob?.status === "completed" ? "job-progress-card--done" : ""} ${activeJob?.status === "completed" && jobHasWarnings ? "job-progress-card--warning" : ""} ${activeJob?.status === "failed" || activeJob?.status === "interrupted" ? "job-progress-card--failed" : ""}`}>
        <div className="progress-card__top">
          <div>
            <strong>{jobHeadline}</strong>
            <span>{jobStage} | {jobRuntime}</span>
          </div>
          <div className="badge-row">
            <Badge label={activeJob?.status === "completed" && jobHasWarnings ? "warnings" : activeJob?.status ?? "queued"} tone={activeJob?.status === "completed" && jobHasWarnings ? "warn" : statusTone(activeJob?.status)} />
            <strong>{jobProgress}%</strong>
          </div>
        </div>
        <div className="progress-track" role="progressbar" aria-valuenow={jobProgress} aria-valuemin={0} aria-valuemax={100}>
          <i style={{ width: `${jobProgress}%` }} />
        </div>
        <p>{activeJob?.message ?? "Starting sentiment accumulation."}</p>
        <small>{jobProviders} | {jobSymbols} | {jobDatasetId}</small>
        <div className="job-step-row">
          {jobSteps.map((step) => (
            <span key={step} className={`job-step job-step--${jobStepState(step)}`}>
              {jobStepState(step) === "active" ? `${step} now` : step}
            </span>
          ))}
        </div>
        <div className="artifact-note">
          <strong>Result file:</strong>
          <span>{jobResultPath}</span>
          <span>{jobSummary}</span>
        </div>
        {activeJob?.error ? (
          <div className="inline-error">
            <AlertTriangle size={16} />
            {activeJob.error}
          </div>
        ) : null}
        {jobWarnings.length ? (
          <div className="inline-warning">
            <AlertTriangle size={16} />
            <span>{jobWarnings.join(" ")}</span>
          </div>
        ) : null}
        <div className="button-row">
          <button type="button" className="ghost-button" onClick={() => void refreshWithJobContext()}>
            <RefreshCw size={17} />
            {activeJob?.status === "completed" ? "Load completed dataset" : "Refresh dataset"}
          </button>
          {activeJob && !["queued", "running"].includes(activeJob.status) ? (
            <button type="button" className="ghost-button" onClick={() => setActiveJob(null)}>
              Dismiss status
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  useEffect(() => {
    void refreshDataset();
  }, []);

  const latestMetadata = useMemo(() => dataset?.metadata ?? {}, [dataset]);
  const lastRunTickers = metadataList(latestMetadata.tickers);
  const lastRunProviders = metadataList(latestMetadata.providers);
  const fetchedHeadlineCount = latestMetadata.fetched_headlines ?? 0;
  const storedHeadlineCount = latestMetadata.stored_headlines ?? dataset?.summary.headline_count ?? 0;
  const returnedHeadlineCount = dataset?.summary.returned_headline_count ?? dataset?.headlines.length ?? 0;
  const storedWarnings = dataset?.warnings ?? [];
  const availableTickers = useMemo(() => {
    const tickers = new Set<string>();
    for (const point of dataset?.daily_points ?? []) tickers.add(String(point.ticker).toUpperCase());
    for (const row of dataset?.headlines ?? []) if (row.ticker) tickers.add(String(row.ticker).toUpperCase());
    for (const row of dataset?.scored_headlines ?? []) if (row.ticker) tickers.add(String(row.ticker).toUpperCase());
    return Array.from(tickers).sort();
  }, [dataset]);
  const availableSources = useMemo(() => {
    const sources = new Set<string>();
    for (const row of dataset?.headlines ?? []) if (row.source) sources.add(String(row.source));
    return Array.from(sources).sort();
  }, [dataset]);
  const sourceCounts = useMemo(() => {
    return (dataset?.headlines ?? []).reduce((map, row) => {
      const source = String(row.source ?? "unknown");
      map.set(source, (map.get(source) ?? 0) + 1);
      return map;
    }, new Map<string, number>());
  }, [dataset]);
  const availableDateRange = useMemo(() => {
    const dates = [
      ...(dataset?.daily_points ?? []).map((point) => dateKey(point.date)),
      ...(dataset?.headlines ?? []).map((row) => dateKey(row.timestamp)),
      ...(dataset?.scored_headlines ?? []).map((row) => dateKey(row.timestamp))
    ].filter(Boolean);
    return {
      start: dates.length ? dates.reduce((min, value) => (value < min ? value : min), dates[0]) : "",
      end: dates.length ? dates.reduce((max, value) => (value > max ? value : max), dates[0]) : ""
    };
  }, [dataset]);
  const selectedTickerSet = useMemo(() => new Set(selectedTickers), [selectedTickers]);
  const selectedTickerLabel = selectedTickers.length ? selectedTickers.join(" + ") : "All symbols";
  const visibleTickerChoices = useMemo(
    () =>
      availableTickers
        .filter((ticker) => ticker.includes(tickerSearch.trim().toUpperCase()) && !selectedTickerSet.has(ticker))
        .slice(0, 20),
    [availableTickers, tickerSearch, selectedTickerSet]
  );
  function addTickerFilter(ticker: string) {
    const normalized = ticker.trim().toUpperCase();
    if (!normalized || normalized === "ALL") {
      setSelectedTickers([]);
      return;
    }
    setSelectedTickers((current) => (current.includes(normalized) ? current : [...current, normalized]));
  }
  function removeTickerFilter(ticker: string) {
    const normalized = ticker.trim().toUpperCase();
    setSelectedTickers((current) => current.filter((item) => item !== normalized));
  }
  const filteredDailyPoints = useMemo(() => {
    return (dataset?.daily_points ?? []).filter((point) => {
      const pointDate = dateKey(point.date);
      if (selectedTickers.length && !selectedTickers.includes(String(point.ticker).toUpperCase())) return false;
      if (chartStart && pointDate < chartStart) return false;
      if (chartEnd && pointDate > chartEnd) return false;
      return true;
    });
  }, [dataset, selectedTickers, chartStart, chartEnd]);
  const tableFilters = useMemo(
    () => ({
      tickers: selectedTickers,
      start: chartStart,
      end: chartEnd,
      source: selectedSource,
      text: headlineSearch
    }),
    [selectedTickers, chartStart, chartEnd, selectedSource, headlineSearch]
  );
  const filteredHeadlines = useMemo(
    () => (dataset?.headlines ?? []).filter((row) => rowMatchesFilters(row, tableFilters)),
    [dataset, tableFilters]
  );
  const filteredScoredHeadlines = useMemo(
    () => (dataset?.scored_headlines ?? []).filter((row) => rowMatchesFilters(row, tableFilters)),
    [dataset, tableFilters]
  );
  const filteredSourceSummary = useMemo(() => {
    const counts = filteredHeadlines.reduce((map, row) => {
      const source = String(row.source ?? "unknown");
      map.set(source, (map.get(source) ?? 0) + 1);
      return map;
    }, new Map<string, number>());
    return Array.from(counts, ([source, headline_count]) => ({ source, headline_count })).sort((a, b) => b.headline_count - a.headline_count);
  }, [filteredHeadlines]);
  const headlinePagination = paginate(filteredHeadlines, headlinePage);
  const scoredPagination = paginate(filteredScoredHeadlines, scoredPage);
  const financialEventSymbols = useMemo(() => {
    const baseSymbols = selectedTickers.length ? selectedTickers : availableTickers.length ? availableTickers : request.symbols;
    return Array.from(new Set(baseSymbols.map((ticker) => ticker.trim().toUpperCase()).filter(Boolean))).slice(0, 8);
  }, [selectedTickers, availableTickers, request.symbols]);
  const financialEventSymbolKey = financialEventSymbols.join(",");
  const financialEventStart = chartStart || availableDateRange.start || request.start;
  const financialEventEnd = chartEnd || availableDateRange.end || request.end;
  const financialComparison = useMemo(
    () => financialSentimentComparison(financialEvents, filteredDailyPoints, financialEventSymbols),
    [financialEvents, filteredDailyPoints, financialEventSymbols]
  );

  useEffect(() => {
    if (!availableTickers.length) {
      setSelectedTickers([]);
      return;
    }
    setSelectedTickers((current) => current.filter((ticker) => availableTickers.includes(ticker)));
  }, [availableTickers]);

  useEffect(() => {
    if (availableSources.length <= 1 || (selectedSource !== "ALL" && !availableSources.includes(selectedSource))) {
      setSelectedSource("ALL");
    }
  }, [availableSources, selectedSource]);

  useEffect(() => {
    if (!chartStart && availableDateRange.start) setChartStart(availableDateRange.start);
    if (!chartEnd && availableDateRange.end) setChartEnd(availableDateRange.end);
  }, [availableDateRange, chartStart, chartEnd]);

  useEffect(() => {
    if (!financialEventSymbols.length || !financialEventStart || !financialEventEnd) return;
    let cancelled = false;
    setFinancialEventsLoading(true);
    setFinancialEventsError(null);
    void getFinancialEvents({
      symbols: financialEventSymbols,
      start: financialEventStart,
      end: financialEventEnd,
      limit: 80
    })
      .then((payload) => {
        if (cancelled) return;
        setFinancialEvents(payload);
      })
      .catch((caught) => {
        if (cancelled) return;
        setFinancialEvents(null);
        setFinancialEventsError(caught instanceof Error ? caught.message : "Unable to load financial events.");
      })
      .finally(() => {
        if (!cancelled) setFinancialEventsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [financialEventSymbolKey, financialEventStart, financialEventEnd]);

  useEffect(() => {
    setHeadlinePage(1);
    setScoredPage(1);
    setFinancialEventsPage(1);
  }, [selectedTickers, selectedSource, chartStart, chartEnd, headlineSearch, dataset]);

  return (
    <div className="page-stack">
      <section className="hero-panel">
        <div>
          <p className="eyebrow">Sentiment Data Lab</p>
          <h2>Build the free news overlay your agents can trade from</h2>
          <span>
            This page gathers free headlines, deduplicates them, scores them with FinBERT when available, and writes the
            daily sentiment file used by PEAD and stat-arb.
          </span>
        </div>
        <Badge label="free data first" tone="good" />
      </section>

      <section className="metric-grid">
        <MetricCard label="Headlines" value={formatNumber(dataset?.summary.headline_count ?? 0, 0)} detail="Raw deduped stories" icon={<Newspaper size={17} />} />
        <MetricCard label="Last Fetch" value={formatNumber(fetchedHeadlineCount, 0)} detail={lastRunTickers ? `Run tickers: ${lastRunTickers}` : "No run metadata yet"} />
        <MetricCard label="Daily Rows" value={formatNumber(dataset?.summary.daily_rows ?? 0, 0)} detail="Ticker-date sentiment rows" />
        <MetricCard label="Tickers" value={formatNumber(dataset?.summary.ticker_count ?? request.symbols.length, 0)} detail={request.symbols.join(" ")} />
        <MetricCard label="Sources" value={formatNumber(dataset?.summary.source_count ?? request.providers.length, 0)} detail={request.providers.join(" + ")} icon={<DatabaseZap size={17} />} />
        <MetricCard label="Financial Events" value={formatNumber(financialEvents?.summary.event_count ?? 0, 0)} detail={financialEventSymbols.join(" ") || "No company selected"} icon={<FileText size={17} />} />
      </section>

      <div className="content-grid">
        <Panel title="1. Sources And Symbols" subtitle="RSS works without a key; API providers are optional supplements and report warnings if credentials fail">
          <label>
            Symbols
            <input value={symbolsText} onChange={(event) => updateSymbolsText(event.target.value)} onBlur={commitSymbolsText} placeholder="AAPL MSFT NVDA GLD KO COKE" />
          </label>
          <div className="provider-check-grid">
            {SOURCE_OPTIONS.map((source) => (
              <label key={source.id} className="provider-check">
                <input
                  type="checkbox"
                  checked={request.providers.includes(source.id)}
                  onChange={(event) => setRequest({ ...request, providers: toggleValue(request.providers, source.id, event.target.checked) })}
                />
                <strong>{source.label}</strong>
                <span>{source.detail}</span>
              </label>
            ))}
          </div>
          <div className="form-grid">
            <label>
              Start
              <input value={request.start} onChange={(event) => setRequest({ ...request, start: event.target.value })} />
            </label>
            <label>
              End
              <input value={request.end} onChange={(event) => setRequest({ ...request, end: event.target.value })} />
            </label>
            <label>
              News files
              <input value={request.news_files.join(" ")} onChange={(event) => setRequest({ ...request, news_files: splitList(event.target.value) })} placeholder={DEFAULT_NEWS_FILE} />
            </label>
            <label>
              RSS feed URLs
              <input value={request.rss_feed_urls.join(" ")} onChange={(event) => setRequest({ ...request, rss_feed_urls: splitList(event.target.value) })} placeholder="Optional. Default Yahoo Finance ticker RSS template is used." />
            </label>
            <label>
              Local web-search feeds
              <input value={request.local_web_search_urls.join(" ")} onChange={(event) => setRequest({ ...request, local_web_search_urls: splitList(event.target.value) })} placeholder="Optional RSS/Atom feeds; {ticker} templates are supported." />
              <small>Used by Local web search. Results are cached locally, then searched without calling GDELT.</small>
            </label>
            <label>
              Local web cache refresh minutes
              <input type="number" min={0} max={1440} value={request.local_web_refresh_minutes} onChange={(event) => setRequest({ ...request, local_web_refresh_minutes: Number(event.target.value) })} />
              <small>Use 60+ for normal work. Set 0 only when you need to force a refetch.</small>
            </label>
            <label>
              Website domains to crawl
              <input value={request.web_research_domains.join(" ")} onChange={(event) => setRequest({ ...request, web_research_domains: splitList(event.target.value) })} placeholder="Optional: reuters.com cnbc.com marketwatch.com" />
              <small>Local web search crawls each domain's sitemap/homepage and caches article pages. GDELT also uses these domains if selected.</small>
            </label>
            <label>
              Website pages per source
              <input type="number" min={1} max={250} value={request.local_web_max_pages_per_source} onChange={(event) => setRequest({ ...request, local_web_max_pages_per_source: Number(event.target.value) })} />
              <small>Higher values gather more text but run slower and may be blocked by some sites.</small>
            </label>
            <label>
              Direct web URLs
              <input value={request.web_research_urls.join(" ")} onChange={(event) => setRequest({ ...request, web_research_urls: splitList(event.target.value) })} placeholder="Optional article URLs or URL templates containing {ticker}" />
              <small>Use this when you want a specific page fetched and summarized.</small>
            </label>
            <label>
              Extra web query terms
              <input value={request.web_research_query_terms} onChange={(event) => setRequest({ ...request, web_research_query_terms: event.target.value })} placeholder="Optional: earnings OR guidance OR inflation" />
              <small>Added to each symbol query. Keep it short to avoid filtering out useful stories.</small>
            </label>
            <label>
              Web articles per symbol
              <input type="number" min={1} max={25} value={request.web_research_max_articles} onChange={(event) => setRequest({ ...request, web_research_max_articles: Number(event.target.value) })} />
              <small>Lower values run faster on weak hardware and slow networks.</small>
            </label>
            <label>
              NewsAPI key
              <input value={request.newsapi_api_key ?? ""} onChange={(event) => setRequest({ ...request, newsapi_api_key: event.target.value || null })} placeholder="Optional unless NewsAPI is selected" />
            </label>
            <label>
              Alpha Vantage key
              <input value={request.alphavantage_api_key ?? ""} onChange={(event) => setRequest({ ...request, alphavantage_api_key: event.target.value || null })} placeholder="Optional unless Alpha Vantage is selected" />
            </label>
            <label>
              Benzinga key
              <input value={request.benzinga_api_key ?? ""} onChange={(event) => setRequest({ ...request, benzinga_api_key: event.target.value || null })} placeholder="Optional unless Benzinga is selected" />
            </label>
          </div>
          <div className="hint-card">
            RSS is a live headline feed. For symbols like GLD, use a recent window; old dates such as 2024 will usually
            return nothing unless you have already accumulated or loaded local historical news. Reddit subreddit URLs
            such as https://www.reddit.com/r/Gold/ are converted to .rss automatically; use one symbol when mapping a
            topic feed to an ETF like GLD. FX pairs such as EURUSD are queried through Yahoo's EURUSD=X alias and then
            stored back under EURUSD. Local web search is the default no-key web tool: it builds a local cached RSS/page
            index, searches that cache for ticker/topic aliases, and avoids GDELT's public search API rate limits.
          </div>
          <div className="button-row">
            <button type="button" className="ghost-button" onClick={() => setRequest({ ...request, ...liveRssDateRange(14) })}>
              Use last 14 days for RSS
            </button>
            <button type="button" className="ghost-button" onClick={() => setRequest({ ...request, start: "2024-01-01", end: "2024-02-14" })}>
              Use sample-file dates
            </button>
          </div>
          <div className="sentiment-panel">
            <label className="checkbox-line">
              <input type="checkbox" checked={request.web_research_fetch_article_text} onChange={(event) => setRequest({ ...request, web_research_fetch_article_text: event.target.checked })} />
              Fetch web pages and create lightweight summaries
            </label>
            <label className="checkbox-line">
              <input type="checkbox" checked={request.use_finbert} onChange={(event) => setRequest({ ...request, use_finbert: event.target.checked })} />
              Use FinBERT when available (heavier, optional)
            </label>
            <label className="checkbox-line">
              <input type="checkbox" checked={request.local_finbert_only} onChange={(event) => setRequest({ ...request, local_finbert_only: event.target.checked })} />
              Use local model cache only during UI runs
            </label>
            <small>For weak hardware, leave FinBERT unchecked. The fallback scorer is fast and runs locally without model downloads.</small>
          </div>
          <div className="button-row">
            <button type="button" className="primary-button" onClick={() => void runAccumulator()} disabled={jobIsRunning} title={jobIsRunning ? "A sentiment job is already running." : "Start a tracked sentiment accumulation job."}>
              {jobIsRunning ? <Loader2 size={17} /> : <Play size={17} />}
              {jobIsRunning ? "Accumulating" : "Run sentiment accumulator"}
            </button>
            <button type="button" className="ghost-button" onClick={() => void refreshDataset()}>
              <RefreshCw size={17} />
              Refresh dataset
            </button>
          </div>
          {sentimentJobProgressCard()}
          {error ? (
            <div className="inline-error">
              <AlertTriangle size={16} />
              {error}
            </div>
          ) : null}
        </Panel>

        <Panel title="2. How Agents Use This" subtitle="Overlay file path and interpretation">
          <div className="artifact-note">
            <strong>Daily sentiment file:</strong>
            <span>{dataset?.daily_sentiment_path ?? dataset?.dataset_id ?? "Latest tenant sentiment dataset"}</span>
          </div>
          <div className="artifact-note">
            <strong>Last accumulator run:</strong>
            <span>
              {lastRunTickers
                ? `${lastRunTickers} fetched ${formatNumber(fetchedHeadlineCount, 0)} new headlines using ${lastRunProviders || "stored providers"}.`
                : "No run metadata yet. Run the accumulator to create a fresh batch summary."}
            </span>
            <span>
              The cache stores {formatNumber(storedHeadlineCount, 0)} raw headlines; this API response returned {formatNumber(returnedHeadlineCount, 0)} rows for frontend filtering and pagination.
            </span>
          </div>
          {storedWarnings.length ? (
            <details className="warning-disclosure">
              <summary>Last run saved {formatNumber(storedWarnings.length, 0)} warning{storedWarnings.length === 1 ? "" : "s"}</summary>
              <p>
                These warnings are persisted with the dataset for audit/debugging. They are not new login errors; run the
                accumulator again with corrected sources to replace them.
              </p>
              <div className="warning-list warning-list--compact">
                {storedWarnings.map((warning, index) => (
                  <span key={`${index}-${warning}`}>{warning}</span>
                ))}
              </div>
            </details>
          ) : null}
          <Explainer
            title="PEAD overlay"
            body="PEAD blends event score and daily sentiment around earnings-like events, then holds for the configured drift window."
            icon={<BrainCircuit size={17} />}
          />
          <Explainer
            title="Stat-arb overlay"
            body="Stat-arb uses sentiment to adjust pair ranking and conviction, helping avoid trades where news disagrees with residual mean reversion."
          />
          <Explainer
            title="Defaults"
            body="Paper agents now start with sentiment sources enabled. You can unselect providers inside Run Paper or point agents directly to this daily sentiment file."
          />
          <small>Last model: {String(latestMetadata.sentiment_model ?? "not scored yet")}</small>
        </Panel>
      </div>

      <Panel title="Sentiment Explorer" subtitle="Choose stored symbols, date windows, and headline sources before reading the charts">
        <div className="sentiment-filter-grid">
          <label>
            Search available symbols
            <input value={tickerSearch} onChange={(event) => setTickerSearch(event.target.value)} placeholder="Try GLD, AAPL, NVDA..." />
          </label>
          <label>
            Add symbol
            <select value="" onChange={(event) => addTickerFilter(event.target.value)}>
              <option value="">Choose a symbol to add</option>
              <option value="ALL">Reset to all symbols</option>
              {availableTickers.map((ticker) => (
                <option key={ticker} value={ticker}>
                  {selectedTickerSet.has(ticker) ? `${ticker} selected` : ticker}
                </option>
              ))}
            </select>
          </label>
          <label>
            Chart start
            <input value={chartStart} onChange={(event) => setChartStart(event.target.value)} placeholder={availableDateRange.start || "YYYY-MM-DD"} />
          </label>
          <label>
            Chart end
            <input value={chartEnd} onChange={(event) => setChartEnd(event.target.value)} placeholder={availableDateRange.end || "YYYY-MM-DD"} />
          </label>
          <label>
            Source filter
            {availableSources.length <= 1 ? (
              <input value={availableSources.length === 1 ? `Only source: ${availableSources[0]}` : "No sources loaded yet"} disabled />
            ) : (
              <select value={selectedSource} onChange={(event) => setSelectedSource(event.target.value)}>
                <option value="ALL">All sources ({formatNumber(dataset?.summary.headline_count ?? 0, 0)} headlines)</option>
                {availableSources.map((source) => (
                  <option key={source} value={source}>
                    {source} ({formatNumber(sourceCounts.get(source) ?? 0, 0)})
                  </option>
                ))}
              </select>
            )}
          </label>
          <label>
            Headline search
            <input value={headlineSearch} onChange={(event) => setHeadlineSearch(event.target.value)} placeholder="Search title, summary, label..." />
          </label>
        </div>
        <div className="hint-card">
          Click a symbol to add it to the explorer filter. Double-click an active symbol to remove it. Choose All symbols
          to clear the symbol filter completely.
        </div>
        <div className="symbol-chip-row">
          <button type="button" className={!selectedTickers.length ? "pill pill--active" : "pill"} onClick={() => setSelectedTickers([])}>
            All symbols
          </button>
          {selectedTickers.map((ticker) => (
            <button
              key={`active-${ticker}`}
              type="button"
              className="pill pill--active pill--removable"
              title="Double-click to remove this symbol"
              onClick={() => addTickerFilter(ticker)}
              onDoubleClick={() => removeTickerFilter(ticker)}
            >
              {ticker} x
            </button>
          ))}
          {visibleTickerChoices.map((ticker) => (
            <button
              key={ticker}
              type="button"
              className={selectedTickerSet.has(ticker) ? "pill pill--active" : "pill"}
              title={selectedTickerSet.has(ticker) ? "Double-click to remove this symbol" : "Click to add this symbol"}
              onClick={() => addTickerFilter(ticker)}
              onDoubleClick={() => removeTickerFilter(ticker)}
            >
              {ticker}
            </button>
          ))}
          <button
            type="button"
            className="pill"
            onClick={() => {
              setSelectedTickers([]);
              setSelectedSource("ALL");
              setHeadlineSearch("");
              setTickerSearch("");
              setChartStart(availableDateRange.start);
              setChartEnd(availableDateRange.end);
            }}
          >
            Reset filters
          </button>
        </div>
        <div className="chart-stat-strip">
          <span>{formatNumber(filteredDailyPoints.length, 0)} daily points</span>
          <span>{formatNumber(filteredHeadlines.length, 0)} raw headlines</span>
          <span>{formatNumber(filteredScoredHeadlines.length, 0)} scored headlines</span>
          <span>{formatNumber(returnedHeadlineCount, 0)} returned rows</span>
          <span>
            Available range {availableDateRange.start || "-"} to {availableDateRange.end || "-"}
          </span>
        </div>
      </Panel>

      <Panel title="Sentiment Overlay Timeline" subtitle="Daily score, headline volume, confidence, and dates">
        <SentimentTimelineChart
          points={filteredDailyPoints}
          title={selectedTickers.length ? `${selectedTickerLabel} sentiment timeline` : "All selected sentiment symbols"}
          detail={`${chartStart || "start"} to ${chartEnd || "end"} | source filter applies to headline tables`}
        />
      </Panel>

      <div className="matrix-comparison-grid">
        <Panel title="Sentiment Matrix" subtitle="Ticker by date matrix with a zero-centered professional diverging palette" className="matrix-panel">
          <SentimentHeatmapChart points={filteredDailyPoints} />
        </Panel>
        <Panel title="Financial Events Matrix" subtitle="Verified company events, reported results, consensus gaps, reactions, and source confidence" className="matrix-panel">
          <FinancialEventsMatrix
            payload={financialEvents}
            isLoading={financialEventsLoading}
            error={financialEventsError}
            page={financialEventsPage}
            onPageChange={setFinancialEventsPage}
          />
        </Panel>
      </div>

      <Panel title="Financial Events Analysis" subtitle="Verified facts are separated from inferred implications and sentiment comparison">
        <div className="financial-analysis-summary">
          <strong>{financialEvents?.analysis.summary ?? "No financial-events analysis loaded yet."}</strong>
          <span>
            Window {financialEventStart || "-"} to {financialEventEnd || "-"} | {financialEventSymbols.join(" + ") || "No company selected"}
          </span>
        </div>
        <div className="financial-analysis-grid">
          <AnalysisList title="Verified Data" rows={financialEvents?.analysis.verified ?? []} empty="No verified financial event facts found for this selection." />
          <AnalysisList title="Inferred Analysis" rows={[...(financialEvents?.analysis.inferred ?? []), ...financialComparison]} empty="No inference available." />
          <AnalysisList title="Risks" rows={financialEvents?.analysis.risks ?? []} empty="No financing, filing, or event risks were identified in retrieved rows." />
          <AnalysisList title="Catalysts" rows={financialEvents?.analysis.catalysts ?? []} empty="No catalyst-class events were identified in retrieved rows." />
          <AnalysisList title="Missing Or Unavailable Data" rows={financialEvents?.analysis.missing_data ?? []} empty="No missing-data notes." />
          <AnalysisList title="Source Notes" rows={financialEvents?.analysis.source_notes ?? []} empty="No source notes." />
        </div>
      </Panel>

      <div className="content-grid">
        <Panel title="Ticker Sentiment Ranking" subtitle="Average weighted sentiment by symbol">
          <SentimentTickerBars points={filteredDailyPoints} />
        </Panel>
        <Panel title="News Source Mix" subtitle="Where raw headlines came from">
          <SentimentSourceBars sources={filteredSourceSummary} />
        </Panel>
      </div>

      <Panel title="Latest Headlines" subtitle="Raw source text before daily aggregation">
        <div className="table-toolbar">
          <strong>{selectedTickerLabel}</strong>
          <PaginationControls page={headlinePagination.page} pageCount={headlinePagination.pageCount} totalRows={filteredHeadlines.length} onPageChange={setHeadlinePage} />
        </div>
        <DataTable
          rows={headlinePagination.pageRows}
          empty={dataset?.summary.headline_count ? "No raw headlines match the current filters." : "No headlines loaded yet. Run the accumulator."}
          getKey={(row, index) => `${row.timestamp ?? "headline"}-${row.ticker ?? "ticker"}-${index}`}
          columns={[
            { key: "timestamp", header: "Time", render: (row) => String(row.timestamp ?? "-").slice(0, 19) },
            { key: "ticker", header: "Ticker", render: (row) => row.ticker ?? "-" },
            { key: "source", header: "Source", render: (row) => row.source ?? "-" },
            { key: "headline", header: "Headline", render: (row) => row.headline ?? row.title ?? "-" },
            { key: "relevance", header: "Relevance", align: "right", render: (row) => formatNumber(row.relevance ?? 0) }
          ]}
        />
      </Panel>

      <Panel title="Scored Headlines" subtitle="FinBERT/rule model output before daily averaging">
        <div className="table-toolbar">
          <strong>{headlineSearch ? `Search: ${headlineSearch}` : "Sentiment model rows"}</strong>
          <PaginationControls page={scoredPagination.page} pageCount={scoredPagination.pageCount} totalRows={filteredScoredHeadlines.length} onPageChange={setScoredPage} />
        </div>
        <DataTable
          rows={scoredPagination.pageRows}
          empty={dataset?.summary.scored_headline_count ? "No scored headlines match the current filters." : "No scored headlines yet."}
          getKey={(row, index) => `${row.timestamp ?? "score"}-${row.ticker ?? "ticker"}-${index}`}
          columns={[
            { key: "timestamp", header: "Time", render: (row) => String(row.timestamp ?? "-").slice(0, 19) },
            { key: "ticker", header: "Ticker", render: (row) => row.ticker ?? "-" },
            { key: "label", header: "Label", render: (row) => <Badge label={String(row.label ?? "n/a")} tone={Number(row.score ?? 0) >= 0 ? "good" : "bad"} /> },
            { key: "score", header: "Score", align: "right", render: (row) => formatNumber(row.score ?? 0) },
            { key: "confidence", header: "Confidence", align: "right", render: (row) => formatNumber(row.confidence ?? 0) },
            { key: "headline", header: "Headline", render: (row) => row.headline ?? row.title ?? "-" }
          ]}
        />
      </Panel>
    </div>
  );
}
