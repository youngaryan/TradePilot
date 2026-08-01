import type { LeaderboardRow, PaperAgentConfig, PaperExecutionConfig, PaperOrder, PaperStrategy } from "../../api/types";
import { formatCurrency, formatNumber, formatPercent } from "./format";
import { aggregateEquityHistory, orderNotional, toNumber } from "./paperUtils";

function scale(value: number, min: number, max: number, low: number, high: number) {
  if (Math.abs(max - min) < 1e-9) return (low + high) / 2;
  return low + ((value - min) / (max - min)) * (high - low);
}

function EmptyChart({ label }: { label: string }) {
  return <div className="empty-state">{label}</div>;
}

export function PortfolioEquityChart({ strategies }: { strategies: PaperStrategy[] }) {
  const points = aggregateEquityHistory(strategies);
  if (points.length < 2) return <EmptyChart label="Run at least two paper batches to build a portfolio equity chart." />;

  const width = 900;
  const height = 280;
  const padding = 34;
  const equities = points.map((point) => point.equity);
  const pnlValues = points.map((point) => point.dailyPnl);
  const minEquity = Math.min(...equities);
  const maxEquity = Math.max(...equities);
  const maxAbsPnl = Math.max(1e-8, ...pnlValues.map((value) => Math.abs(value)));

  const x = (index: number) => scale(index, 0, points.length - 1, padding, width - padding);
  const yEquity = (value: number) => scale(value, minEquity, maxEquity, height * 0.62, padding);
  const yPnl = (value: number) => scale(value, -maxAbsPnl, maxAbsPnl, height - padding, height * 0.72);
  const equityPath = points.map((point, index) => `${x(index)},${yEquity(point.equity)}`).join(" ");
  const zeroPnl = yPnl(0);

  return (
    <svg className="ops-chart ops-chart--large" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Portfolio equity and daily PnL">
      <line x1={padding} x2={width - padding} y1={height * 0.66} y2={height * 0.66} className="chart-axis" />
      <line x1={padding} x2={width - padding} y1={zeroPnl} y2={zeroPnl} className="chart-axis" />
      {points.map((point, index) => {
        const barX = x(index);
        const barY = yPnl(point.dailyPnl);
        return (
          <rect
            key={`${point.timestamp}-${index}`}
            x={barX - 3}
            y={Math.min(barY, zeroPnl)}
            width={6}
            height={Math.max(Math.abs(zeroPnl - barY), 1)}
            rx={3}
            className={point.dailyPnl >= 0 ? "chart-bar chart-bar--positive" : "chart-bar chart-bar--negative"}
          />
        );
      })}
      <polyline points={equityPath} fill="none" className="chart-equity-line" />
      <text x={padding} y={22} className="chart-label">
        Equity {formatCurrency(points.at(-1)?.equity ?? 0)}
      </text>
      <text x={width - 260} y={22} className="chart-label">
        Latest PnL {formatCurrency(points.at(-1)?.dailyPnl ?? 0)}
      </text>
    </svg>
  );
}

export function StrategyPnlBars({ leaderboard }: { leaderboard: LeaderboardRow[] }) {
  if (!leaderboard.length) return <EmptyChart label="No strategy PnL data available." />;
  const rows = leaderboard.slice(0, 10);
  const maxAbsPnl = Math.max(1e-8, ...rows.map((row) => Math.abs(row.daily_pnl)));

  return (
    <div className="horizontal-chart">
      {rows.map((row) => (
        <div key={row.strategy} className="horizontal-chart__row">
          <span>{row.strategy}</span>
          <div className="horizontal-chart__track">
            <i
              data-side={row.daily_pnl >= 0 ? "positive" : "negative"}
              style={{ width: `${Math.max((Math.abs(row.daily_pnl) / maxAbsPnl) * 100, 2)}%` }}
            />
          </div>
          <strong>{formatCurrency(row.daily_pnl)}</strong>
        </div>
      ))}
    </div>
  );
}

export function ExposureBars({ strategies }: { strategies: PaperStrategy[] }) {
  const rows = strategies
    .map((strategy) => ({
      name: strategy.name,
      gross: strategy.gross_exposure_ratio,
      positions: strategy.position_count
    }))
    .sort((a, b) => b.gross - a.gross);
  if (!rows.length) return <EmptyChart label="No exposure data available." />;

  return (
    <div className="horizontal-chart">
      {rows.map((row) => (
        <div key={row.name} className="horizontal-chart__row">
          <span>{row.name}</span>
          <div className="horizontal-chart__track">
            <i data-side="neutral" style={{ width: `${Math.min(row.gross * 100, 100)}%` }} />
          </div>
          <strong>{formatPercent(row.gross)}</strong>
        </div>
      ))}
    </div>
  );
}

