import { Banknote, BriefcaseBusiness, HelpCircle, TrendingUp } from "lucide-react";

import type { BacktestJob, HealthResponse, PaperDashboardPayload, PaperRunJob, SystemMetadata } from "../api/types";
import { Badge } from "../components/Badge";
import { LeaderboardBars, PortfolioEquityChart } from "../components/Charts";
import { Explainer, MetricCard, Panel, SectionHeader } from "../components/Cards";
import { DataTable } from "../components/Table";
import { formatCurrency, formatDateTime, formatNumber, formatPercent, pipelineLabel, statusTone, toneFromNumber } from "../utils/format";

export function CommandCenter({
  payload,
  health,
  metadata,
  paperJobs,
  backtestJobs
}: {
  payload: PaperDashboardPayload;
  health: HealthResponse | null;
  metadata: SystemMetadata | null;
  paperJobs: PaperRunJob[];
  backtestJobs: BacktestJob[];
}) {
  const latestPaperJob = paperJobs[0];
  const latestBacktestJob = backtestJobs[0];
  const moneyMoved = payload.totals.daily_pnl !== 0 || payload.totals.rebalance_cost_pnl !== 0;

  return (
    <div className="page-stack">
      <section className="hero-panel hero-panel--command">
        <div>
          <p className="eyebrow">Simple View</p>
          <h2>What happened to the fake money?</h2>
          <span>
            This page only shows the numbers you need first: total fake equity, today&apos;s PnL, cash, exposure, and which
            agent made or lost money. Everything is paper-only.
          </span>
        </div>
        <div className="hero-status-grid">
          <Badge label={health?.status === "ok" ? "API online" : "API unknown"} tone={health?.status === "ok" ? "good" : "warn"} />
          <Badge label={`${metadata?.counts?.experiment_runs ?? 0} experiments`} tone="info" />
          <Badge label={`${payload.strategies.length} sleeves`} tone="neutral" />
        </div>
      </section>

      <section className="metric-grid">
        <MetricCard label="Total Equity" value={formatCurrency(payload.totals.equity)} detail="All fake-money ledgers combined" icon={<Banknote size={17} />} />
        <MetricCard label="Daily PnL" value={formatCurrency(payload.totals.daily_pnl)} tone={toneFromNumber(payload.totals.daily_pnl)} detail="Mark-to-market since prior paper run" icon={<TrendingUp size={17} />} />
        <MetricCard label="Cash" value={formatCurrency(payload.totals.cash)} detail="Uninvested cash across ledgers" />
        <MetricCard label="Gross Exposure" value={formatPercent(payload.totals.gross_exposure_ratio)} detail="Absolute deployed capital / equity" icon={<BriefcaseBusiness size={17} />} />
      </section>

      <Panel title="Plain English Summary" subtitle={payload.asof_date ?? "No as-of date"}>
        <div className="plain-summary">
          <HelpCircle size={22} />
          <div>
            <strong>
              {moneyMoved
                ? `The paper book ${payload.totals.daily_pnl >= 0 ? "made" : "lost"} ${formatCurrency(Math.abs(payload.totals.daily_pnl))} on the latest marked run.`
                : "No meaningful fake-money movement is recorded yet."}
            </strong>
            <span>
              Cash is {formatCurrency(payload.totals.cash)}, gross exposure is {formatPercent(payload.totals.gross_exposure_ratio)}, and the latest rebalance created {formatNumber(payload.totals.trade_count, 0)} simulated trade(s).
            </span>
          </div>
        </div>
      </Panel>

      <div className="content-grid content-grid--wide">
        <Panel title="Money Over Time" subtitle="Only useful after multiple paper runs">
          <PortfolioEquityChart strategies={payload.strategies} />
          <Explainer
            title="Chart translation"
            body="The line is fake-money equity. Green/red bars are daily profit or loss. If it is empty, run a date-range replay in Run Paper."
            items={[
              "Up line means fake capital grew.",
              "Down bars mean losing days.",
              "One point is not a trend."
            ]}
          />
        </Panel>

        <Panel title="Who Made Or Lost Money?" subtitle="Latest paper state">
          <LeaderboardBars leaderboard={payload.leaderboard} />
        </Panel>
      </div>

      <section>
        <Panel title="Strategy Leaderboard" subtitle={`${payload.leaderboard.length} sleeves`}>
          <DataTable
            rows={payload.leaderboard}
            empty="No strategies have paper ledger state yet."
            getKey={(row) => row.strategy}
            columns={[
              {
                key: "strategy",
                header: "Strategy",
                render: (row) => (
                  <div className="stacked-cell">
                    <strong>{row.strategy}</strong>
                    <span>{pipelineLabel(row.pipeline)} | {row.mode}</span>
                  </div>
                )
              },
              { key: "equity", header: "Equity", align: "right", render: (row) => formatCurrency(row.equity) },
              {
                key: "return",
                header: "Return",
                align: "right",
                render: (row) => <span className={`number number--${toneFromNumber(row.return_since_inception)}`}>{formatPercent(row.return_since_inception)}</span>
              },
              {
                key: "pnl",
                header: "Daily PnL",
                align: "right",
                render: (row) => <span className={`number number--${toneFromNumber(row.daily_pnl)}`}>{formatCurrency(row.daily_pnl)}</span>
              },
              { key: "gross", header: "Gross", align: "right", render: (row) => formatPercent(row.gross_exposure_ratio) }
            ]}
          />
        </Panel>
      </section>

      <details className="advanced-details">
        <summary>Show backend/job details</summary>
        <Panel title="Latest Jobs" subtitle="Useful when something is running or failed">
          <div className="job-summary-list">
            <div>
              <Badge label={latestPaperJob?.status ?? "no paper job"} tone={statusTone(latestPaperJob?.status)} />
              <strong>Paper worker</strong>
              <span>{latestPaperJob?.message ?? "No paper deployment has been launched from this backend yet."}</span>
              <small>{formatDateTime(latestPaperJob?.updated_at_utc)}</small>
            </div>
            <div>
              <Badge label={latestBacktestJob?.status ?? "no backtest job"} tone={statusTone(latestBacktestJob?.status)} />
              <strong>Backtest worker</strong>
              <span>{latestBacktestJob?.message ?? "No research job has been launched from this backend yet."}</span>
              <small>{formatDateTime(latestBacktestJob?.updated_at_utc)}</small>
            </div>
          </div>
        </Panel>
      </details>

      <SectionHeader eyebrow="Glossary" title="The numbers in normal words" />
      <section className="explain-grid explain-grid--simple">
        <Explainer
          title="Equity"
          body="How much fake money the combined paper accounts are worth after marking positions to latest prices."
        />
        <Explainer
          title="Daily PnL"
          body="How much the fake accounts made or lost since the previous saved paper snapshot."
        />
        <Explainer
          title="Gross exposure"
          body="How much capital is deployed before netting longs and shorts. Higher exposure means higher risk."
        />
      </section>
    </div>
  );
}
