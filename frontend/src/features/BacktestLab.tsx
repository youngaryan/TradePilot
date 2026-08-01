import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Bot, CheckCircle2, FlaskConical, Loader2, Play, Save, Send, ShieldAlert, SlidersHorizontal } from "lucide-react";

import { approveStrategySpec, chatStrategyBuilder, getBacktestJob, getStrategyCatalog, listBacktestJobs, startBacktest } from "../api/client";
import type { BacktestJob, BacktestRunRequest, BacktestTemplate, StrategyBuilderMessage, StrategyBuilderResponse, StrategyCatalogItem, StrategySpec } from "../api/types";
import { Badge } from "../components/Badge";
import { BacktestPerformanceChart } from "../components/BacktestPerformanceChart";
import { BacktestEquityChart } from "../components/Charts";
import { Explainer, MetricCard, Panel } from "../components/Cards";
import { DataTable } from "../components/Table";
import { formatNumber, formatPercent, jobDisplayStatus, pipelineLabel, splitList, splitSymbols, statusTone, toneFromNumber, toNumber } from "../utils/format";
import { parseJsonObject } from "../utils/quant";

const defaultRequest: BacktestRunRequest = {
  pipeline: "time_series_momentum",
  symbols: ["SPY", "QQQ", "TLT", "GLD"],
  start: "2018-01-01",
  end: "2026-04-15",
  interval: "1d",
  trading_mode: "daily",
  compare_modes: false,
  experiment_name: "cockpit_backtest",
  sector_map_path: null,
  event_file: null,
  use_sec_companyfacts: false,
  include_sec_filings: false,
  sec_filing_forms: ["8-K", "10-Q", "10-K"],
  edgar_user_agent: null,
  train_bars: 300,
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

const SECTOR_MAP_PIPELINES = new Set(["stat_arb", "graph_stat_arb"]);
const EVENT_PIPELINES = new Set(["edgar_event", "pead_sentiment"]);
const SENTIMENT_PIPELINES = new Set(["stat_arb", "pead_sentiment"]);
const COMMITTEE_SIGNAL_PIPELINE = "committee_signal_follower";
const DEFAULT_SENTIMENT_PARAMETERS = {
  news_provider_names: ["rss", "local_web", "local"],
  news_files: ["examples/news_headlines.sample.csv"],
  use_finbert: false,
  local_finbert_only: true,
  local_web_refresh_minutes: 60,
  local_web_max_pages_per_source: 30,
  web_research_max_articles: 4,
  web_research_fetch_article_text: true
};

type PipelineExample = {
  symbols?: unknown;
  params?: unknown;
  sector_map_path?: unknown;
  event_file?: unknown;
  name?: unknown;
};

function asStringArray(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  return value.map((item) => String(item).trim()).filter(Boolean);
}

function asParameterObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function asOptionalString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function metricValue(summary: Record<string, unknown>, key: string, formatter: (value: unknown) => string) {
  return formatter(summary[key]);
}

function optionalNumber(value: unknown, digits = 2) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? formatNumber(parsed, digits) : "n/a";
}

function templateToRequest(template: BacktestTemplate): BacktestRunRequest {
  const isSectorMapPipeline = SECTOR_MAP_PIPELINES.has(template.pipeline);
  const isEventPipeline = EVENT_PIPELINES.has(template.pipeline);
  const isCommitteeSignalPipeline = template.pipeline === COMMITTEE_SIGNAL_PIPELINE;
  const parameters = SENTIMENT_PIPELINES.has(template.pipeline)
    ? { ...DEFAULT_SENTIMENT_PARAMETERS, ...template.parameters }
    : template.parameters;
  return {
    ...defaultRequest,
    pipeline: template.pipeline,
    symbols: isSectorMapPipeline ? [] : template.symbols,
    start: template.start,
    end: template.end,
    interval: template.trading_mode === "short_term" ? "1h" : "1d",
    trading_mode: template.trading_mode ?? defaultRequest.trading_mode,
    compare_modes: template.compare_modes ?? false,
    experiment_name: template.id,
    train_bars: isCommitteeSignalPipeline ? 1 : template.train_bars ?? defaultRequest.train_bars,
    test_bars: template.test_bars ?? defaultRequest.test_bars,
    step_bars: template.step_bars ?? defaultRequest.step_bars,
    purge_bars: isCommitteeSignalPipeline ? 0 : template.purge_bars ?? defaultRequest.purge_bars,
    embargo_bars: template.embargo_bars ?? defaultRequest.embargo_bars,
    pbo_partitions: template.pbo_partitions ?? defaultRequest.pbo_partitions,
    sector_map_path: isSectorMapPipeline ? template.sector_map_path ?? "examples/sector_map.sample.json" : null,
    event_file: isEventPipeline ? template.event_file ?? "examples/events.sample.csv" : null,
    use_sec_companyfacts: false,
    include_sec_filings: false,
    edgar_user_agent: null,
    parameters
  };
}