export function OrderNotionalChart({ orders }: { orders: Array<PaperOrder & { strategy?: string }> }) {
  if (!orders.length) return <EmptyChart label="No simulated orders on the latest paper run." />;
  const totals = new Map<string, number>();
  for (const order of orders) {
    const key = order.strategy ?? "unknown";
    totals.set(key, (totals.get(key) ?? 0) + orderNotional(order));
  }
  const rows = Array.from(totals, ([strategy, notional]) => ({ strategy, notional })).sort((a, b) => b.notional - a.notional);
  const maxNotional = Math.max(1e-8, ...rows.map((row) => row.notional));

  return (
    <div className="horizontal-chart">
      {rows.map((row) => (
        <div key={row.strategy} className="horizontal-chart__row">
          <span>{row.strategy}</span>
          <div className="horizontal-chart__track">
            <i data-side="neutral" style={{ width: `${Math.max((row.notional / maxNotional) * 100, 2)}%` }} />
          </div>
          <strong>{formatCurrency(row.notional)}</strong>
        </div>
      ))}
    </div>
  );
}

export function CapitalBreakdownChart({ strategies }: { strategies: PaperStrategy[] }) {
  if (!strategies.length) return <EmptyChart label="No strategy capital data available." />;
  const total = strategies.reduce((sum, strategy) => sum + Math.abs(strategy.equity), 0) || 1;
  let offset = 0;

  return (
    <div className="capital-stack">
      <div className="capital-stack__bar">
        {strategies.map((strategy, index) => {
          const width = (Math.abs(strategy.equity) / total) * 100;
          const style = { left: `${offset}%`, width: `${width}%` };
          offset += width;
          return <i key={strategy.name} style={style} data-index={index % 6} title={`${strategy.name}: ${formatCurrency(strategy.equity)}`} />;
        })}
      </div>
      <div className="capital-stack__legend">
        {strategies.map((strategy, index) => (
          <span key={strategy.name}>
            <i data-index={index % 6} />
            {strategy.name} {formatCurrency(strategy.equity)}
          </span>
        ))}
      </div>
    </div>
  );
}

export function PositionConcentrationChart({ strategy }: { strategy: PaperStrategy }) {
  const positions = Object.entries(strategy.positions)
    .map(([instrument, quantity]) => ({
      instrument,
      quantity: Math.abs(toNumber(quantity)),
      target: Math.abs(strategy.target_weights[instrument] ?? 0)
    }))
    .sort((a, b) => b.target - a.target)
    .slice(0, 10);
  if (!positions.length) return <EmptyChart label="No open positions for this strategy." />;
  const maxTarget = Math.max(1e-8, ...positions.map((row) => row.target));

  return (
    <div className="horizontal-chart">
      {positions.map((row) => (
        <div key={row.instrument} className="horizontal-chart__row">
          <span>{row.instrument}</span>
          <div className="horizontal-chart__track">
            <i data-side="neutral" style={{ width: `${Math.max((row.target / maxTarget) * 100, 2)}%` }} />
          </div>
          <strong>{formatPercent(row.target)}</strong>
        </div>
      ))}
    </div>
  );
}

export function DeploymentAgentChart({ agents }: { agents: PaperAgentConfig[] }) {
  if (!agents.length) return <EmptyChart label="Add at least one agent to visualize the deployment." />;
  const pipelineCounts = new Map<string, number>();
  const intervalCounts = new Map<string, number>();
  for (const agent of agents) {
    pipelineCounts.set(agent.pipeline, (pipelineCounts.get(agent.pipeline) ?? 0) + 1);
    intervalCounts.set(agent.interval, (intervalCounts.get(agent.interval) ?? 0) + 1);
  }
  const rows = [
    ...Array.from(pipelineCounts, ([label, count]) => ({ group: "Method", label, count })),
    ...Array.from(intervalCounts, ([label, count]) => ({ group: "Timeframe", label, count })),
  ];
  const maxCount = Math.max(1, ...rows.map((row) => row.count));

  return (
    <div className="horizontal-chart">
      {rows.map((row) => (
        <div key={`${row.group}-${row.label}`} className="horizontal-chart__row">
          <span>{row.group}: {row.label}</span>
          <div className="horizontal-chart__track">
            <i data-side="neutral" style={{ width: `${Math.max((row.count / maxCount) * 100, 6)}%` }} />
          </div>
          <strong>{row.count}</strong>
        </div>
      ))}
    </div>
  );
}

export function SentimentCoverageChart({ agents }: { agents: PaperAgentConfig[] }) {
  if (!agents.length) return <EmptyChart label="No agents configured." />;
  const enabled = agents.filter(
    (agent) => agent.use_finbert || Boolean(agent.daily_sentiment_file) || Boolean(agent.news_provider_names?.length)
  ).length;
  const disabled = agents.length - enabled;
  const rows = [
    { label: "Sentiment enabled", value: enabled, side: "positive" },
    { label: "Price/event only", value: disabled, side: "neutral" },
  ];
  const maxValue = Math.max(1, ...rows.map((row) => row.value));

  return (
    <div className="horizontal-chart">
      {rows.map((row) => (
        <div key={row.label} className="horizontal-chart__row">
          <span>{row.label}</span>
          <div className="horizontal-chart__track">
            <i data-side={row.side} style={{ width: `${Math.max((row.value / maxValue) * 100, row.value ? 6 : 0)}%` }} />
          </div>
          <strong>{row.value}</strong>
        </div>
      ))}
    </div>
  );
}

