import { Activity, TrendingUp, Wallet } from "lucide-react";

import type { PaperDashboardPayload, PaperStrategy } from "../../api/types";
import { MetricTile } from "../../components/MetricTile";
import { StatusBadge } from "../../components/StatusBadge";
import { CapitalBreakdownChart, ExposureBars, PortfolioEquityChart, StrategyPnlBars } from "./PaperCharts";
import { StrategyDetail } from "./StrategyDetail";
import { formatCurrency, formatNumber, formatPercent, toneFromValue } from "./format";
import { pipelineLabel } from "./paperUtils";

function Leaderboard({ payload }: { payload: PaperDashboardPayload }) {
  return (
    <section className="panel">
      <div className="panel__header">
        <h2>Leaderboard</h2>
        <span>{payload.leaderboard.length} sleeves</span>
      </div>
      <div className="table-shell">
        <table>
          <thead>
            <tr>
              <th>Strategy</th>
              <th>Pipeline</th>
              <th>Equity</th>
              <th>Return</th>
              <th>Daily PnL</th>
              <th>Gross</th>
            </tr>
          </thead>
          <tbody>
            {payload.leaderboard.map((row) => (
              <tr key={row.strategy}>
                <td>
                  <strong>{row.strategy}</strong>
                  <small>{row.mode}</small>
                </td>
                <td>{pipelineLabel(row.pipeline)}</td>
                <td>{formatCurrency(row.equity)}</td>
                <td className={`numeric numeric--${toneFromValue(row.return_since_inception)}`}>{formatPercent(row.return_since_inception)}</td>
                <td className={`numeric numeric--${toneFromValue(row.daily_pnl)}`}>{formatCurrency(row.daily_pnl)}</td>
                <td>{formatPercent(row.gross_exposure_ratio)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function OverviewPage({
  payload,
  selectedStrategy
}: {
  payload: PaperDashboardPayload;
  selectedStrategy: PaperStrategy | null;
}) {
  return (
    <div className="overview-page">
      <section className="metrics-grid">
        <MetricTile label="Total Equity" value={formatCurrency(payload.totals.equity)} icon={<Wallet size={16} />} />
        <MetricTile label="Daily PnL" value={formatCurrency(payload.totals.daily_pnl)} tone={toneFromValue(payload.totals.daily_pnl)} />
        <MetricTile label="Cash" value={formatCurrency(payload.totals.cash)} />
        <MetricTile label="Gross Ratio" value={formatPercent(payload.totals.gross_exposure_ratio)} />
        <MetricTile label="Trades" value={formatNumber(payload.totals.trade_count)} icon={<Activity size={16} />} />
        <MetricTile label="Turnover" value={formatCurrency(payload.totals.turnover)} icon={<TrendingUp size={16} />} />
      </section>

      <section className="panel ops-hero-card">
        <div>
          <p className="eyebrow">Shadow Live State</p>
          <h2>Fake-money capital across all running sleeves</h2>
          <span>
            This is not broker live trading yet. Each run rebuilds signals, simulates orders, updates ledgers, and refreshes this console.
          </span>
        </div>
        <StatusBadge label={payload.run_timestamp_utc ? "latest state loaded" : "waiting for first run"} tone={payload.run_timestamp_utc ? "positive" : "warning"} />
      </section>

      <div className="analytics-grid">
        <section className="panel analytics-grid__wide">
          <div className="panel__header">
            <h2>Portfolio Equity And PnL</h2>
            <span>{payload.asof_date ?? "No as-of date"}</span>
          </div>
          <PortfolioEquityChart strategies={payload.strategies} />
        </section>

        <section className="panel">
          <div className="panel__header">
            <h2>Daily PnL By Sleeve</h2>
            <span>Latest run</span>
          </div>
          <StrategyPnlBars leaderboard={payload.leaderboard} />
        </section>

        <section className="panel">
          <div className="panel__header">
            <h2>Gross Exposure</h2>
            <span>Risk footprint</span>
          </div>
          <ExposureBars strategies={payload.strategies} />
        </section>

        <section className="panel analytics-grid__wide">
          <div className="panel__header">
            <h2>Capital Allocation</h2>
            <span>Equity by sleeve</span>
          </div>
          <CapitalBreakdownChart strategies={payload.strategies} />
        </section>
      </div>

      <div className="content-grid">
        <Leaderboard payload={payload} />
        {selectedStrategy ? <StrategyDetail strategy={selectedStrategy} /> : null}
      </div>
    </div>
  );
}
