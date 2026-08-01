import { Wallet } from "lucide-react";

import type { PaperStrategy } from "../../api/types";
import { MetricTile } from "../../components/MetricTile";
import { StatusBadge } from "../../components/StatusBadge";
import { EquitySparkline } from "./EquitySparkline";
import { PositionConcentrationChart } from "./PaperCharts";
import { formatCurrency, formatNumber, formatPercent, toneFromValue } from "./format";
import { pipelineLabel } from "./paperUtils";

function WeightBars({ strategy }: { strategy: PaperStrategy }) {
  const rows = Object.entries(strategy.target_weights)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    .slice(0, 12);

  if (!rows.length) {
    return <div className="empty-state">No target weights on the latest run.</div>;
  }

  return (
    <div className="weight-list">
      {rows.map(([instrument, weight]) => (
        <div key={instrument} className="weight-row">
          <span>{instrument}</span>
          <div className="weight-track">
            <i style={{ width: `${Math.min(Math.abs(weight) * 100, 100)}%` }} data-side={weight >= 0 ? "long" : "short"} />
          </div>
          <strong>{formatPercent(weight)}</strong>
        </div>
      ))}
    </div>
  );
}

function PositionTable({ strategy }: { strategy: PaperStrategy }) {
  const positions = Object.entries(strategy.positions).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));

  if (!positions.length) {
    return <div className="empty-state">No open positions.</div>;
  }

  return (
    <div className="table-shell table-shell--compact">
      <table>
        <thead>
          <tr>
            <th>Instrument</th>
            <th>Quantity</th>
            <th>Target</th>
          </tr>
        </thead>
        <tbody>
          {positions.map(([instrument, quantity]) => (
            <tr key={instrument}>
              <td>{instrument}</td>
              <td>{formatNumber(quantity)}</td>
              <td>{formatPercent(strategy.target_weights[instrument] ?? 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StrategyDetail({ strategy }: { strategy: PaperStrategy }) {
  return (
    <div className="strategy-detail">
      <section className="panel panel--chart">
        <div className="panel__header">
          <h2>{strategy.name}</h2>
          <StatusBadge label={pipelineLabel(strategy.pipeline)} />
        </div>
        <EquitySparkline history={strategy.history} />
      </section>
      <section className="panel">
        <div className="panel__header">
          <h2>Capital</h2>
          <span>{strategy.mode}</span>
        </div>
        <div className="mini-grid">
          <MetricTile label="Equity" value={formatCurrency(strategy.equity)} icon={<Wallet size={16} />} />
          <MetricTile label="Daily PnL" value={formatCurrency(strategy.daily_pnl)} tone={toneFromValue(strategy.daily_pnl)} />
          <MetricTile label="Return" value={formatPercent(strategy.return_since_inception)} tone={toneFromValue(strategy.return_since_inception)} />
          <MetricTile label="Gross Exposure" value={formatPercent(strategy.gross_exposure_ratio)} />
        </div>
      </section>
      <section className="panel">
        <div className="panel__header">
          <h2>Target Weights</h2>
          <span>{strategy.position_count} positions</span>
        </div>
        <WeightBars strategy={strategy} />
      </section>
      <section className="panel">
        <div className="panel__header">
          <h2>Position Concentration</h2>
          <span>Top targets</span>
        </div>
        <PositionConcentrationChart strategy={strategy} />
      </section>
      <section className="panel strategy-detail__wide">
        <div className="panel__header">
          <h2>Positions</h2>
          <span>{formatCurrency(strategy.gross_exposure)}</span>
        </div>
        <PositionTable strategy={strategy} />
      </section>
    </div>
  );
}