export function AgentSymbolCoverageChart({ agents }: { agents: PaperAgentConfig[] }) {
  if (!agents.length) return <EmptyChart label="No agents configured." />;
  const symbolCounts = new Map<string, number>();
  let sectorMapAgents = 0;
  for (const agent of agents) {
    if (agent.symbols.length) {
      for (const symbol of agent.symbols) {
        symbolCounts.set(symbol, (symbolCounts.get(symbol) ?? 0) + 1);
      }
    } else if (agent.sector_map_path) {
      sectorMapAgents += 1;
    }
  }
  const rows = Array.from(symbolCounts, ([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
    .slice(0, 12);
  if (sectorMapAgents > 0) {
    rows.push({ label: "Sector-map universe", count: sectorMapAgents });
  }
  if (!rows.length) return <EmptyChart label="Add symbols or a sector map to see deployment coverage." />;
  const maxCount = Math.max(1, ...rows.map((row) => row.count));

  return (
    <div className="horizontal-chart">
      {rows.map((row) => (
        <div key={row.label} className="horizontal-chart__row">
          <span>{row.label}</span>
          <div className="horizontal-chart__track">
            <i data-side="neutral" style={{ width: `${Math.max((row.count / maxCount) * 100, 6)}%` }} />
          </div>
          <strong>{row.count}</strong>
        </div>
      ))}
    </div>
  );
}

export function ExecutionCostChart({ execution }: { execution: PaperExecutionConfig }) {
  const rows = [
    { label: "Commission", value: execution.commission_bps, display: `${formatNumber(execution.commission_bps)} bps`, basis: 10 },
    { label: "Slippage", value: execution.slippage_bps, display: `${formatNumber(execution.slippage_bps)} bps`, basis: 10 },
    { label: "Min trade", value: execution.min_trade_notional, display: formatCurrency(execution.min_trade_notional), basis: 2_000 },
    { label: "Weight tolerance", value: execution.weight_tolerance, display: formatPercent(execution.weight_tolerance), basis: 0.02 },
  ];

  return (
    <div className="horizontal-chart">
      {rows.map((row) => (
        <div key={row.label} className="horizontal-chart__row">
          <span>{row.label}</span>
          <div className="horizontal-chart__track">
            <i data-side="neutral" style={{ width: `${Math.max(Math.min((row.value / row.basis) * 100, 100), 4)}%` }} />
          </div>
          <strong>{row.display}</strong>
        </div>
      ))}
    </div>
  );
}

export function StrategyRiskReturnChart({ strategies }: { strategies: PaperStrategy[] }) {
  if (!strategies.length) return <EmptyChart label="Run paper strategies to populate risk and return." />;

  const width = 900;
  const height = 280;
  const padding = 38;
  const pnlValues = strategies.map((strategy) => strategy.daily_pnl);
  const exposureValues = strategies.map((strategy) => strategy.gross_exposure_ratio);
  const minPnl = Math.min(-1, ...pnlValues);
  const maxPnl = Math.max(1, ...pnlValues);
  const maxExposure = Math.max(0.25, ...exposureValues);
  const x = (value: number) => scale(value, 0, maxExposure, padding, width - padding);
  const y = (value: number) => scale(value, minPnl, maxPnl, height - padding, padding);
  const zeroY = y(0);

  return (
    <svg className="ops-chart ops-chart--large" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Strategy risk return chart">
      <line x1={padding} x2={width - padding} y1={zeroY} y2={zeroY} className="chart-axis" />
      <line x1={padding} x2={padding} y1={padding} y2={height - padding} className="chart-axis" />
      {strategies.map((strategy, index) => {
        const radius = Math.max(7, Math.min(20, 7 + strategy.trade_count * 1.4));
        const positive = strategy.daily_pnl >= 0;
        return (
          <g key={strategy.name}>
            <circle
              cx={x(strategy.gross_exposure_ratio)}
              cy={y(strategy.daily_pnl)}
              r={radius}
              className={positive ? "chart-bubble chart-bubble--positive" : "chart-bubble chart-bubble--negative"}
            />
            <text x={x(strategy.gross_exposure_ratio) + radius + 5} y={y(strategy.daily_pnl) + 4} className="chart-label">
              {strategy.name.slice(0, 24)}
            </text>
            {index === 0 ? (
              <text x={padding} y={22} className="chart-label">
                X exposure | Y daily PnL | bubble trades
              </text>
            ) : null}
          </g>
        );
      })}
      <text x={padding} y={height - 10} className="chart-label">
        0% exposure
      </text>
      <text x={width - 150} y={height - 10} className="chart-label">
        {formatPercent(maxExposure)} exposure
      </text>
    </svg>
  );
}
