import { useMemo } from "react";
import { Link, useNavigate } from "react-router";
import {
  Activity,
  ArrowRight,
  FlaskConical,
  Layers,
  RefreshCw,
  Sparkles,
} from "lucide-react";

import type { AccessContext } from "../../access/model";
import type { BacktestJob } from "../../api/types";
import { SeriesChart, type SeriesPoint } from "../../charts/SeriesChart";
import {
  Button,
  Card,
  DataGrid,
  Disclosure,
  EmptyPanel,
  InlineNotice,
  LoadingBlock,
  Metric,
  MetricGrid,
  PageHeader,
  SectionTitle,
  StatusIndicator,
  Tag,
  type GridColumn,
} from "../../ui";
import { formatCurrency, formatDateTime, formatNumber, formatPercent, pipelineLabel } from "../../utils/format";
import { useOverviewData } from "./useOverviewData";

export interface OverviewPageProps {
  activeOrgId?: string | null;
  /** Supplied by the shell. When absent the page falls back to `hasPremium`. */
  access?: AccessContext;
  hasPremium?: boolean;
  displayName?: string;
  workspaceLabel?: string;
  backendOnline?: boolean;
  onRefreshSession?: () => void;
}

const METRIC_EXPLAIN = {
  equity: {
    term: "Simulated equity",
    body: "The total value of the simulated paper ledger across every agent in this workspace. It is produced by an internal simulator — it is not a broker account balance and no real money is involved.",
  },
  pnl: {
    term: "Simulated profit and loss",
    body: "The change in simulated equity recorded on the latest paper run, including modelled commission and slippage. Nothing here is realised cash.",
  },
  agents: {
    term: "Paper agents",
    body: "Strategies currently deployed to the paper simulator. Each keeps its own simulated cash, positions, target weights, and order log.",
  },
  runs: {
    term: "Validation runs",
    body: "Backtests saved in this workspace. A validation run measures how a rule would have behaved on historical data — it does not predict future results.",
  },
};

