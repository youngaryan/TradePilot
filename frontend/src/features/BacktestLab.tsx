import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, FlaskConical, Loader2, Play, ShieldAlert, SlidersHorizontal } from "lucide-react";

import { getBacktestJob, listBacktestJobs, startBacktest } from "../api/client";
import type { BacktestJob, BacktestRunRequest, BacktestTemplate, StrategyCatalogItem } from "../api/types";
import { Badge } from "../components/Badge";
import { BacktestEquityChart } from "../components/Charts";
import { Explainer, MetricCard, Panel } from "../components/Cards";
import { DataTable } from "../components/Table";
import { formatNumber, formatPercent, pipelineLabel, splitList, splitSymbols, statusTone, toneFromNumber, toNumber } from "../utils/format";
import { parseJsonObject } from "../utils/quant";

const defaultRequest: BacktestRunRequest = {
  pipeline: "time_series_momentum",
  symbols: ["SPY", "QQQ", "TLT", "GLD"],
  start: "2018-01-01",
  end: "2026-04-15",
  interval: "1d",
  experiment_name: "cockpit_backtest",
  sector_map_path: null,
  event_file: null,
  use_sec_companyfacts: false,
  include_sec_filings: false,
  sec_filing_forms: ["8-K", "10-Q", "10-K"],
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

const SECTOR_MAP_PIPELINES = new Set(["stat_arb", "graph_stat_arb"]);
const EVENT_PIPELINES = new Set(["edgar_event", "pead_sentiment"]);

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

function templateToRequest(template: BacktestTemplate): BacktestRunRequest {
  const isSectorMapPipeline = SECTOR_MAP_PIPELINES.has(template.pipeline);
  const isEventPipeline = EVENT_PIPELINES.has(template.pipeline);
  return {
    ...defaultRequest,
    pipeline: template.pipeline,
    symbols: isSectorMapPipeline ? [] : template.symbols,
    start: template.start,
    end: template.end,
    experiment_name: template.id,
    sector_map_path: isSectorMapPipeline ? template.sector_map_path ?? "examples/sector_map.sample.json" : null,
    event_file: isEventPipeline ? template.event_file ?? "examples/events.sample.csv" : null,
    use_sec_companyfacts: false,
    include_sec_filings: false,
    edgar_user_agent: null,
    parameters: template.parameters
  };
}

export function BacktestLab({
  catalog,
  templates,
  jobs,
  onJobsChange
}: {
  catalog: StrategyCatalogItem[];
  templates: BacktestTemplate[];
  jobs: BacktestJob[];
  onJobsChange: (jobs: BacktestJob[]) => void;
}) {
  const [request, setRequest] = useState<BacktestRunRequest>(defaultRequest);
  const [parametersText, setParametersText] = useState(JSON.stringify(defaultRequest.parameters, null, 2));
  const [activeJob, setActiveJob] = useState<BacktestJob | null>(jobs[0] ?? null);
  const [error, setError] = useState<string | null>(null);
  const [isLaunching, setIsLaunching] = useState(false);
  const activeStrategy = useMemo(
    () => catalog.find((item) => item.pipeline === request.pipeline || item.id === request.pipeline),
    [catalog, request.pipeline]
  );
  const usesSectorMap = SECTOR_MAP_PIPELINES.has(request.pipeline);
  const usesEventInputs = EVENT_PIPELINES.has(request.pipeline);
  const usesSentimentInputs = request.pipeline === "pead_sentiment";
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
    setParametersText(JSON.stringify(next.parameters, null, 2));
    setError(null);
  }

  function applyPipeline(pipeline: string) {
    const item = catalog.find((strategy) => strategy.pipeline === pipeline || strategy.id === pipeline);
    const example = (item?.paper_config_example ?? {}) as PipelineExample;
    const isSectorMapPipeline = SECTOR_MAP_PIPELINES.has(pipeline);
    const isEventPipeline = EVENT_PIPELINES.has(pipeline);
    const params = asParameterObject(example.params) ?? {};
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
      parameters: params
    };
    setRequest(next);
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
      const job = await startBacktest({
        ...request,
        symbols: usesSectorMap ? [] : request.symbols,
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
            <label>
              Pipeline
              <select value={request.pipeline} onChange={(event) => applyPipeline(event.target.value)}>
                {catalog.map((item) => (
                  <option key={item.id} value={item.pipeline}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Symbols
              <input
                value={usesSectorMap ? "" : request.symbols.join(" ")}
                disabled={usesSectorMap}
                onChange={(event) => setRequest({ ...request, symbols: splitSymbols(event.target.value) })}
                placeholder={usesSectorMap ? "Loaded from the sector map" : "SPY QQQ TLT GLD"}
              />
              {usesSectorMap ? <small>This pipeline trades every ticker in the sector map, not the Symbols box.</small> : null}
            </label>
            <label>
              Start
              <input value={request.start} onChange={(event) => setRequest({ ...request, start: event.target.value })} />
            </label>
            <label>
              End
              <input value={request.end} onChange={(event) => setRequest({ ...request, end: event.target.value })} />
            </label>
            {usesSectorMap ? (
              <label>
                Sector map
                <input value={request.sector_map_path ?? ""} onChange={(event) => setRequest({ ...request, sector_map_path: event.target.value || null })} placeholder="examples/sector_map.sample.json" />
                <small>Required so graph/stat-arb sectors are explicit and auditable.</small>
              </label>
            ) : null}
            {usesEventInputs ? (
              <label>
                Event file
                <input value={request.event_file ?? ""} onChange={(event) => setRequest({ ...request, event_file: event.target.value || null })} placeholder="examples/events.sample.csv" />
                <small>Use the sample file for local PEAD testing, or enable official SEC sources below.</small>
              </label>
            ) : null}
          </div>

          {usesEventInputs ? (
          <div className="sentiment-panel official-events-panel">
            <label className="checkbox-line">
              <input
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
            <label className="checkbox-line">
              <input
                type="checkbox"
                checked={Boolean(request.use_sec_companyfacts)}
                onChange={(event) => setRequest({ ...request, use_sec_companyfacts: event.target.checked })}
              />
              Include SEC company facts scores
            </label>
            {request.include_sec_filings || request.use_sec_companyfacts ? (
              <div className="form-grid">
                {request.include_sec_filings ? (
                  <label>
                    SEC filing forms
                    <input
                      value={(request.sec_filing_forms ?? ["8-K", "10-Q", "10-K"]).join(" ")}
                      onChange={(event) => setRequest({ ...request, sec_filing_forms: splitList(event.target.value).map((form) => form.toUpperCase()) })}
                      placeholder="8-K 10-Q 10-K"
                    />
                  </label>
                ) : null}
                <label>
                  SEC user agent
                  <input
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
              <strong>PEAD sentiment overlay</strong>
              <p>
                PEAD v1 can run from event scores alone. Add daily sentiment when you have a CSV/parquet with
                date, ticker, sentiment_score, sentiment_abs, confidence, article_count, and probability columns.
              </p>
              <div className="form-grid">
                <label>
                  Daily sentiment file
                  <input
                    value={stringParameter("daily_sentiment_file")}
                    onChange={(event) => updateParameter("daily_sentiment_file", event.target.value || null)}
                    placeholder="examples/daily_sentiment.sample.csv"
                  />
                  <small>Optional. Leave blank to test event-score-only PEAD.</small>
                </label>
                <label>
                  Sentiment window days
                  <input
                    type="number"
                    value={stringParameter("sentiment_window_days") || "2"}
                    onChange={(event) => updateParameter("sentiment_window_days", Number(event.target.value))}
                  />
                  <small>How many prior calendar days of sentiment are blended into each event.</small>
                </label>
              </div>
              <label className="checkbox-line">
                <input
                  type="checkbox"
                  checked={booleanParameter("require_sentiment")}
                  onChange={(event) => updateParameter("require_sentiment", event.target.checked)}
                />
                Require sentiment coverage before PEAD can trade
              </label>
            </div>
          ) : null}

          <div className="form-grid form-grid--tight">
            <label>
              Train bars
              <input type="number" value={request.train_bars} onChange={(event) => setRequest({ ...request, train_bars: Number(event.target.value) })} />
            </label>
            <label>
              Test bars
              <input type="number" value={request.test_bars} onChange={(event) => setRequest({ ...request, test_bars: Number(event.target.value) })} />
            </label>
            <label>
              Purge bars
              <input type="number" value={request.purge_bars} onChange={(event) => setRequest({ ...request, purge_bars: Number(event.target.value) })} />
            </label>
            <label>
              PBO partitions
              <input type="number" value={request.pbo_partitions} onChange={(event) => setRequest({ ...request, pbo_partitions: Number(event.target.value) })} />
            </label>
          </div>

          <label>
            Parameters JSON
            <textarea rows={8} value={parametersText} onChange={(event) => setParametersText(event.target.value)} spellCheck={false} />
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
              <Badge label={activeJob?.status ?? "idle"} tone={statusTone(activeJob?.status)} />
              <strong>{Math.round((activeJob?.progress ?? 0) * 100)}%</strong>
            </div>
            <div className="progress-track"><i style={{ width: `${Math.round((activeJob?.progress ?? 0) * 100)}%` }} /></div>
            <p>{activeJob?.message ?? "Launch a backtest agent to see progress."}</p>
            {activeJob?.error ? <div className="inline-error"><AlertTriangle size={16} />{activeJob.error}</div> : null}
          </div>
        </Panel>

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

      {result ? (
        <Panel title="Equity And Drawdown" subtitle={result.artifact_dir ?? "No artifact directory"}>
          <BacktestEquityChart points={result.equity_curve_points} />
          <div className="artifact-note">
            <strong>Artifacts saved to:</strong>
            <span>{result.artifact_dir}</span>
          </div>
        </Panel>
      ) : null}

      <Panel title="Recent Backtest Jobs" subtitle={`${jobs.length} persisted jobs`}>
        <DataTable
          rows={jobs}
          empty="No backtest jobs have been launched yet."
          getKey={(row) => row.id}
          columns={[
            { key: "status", header: "Status", render: (row) => <Badge label={row.status} tone={statusTone(row.status)} /> },
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
