import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, BrainCircuit, DatabaseZap, Loader2, Newspaper, Play, RefreshCw } from "lucide-react";

import { accumulateSentiment, getSentimentDataset } from "../api/client";
import type { SentimentAccumulationRequest, SentimentDatasetPayload } from "../api/types";
import { Badge } from "../components/Badge";
import { SentimentHeatmapChart, SentimentSourceBars, SentimentTickerBars, SentimentTimelineChart } from "../components/Charts";
import { Explainer, MetricCard, Panel } from "../components/Cards";
import { DataTable } from "../components/Table";
import { formatNumber, splitList, splitSymbols } from "../utils/format";

const DEFAULT_OUTPUT_DIR = "data/sentiment_cache/shadow";
const DEFAULT_NEWS_FILE = "examples/news_headlines.sample.csv";
const TABLE_PAGE_SIZE = 12;
const SOURCE_OPTIONS = [
  { id: "rss", label: "RSS firehose", detail: "Free ticker RSS feeds; default source for live headline flow." },
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
    providers: ["rss", "local"],
    rss_feed_urls: [],
    news_files: [DEFAULT_NEWS_FILE],
    newsapi_api_key: null,
    alphavantage_api_key: null,
    benzinga_api_key: null,
    output_dir: DEFAULT_OUTPUT_DIR,
    use_finbert: true,
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

export function SentimentLab() {
  const [request, setRequest] = useState<SentimentAccumulationRequest>(() => defaultRequest());
  const [dataset, setDataset] = useState<SentimentDatasetPayload | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedTickers, setSelectedTickers] = useState<string[]>([]);
  const [tickerSearch, setTickerSearch] = useState("");
  const [selectedSource, setSelectedSource] = useState("ALL");
  const [chartStart, setChartStart] = useState("");
  const [chartEnd, setChartEnd] = useState("");
  const [headlineSearch, setHeadlineSearch] = useState("");
  const [headlinePage, setHeadlinePage] = useState(1);
  const [scoredPage, setScoredPage] = useState(1);

  async function refreshDataset(outputDir = request.output_dir) {
    setError(null);
    try {
      setDataset(await getSentimentDataset(outputDir));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load sentiment dataset.");
    }
  }

  async function runAccumulator() {
    setError(null);
    if (!request.symbols.length) {
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
      setDataset(await accumulateSentiment(request));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to accumulate sentiment.");
    } finally {
      setIsRunning(false);
    }
  }

  useEffect(() => {
    void refreshDataset(DEFAULT_OUTPUT_DIR);
  }, []);

  const latestMetadata = useMemo(() => dataset?.metadata ?? {}, [dataset]);
  const lastRunTickers = metadataList(latestMetadata.tickers);
  const lastRunProviders = metadataList(latestMetadata.providers);
  const fetchedHeadlineCount = latestMetadata.fetched_headlines ?? 0;
  const storedHeadlineCount = latestMetadata.stored_headlines ?? dataset?.summary.headline_count ?? 0;
  const returnedHeadlineCount = dataset?.summary.returned_headline_count ?? dataset?.headlines.length ?? 0;
  const warnings = dataset?.warnings ?? [];
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
    setHeadlinePage(1);
    setScoredPage(1);
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
      </section>

      <div className="content-grid">
        <Panel title="1. Sources And Symbols" subtitle="RSS works without a key; API providers are optional supplements and report warnings if credentials fail">
          <label>
            Symbols
            <input value={request.symbols.join(" ")} onChange={(event) => setRequest({ ...request, symbols: splitSymbols(event.target.value) })} />
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
              Output directory
              <input value={request.output_dir ?? ""} onChange={(event) => setRequest({ ...request, output_dir: event.target.value || null })} />
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
            stored back under EURUSD.
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
              <input type="checkbox" checked={request.use_finbert} onChange={(event) => setRequest({ ...request, use_finbert: event.target.checked })} />
              Score with FinBERT when available
            </label>
            <label className="checkbox-line">
              <input type="checkbox" checked={request.local_finbert_only} onChange={(event) => setRequest({ ...request, local_finbert_only: event.target.checked })} />
              Use local model cache only during UI runs
            </label>
          </div>
          <div className="button-row">
            <button type="button" className="primary-button" onClick={() => void runAccumulator()} disabled={isRunning}>
              {isRunning ? <Loader2 size={17} /> : <Play size={17} />}
              {isRunning ? "Accumulating" : "Run sentiment accumulator"}
            </button>
            <button type="button" className="ghost-button" onClick={() => void refreshDataset()}>
              <RefreshCw size={17} />
              Refresh dataset
            </button>
          </div>
          {error ? (
            <div className="inline-error">
              <AlertTriangle size={16} />
              {error}
            </div>
          ) : null}
          {warnings.length ? (
            <div className="inline-warning">
              <AlertTriangle size={16} />
              <span>{warnings.join(" ")}</span>
            </div>
          ) : null}
        </Panel>

        <Panel title="2. How Agents Use This" subtitle="Overlay file path and interpretation">
          <div className="artifact-note">
            <strong>Daily sentiment file:</strong>
            <span>{dataset?.daily_sentiment_path ?? `${DEFAULT_OUTPUT_DIR}/daily_sentiment.parquet`}</span>
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

      <Panel title="Sentiment Heat Map" subtitle="Ticker by date matrix with a zero-centered professional diverging palette">
        <SentimentHeatmapChart points={filteredDailyPoints} />
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