function backtestReturn(job: BacktestJob): number | null {
  const summary = (job.result?.summary ?? {}) as Record<string, unknown>;
  const value = summary.total_return;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function backtestPbo(job: BacktestJob): number | null {
  const summary = (job.result?.summary ?? {}) as Record<string, unknown>;
  const value = summary.pbo;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Overview — the answer to "where am I, what is the state of my research, and
 * what should I do next?".
 *
 * Every figure comes from a tenant-scoped API response. When a value has not
 * been produced yet the panel says so explicitly instead of showing a zero that
 * looks like a measurement.
 */
export function OverviewPage({
  activeOrgId = null,
  access,
  hasPremium,
  displayName,
  workspaceLabel,
  backendOnline,
  onRefreshSession,
}: OverviewPageProps) {
  const navigate = useNavigate();
  const data = useOverviewData(activeOrgId);
  const premium = access ? access.hasPremium : Boolean(hasPremium);

  const strategies = data.paper?.strategies ?? [];
  const leaderboard = data.paper?.leaderboard ?? [];
  const totals = data.paper?.totals;
  const hasPaperState = strategies.length > 0 || leaderboard.length > 0;

  const strategyName = (pipeline: string | null | undefined) => {
    if (!pipeline) return "Unknown strategy";
    const known = data.catalog?.find((item) => item.pipeline === pipeline)?.name;
    if (known) return known;
    if (pipeline.startsWith("user_strategy:")) return "Workspace strategy";
    if (pipeline.startsWith("marketplace_strategy:")) return "Community strategy";
    return pipelineLabel(pipeline);
  };

  const equitySeries = useMemo<SeriesPoint[]>(() => {
    // Aggregate every agent's history into one workspace equity curve, keyed by
    // timestamp so agents deployed at different times still line up.
    const byTimestamp = new Map<string, number>();
    for (const strategy of strategies) {
      for (const row of strategy.history ?? []) {
        const timestamp = String(row.timestamp ?? "").slice(0, 10);
        const equity = Number(row.equity_after);
        if (!timestamp || !Number.isFinite(equity)) continue;
        byTimestamp.set(timestamp, (byTimestamp.get(timestamp) ?? 0) + equity);
      }
    }
    return Array.from(byTimestamp.entries())
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([label, value]) => ({ label, value }));
  }, [strategies]);

  const completedRuns = data.backtestJobs.filter((job) => job.status === "completed");
  const runningRuns = data.backtestJobs.filter((job) => job.status === "queued" || job.status === "running");
  const failedRuns = data.backtestJobs.filter((job) => job.status === "failed" || job.status === "interrupted");
  const deployableRuns = completedRuns.filter((job) => Boolean(job.result));

  const researchCompleted = data.researchJobs.filter((job) => job.status === "completed");
  const researchRunning = data.researchJobs.filter((job) => job.status === "queued" || job.status === "running");

  // The API returns a zero-filled `totals` block even before the first paper run.
  // A zero is a *measurement*, so only treat these as values once a run exists.
  const hasLedger = hasPaperState || Boolean(data.paper?.run_timestamp_utc) || Boolean(data.paper?.asof_date);
  const dailyPnl = hasLedger ? totals?.daily_pnl : undefined;
  const equity = hasLedger ? totals?.equity : undefined;
  const priorEquity = equity != null && dailyPnl != null ? equity - dailyPnl : null;
  const dailyPct = priorEquity && priorEquity !== 0 && dailyPnl != null ? dailyPnl / priorEquity : null;

  const sentimentRows = data.sentiment?.daily_points?.length ?? 0;
  const sentimentAnchor = (() => {
    const dates = (data.sentiment?.daily_points ?? [])
      .map((point) => String(point.date ?? "").slice(0, 10))
      .filter((value) => /^\d{4}-\d{2}-\d{2}$/.test(value))
      .sort();
    return dates.at(-1) ?? null;
  })();

  const agentColumns: Array<GridColumn<typeof leaderboard[number]>> = [
    {
      key: "agent",
      header: "Agent",
      render: (row) => (
        <span className="stacked-cell">
          <strong>{row.strategy}</strong>
          <span>{strategyName(row.pipeline)}</span>
        </span>
      ),
    },
    {
      key: "equity",
      header: "Simulated equity",
      align: "right",
      render: (row) => formatCurrency(row.equity),
    },
    {
      key: "return",
      header: "Return since start",
      align: "right",
      render: (row) => (
        <span className={row.return_since_inception >= 0 ? "ui-pos" : "ui-neg"}>
          {row.return_since_inception >= 0 ? "+" : ""}
          {formatPercent(row.return_since_inception)}
        </span>
      ),
    },
    {
      key: "pnl",
      header: "Latest P&L",
      align: "right",
      render: (row) => (
        <span className={row.daily_pnl >= 0 ? "ui-pos" : "ui-neg"}>
          {row.daily_pnl >= 0 ? "+" : ""}
          {formatCurrency(row.daily_pnl)}
        </span>
      ),
    },
    {
      key: "trades",
      header: "Trades",
      align: "right",
      render: (row) => formatNumber(row.trade_count, 0),
    },
  ];

  const nextActions = buildNextActions({
    premium,
    hasPaperState,
    deployableRunCount: deployableRuns.length,
    completedRunCount: completedRuns.length,
    catalogCount: data.catalog?.length ?? 0,
    sentimentRows,
    researchCount: researchCompleted.length,
  });

  return (
    <>
      <PageHeader
        eyebrow="Overview"
        title={displayName ? `Research desk — ${displayName}` : "Research desk"}
        description="The current state of this workspace: simulated portfolio, validation activity, research output, and how fresh the underlying data is."
        meta={
          <>
            {workspaceLabel ? <span>Workspace: {workspaceLabel}</span> : null}
            <StatusIndicator tone={backendOnline === false ? "warn" : "good"}>
              {backendOnline === false ? "API degraded" : "API online"}
            </StatusIndicator>
            <span>
              Paper ledger as of {data.paper?.asof_date ?? "no run yet"}
              {data.paper?.run_timestamp_utc ? ` · run ${formatDateTime(data.paper.run_timestamp_utc)}` : ""}
            </span>
          </>
        }
        actions={
          <Button
            variant="secondary"
            icon={<RefreshCw size={14} className={data.isRefreshing ? "spin" : undefined} />}
            disabled={data.isRefreshing}
            onClick={() => {
              void data.reload();
              onRefreshSession?.();
            }}
          >
            {data.isRefreshing ? "Refreshing" : "Refresh"}
          </Button>
        }
      />

      {data.jobsError ? (
        <InlineNotice tone="warn" title="Partial data">
          {data.jobsError} The figures below reflect only what loaded successfully.
        </InlineNotice>
      ) : null}

      <MetricGrid>
        <Metric
          label="Simulated equity"
          explain={METRIC_EXPLAIN.equity}
          value={equity != null ? formatCurrency(equity) : undefined}
          unavailable={equity == null ? "No paper ledger yet" : undefined}
          footnote={
            equity != null
              ? `${strategies.length} ${strategies.length === 1 ? "agent" : "agents"} · simulated capital`
              : "Deploy a validated backtest to create one"
          }
        />
        <Metric
          label="Latest simulated P&L"
          explain={METRIC_EXPLAIN.pnl}
          value={dailyPnl != null ? `${dailyPnl >= 0 ? "+" : ""}${formatCurrency(dailyPnl)}` : undefined}
          unavailable={dailyPnl == null ? "Not measured yet" : undefined}
          tone={dailyPnl == null ? "neutral" : dailyPnl >= 0 ? "good" : "bad"}
          footnote={
            dailyPct != null
              ? `${dailyPct >= 0 ? "+" : ""}${formatPercent(dailyPct)} of prior equity`
              : data.paper?.asof_date
                ? `As of ${data.paper.asof_date}`
                : undefined
          }
        />
        <Metric
          label="Paper agents"
          explain={METRIC_EXPLAIN.agents}
          value={formatNumber(strategies.length, 0)}
          footnote={hasPaperState ? "Running forward on simulated capital" : "None deployed"}
        />
        <Metric
          label="Validation runs"
          explain={METRIC_EXPLAIN.runs}
          value={formatNumber(data.backtestJobs.length, 0)}
          footnote={
            data.backtestJobs.length
              ? `${completedRuns.length} completed · ${runningRuns.length} in flight · ${failedRuns.length} failed`
              : "No backtests saved yet"
          }
        />
      </MetricGrid>

      <div className="grid-two grid-two--wide-left">
        <Card
          title="Simulated portfolio"
          subtitle="Forward simulation on paper capital. Historical validation is shown separately under Backtests."
          actions={<Tag tone="info">Forward simulation</Tag>}
        >
          {data.paperLoading ? (
            <LoadingBlock label="Loading the simulated portfolio" lines={4} />
          ) : data.paperError ? (
            <InlineNotice tone="bad" title="Portfolio unavailable">
              {data.paperError}
            </InlineNotice>
          ) : !hasPaperState ? (
            <EmptyPanel
              icon={<Activity size={18} />}
              title="No paper agents running yet"
              body="Paper trading starts from a completed backtest, so the strategy you deploy has already been validated against history. Run a backtest first, then deploy it here."
              actions={
                <>
                  <Button variant="primary" icon={<FlaskConical size={14} />} onClick={() => navigate("/backtests")}>
                    Run a backtest
                  </Button>
                  <Button variant="secondary" icon={<Layers size={14} />} onClick={() => navigate("/strategies")}>
                    Browse strategies
                  </Button>
                </>
              }
            />
          ) : (
            <>
              {equitySeries.length > 1 ? (
                <SeriesChart
                  points={equitySeries}
                  title="Simulated equity across all paper agents"
                  seriesLabel="Simulated equity"
                  caption="Sum of every agent's simulated equity by ledger date. Gaps mean an agent had not been deployed yet on that date."
                  format={(value) => formatCurrency(value)}
                />
              ) : (
                <InlineNotice tone="neutral" compact>
                  Only one ledger observation exists so far, so there is no curve to plot yet.
                </InlineNotice>
              )}
              <DataGrid
                rows={leaderboard}
                columns={agentColumns}
                caption="Paper agents with simulated equity, return since inception, latest profit and loss, and trade count"
                getKey={(row, index) => `${row.strategy}-${row.pipeline}-${index}`}
                empty="No agent rows were returned for this workspace."
                summary={
                  totals
                    ? `Gross exposure ${formatPercent(totals.gross_exposure_ratio)} of simulated equity · cash ${formatCurrency(totals.cash)} · ${formatNumber(totals.position_count, 0)} positions.`
                    : undefined
                }
              />
              <Button variant="ghost" size="sm" iconEnd={<ArrowRight size={14} />} onClick={() => navigate("/paper")}>
                Open paper trading
              </Button>
            </>
          )}
        </Card>

        <div className="ui-stack">
          <Card title="Next action" subtitle="Based on what this workspace has actually produced.">
            {nextActions.length === 0 ? (
              <p className="ui-card__subtitle">Nothing is waiting on you right now.</p>
            ) : (
              <ul className="principle-list">
                {nextActions.map((action) => (
                  <li key={action.title}>
                    <ArrowRight size={13} aria-hidden="true" style={{ marginTop: 3, flexShrink: 0 }} />
                    <span>
                      <strong style={{ color: "var(--text-primary)" }}>{action.title}</strong>
                      <br />
                      {action.body}
                      {action.href ? (
                        <>
                          {" "}
                          <Link className="ui-link" to={action.href}>{action.linkLabel}</Link>
                        </>
                      ) : null}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Data freshness" subtitle="Whether what you are looking at is current.">
            <dl className="ui-access__dl" style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: "8px 12px", margin: 0, fontSize: "var(--text-xs)" }}>
              <dt style={{ color: "var(--text-secondary)" }}>Paper ledger date</dt>
              <dd style={{ margin: 0 }} className={data.paper?.asof_date ? "ui-num" : undefined}>
                {data.paper?.asof_date ?? "No run yet"}
              </dd>
              <dt style={{ color: "var(--text-secondary)" }}>Latest paper run</dt>
              <dd style={{ margin: 0 }}>
                {data.paper?.run_timestamp_utc ? formatDateTime(data.paper.run_timestamp_utc) : "No run yet"}
              </dd>
              <dt style={{ color: "var(--text-secondary)" }}>Sentiment observations</dt>
              <dd style={{ margin: 0 }} className={data.sentimentError ? undefined : "ui-num"}>
                {data.sentimentError ? "Unavailable" : formatNumber(sentimentRows, 0)}
              </dd>
              <dt style={{ color: "var(--text-secondary)" }}>Latest sentiment date</dt>
              <dd style={{ margin: 0 }} className={sentimentAnchor ? "ui-num" : undefined}>
                {sentimentAnchor ?? "No dataset yet"}
              </dd>
              <dt style={{ color: "var(--text-secondary)" }}>Backend</dt>
              <dd style={{ margin: 0 }}>{backendOnline === false ? "Degraded" : "Online"}</dd>
            </dl>
            {data.sentimentError ? (
              <InlineNotice tone="warn" compact>
                {data.sentimentError}
              </InlineNotice>
            ) : null}
            {data.sentimentLoading ? <LoadingBlock label="Loading sentiment freshness" lines={1} /> : null}
          </Card>
        </div>
      </div>

      <section className="ui-stack" aria-labelledby="overview-validation">
        <SectionTitle title="Validation activity" id="overview-validation">
          <Button variant="ghost" size="sm" iconEnd={<ArrowRight size={13} />} onClick={() => navigate("/backtests")}>
            Open backtests
          </Button>
        </SectionTitle>
        {data.backtestJobs.length === 0 ? (
          <EmptyPanel
            icon={<FlaskConical size={18} />}
            title="No backtests in this workspace yet"
            body="A backtest is the evidence step between an idea and a simulated deployment. It reports return, drawdown, per-fold behaviour, and an overfitting estimate."
            actions={
              <Button variant="primary" onClick={() => navigate("/backtests")}>
                Configure a backtest
              </Button>
            }
          />
        ) : (
          <DataGrid
            rows={data.backtestJobs.slice(0, 6)}
            columns={[
              {
                key: "strategy",
                header: "Strategy",
                render: (job) => {
                  const request = (job.request ?? {}) as { pipeline?: string; symbols?: string[] };
                  return (
                    <span className="stacked-cell">
                      <strong>{strategyName(request.pipeline)}</strong>
                      <span>{Array.isArray(request.symbols) && request.symbols.length ? request.symbols.join(" ") : "No universe recorded"}</span>
                    </span>
                  );
                },
              },
              {
                key: "status",
                header: "Status",
                render: (job) => (
                  <StatusIndicator
                    tone={
                      job.status === "completed"
                        ? job.warnings?.length
                          ? "warn"
                          : "good"
                        : job.status === "failed" || job.status === "interrupted"
                          ? "bad"
                          : "info"
                    }
                    busy={job.status === "running" || job.status === "queued"}
                  >
                    {job.status === "completed" && job.warnings?.length
                      ? `Completed · ${job.warnings.length} warning${job.warnings.length === 1 ? "" : "s"}`
                      : job.status}
                  </StatusIndicator>
                ),
              },
              {
                key: "return",
                header: "Total return",
                align: "right",
                render: (job) => {
                  const value = backtestReturn(job);
                  if (value == null) return <span className="ui-table__muted">Not reported</span>;
                  return (
                    <span className={value >= 0 ? "ui-pos" : "ui-neg"}>
                      {value >= 0 ? "+" : ""}
                      {formatPercent(value)}
                    </span>
                  );
                },
              },
              {
                key: "pbo",
                header: "Overfitting (PBO)",
                label: "PBO",
                align: "right",
                render: (job) => {
                  const value = backtestPbo(job);
                  if (value == null) return <span className="ui-table__muted">Not computed</span>;
                  const pct = Math.round(value * 100);
                  return (
                    <span className={pct < 20 ? "ui-pos" : pct < 40 ? undefined : "ui-neg"}>
                      {pct}%{pct < 20 ? " · low" : pct < 40 ? " · moderate" : " · high"}
                    </span>
                  );
                },
              },
              {
                key: "created",
                header: "Started",
                align: "right",
                render: (job) => formatDateTime(job.created_at_utc),
              },
            ]}
            caption="Recent backtests with status, total return, overfitting estimate, and start time"
            getKey={(job) => job.id}
            summary={`Showing the ${Math.min(6, data.backtestJobs.length)} most recent of ${data.backtestJobs.length} saved runs. Overfitting (PBO) below 20% is generally treated as trustworthy; above 50% suggests the result is fitted to noise.`}
          />
        )}
      </section>

      <div className="grid-two">
        <Card
          title="Strategy discovery"
          subtitle="Available in this workspace — built-in, benchmark, workspace-authored, and community strategies."
          actions={
            <Button variant="ghost" size="sm" iconEnd={<ArrowRight size={13} />} onClick={() => navigate("/strategies")}>
              Open library
            </Button>
          }
        >
          {data.catalogLoading ? (
            <LoadingBlock label="Loading the strategy library" lines={3} />
          ) : data.catalogError ? (
            <InlineNotice tone="bad" title="Strategy library unavailable">
              {data.catalogError}
            </InlineNotice>
          ) : (data.catalog?.length ?? 0) === 0 ? (
            <EmptyPanel
              icon={<Layers size={18} />}
              title="No strategies yet"
              body="No strategies are available in this workspace."
              actions={
                <Button variant="secondary" onClick={() => navigate("/strategies/builder")}>
                  Describe a strategy
                </Button>
              }
            />
          ) : (
            <>
              <ul className="principle-list">
                {(data.catalog ?? []).slice(0, 5).map((item) => (
                  <li key={item.id}>
                    <Layers size={13} aria-hidden="true" style={{ marginTop: 3, flexShrink: 0 }} />
                    <span>
                      <strong style={{ color: "var(--text-primary)" }}>{item.name}</strong>
                      {" · "}
                      {item.family}
                      <br />
                      {item.summary}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="ui-card__subtitle">
                {formatNumber(data.catalog?.length ?? 0, 0)} strategies available in this workspace.
              </p>
            </>
          )}
        </Card>

        <Card
          title="Research activity"
          subtitle="Informational multi-analyst reviews. Not financial advice and not a trading signal."
          actions={
            <Button variant="ghost" size="sm" iconEnd={<ArrowRight size={13} />} onClick={() => navigate("/research")}>
              Open research
            </Button>
          }
        >
          {data.researchJobs.length === 0 ? (
            <EmptyPanel
              icon={<Sparkles size={18} />}
              title="No research runs yet"
              body={
                premium
                  ? "Run a review on a ticker to get bull, bear, technical, and risk perspectives with linked sources and a stated confidence."
                  : "AI research is a paid workflow. You can still read any saved reports in this workspace and learn how the review is assembled."
              }
              actions={
                <Button variant="secondary" onClick={() => navigate(premium ? "/research" : "/pricing")}>
                  {premium ? "Start a review" : "See what unlocks it"}
                </Button>
              }
            />
          ) : (
            <>
              <MetricGrid>
                <Metric label="Completed reviews" value={formatNumber(researchCompleted.length, 0)} />
                <Metric label="In flight" value={formatNumber(researchRunning.length, 0)} />
              </MetricGrid>
              <Disclosure summary={`Show the ${Math.min(5, data.researchJobs.length)} most recent research runs`}>
                <ul className="principle-list">
                  {data.researchJobs.slice(0, 5).map((job) => {
                    const request = (job.request ?? {}) as { ticker?: string; horizon?: string };
                    return (
                      <li key={job.id}>
                        <span>
                          <strong style={{ color: "var(--text-primary)" }}>{request.ticker ?? "Unknown ticker"}</strong>
                          {" · "}
                          {job.status}
                          {job.warnings?.length ? ` · ${job.warnings.length} warning${job.warnings.length === 1 ? "" : "s"}` : ""}
                          <br />
                          {formatDateTime(job.created_at_utc)}
                          {request.horizon ? ` · ${request.horizon} horizon` : ""}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </Disclosure>
            </>
          )}
        </Card>
      </div>

      <InlineNotice tone="info" title="What these numbers are">
        Simulated equity and profit and loss come from an internal paper ledger, not a broker. Backtest metrics
        describe historical behaviour of a rule and are not a forecast. Research output is informational.
        Anything the API has not produced is labelled as unavailable rather than shown as zero.
      </InlineNotice>
    </>
  );
}

function buildNextActions(input: {
  premium: boolean;
  hasPaperState: boolean;
  deployableRunCount: number;
  completedRunCount: number;
  catalogCount: number;
  sentimentRows: number;
  researchCount: number;
}) {
  const actions: Array<{ title: string; body: string; href?: string; linkLabel?: string }> = [];

  if (!input.premium) {
    actions.push({
      title: "Explore before upgrading",
      body: "Browsing the strategy library, describing an idea in the builder, and reading saved records are all free. Running compute-heavy jobs is what requires a paid plan.",
      href: "/pricing",
      linkLabel: "See plan comparison",
    });
  }

  if (input.catalogCount === 0) {
    actions.push({
      title: "Add a strategy",
      body: "This workspace has no strategies available yet. Describe an idea in plain English and the builder will produce a reviewable specification.",
      href: "/strategies/builder",
      linkLabel: "Open the builder",
    });
  } else if (input.completedRunCount === 0) {
    actions.push({
      title: "Validate a strategy against history",
      body: "Pick a strategy and a universe, then run a walk-forward backtest. That produces the evidence you need before any deployment.",
      href: "/backtests",
      linkLabel: "Configure a backtest",
    });
  }

  if (input.deployableRunCount > 0 && !input.hasPaperState) {
    actions.push({
      title: "Deploy a validated run to paper trading",
      body: `${input.deployableRunCount} completed backtest${input.deployableRunCount === 1 ? "" : "s"} can be promoted to a simulated agent. Review the warnings and overfitting estimate first.`,
      href: "/paper",
      linkLabel: "Open paper trading",
    });
  }

  if (input.sentimentRows === 0) {
    actions.push({
      title: "Build a news dataset",
      body: "Sentiment features stay empty until a dataset exists. Building one also lets you inspect the exact headlines behind each score.",
      href: "/sentiment",
      linkLabel: "Open data & sentiment",
    });
  }

  return actions.slice(0, 4);
}

export default OverviewPage;
