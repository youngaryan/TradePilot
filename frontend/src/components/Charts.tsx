import type { LeaderboardRow, PaperStrategy } from "../api/types";
import { formatCurrency, formatNumber, formatPercent, pipelineLabel, toNumber } from "../utils/format";
import { aggregateEquityHistory, orderNotional } from "../utils/quant";
import type { PaperOrder } from "../api/types";

function scale(value: number, min: number, max: number, low: number, high: number) {
  if (Math.abs(max - min) < 1e-9) return (low + high) / 2;
  return low + ((value - min) / (max - min)) * (high - low);
}

function EmptyChart({ label }: { label: string }) {
  return <div className="empty-state chart-empty">{label}</div>;
}

export function PortfolioEquityChart({ strategies }: { strategies: PaperStrategy[] }) {
  const points = aggregateEquityHistory(strategies);
  if (points.length < 2) return <EmptyChart label="Run at least two paper batches to build the equity trail." />;

  const width = 900;
  const height = 300;
  const padding = 36;
  const equities = points.map((point) => point.equity);
  const pnlValues = points.map((point) => point.dailyPnl);
  const minEquity = Math.min(...equities);
  const maxEquity = Math.max(...equities);
  const maxAbsPnl = Math.max(1, ...pnlValues.map((value) => Math.abs(value)));
  const x = (index: number) => scale(index, 0, points.length - 1, padding, width - padding);
  const yEquity = (value: number) => scale(value, minEquity, maxEquity, height * 0.62, padding);
  const yPnl = (value: number) => scale(value, -maxAbsPnl, maxAbsPnl, height - padding, height * 0.73);
  const equityPath = points.map((point, index) => `${x(index)},${yEquity(point.equity)}`).join(" ");
  const zeroY = yPnl(0);

  return (
    <svg className="chart chart--large" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Portfolio equity and PnL">
      <defs>
        <linearGradient id="equityGradient" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#0b5cad" stopOpacity="0.2" />
          <stop offset="100%" stopColor="#0b5cad" stopOpacity="0" />
        </linearGradient>
      </defs>
      <line x1={padding} x2={width - padding} y1={height * 0.67} y2={height * 0.67} className="chart-axis" />
      <line x1={padding} x2={width - padding} y1={zeroY} y2={zeroY} className="chart-axis chart-axis--dashed" />
      {points.map((point, index) => {
        const barX = x(index);
        const barY = yPnl(point.dailyPnl);
        return (
          <rect
            key={`${point.timestamp}-${index}`}
            x={barX - 3}
            y={Math.min(barY, zeroY)}
            width={6}
            height={Math.max(Math.abs(barY - zeroY), 1)}
            rx={3}
            className={point.dailyPnl >= 0 ? "chart-bar chart-bar--good" : "chart-bar chart-bar--bad"}
          />
        );
      })}
      <polyline points={equityPath} fill="none" className="chart-line chart-line--primary" />
      <text x={padding} y={24} className="chart-label">
        Equity {formatCurrency(points.at(-1)?.equity, true)}
      </text>
      <text x={width - 240} y={24} className="chart-label">
        Latest PnL {formatCurrency(points.at(-1)?.dailyPnl, true)}
      </text>
    </svg>
  );
}

export function BacktestEquityChart({
  points
}: {
  points: Array<{ timestamp: string; equity: number; drawdown: number; net_return: number }>;
}) {
  if (!points.length) return <EmptyChart label="No backtest equity curve returned yet." />;

  const width = 900;
  const height = 300;
  const padding = 36;
  const equities = points.map((point) => point.equity);
  const drawdowns = points.map((point) => point.drawdown);
  const minEquity = Math.min(...equities);
  const maxEquity = Math.max(...equities);
  const minDrawdown = Math.min(-0.01, ...drawdowns);
  const x = (index: number) => scale(index, 0, points.length - 1, padding, width - padding);
  const yEquity = (value: number) => scale(value, minEquity, maxEquity, height * 0.58, padding);
  const yDrawdown = (value: number) => scale(Math.abs(value), 0, Math.abs(minDrawdown), height - padding, height * 0.72);
  const equityPath = points.map((point, index) => `${x(index)},${yEquity(point.equity)}`).join(" ");
  const drawdownPath = points.map((point, index) => `${x(index)},${yDrawdown(point.drawdown)}`).join(" ");

  return (
    <svg className="chart chart--large" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Backtest equity and drawdown">
      <line x1={padding} x2={width - padding} y1={height * 0.66} y2={height * 0.66} className="chart-axis" />
      <polyline points={equityPath} fill="none" className="chart-line chart-line--primary" />
      <polyline points={drawdownPath} fill="none" className="chart-line chart-line--danger" />
      <text x={padding} y={24} className="chart-label">
        Final equity {formatNumber(points.at(-1)?.equity)}
      </text>
      <text x={width - 220} y={24} className="chart-label">
        Max drawdown {formatPercent(minDrawdown)}
      </text>
    </svg>
  );
}

