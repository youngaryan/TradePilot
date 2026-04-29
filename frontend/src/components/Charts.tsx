import type { LeaderboardRow, PaperStrategy, SentimentDailyPoint, SentimentSourceSummary } from "../api/types";
import { formatCurrency, formatNumber, formatPercent, pipelineLabel, toNumber } from "../utils/format";
import { aggregateEquityHistory, orderNotional } from "../utils/quant";
import type { PaperOrder } from "../api/types";

function scale(value: number, min: number, max: number, low: number, high: number) {
  if (Math.abs(max - min) < 1e-9) return (low + high) / 2;
  return low + ((value - min) / (max - min)) * (high - low);
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function hexToRgb(hex: string) {
  const normalized = hex.replace("#", "");
  return {
    r: Number.parseInt(normalized.slice(0, 2), 16),
    g: Number.parseInt(normalized.slice(2, 4), 16),
    b: Number.parseInt(normalized.slice(4, 6), 16)
  };
}

function mixHex(from: string, to: string, amount: number) {
  const a = hexToRgb(from);
  const b = hexToRgb(to);
  const t = clamp(amount, 0, 1);
  const channel = (start: number, end: number) => Math.round(start + (end - start) * t).toString(16).padStart(2, "0");
  return `#${channel(a.r, b.r)}${channel(a.g, b.g)}${channel(a.b, b.b)}`;
}

function sentimentColor(value: number) {
  const neutral = "#f3f6f8";
  if (value > 0) return mixHex(neutral, "#0f766e", Math.sqrt(clamp(value, 0, 1)));
  if (value < 0) return mixHex(neutral, "#b42318", Math.sqrt(clamp(Math.abs(value), 0, 1)));
  return neutral;
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

function shortDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function SentimentTimelineChart({
  points,
  title = "Sentiment overlay",
  detail
}: {
  points: SentimentDailyPoint[];
  title?: string;
  detail?: string;
}) {
  if (!points.length) return <EmptyChart label="No sentiment points yet. Run the accumulator to build the overlay dataset." />;

  const width = 900;
  const height = 360;
  const padding = 58;
  const rightPadding = 34;
  const bottomPadding = 58;
  const byDate = Array.from(
    points.reduce((map, point) => {
      const dateKey = String(point.date).slice(0, 10);
      const current = map.get(dateKey) ?? { date: dateKey, article_count: 0, weighted_sentiment: 0, confidence: 0, rows: 0 };
      const articles = Math.max(0, toNumber(point.article_count));
      current.article_count += articles;
      current.weighted_sentiment += toNumber(point.sentiment_score) * Math.max(articles, 1);
      current.confidence += toNumber(point.confidence);
      current.rows += 1;
      map.set(dateKey, current);
      return map;
    }, new Map<string, { date: string; article_count: number; weighted_sentiment: number; confidence: number; rows: number }>())
  ).map(([, value]) => ({
    ...value,
    sentiment: value.article_count > 0 ? value.weighted_sentiment / value.article_count : 0,
    confidence_avg: value.rows > 0 ? value.confidence / value.rows : 0
  })).sort((a, b) => a.date.localeCompare(b.date));

  const maxArticles = Math.max(1, ...byDate.map((point) => point.article_count));
  const x = (index: number) => scale(index, 0, Math.max(byDate.length - 1, 1), padding, width - rightPadding);
  const ySentiment = (value: number) => scale(value, -1, 1, height - bottomPadding, padding);
  const yArticle = (value: number) => scale(value, 0, maxArticles, height - bottomPadding, height * 0.60);
  const zeroY = ySentiment(0);
  const sentimentPath = byDate.map((point, index) => `${x(index)},${ySentiment(point.sentiment)}`).join(" ");
  const tickIndexes = byDate
    .map((_, index) => index)
    .filter((index) => byDate.length <= 8 || index === 0 || index === byDate.length - 1 || index % Math.ceil(byDate.length / 6) === 0);
  const latest = byDate.at(-1);
  const averageSentiment = byDate.reduce((sum, point) => sum + point.sentiment, 0) / Math.max(byDate.length, 1);
  const totalArticles = byDate.reduce((sum, point) => sum + point.article_count, 0);
  const bestPoint = byDate.reduce((best, point) => (point.sentiment > best.sentiment ? point : best), byDate[0]);
  const worstPoint = byDate.reduce((worst, point) => (point.sentiment < worst.sentiment ? point : worst), byDate[0]);

  return (
    <svg className="chart chart--large" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Sentiment score and article count">
      <defs>
        <linearGradient id="sentimentAreaGradient" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#0b5cad" stopOpacity="0.14" />
          <stop offset="100%" stopColor="#0b5cad" stopOpacity="0" />
        </linearGradient>
      </defs>
      {[1, 0.5, 0, -0.5, -1].map((tick) => (
        <g key={tick}>
          <line x1={padding} x2={width - rightPadding} y1={ySentiment(tick)} y2={ySentiment(tick)} className={tick === 0 ? "chart-axis chart-axis--dashed" : "chart-grid"} />
          <text x={padding - 12} y={ySentiment(tick) + 4} className="chart-tick" textAnchor="end">
            {formatNumber(tick)}
          </text>
        </g>
      ))}
      <line x1={padding} x2={padding} y1={padding} y2={height - bottomPadding} className="chart-axis" />
      <line x1={padding} x2={width - rightPadding} y1={height - bottomPadding} y2={height - bottomPadding} className="chart-axis" />
      {byDate.map((point, index) => {
        const barX = x(index);
        const barY = yArticle(point.article_count);
        return (
          <rect
            key={point.date}
            x={barX - 6}
            y={barY}
            width={12}
            height={Math.max(height - bottomPadding - barY, 1)}
            rx={4}
            className="chart-bar chart-bar--neutral"
          >
            <title>{`${point.date}: ${formatNumber(point.article_count, 0)} articles`}</title>
          </rect>
        );
      })}
      <polygon
        points={`${padding},${zeroY} ${sentimentPath} ${width - rightPadding},${zeroY}`}
        fill="url(#sentimentAreaGradient)"
      />
      <polyline points={sentimentPath} fill="none" className="chart-line chart-line--primary" />
      {byDate.map((point, index) => {
        const sentimentClass = point.sentiment >= 0 ? "chart-bubble chart-bubble--good" : "chart-bubble chart-bubble--bad";
        return (
          <g key={`${point.date}-dot`}>
            <circle
              cx={x(index)}
              cy={ySentiment(point.sentiment)}
              r={Math.max(5, 5 + point.confidence_avg * 6)}
              className={sentimentClass}
            >
              <title>
                {`${point.date}\nSentiment: ${formatNumber(point.sentiment)}\nArticles: ${formatNumber(point.article_count, 0)}\nConfidence: ${formatNumber(point.confidence_avg)}`}
              </title>
            </circle>
            {(point.date === bestPoint.date || point.date === worstPoint.date || point.date === latest?.date) ? (
              <text x={x(index)} y={ySentiment(point.sentiment) - 16} className="chart-mini-label" textAnchor="middle">
                {formatNumber(point.sentiment)}
              </text>
            ) : null}
          </g>
        );
      })}
      {tickIndexes.map((index) => (
        <g key={`${byDate[index].date}-tick`}>
          <line x1={x(index)} x2={x(index)} y1={height - bottomPadding} y2={height - bottomPadding + 6} className="chart-axis" />
          <text x={x(index)} y={height - 30} className="chart-tick" textAnchor="middle">
            {shortDate(byDate[index].date)}
          </text>
          <text x={x(index)} y={height - 14} className="chart-tick chart-tick--muted" textAnchor="middle">
            {byDate[index].date.slice(0, 4)}
          </text>
        </g>
      ))}
      <text x={padding} y={24} className="chart-label">
        {title}
      </text>
      <text x={padding} y={43} className="chart-sub-label">
        {detail ?? "Line = weighted sentiment | bars = articles | dot size = confidence"}
      </text>
      <text x={width - 330} y={24} className="chart-label">
        Latest {formatNumber(latest?.sentiment)} | Avg {formatNumber(averageSentiment)}
      </text>
      <text x={width - 330} y={43} className="chart-sub-label">
        {formatNumber(totalArticles, 0)} articles | high {formatNumber(bestPoint.sentiment)} on {shortDate(bestPoint.date)} | low {formatNumber(worstPoint.sentiment)} on {shortDate(worstPoint.date)}
      </text>
    </svg>
  );
}

export function SentimentTickerBars({ points }: { points: SentimentDailyPoint[] }) {
  const rows = Array.from(
    points.reduce((map, point) => {
      const current = map.get(point.ticker) ?? { ticker: point.ticker, article_count: 0, weighted_sentiment: 0 };
      const articles = Math.max(0, toNumber(point.article_count));
      current.article_count += articles;
      current.weighted_sentiment += toNumber(point.sentiment_score) * Math.max(articles, 1);
      map.set(point.ticker, current);
      return map;
    }, new Map<string, { ticker: string; article_count: number; weighted_sentiment: number }>())
  ).map(([, value]) => ({
    label: value.ticker,
    value: value.article_count > 0 ? value.weighted_sentiment / value.article_count : 0,
    detail: `${formatNumber(value.article_count, 0)} articles`
  })).sort((a, b) => Math.abs(b.value) - Math.abs(a.value));

  return <HorizontalBars valueKind="number" rows={rows} />;
}

export function SentimentHeatmapChart({ points }: { points: SentimentDailyPoint[] }) {
  if (!points.length) return <EmptyChart label="No sentiment heatmap data for the current filters." />;

  const normalized = points
    .map((point) => ({
      date: String(point.date).slice(0, 10),
      ticker: String(point.ticker).toUpperCase(),
      sentiment: toNumber(point.sentiment_score),
      confidence: toNumber(point.confidence),
      article_count: toNumber(point.article_count)
    }))
    .filter((point) => point.date && point.ticker);
  if (!normalized.length) return <EmptyChart label="No usable sentiment heatmap rows after filtering." />;

  const dates = Array.from(new Set(normalized.map((point) => point.date))).sort();
  const rows = Array.from(
    normalized.reduce((map, point) => {
      const current = map.get(point.ticker) ?? { ticker: point.ticker, latest: -Infinity, latestSentiment: 0, articles: 0 };
      const dateTime = new Date(point.date).getTime();
      if (dateTime >= current.latest) {
        current.latest = dateTime;
        current.latestSentiment = point.sentiment;
      }
      current.articles += point.article_count;
      map.set(point.ticker, current);
      return map;
    }, new Map<string, { ticker: string; latest: number; latestSentiment: number; articles: number }>())
  ).map(([, value]) => value)
    .sort((a, b) => Math.abs(b.latestSentiment) - Math.abs(a.latestSentiment) || a.ticker.localeCompare(b.ticker));

  const valueMap = new Map(normalized.map((point) => [`${point.ticker}|${point.date}`, point]));
  const maxArticles = Math.max(1, ...normalized.map((point) => point.article_count));
  const cellSize = clamp(Math.floor(760 / Math.max(dates.length, 1)), 16, 30);
  const rowHeight = 30;
  const left = 78;
  const top = 76;
  const right = 106;
  const bottom = 70;
  const width = Math.max(920, left + right + dates.length * cellSize);
  const height = top + bottom + rows.length * rowHeight;
  const tickStep = Math.max(1, Math.ceil(dates.length / 9));

  return (
    <div className="heatmap-scroll" role="img" aria-label="Sentiment heatmap by ticker and date">
      <svg className="chart chart--heatmap" style={{ minWidth: width }} viewBox={`0 0 ${width} ${height}`}>
        <text x={left} y={24} className="chart-label">
          Color = sentiment score | dot size = article volume | stronger borders = confidence
        </text>
        <g transform={`translate(${left}, 38)`}>
          {[-1, -0.5, 0, 0.5, 1].map((value, index) => (
            <g key={value} transform={`translate(${index * 70}, 0)`}>
              <rect width={58} height={14} rx={7} fill={sentimentColor(value)} stroke="#d4dfeb" />
              <text x={29} y={30} textAnchor="middle" className="chart-tick">
                {value > 0 ? `+${value}` : value}
              </text>
            </g>
          ))}
          <text x={388} y={12} className="chart-sub-label">
            Neutral center is zero
          </text>
        </g>
        {dates.map((date, index) => {
          if (index % tickStep !== 0 && index !== dates.length - 1) return null;
          const x = left + index * cellSize + cellSize / 2;
          return (
            <g key={`${date}-axis`} transform={`translate(${x}, ${top - 10}) rotate(-35)`}>
              <text textAnchor="end" className="chart-tick">
                {shortDate(date)}
              </text>
            </g>
          );
        })}
        {rows.map((row, rowIndex) => {
          const y = top + rowIndex * rowHeight;
          return (
            <g key={row.ticker}>
              <text x={left - 12} y={y + 19} textAnchor="end" className="chart-label">
                {row.ticker}
              </text>
              {dates.map((date, dateIndex) => {
                const point = valueMap.get(`${row.ticker}|${date}`);
                const x = left + dateIndex * cellSize;
                const confidence = point ? clamp(point.confidence, 0, 1) : 0;
                const radius = point ? clamp((point.article_count / maxArticles) * (cellSize / 2.6), 2, cellSize / 2.8) : 0;
                return (
                  <g key={`${row.ticker}-${date}`}>
                    <rect
                      x={x + 1}
                      y={y + 2}
                      width={cellSize - 2}
                      height={rowHeight - 5}
                      rx={5}
                      fill={point ? sentimentColor(point.sentiment) : "#edf2f7"}
                      opacity={point ? 0.62 + confidence * 0.38 : 0.38}
                      stroke={point ? "rgba(19, 34, 56, 0.22)" : "rgba(143, 161, 184, 0.18)"}
                      strokeWidth={point ? 0.8 + confidence * 1.2 : 0.6}
                    >
                      <title>
                        {point
                          ? `${row.ticker} ${date}\nSentiment: ${formatNumber(point.sentiment)}\nArticles: ${formatNumber(point.article_count, 0)}\nConfidence: ${formatNumber(point.confidence)}`
                          : `${row.ticker} ${date}\nNo sentiment row`}
                      </title>
                    </rect>
                    {point ? (
                      <circle cx={x + cellSize / 2} cy={y + rowHeight / 2} r={radius} fill="rgba(15, 28, 42, 0.28)">
                        <title>{`${formatNumber(point.article_count, 0)} articles`}</title>
                      </circle>
                    ) : null}
                    {point && cellSize >= 24 ? (
                      <text x={x + cellSize / 2} y={y + rowHeight / 2 + 4} textAnchor="middle" className="heatmap-cell-label">
                        {formatNumber(point.sentiment)}
                      </text>
                    ) : null}
                  </g>
                );
              })}
              <text x={left + dates.length * cellSize + 12} y={y + 18} className="chart-tick">
                latest {formatNumber(row.latestSentiment)} | {formatNumber(row.articles, 0)} articles
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function SentimentSourceBars({ sources }: { sources: SentimentSourceSummary[] }) {
  return (
    <HorizontalBars
      valueKind="number"
      rows={sources.map((source) => ({
        label: source.source || "unknown",
        value: source.headline_count,
        tone: "neutral" as const
      }))}
    />
  );
}