export function BacktestLab({
  catalog,
  templates,
  jobs,
  onJobsChange,
  onCatalogChange,
  strategyBuilderMode = "rules"
}: {
  catalog: StrategyCatalogItem[];
  templates: BacktestTemplate[];
  jobs: BacktestJob[];
  onJobsChange: (jobs: BacktestJob[]) => void;
  onCatalogChange: (catalog: StrategyCatalogItem[]) => void;
  strategyBuilderMode?: "rules" | "llm";
}) {
  const builderIsLlm = strategyBuilderMode === "llm";
  const [request, setRequest] = useState<BacktestRunRequest>(defaultRequest);
  const [symbolsText, setSymbolsText] = useState(defaultRequest.symbols.join(" "));
  const [parametersText, setParametersText] = useState(JSON.stringify(defaultRequest.parameters, null, 2));
  const [activeJob, setActiveJob] = useState<BacktestJob | null>(jobs[0] ?? null);
  const [error, setError] = useState<string | null>(null);
  const [isLaunching, setIsLaunching] = useState(false);
  const [builderMessages, setBuilderMessages] = useState<StrategyBuilderMessage[]>([]);
  const [builderInput, setBuilderInput] = useState("");
  const [builderResponse, setBuilderResponse] = useState<StrategyBuilderResponse | null>(null);
  const [draftSpec, setDraftSpec] = useState<StrategySpec | null>(null);
  const [builderNotice, setBuilderNotice] = useState<string | null>(null);
  const [builderError, setBuilderError] = useState<string | null>(null);
  const [isBuilderBusy, setIsBuilderBusy] = useState(false);
  const activeStrategy = useMemo(
    () => catalog.find((item) => item.pipeline === request.pipeline || item.id === request.pipeline),
    [catalog, request.pipeline]
  );
  const usesSectorMap = SECTOR_MAP_PIPELINES.has(request.pipeline);
  const usesEventInputs = EVENT_PIPELINES.has(request.pipeline);
  const usesSentimentInputs = SENTIMENT_PIPELINES.has(request.pipeline);
  const usesCommitteeSignals = request.pipeline === COMMITTEE_SIGNAL_PIPELINE;
  const parsedParameters = useMemo(() => {
    try {
      return parseJsonObject(parametersText, "Backtest parameters");
    } catch {
      return request.parameters;
    }
  }, [parametersText, request.parameters]);

  function applyTemplate(template: BacktestTemplate) {
    const next = templateToRequest(template);
    setRequest(next);
    setSymbolsText(next.symbols.join(" "));
    setParametersText(JSON.stringify(next.parameters, null, 2));
    setError(null);
  }

  function applyPipeline(pipeline: string) {
    const item = catalog.find((strategy) => strategy.pipeline === pipeline || strategy.id === pipeline);
    const example = (item?.paper_config_example ?? {}) as PipelineExample;
    const isSectorMapPipeline = SECTOR_MAP_PIPELINES.has(pipeline);
    const isEventPipeline = EVENT_PIPELINES.has(pipeline);
    const isCommitteeSignalPipeline = pipeline === COMMITTEE_SIGNAL_PIPELINE;
    const rawParams = asParameterObject(example.params) ?? {};
    const params = SENTIMENT_PIPELINES.has(pipeline) ? { ...DEFAULT_SENTIMENT_PARAMETERS, ...rawParams } : rawParams;
    const next: BacktestRunRequest = {
      ...request,
      pipeline,
      symbols: isSectorMapPipeline ? [] : asStringArray(example.symbols) ?? request.symbols,
      sector_map_path: isSectorMapPipeline ? asOptionalString(example.sector_map_path) ?? request.sector_map_path ?? "examples/sector_map.sample.json" : null,
      event_file: isEventPipeline ? asOptionalString(example.event_file) ?? request.event_file ?? "examples/events.sample.csv" : null,
      use_sec_companyfacts: false,
      include_sec_filings: false,
      edgar_user_agent: isEventPipeline ? request.edgar_user_agent : null,
      experiment_name: asOptionalString(example.name) ?? `${pipeline}_ui`,
      train_bars: isCommitteeSignalPipeline ? 1 : request.train_bars,
      purge_bars: isCommitteeSignalPipeline ? 0 : request.purge_bars,
      parameters: params
    };
    setRequest(next);
    setSymbolsText(next.symbols.join(" "));
    setParametersText(JSON.stringify(params, null, 2));
    setError(null);
  }

  function updateParameter(key: string, value: unknown) {
    const nextParameters = {
      ...parsedParameters,
      [key]: value
    };
    if (value === "" || value === null) {
      delete nextParameters[key];
    }
    setRequest({ ...request, parameters: nextParameters });
    setParametersText(JSON.stringify(nextParameters, null, 2));
  }

  function stringParameter(key: string) {
    const value = parsedParameters[key];
    return value === undefined || value === null ? "" : String(value);
  }

  function booleanParameter(key: string) {
    return Boolean(parsedParameters[key]);
  }

  async function refreshJobs() {
    onJobsChange(await listBacktestJobs());
  }

  async function launch() {
    setError(null);
    let parameters: Record<string, unknown>;
    try {
      parameters = parseJsonObject(parametersText, "Backtest parameters");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Backtest parameters: invalid JSON");
      return;
    }
    if (usesSectorMap && !request.sector_map_path) {
      setError("Graph/stat-arb pipelines use a sector-map universe. Set Sector map to examples/sector_map.sample.json or choose the Graph Stat-Arb template.");
      return;
    }
    if (usesEventInputs && !request.event_file && !request.include_sec_filings && !request.use_sec_companyfacts) {
      setError("Event pipelines need an event file or an official SEC source. For the PEAD demo use examples/events.sample.csv.");
      return;
    }
    if ((request.include_sec_filings || request.use_sec_companyfacts) && !String(request.edgar_user_agent ?? "").includes("@")) {
      setError("Official SEC event backtests need an SEC user agent with a contact email.");
      return;
    }
    setIsLaunching(true);
    try {
      const finalSymbols = usesSectorMap ? [] : splitSymbols(symbolsText);

      const job = await startBacktest({
        ...request,
        symbols: finalSymbols,
        parameters
      });
      setActiveJob(job);
      await refreshJobs();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to launch backtest.");
    } finally {
      setIsLaunching(false);
    }
  }

  async function sendBuilderMessage() {
    const content = builderInput.trim();
    if (!content) return;
    const nextMessages: StrategyBuilderMessage[] = [...builderMessages, { role: "user", content }];
    setBuilderMessages(nextMessages);
    setBuilderInput("");
    setBuilderError(null);
    setBuilderNotice(null);
    setIsBuilderBusy(true);
    try {
      const response = await chatStrategyBuilder(nextMessages, draftSpec as unknown as Record<string, unknown> | null);
      setBuilderResponse(response);
      setDraftSpec(response.draft_spec ?? null);
      setBuilderMessages([...nextMessages, { role: "assistant", content: response.assistant_message }]);
    } catch (caught) {
      setBuilderError(caught instanceof Error ? caught.message : "The strategy builder could not process that request.");
    } finally {
      setIsBuilderBusy(false);
    }
  }

  async function approveDraft() {
    if (!draftSpec) return;
    setBuilderError(null);
    setBuilderNotice(null);
    setIsBuilderBusy(true);
    try {
      const response = await approveStrategySpec(draftSpec as unknown as Record<string, unknown>, `Approved ${draftSpec.name} from the strategy-builder UI.`, builderResponse?.provenance_token);
      const nextCatalog = await getStrategyCatalog();
      onCatalogChange(nextCatalog);
      applyPipeline(response.catalog_item.pipeline);
      setBuilderNotice(`Saved ${response.strategy.name}. It is now available only in your strategy list.`);
    } catch (caught) {
      setBuilderError(caught instanceof Error ? caught.message : "The strategy could not be approved.");
    } finally {
      setIsBuilderBusy(false);
    }
  }

  useEffect(() => {
    if (!activeJob || !["queued", "running"].includes(activeJob.status)) return;
    const timer = window.setInterval(() => {
      void getBacktestJob(activeJob.id).then((job) => {
        setActiveJob(job);
        void refreshJobs();
      });
    }, 1400);
    return () => window.clearInterval(timer);
  }, [activeJob]);

  const result = activeJob?.result;
  const summary = result?.summary ?? {};
  const validation = result?.validation ?? {};
  const visualization = result?.visualization ?? activeJob?.progress_snapshot ?? null;
  const visualMetrics = visualization?.metrics ?? {};
  const performanceMetrics = result
    ? {
        total_return: summary.total_return ?? visualMetrics.total_return,
        cagr: summary.annualized_return ?? visualMetrics.cagr,
        sharpe: summary.sharpe ?? visualMetrics.sharpe,
        max_drawdown: summary.max_drawdown ?? visualMetrics.max_drawdown,
        win_rate: summary.hit_rate ?? visualMetrics.win_rate,
        profit_factor: visualMetrics.profit_factor,
        baseline_total_return: summary.baseline_total_return ?? visualMetrics.baseline_total_return,
        baseline_cagr: summary.baseline_cagr ?? visualMetrics.baseline_cagr,
        baseline_max_drawdown: summary.baseline_max_drawdown ?? visualMetrics.baseline_max_drawdown,
        benchmark_outperformance: summary.benchmark_outperformance ?? visualMetrics.benchmark_outperformance
      }
    : visualMetrics;
  const tradeRows = visualization?.trade_summary ?? result?.trade_summary ?? [];
  const comparisonRows = result?.comparison?.leaderboard ?? [];

  return (
    <div className="page-stack">
      <section className="hero-panel">
        <div>
          <p className="eyebrow">Research Lab</p>
          <h2>Walk-forward backtests with explicit validation gates</h2>
          <span>
            The backend trains and tests strategies across rolling time folds, applies simulated execution costs, saves artifacts,
            and reports DSR/PBO-style evidence before anything gets promoted to paper trading.
          </span>
        </div>
        <Badge label="validation first" tone="info" />
      </section>

      <div className="content-grid">
        <Panel title="Backtest Setup" subtitle={activeStrategy?.name ?? "Custom strategy"}>
          <div className="template-grid">
            {templates.map((template) => (
              <button key={template.id} type="button" className="template-card" onClick={() => applyTemplate(template)}>
                <FlaskConical size={16} />
                <strong>{template.name}</strong>
                <span>{template.description}</span>
              </button>
            ))}
          </div>

          <div className="form-grid">
            <label htmlFor="bt-pipeline">
              Pipeline
              <select id="bt-pipeline" value={request.pipeline} onChange={(event) => applyPipeline(event.target.value)}>
                {catalog.map((item) => (
                  <option key={item.id} value={item.pipeline}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label htmlFor="bt-symbols">
              Symbols
              <input id="bt-symbols"
                // value={usesSectorMap ? "" : request.symbols.join(" ")}
                value={usesSectorMap ? "" : symbolsText}
                disabled={usesSectorMap}
                // onChange={(event) => setRequest({ ...request, symbols: splitSymbols(event.target.value) })}
                onChange={(event) => {setSymbolsText(event.target.value);}}
                // onBlur={() => {const nextSymbols = splitSymbols(symbolsText);setSymbolsText(nextSymbols.join(" "));setRequest((current) => ({...current,symbols: nextSymbols}));}}
                placeholder={usesSectorMap ? "Loaded from the sector map" : "SPY QQQ TLT GLD"}
              />
              {usesSectorMap ? <small>This pipeline trades every ticker in the sector map, not the Symbols box.</small> : null}
            </label>
            <label htmlFor="bt-start">
              Start
              <input id="bt-start" value={request.start} onChange={(event) => setRequest({ ...request, start: event.target.value })} />
            </label>
            <label htmlFor="bt-end">
              End
              <input id="bt-end" value={request.end} onChange={(event) => setRequest({ ...request, end: event.target.value })} />
            </label>
            <label htmlFor="bt-trading-mode">
              Trading mode
              <select
                id="bt-trading-mode"
                value={request.trading_mode ?? "daily"}
                onChange={(event) => {
                  const tradingMode = event.target.value as "daily" | "short_term";
                  setRequest({
                    ...request,
                    trading_mode: tradingMode,
                    interval: tradingMode === "short_term" ? "1h" : "1d",
                    bars_per_year: tradingMode === "short_term" ? 1638 : 252,
                    train_bars: tradingMode === "short_term" ? 390 : request.train_bars,
                    test_bars: tradingMode === "short_term" ? 78 : request.test_bars,
                    step_bars: tradingMode === "short_term" ? 78 : request.step_bars
                  });
                }}
              >
                <option value="daily">Daily</option>
                <option value="short_term">Short-term 1h / 4h</option>
              </select>
            </label>
            <label className="checkbox-line" htmlFor="bt-compare-modes">
              <input
                id="bt-compare-modes"
                type="checkbox"
                checked={Boolean(request.compare_modes)}
                onChange={(event) => setRequest({ ...request, compare_modes: event.target.checked })}
              />
              Compare daily and short-term
            </label>
            {usesSectorMap ? (
              <label htmlFor="bt-sector-map">
                Sector map
                <input id="bt-sector-map" value={request.sector_map_path ?? ""} onChange={(event) => setRequest({ ...request, sector_map_path: event.target.value || null })} placeholder="examples/sector_map.sample.json" />
                <small>Required so graph/stat-arb sectors are explicit and auditable.</small>
              </label>
            ) : null}
            {usesEventInputs ? (
              <label htmlFor="bt-event-file">
                Event file
                <input id="bt-event-file" value={request.event_file ?? ""} onChange={(event) => setRequest({ ...request, event_file: event.target.value || null })} placeholder="examples/events.sample.csv" />
                <small>Use the sample file for local PEAD testing, or enable official SEC sources below.</small>
              </label>
            ) : null}
          </div>

          {usesEventInputs ? (
          <div className="sentiment-panel official-events-panel">
            <label className="checkbox-line" htmlFor="bt-include-official-sec-filing-events">
              <input
                id="bt-include-official-sec-filing-events"
                type="checkbox"
                checked={Boolean(request.include_sec_filings)}
                onChange={(event) => setRequest({
                  ...request,
                  include_sec_filings: event.target.checked,
                  sec_filing_forms: event.target.checked && !request.sec_filing_forms?.length ? ["8-K", "10-Q", "10-K"] : request.sec_filing_forms
                })}
              />
              Include official SEC filing events
            </label>
            <p>
              For event-driven research this adds official SEC EDGAR company events, such as earnings 8-K filings,
              10-Q quarterly reports, and 10-K annual reports.
            </p>
            <label className="checkbox-line" htmlFor="bt-include-sec-company-facts-scores">
              <input
                id="bt-include-sec-company-facts-scores"
                type="checkbox"
                checked={Boolean(request.use_sec_companyfacts)}
                onChange={(event) => setRequest({ ...request, use_sec_companyfacts: event.target.checked })}
              />
              Include SEC company facts scores
            </label>
            {request.include_sec_filings || request.use_sec_companyfacts ? (
              <div className="form-grid">
                {request.include_sec_filings ? (
                  <label htmlFor="bt-sec-filing-forms">
                    SEC filing forms
                    <input id="bt-sec-filing-forms"
                      value={(request.sec_filing_forms ?? ["8-K", "10-Q", "10-K"]).join(" ")}
                      onChange={(event) => setRequest({ ...request, sec_filing_forms: splitList(event.target.value).map((form) => form.toUpperCase()) })}
                      placeholder="8-K 10-Q 10-K"
                    />
                  </label>
                ) : null}
                <label htmlFor="bt-sec-user-agent">
                  SEC user agent
                  <input id="bt-sec-user-agent"
                    value={request.edgar_user_agent ?? ""}
                    onChange={(event) => setRequest({ ...request, edgar_user_agent: event.target.value || null })}
                    placeholder="Your Name your@email.com"
                  />
                  <small>Required by the SEC API when official EDGAR sources are enabled.</small>
                </label>
              </div>
            ) : null}
          </div>
          ) : null}

          {usesSentimentInputs ? (
            <div className="sentiment-panel official-events-panel">
              <strong>Sentiment overlay</strong>
              <p>
                Use a precomputed daily sentiment file, or let the backend build one from RSS/web/local headlines.
                For weak hardware, keep FinBERT off and use the lightweight fallback scorer.
              </p>
              <div className="form-grid">
                <label htmlFor="bt-daily-sentiment-file">
                  Daily sentiment file
                  <input id="bt-daily-sentiment-file"
                    value={stringParameter("daily_sentiment_file")}
                    onChange={(event) => updateParameter("daily_sentiment_file", event.target.value || null)}
                    placeholder="examples/daily_sentiment.sample.csv"
                  />
                  <small>Optional. Leave blank to fetch/score headlines from the selected providers.</small>
                </label>
                <label htmlFor="bt-news-providers">
                  News providers
                  <input id="bt-news-providers"
                    value={Array.isArray(parsedParameters.news_provider_names) ? parsedParameters.news_provider_names.join(" ") : ""}
                    onChange={(event) => updateParameter("news_provider_names", splitList(event.target.value))}
                    placeholder="rss local_web local"
                  />
                  <small>Use rss local_web local for free no-key sources. Add GDELT web or API providers only when you need them.</small>
                </label>
                <label htmlFor="bt-rss-feeds">
                  RSS feeds
                  <input id="bt-rss-feeds"
                    value={Array.isArray(parsedParameters.rss_feed_urls) ? parsedParameters.rss_feed_urls.join(" ") : ""}
                    onChange={(event) => updateParameter("rss_feed_urls", splitList(event.target.value))}
                    placeholder="optional; Yahoo ticker RSS is used by default"
                  />
                </label>
                <label htmlFor="bt-local-web-search-feeds">
                  Local web-search feeds
                  <input id="bt-local-web-search-feeds"
                    value={Array.isArray(parsedParameters.local_web_search_urls) ? parsedParameters.local_web_search_urls.join(" ") : ""}
                    onChange={(event) => updateParameter("local_web_search_urls", splitList(event.target.value))}
                    placeholder="optional RSS/Atom feeds or {ticker} templates"
                  />
                </label>
                <label htmlFor="bt-local-web-cache-refresh-minutes">
                  Local web cache refresh minutes
                  <input id="bt-local-web-cache-refresh-minutes"
                    type="number"
                    value={stringParameter("local_web_refresh_minutes") || "60"}
                    onChange={(event) => updateParameter("local_web_refresh_minutes", Number(event.target.value))}
                  />
                </label>
                <label htmlFor="bt-website-domains-to-crawl">
                  Website domains to crawl
                  <input id="bt-website-domains-to-crawl"
                    value={Array.isArray(parsedParameters.web_research_domains) ? parsedParameters.web_research_domains.join(" ") : ""}
                    onChange={(event) => updateParameter("web_research_domains", splitList(event.target.value))}
                    placeholder="reuters.com cnbc.com marketwatch.com"
                  />
                </label>
                <label htmlFor="bt-website-pages-per-source">
                  Website pages per source
                  <input id="bt-website-pages-per-source"
                    type="number"
                    value={stringParameter("local_web_max_pages_per_source") || "30"}
                    onChange={(event) => updateParameter("local_web_max_pages_per_source", Number(event.target.value))}
                  />
                </label>
                <label htmlFor="bt-direct-web-urls">
                  Direct web URLs
                  <input id="bt-direct-web-urls"
                    value={Array.isArray(parsedParameters.web_research_urls) ? parsedParameters.web_research_urls.join(" ") : ""}
                    onChange={(event) => updateParameter("web_research_urls", splitList(event.target.value))}
                    placeholder="optional article URLs or {ticker} templates"
                  />
                </label>
                <label htmlFor="bt-web-articles-per-symbol">
                  Web articles per symbol
                  <input id="bt-web-articles-per-symbol"
                    type="number"
                    value={stringParameter("web_research_max_articles") || "4"}
                    onChange={(event) => updateParameter("web_research_max_articles", Number(event.target.value))}
                  />
                </label>
                <label htmlFor="bt-news-files">
                  News files
                  <input id="bt-news-files"
                    value={Array.isArray(parsedParameters.news_files) ? parsedParameters.news_files.join(" ") : ""}
                    onChange={(event) => updateParameter("news_files", splitList(event.target.value))}
                    placeholder="examples/news_headlines.sample.csv"
                  />
                </label>
                <label htmlFor="bt-sentiment-window-days">
                  Sentiment window days
                  <input id="bt-sentiment-window-days"
                    type="number"
                    value={stringParameter("sentiment_window_days") || "2"}
                    onChange={(event) => updateParameter("sentiment_window_days", Number(event.target.value))}
                  />
                  <small>How many prior calendar days of sentiment are blended into each event.</small>
                </label>
              </div>
              <label className="checkbox-line" htmlFor="bt-fetch-web-pages-and-create-lightweight-summaries">
                <input
                  id="bt-fetch-web-pages-and-create-lightweight-summaries"
                  type="checkbox"
                  checked={parsedParameters.web_research_fetch_article_text !== false}
                  onChange={(event) => updateParameter("web_research_fetch_article_text", event.target.checked)}
                />
                Fetch web pages and create lightweight summaries
              </label>
              <label className="checkbox-line" htmlFor="bt-use-finbert-when-available-heavier">
                <input
                  id="bt-use-finbert-when-available-heavier"
                  type="checkbox"
                  checked={booleanParameter("use_finbert")}
                  onChange={(event) => updateParameter("use_finbert", event.target.checked)}
                />
                Use FinBERT when available (heavier)
              </label>
              <label className="checkbox-line" htmlFor="bt-require-sentiment-coverage-before-pead-can-trade">
                <input
                  id="bt-require-sentiment-coverage-before-pead-can-trade"
                  type="checkbox"
                  checked={booleanParameter("require_sentiment")}
                  onChange={(event) => updateParameter("require_sentiment", event.target.checked)}
                />
                Require sentiment coverage before PEAD can trade
              </label>
            </div>
          ) : null}

          {!usesCommitteeSignals ? (
            <div className="form-grid form-grid--tight">
              <label htmlFor="bt-train-bars">
                Train bars
                <input id="bt-train-bars" type="number" value={request.train_bars} onChange={(event) => setRequest({ ...request, train_bars: Number(event.target.value) })} />
              </label>
              <label htmlFor="bt-test-bars">
                Test bars
                <input id="bt-test-bars" type="number" value={request.test_bars} onChange={(event) => setRequest({ ...request, test_bars: Number(event.target.value) })} />
              </label>
              <label htmlFor="bt-purge-bars">
                Purge bars
                <input id="bt-purge-bars" type="number" value={request.purge_bars} onChange={(event) => setRequest({ ...request, purge_bars: Number(event.target.value) })} />
              </label>
              <label htmlFor="bt-pbo-partitions">
                PBO partitions
                <input id="bt-pbo-partitions" type="number" value={request.pbo_partitions} onChange={(event) => setRequest({ ...request, pbo_partitions: Number(event.target.value) })} />
              </label>
            </div>
          ) : null}

          <label htmlFor="bt-parameters-json">
            Parameters JSON
            <textarea id="bt-parameters-json" rows={8} value={parametersText} onChange={(event) => setParametersText(event.target.value)} spellCheck={false} />
          </label>

          <div className="button-row">
            <button type="button" className="primary-button" onClick={() => void launch()} disabled={isLaunching}>
              {isLaunching ? <Loader2 size={17} /> : <Play size={17} />}
              {isLaunching ? "Launching" : "Launch backtest agent"}
            </button>
          </div>
          {error ? (
            <div className="inline-error">
              <AlertTriangle size={16} />
              {error}
            </div>
          ) : null}
        </Panel>

        <Panel title={builderIsLlm ? "AI-assisted Strategy Builder" : "Rule-based Strategy Builder"} subtitle="Clarify, review, approve, then backtest">
          <div className="strategy-builder">
            <div className="strategy-builder__messages">
              {builderMessages.length ? (
                builderMessages.map((message, index) => (
                  <div key={`${message.role}-${index}`} className={`chat-bubble chat-bubble--${message.role}`}>
                    <strong>{message.role === "user" ? "You" : "Builder"}</strong>
                    <span>{message.content}</span>
                  </div>
                ))
              ) : (
                <Explainer
                  title="Schema-first generation"
                  body="The builder only creates validated rule specs from supported indicators. It will ask for missing details and requires approval before saving."
                  icon={<Bot size={17} />}
                />
              )}
            </div>

            {builderResponse?.questions.length ? (
              <div className="strategy-builder__questions">
                <strong>Clarify these points</strong>
                <ul>
                  {builderResponse.questions.map((question) => <li key={question}>{question}</li>)}
                </ul>
              </div>
            ) : null}

            {draftSpec ? (
              <div className="strategy-spec-review">
                <div className="strategy-spec-review__top">
                  <div>
                    <strong>{draftSpec.name}</strong>
                    <span>{draftSpec.summary}</span>
                  </div>
                  <Badge label={builderResponse?.state ?? "draft"} tone={builderResponse?.state === "ready_for_approval" ? "good" : "warn"} />
                </div>
                <div className="strategy-spec-grid">
                  <span><strong>Universe</strong>{draftSpec.asset_universe.symbols.join(", ")}</span>
                  <span><strong>Timeframe</strong>{draftSpec.timeframe}</span>
                  <span><strong>Side</strong>{draftSpec.side.replace("_", " ")}</span>
                  <span><strong>Sizing</strong>{String(draftSpec.position_sizing.max_position_per_symbol ?? "n/a")} max per symbol</span>
                </div>
                <DataTable
                  rows={[...draftSpec.entry_rules.map((rule) => ({ ...rule, group: "Entry" })), ...draftSpec.exit_rules.map((rule) => ({ ...rule, group: "Exit" }))]}
                  empty="No rules in the draft spec."
                  getKey={(row) => `${row.group}-${row.kind}`}
                  columns={[
                    { key: "group", header: "Rule", render: (row) => <Badge label={row.group} tone={row.group === "Entry" ? "good" : "warn"} /> },
                    { key: "kind", header: "Condition", render: (row) => row.kind },
                    { key: "params", header: "Parameters", render: (row) => JSON.stringify(row.parameters) }
                  ]}
                />
                {builderResponse?.validation.warnings.length ? (
                  <div className="inline-warning">
                    <AlertTriangle size={16} />
                    {builderResponse.validation.warnings.join(" ")}
                  </div>
                ) : null}
                <details>
                  <summary>Full StrategySpec JSON</summary>
                  <pre>{JSON.stringify(draftSpec, null, 2)}</pre>
                </details>
                <button type="button" className="primary-button" onClick={() => void approveDraft()} disabled={isBuilderBusy || builderResponse?.state !== "ready_for_approval"}>
                  {isBuilderBusy ? <Loader2 size={17} /> : <Save size={17} />}
                  Approve and save strategy
                </button>
              </div>
            ) : null}

            <div className="strategy-builder__input">
              <textarea
                rows={4}
                value={builderInput}
                onChange={(event) => setBuilderInput(event.target.value)}
                placeholder="Example: Trade SPY and QQQ on daily bars. Buy equal weight when RSI 14 is below 30, exit above 55, use a 10% stop loss and 3 bps costs."
              />
              <button type="button" className="primary-button" onClick={() => void sendBuilderMessage()} disabled={isBuilderBusy || !builderInput.trim()}>
                {isBuilderBusy ? <Loader2 size={17} /> : <Send size={17} />}
                Send
              </button>
            </div>

            {builderNotice ? <div className="inline-success"><CheckCircle2 size={16} />{builderNotice}</div> : null}
            {builderError ? <div className="inline-error"><AlertTriangle size={16} />{builderError}</div> : null}
            <small className="strategy-builder__disclaimer">{builderIsLlm ? "AI-assisted" : "Rule-generated"} strategies are user-approved research specs, not executable code or investment advice. Backtest performance does not guarantee future results.</small>
          </div>
        </Panel>

        <Panel title="What The Agent Will Do" subtitle="Backend execution plan">
          <div className="research-plan">
            <div>
              <SlidersHorizontal size={17} />
              <strong>Build request</strong>
              <span>Validate required fields for {pipelineLabel(request.pipeline)}.</span>
            </div>
            <div>
              <ShieldAlert size={17} />
              <strong>Purged folds</strong>
              <span>Use train/test windows with purge bars to reduce lookahead contamination.</span>
            </div>
            <div>
              <CheckCircle2 size={17} />
              <strong>Promotion verdict</strong>
              <span>Report Sharpe, DSR, PBO, drawdown, turnover, and fold count.</span>
            </div>
          </div>
          <Explainer
            title="Why DSR and PBO matter"
            body="High backtest returns can come from trying many variants until one looks good. DSR and PBO are guardrails against overfit research."
          />
        </Panel>
      </div>

      <div className="content-grid content-grid--wide">
        <Panel title="Backtest Status" subtitle={activeJob?.id ?? "No active research job"}>
          <div className="progress-card">
            <div className="progress-card__top">
              <Badge label={jobDisplayStatus(activeJob)} tone={statusTone(jobDisplayStatus(activeJob))} />
              <strong>{Math.round((activeJob?.progress ?? 0) * 100)}%</strong>
            </div>
            <div className="progress-track"><i style={{ width: `${Math.round((activeJob?.progress ?? 0) * 100)}%` }} /></div>
            <p>{activeJob?.message ?? "Launch a backtest agent to see progress."}</p>
            {activeJob?.error ? <div className="inline-error"><AlertTriangle size={16} />{activeJob.error}</div> : null}
          </div>
        </Panel>

        {visualization ? (
          <Panel title="Performance vs Baseline" subtitle={visualization.baseline_label}>
            <section className="metric-grid metric-grid--compact">
              <MetricCard label="Total Return" value={formatPercent(performanceMetrics.total_return)} tone={toneFromNumber(performanceMetrics.total_return)} />
              <MetricCard label="CAGR" value={formatPercent(performanceMetrics.cagr)} tone={toneFromNumber(performanceMetrics.cagr)} />
              <MetricCard label="Sharpe" value={formatNumber(performanceMetrics.sharpe)} />
              <MetricCard label="Max Drawdown" value={formatPercent(performanceMetrics.max_drawdown)} tone={toneFromNumber(toNumber(performanceMetrics.max_drawdown) * -1)} />
              <MetricCard label="Win Rate" value={formatPercent(performanceMetrics.win_rate)} />
              <MetricCard label="Profit Factor" value={optionalNumber(performanceMetrics.profit_factor)} />
              <MetricCard label="Baseline Return" value={formatPercent(performanceMetrics.baseline_total_return)} tone={toneFromNumber(performanceMetrics.baseline_total_return)} />
              <MetricCard label="Outperformance" value={formatPercent(performanceMetrics.benchmark_outperformance)} tone={toneFromNumber(performanceMetrics.benchmark_outperformance)} />
            </section>
          </Panel>
        ) : null}

        <Panel title="Validation Summary" subtitle={result?.decision?.headline ?? "Waiting for completed result"}>
          {result ? (
            <>
              <section className="metric-grid metric-grid--compact">
                <MetricCard label="Annual Return" value={metricValue(summary, "annualized_return", formatPercent)} tone={toneFromNumber(summary.annualized_return)} />
                <MetricCard label="Sharpe" value={metricValue(summary, "sharpe", formatNumber)} />
                <MetricCard label="DSR" value={metricValue(validation, "dsr", formatNumber)} />
                <MetricCard label="PBO" value={metricValue(validation, "pbo", formatPercent)} />
                <MetricCard label="Max DD" value={metricValue(summary, "max_drawdown", formatPercent)} tone={toneFromNumber(toNumber(summary.max_drawdown) * -1)} />
                <MetricCard label="Turnover" value={metricValue(summary, "avg_turnover", formatPercent)} />
              </section>
              {comparisonRows.length ? (
                <DataTable
                  rows={comparisonRows}
                  empty="No mode comparison rows were returned."
                  getKey={(row) => String(row.trading_mode ?? row.execution_interval ?? "mode")}
                  columns={[
                    { key: "mode", header: "Mode", render: (row) => <Badge label={String(row.trading_mode ?? "unknown")} tone={row.trading_mode === "short_term" ? "warn" : "info"} /> },
                    { key: "interval", header: "Interval", render: (row) => String(row.execution_interval ?? "n/a") },
                    { key: "sharpe", header: "Sharpe", align: "right", render: (row) => optionalNumber(row.sharpe) },
                    { key: "return", header: "Return", align: "right", render: (row) => formatPercent(row.total_return) },
                    { key: "drawdown", header: "Max DD", align: "right", render: (row) => formatPercent(row.max_drawdown) },
                    { key: "verdict", header: "Verdict", render: (row) => String(row.verdict ?? "n/a") }
                  ]}
                />
              ) : null}
              <div className="check-grid">
                {result.decision.checks.map((check) => (
                  <div key={check.name} className={check.passed ? "check-card check-card--pass" : "check-card check-card--fail"}>
                    <CheckCircle2 size={17} />
                    <strong>{check.name}</strong>
                    <span>{check.value === null ? "n/a" : formatNumber(check.value)}</span>
                    <small>{check.message}</small>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <Explainer title="No result yet" body="Completed jobs will show validation metrics here. Failed jobs keep their error messages in the status panel." />
          )}
        </Panel>
      </div>

      {visualization || result ? (
        <Panel title="Real-Time Backtest Visualization" subtitle={result?.artifact_dir ?? activeJob?.message ?? "Waiting for synchronized fold snapshots"}>
          {visualization ? (
            <BacktestPerformanceChart payload={visualization} isRunning={activeJob?.status === "running"} />
          ) : result ? (
            <BacktestEquityChart points={result.equity_curve_points} />
          ) : null}
          <div className="artifact-note">
            <strong>Artifacts saved to:</strong>
            <span>{result?.artifact_dir ?? "Artifacts will be available after completion."}</span>
          </div>
        </Panel>
      ) : null}

      {visualization ? (
        <Panel title="Trade-Level Summary" subtitle="Closed trades from the fill ledger">
          <DataTable
            rows={tradeRows}
            empty="No entry or exit events were produced by the current backtest."
            getKey={(row) => row.id}
            columns={[
              { key: "symbol", header: "Symbol", render: (row) => row.symbol ?? "Portfolio" },
              { key: "side", header: "Side", render: (row) => <Badge label={row.side} tone={row.side === "long" ? "good" : row.side === "short" ? "bad" : "neutral"} /> },
              { key: "entry", header: "Entry", render: (row) => row.entry_timestamp.slice(0, 10) },
              { key: "exit", header: "Exit", render: (row) => row.exit_timestamp?.slice(0, 10) ?? "Open" },
              { key: "holding", header: "Bars", align: "right", render: (row) => formatNumber(row.holding_period_bars, 0) },
              { key: "pnl", header: "P&L", align: "right", render: (row) => optionalNumber(row.pnl) },
              { key: "return", header: "Return", align: "right", render: (row) => formatPercent(row.return_pct) },
              { key: "status", header: "Status", render: (row) => <Badge label={row.status} tone={row.status === "closed" ? "neutral" : "warn"} /> }
            ]}
          />
        </Panel>
      ) : null}

      <Panel title="Recent Backtest Jobs" subtitle={`${jobs.length} persisted jobs`}>
        <DataTable
          rows={jobs}
          empty="No backtest jobs have been launched yet."
          getKey={(row) => row.id}
          columns={[
            { key: "status", header: "Status", render: (row) => <Badge label={jobDisplayStatus(row)} tone={statusTone(jobDisplayStatus(row))} /> },
            { key: "pipeline", header: "Pipeline", render: (row) => pipelineLabel(String((row.request as Record<string, unknown>).pipeline ?? "unknown")) },
            { key: "progress", header: "Progress", align: "right", render: (row) => `${Math.round((row.progress ?? 0) * 100)}%` },
            { key: "message", header: "Message", render: (row) => row.message ?? "-" },
            { key: "open", header: "Inspect", render: (row) => <button type="button" className="link-button" onClick={() => setActiveJob(row)}>Open</button> }
          ]}
        />
      </Panel>

      <section className="explain-grid">
        <Explainer title="Backtest output" body="Completed runs produce summary JSON, validation JSON, fold metrics, equity curves, charts, and a decision report." />
        <Explainer title="Not a trading signal by itself" body="A good run is a research candidate. It should be paper replayed and monitored before real broker integration." />
        <Explainer title="Costs are included" body="The broker simulation subtracts commission, spread, slippage, impact, borrow, funding, and latency assumptions." />
      </section>
    </div>
  );
}