export function HorizontalBars({
  rows,
  valueKind = "number"
}: {
  rows: Array<{ label: string; value: number; detail?: string; tone?: "good" | "bad" | "neutral" }>;
  valueKind?: "currency" | "percent" | "number";
}) {
  if (!rows.length) return <EmptyChart label="No chart data available." />;
  const maxAbs = Math.max(1e-8, ...rows.map((row) => Math.abs(row.value)));
  const format = valueKind === "currency" ? formatCurrency : valueKind === "percent" ? formatPercent : formatNumber;

  return (
    <div className="bar-list">
      {rows.map((row) => (
        <div className="bar-row" key={row.label}>
          <span>{row.label}</span>
          <div className="bar-track">
            <i
              data-tone={row.tone ?? (row.value > 0 ? "good" : row.value < 0 ? "bad" : "neutral")}
              style={{ width: `${Math.max((Math.abs(row.value) / maxAbs) * 100, row.value === 0 ? 0 : 4)}%` }}
            />
          </div>
          <strong>{format(row.value)}</strong>
          {row.detail ? <small>{row.detail}</small> : null}
        </div>
      ))}
    </div>
  );
}

export function LeaderboardBars({ leaderboard }: { leaderboard: LeaderboardRow[] }) {
  return (
    <HorizontalBars
      valueKind="currency"
      rows={leaderboard.slice(0, 8).map((row) => ({
        label: row.strategy,
        value: row.daily_pnl,
        detail: pipelineLabel(row.pipeline)
      }))}
    />
  );
}

export function ExposureBars({ strategies }: { strategies: PaperStrategy[] }) {
  return (
    <HorizontalBars
      valueKind="percent"
      rows={strategies
        .map((strategy) => ({
          label: strategy.name,
          value: strategy.gross_exposure_ratio,
          detail: `${strategy.position_count} positions`,
          tone: "neutral" as const
        }))
        .sort((a, b) => b.value - a.value)}
    />
  );
}

export function AllocationStrip({ strategies }: { strategies: PaperStrategy[] }) {
  if (!strategies.length) return <EmptyChart label="No allocation state available." />;
  const total = strategies.reduce((sum, strategy) => sum + Math.abs(strategy.equity), 0) || 1;
  let left = 0;
  return (
    <div className="allocation-strip">
      <div className="allocation-strip__bar">
        {strategies.map((strategy, index) => {
          const width = (Math.abs(strategy.equity) / total) * 100;
          const style = { left: `${left}%`, width: `${width}%` };
          left += width;
          return <i key={strategy.name} style={style} data-index={index % 8} title={`${strategy.name}: ${formatCurrency(strategy.equity)}`} />;
        })}
      </div>
      <div className="allocation-strip__legend">
        {strategies.map((strategy, index) => (
          <span key={strategy.name}>
            <i data-index={index % 8} />
            {strategy.name} {formatCurrency(strategy.equity, true)}
          </span>
        ))}
      </div>
    </div>
  );
}

export function RiskReturnMap({ strategies }: { strategies: PaperStrategy[] }) {
  if (!strategies.length) return <EmptyChart label="Run paper strategies to populate the risk map." />;
  const width = 900;
  const height = 300;
  const padding = 42;
  const pnl = strategies.map((strategy) => strategy.daily_pnl);
  const exposure = strategies.map((strategy) => strategy.gross_exposure_ratio);
  const minPnl = Math.min(-1, ...pnl);
  const maxPnl = Math.max(1, ...pnl);
  const maxExposure = Math.max(0.25, ...exposure);
  const x = (value: number) => scale(value, 0, maxExposure, padding, width - padding);
  const y = (value: number) => scale(value, minPnl, maxPnl, height - padding, padding);
  const zeroY = y(0);

  return (
    <svg className="chart chart--large" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Risk return map">
      <line x1={padding} x2={width - padding} y1={zeroY} y2={zeroY} className="chart-axis chart-axis--dashed" />
      <line x1={padding} x2={padding} y1={padding} y2={height - padding} className="chart-axis" />
      {strategies.map((strategy) => {
        const radius = Math.max(7, Math.min(22, 7 + strategy.trade_count * 1.5));
        return (
          <g key={strategy.name}>
            <circle
              cx={x(strategy.gross_exposure_ratio)}
              cy={y(strategy.daily_pnl)}
              r={radius}
              className={strategy.daily_pnl >= 0 ? "chart-bubble chart-bubble--good" : "chart-bubble chart-bubble--bad"}
            />
            <text x={x(strategy.gross_exposure_ratio) + radius + 6} y={y(strategy.daily_pnl) + 4} className="chart-label">
              {strategy.name.slice(0, 26)}
            </text>
          </g>
        );
      })}
      <text x={padding} y={24} className="chart-label">
        X exposure | Y daily PnL | bubble trade count
      </text>
    </svg>
  );
}

export function OrderNotionalBars({ orders }: { orders: Array<PaperOrder & { strategy?: string }> }) {
  const totals = new Map<string, number>();
  for (const order of orders) {
    const key = String(order.strategy ?? "unknown");
    totals.set(key, (totals.get(key) ?? 0) + orderNotional(order));
  }
  return (
    <HorizontalBars
      valueKind="currency"
      rows={Array.from(totals, ([label, value]) => ({ label, value, tone: "neutral" as const })).sort((a, b) => b.value - a.value)}
    />
  );
}

export function StrategyConcentrationBars({ strategy }: { strategy: PaperStrategy | null }) {
  if (!strategy) return <EmptyChart label="Choose a strategy to inspect concentration." />;
  const rows = Object.entries(strategy.target_weights)
    .map(([label, value]) => ({ label, value: Math.abs(toNumber(value)), tone: "neutral" as const }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 12);
  return <HorizontalBars valueKind="percent" rows={rows} />;
}
