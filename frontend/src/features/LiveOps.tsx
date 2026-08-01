import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Building2, CalendarDays, CheckCircle2, Loader2, Newspaper, Play, Plus, Route } from "lucide-react";

import { getPaperRunJob, listPaperRunJobs, startPaperRunJob } from "../api/client";
import type { PaperAgentConfig, PaperDashboardPayload, PaperExecutionConfig, PaperRunJob, PaperRunRequest, StrategyCatalogItem } from "../api/types";
import { AgentEditor, withDefaultSentiment } from "../components/AgentEditor";
import { Badge } from "../components/Badge";
import { ExposureBars, OrderNotionalBars, PortfolioEquityChart, RiskReturnMap } from "../components/Charts";
import { Explainer, MetricCard, Panel } from "../components/Cards";
import { DataTable } from "../components/Table";
import { formatCurrency, formatNumber, formatPercent, jobDisplayStatus, statusTone } from "../utils/format";
import { agentHasSentiment, defaultAgentId, getAllOrders, parseJsonObject } from "../utils/quant";

type DateMode = "single" | "range";

const defaultExecution: PaperExecutionConfig = {
  initial_cash: 100_000,
  commission_bps: 0.5,
  slippage_bps: 1.0,
  min_trade_notional: 100,
  weight_tolerance: 0.0025
};

function starterAgents(): PaperAgentConfig[] {
  return [
    {
      id: defaultAgentId(),
      name: "etf_trend_core",
      pipeline: "etf_trend",
      symbols: ["SPY", "QQQ", "IWM", "TLT", "GLD", "XLK"],
      interval: "1d",
      lookback_bars: 800,
      params: { top_n: 3, trend_window: 200, rebalance_bars: 21 }
    },
    {
      id: defaultAgentId(),
      name: "vol_target_trend_shadow",
      pipeline: "volatility_target_trend",
      symbols: ["SPY", "QQQ", "TLT", "GLD"],
      interval: "1d",
      lookback_bars: 360,
      params: { trend_window: 120, volatility_window: 20, target_volatility: 0.15, max_strategy_weight: 0.25 }
    },
    {
      id: defaultAgentId(),
      name: "residual_stat_arb_shadow",
      pipeline: "stat_arb",
      symbols: [],
      interval: "1d",
      lookback_bars: 620,
      sector_map_path: "examples/sector_map.sample.json",
      params: { include_residual_book: true, include_classic_pairs: true, top_n_pairs: 3, residual_lookback: 60 }
    }
  ];
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

function catalogLaunchItems(catalog: StrategyCatalogItem[]) {
  const launchable = catalog.filter((item) => item.family === "Directional" || ["etf_trend", "stat_arb", "graph_stat_arb", "edgar_event", "pead_sentiment", "committee_signal_follower"].includes(item.id));
  return launchable.length ? launchable : [];
}

export function LiveOps({
  payload,
  catalog,
  paperJobs,
  onJobsChange,
  onPaperPayload,
  onRefresh
}: {
  payload: PaperDashboardPayload;
  catalog: StrategyCatalogItem[];
  paperJobs: PaperRunJob[];
  onJobsChange: (jobs: PaperRunJob[]) => void;
  onPaperPayload: (payload: PaperDashboardPayload) => void;
  onRefresh: () => void;
}) {
  const launchCatalog = useMemo(() => catalogLaunchItems(catalog), [catalog]);
  const [initialAgents] = useState<PaperAgentConfig[]>(() => starterAgents());
  const [agents, setAgents] = useState<PaperAgentConfig[]>(() => initialAgents);
  const [execution, setExecution] = useState<PaperExecutionConfig>(defaultExecution);
  const [paramsDrafts, setParamsDrafts] = useState<Record<string, string>>(() =>
    Object.fromEntries(initialAgents.map((agent) => [agent.id, JSON.stringify(agent.params, null, 2)]))
  );
  const [dateMode, setDateMode] = useState<DateMode>("single");
  const [asofDate, setAsofDate] = useState(payload.asof_date ?? "");
  const [asofStart, setAsofStart] = useState(payload.asof_date ?? "");
  const [asofEnd, setAsofEnd] = useState(payload.asof_date ?? "");
  const [activeJob, setActiveJob] = useState<PaperRunJob | null>(paperJobs[0] ?? null);
  const [error, setError] = useState<string | null>(null);
  const [isLaunching, setIsLaunching] = useState(false);
  const orders = useMemo(() => getAllOrders(payload.strategies), [payload.strategies]);

  const replayDays = dateMode === "range" ? businessDayCount(asofStart, asofEnd) : 1;
  const sentimentCount = agents.filter(agentHasSentiment).length;

  function setAgent(next: PaperAgentConfig) {
    setAgents((current) => current.map((agent) => (agent.id === next.id ? next : agent)));
  }

  function addAgent(item = launchCatalog[0]) {
    const example = item?.paper_config_example as {
      symbols?: string[];
      params?: Record<string, unknown>;
      sector_map_path?: string;
      event_file?: string;
      use_sec_companyfacts?: boolean;
      include_sec_filings?: boolean;
      sec_filing_forms?: string[];
      edgar_user_agent?: string;
      lookback_bars?: number;
    } | undefined;
    const agent: PaperAgentConfig = withDefaultSentiment({
      id: defaultAgentId(),
      name: `${item?.id ?? "agent"}_${agents.length + 1}`,
      pipeline: item?.pipeline ?? "etf_trend",
      symbols: example?.symbols ?? ["SPY", "QQQ"],
      interval: "1d",
      lookback_bars: example?.lookback_bars ?? 360,
      sector_map_path: example?.sector_map_path,
      event_file: example?.event_file,
      use_sec_companyfacts: example?.use_sec_companyfacts,
      include_sec_filings: example?.include_sec_filings,
      sec_filing_forms: example?.sec_filing_forms,
      edgar_user_agent: example?.edgar_user_agent,
      params: example?.params ?? {}
    });
    setAgents((current) => [...current, agent]);
    setParamsDrafts((current) => ({ ...current, [agent.id]: JSON.stringify(agent.params, null, 2) }));
  }

  function buildRequest(): PaperRunRequest {
    if (!agents.length) throw new Error("Add at least one paper agent.");
    const names = agents.map((agent) => agent.name.trim());
    if (names.some((name) => !name)) throw new Error("Every agent needs a name.");
    if (new Set(names).size !== names.length) throw new Error("Agent names must be unique because each one owns a ledger.");
    if (dateMode === "range" && replayDays < 1) throw new Error("Date range must contain at least one business day.");
    const missingSecIdentity = agents.filter((agent) =>
      (agent.include_sec_filings || agent.use_sec_companyfacts) && !String(agent.edgar_user_agent ?? "").includes("@")
    );
    if (missingSecIdentity.length) {
      throw new Error(`Official SEC event agents need an SEC user agent with an email: ${missingSecIdentity.map((agent) => agent.name).join(", ")}.`);
    }

    return {
      deployment_config: {
        execution,
        strategies: agents.map((agent) => ({
          name: agent.name,
          pipeline: agent.pipeline,
          symbols: agent.symbols,
          sector_map_path: agent.sector_map_path || undefined,
          event_file: agent.event_file || undefined,
          use_sec_companyfacts: Boolean(agent.use_sec_companyfacts),
          include_sec_filings: Boolean(agent.include_sec_filings),
          sec_filing_forms: agent.sec_filing_forms ?? ["8-K", "10-Q", "10-K"],
          edgar_user_agent: agent.edgar_user_agent || undefined,
          daily_sentiment_file: agent.daily_sentiment_file || undefined,
          news_provider_names: agent.news_provider_names ?? [],
          news_files: agent.news_files ?? [],
          rss_feed_urls: agent.rss_feed_urls ?? [],
          local_web_search_urls: agent.local_web_search_urls ?? [],
          local_web_refresh_minutes: agent.local_web_refresh_minutes ?? 60,
          local_web_max_pages_per_source: agent.local_web_max_pages_per_source ?? 30,
          web_research_urls: agent.web_research_urls ?? [],
          web_research_domains: agent.web_research_domains ?? [],
          web_research_query_terms: agent.web_research_query_terms ?? "",
          web_research_max_articles: agent.web_research_max_articles ?? 4,
          web_research_fetch_article_text: agent.web_research_fetch_article_text ?? true,
          newsapi_api_key: agent.newsapi_api_key || undefined,
          use_finbert: Boolean(agent.use_finbert),
          local_finbert_only: Boolean(agent.local_finbert_only),
          news_topics: agent.news_topics ?? [],
          interval: agent.interval,
          lookback_bars: agent.lookback_bars,
          params: parseJsonObject(paramsDrafts[agent.id] ?? JSON.stringify(agent.params), `${agent.name} parameters`)
        }))
      },
      asof_date: dateMode === "single" ? asofDate || null : null,
      asof_start: dateMode === "range" ? asofStart || null : null,
      asof_end: dateMode === "range" ? asofEnd || null : null
    };
  }

  async function refreshJobs() {
    onJobsChange(await listPaperRunJobs());
  }

  async function launch() {
    setError(null);
    setIsLaunching(true);
    try {
      const job = await startPaperRunJob(buildRequest());
      setActiveJob(job);
      await refreshJobs();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to launch paper agents.");
    } finally {
      setIsLaunching(false);
    }
  }

  useEffect(() => {
    if (!activeJob || !["queued", "running"].includes(activeJob.status)) return;
    const timer = window.setInterval(() => {
      void getPaperRunJob(activeJob.id).then((job) => {
        setActiveJob(job);
        void refreshJobs();
        if (job.status === "completed" && job.result) {
          onPaperPayload(job.result);
        }
      });
    }, 1400);
    return () => window.clearInterval(timer);
  }, [activeJob]);

  return (
    <div className="page-stack">
      <section className="hero-panel">
        <div>
          <p className="eyebrow">Run Paper</p>
          <h2>Run fake-money agents and watch the account change</h2>
          <span>
            Pick the fake cash, choose one date or a replay range, then deploy. Advanced settings let you change symbols,
            timeframes, sentiment, and official SEC company-event inputs.
          </span>
        </div>
        <Badge label="shadow only" tone="warn" />
      </section>

      <section className="metric-grid">
        <MetricCard label="Configured Agents" value={formatNumber(agents.length, 0)} detail="Will run sequentially" />
        <MetricCard label="Replay Days" value={formatNumber(replayDays, 0)} detail={dateMode === "range" ? "Business days" : "Single as-of run"} icon={<CalendarDays size={17} />} />
        <MetricCard label="Sentiment Agents" value={formatNumber(sentimentCount, 0)} detail="News-aware specs" icon={<Newspaper size={17} />} />
        <MetricCard label="Current Equity" value={formatCurrency(payload.totals.equity)} detail="Before the next run" />
      </section>

      <div className="content-grid">
        <Panel title="1. Fake Account And Dates" subtitle="Simple controls first">
          <div className="form-grid">
            <label htmlFor="lo-initial-cash">
              Initial cash
              <input id="lo-initial-cash" type="number" value={execution.initial_cash} onChange={(event) => setExecution({ ...execution, initial_cash: Number(event.target.value) })} />
            </label>
            <label htmlFor="lo-commission-bps">
              Commission bps
              <input id="lo-commission-bps" type="number" value={execution.commission_bps} onChange={(event) => setExecution({ ...execution, commission_bps: Number(event.target.value) })} />
            </label>
            <label htmlFor="lo-slippage-bps">
              Slippage bps
              <input id="lo-slippage-bps" type="number" value={execution.slippage_bps} onChange={(event) => setExecution({ ...execution, slippage_bps: Number(event.target.value) })} />
            </label>
            <label htmlFor="lo-min-trade-notional">
              Min trade notional
              <input id="lo-min-trade-notional" type="number" value={execution.min_trade_notional} onChange={(event) => setExecution({ ...execution, min_trade_notional: Number(event.target.value) })} />
            </label>
          </div>

          <div className="toggle-row">
            <button type="button" className={dateMode === "single" ? "pill pill--active" : "pill"} onClick={() => setDateMode("single")}>Single date</button>
            <button type="button" className={dateMode === "range" ? "pill pill--active" : "pill"} onClick={() => setDateMode("range")}>Date range replay</button>
          </div>

          {dateMode === "single" ? (
            <label htmlFor="lo-as-of-date">
              As-of date
              <input id="lo-as-of-date" value={asofDate} onChange={(event) => setAsofDate(event.target.value)} placeholder="YYYY-MM-DD or blank for today" />
            </label>
          ) : (
            <div className="form-grid">
              <label htmlFor="lo-replay-start">
                Replay start
                <input id="lo-replay-start" value={asofStart} onChange={(event) => setAsofStart(event.target.value)} placeholder="YYYY-MM-DD" />
              </label>
              <label htmlFor="lo-replay-end">
                Replay end
                <input id="lo-replay-end" value={asofEnd} onChange={(event) => setAsofEnd(event.target.value)} placeholder="YYYY-MM-DD" />
              </label>
            </div>
          )}

          <div className="button-row">
            <button type="button" className="primary-button" onClick={() => void launch()} disabled={isLaunching}>
              {isLaunching ? <Loader2 size={17} /> : <Play size={17} />}
              {isLaunching ? "Launching" : "Deploy agents"}
            </button>
            <button type="button" className="ghost-button" onClick={onRefresh}>Refresh state</button>
          </div>

          {error ? (
            <div className="inline-error">
              <AlertTriangle size={16} />
              {error}
            </div>
          ) : null}
        </Panel>

        <Panel title="2. Worker Progress" subtitle={activeJob?.id ?? "No active job"}>
          <div className="progress-card">
            <div className="progress-card__top">
              <Badge label={jobDisplayStatus(activeJob)} tone={statusTone(jobDisplayStatus(activeJob))} />
              <strong>{Math.round((activeJob?.progress ?? 0) * 100)}%</strong>
            </div>
            <div className="progress-track">
              <i style={{ width: `${Math.round((activeJob?.progress ?? 0) * 100)}%` }} />
            </div>
            <p>{activeJob?.message ?? "Configure agents and launch a paper job."}</p>
            <div className="execution-path">
              {["Load config", "Build signals", "Simulate orders", "Save ledgers"].map((step, index) => (
                <div key={step} className={(activeJob?.progress ?? 0) >= (index + 1) / 4 ? "path-step path-step--done" : "path-step"}>
                  <CheckCircle2 size={16} />
                  {step}
                </div>
              ))}
            </div>
          </div>
          <Explainer
            title="What deploy means here"
            body="Deploy means run the fake-money paper engine. It does not route orders to a broker. The result is saved positions, simulated orders, PnL, and charts."
          />
        </Panel>
      </div>

      <section className="explain-grid explain-grid--simple">
        <Explainer title="What runs now" body={`${formatNumber(agents.length, 0)} agent(s) will run with ${formatCurrency(execution.initial_cash)} starting fake cash each.`} icon={<Route size={17} />} />
        <Explainer title="Official events" body="Turn on SEC filings inside agent setup to include earnings 8-K, 10-Q, and 10-K dates. It requires a contact email user-agent." icon={<Building2 size={17} />} />
        <Explainer title="How to read the result" body="After completion, Home shows the plain-English money summary. Extra charts are below for deeper inspection." />
      </section>

      <details className="advanced-details">
        <summary>Change agents, symbols, sentiment, or official SEC events</summary>
        <Panel title="Agent Setup" subtitle="Methods, universes, timeframes, sentiment, official events">
          <div className="template-grid">
            {launchCatalog.slice(0, 5).map((item) => (
              <button key={item.id} type="button" className="template-card" onClick={() => addAgent(item)}>
                <Plus size={16} />
                <strong>{item.name}</strong>
                <span>{item.summary}</span>
              </button>
            ))}
          </div>
          <div className="agent-grid">
            {agents.map((agent) => (
              <AgentEditor
                key={agent.id}
                agent={agent}
                catalog={launchCatalog}
                paramsText={paramsDrafts[agent.id] ?? JSON.stringify(agent.params, null, 2)}
                onChange={setAgent}
                onParamsChange={(value) => setParamsDrafts((current) => ({ ...current, [agent.id]: value }))}
                onClone={() => {
                  const cloned = { ...agent, id: defaultAgentId(), name: `${agent.name}_copy` };
                  setAgents((current) => [...current, cloned]);
                  setParamsDrafts((current) => ({ ...current, [cloned.id]: current[agent.id] ?? JSON.stringify(agent.params, null, 2) }));
                }}
                onRemove={() => {
                  setAgents((current) => current.filter((item) => item.id !== agent.id));
                  setParamsDrafts((current) => {
                    const next = { ...current };
                    delete next[agent.id];
                    return next;
                  });
                }}
              />
            ))}
          </div>
        </Panel>
      </details>

      <details className="advanced-details">
        <summary>Show charts, risk, simulated orders, and job history</summary>
        <div className="content-grid content-grid--wide">
          <Panel title="Fake Money Over Time" subtitle="Saved paper ledger history">
            <PortfolioEquityChart strategies={payload.strategies} />
            <Explainer title="How to read it" body="The line is fake account equity through saved paper runs. If it has only one point, run a date-range replay first." />
          </Panel>
          <Panel title="Simulated Orders" subtitle="Latest rebalance size">
            <OrderNotionalBars orders={orders} />
          </Panel>
        </div>

        <div className="content-grid">
          <Panel title="Exposure By Agent" subtitle="How much fake capital is deployed">
            <ExposureBars strategies={payload.strategies} />
          </Panel>
          <Panel title="Risk / Return Map" subtitle="Profit/loss versus exposure">
            <RiskReturnMap strategies={payload.strategies} />
          </Panel>
        </div>

        <section className="explain-grid">
          <Explainer title="Signal path" body="The backend builds each strategy snapshot from the selected method, symbols, timeframe, lookback, and optional sentiment/event settings." icon={<Route size={17} />} />
          <Explainer title="Ledger path" body="The fake broker compares target weights with current positions, creates simulated trades, updates cash, then saves JSON ledgers." />
          <Explainer title="Replay path" body="Date ranges run one business day at a time, so you can observe how the book would have evolved through time." />
        </section>

        <Panel title="Recent Paper Jobs" subtitle={`${paperJobs.length} persisted jobs`}>
          <DataTable
            rows={paperJobs}
            empty="No paper jobs have been launched yet."
            getKey={(row) => row.id}
            columns={[
              { key: "status", header: "Status", render: (row) => <Badge label={jobDisplayStatus(row)} tone={statusTone(jobDisplayStatus(row))} /> },
              { key: "progress", header: "Progress", align: "right", render: (row) => `${Math.round(row.progress * 100)}%` },
              { key: "stage", header: "Stage", render: (row) => row.stage },
              { key: "message", header: "Message", render: (row) => row.message },
              { key: "select", header: "Inspect", render: (row) => <button type="button" className="link-button" onClick={() => setActiveJob(row)}>Open</button> }
            ]}
          />
        </Panel>
      </details>
    </div>
  );
}
