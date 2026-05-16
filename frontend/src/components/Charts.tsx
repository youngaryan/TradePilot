import { memo } from "react";
import type { LeaderboardRow, PaperStrategy, SentimentDailyPoint, SentimentSourceSummary, TelemetryEventRecord } from "../api/types";
import { formatCurrency, formatDateTime, formatNumber, formatPercent, pipelineLabel, toNumber } from "../utils/format";
import { aggregateEquityHistory, orderNotional } from "../utils/quant";
import { telemetryBucketLabel, telemetryCategory, telemetryEventTime, telemetryIsError, telemetryLatencyMs, telemetryToneForCategory } from "../utils/telemetry";
import type { PaperOrder } from "../api/types";
import { Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";

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

function shortDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export const PortfolioEquityChart = memo(function PortfolioEquityChart({ strategies }: { strategies: PaperStrategy[] }) {
  const points = aggregateEquityHistory(strategies);
  if (points.length < 2) return <EmptyChart label="Run at least two paper batches to build the equity trail." />;

  const data = points.map((point) => ({
    ts: point.timestamp,
    equity: point.equity,
    pnl: point.dailyPnl,
  }));
  const latest = data.at(-1);

  return (
    <div aria-label="Portfolio equity curve">
      <div className="chart-flex-header">
        <span className="chart-label">Equity {formatCurrency(latest?.equity, true)}</span>
        <span className="chart-label">Latest PnL {formatCurrency(latest?.pnl, true)}</span>
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
          <XAxis dataKey="ts" hide />
          <YAxis yAxisId="equity" domain={["auto", "auto"]} hide />
          <YAxis yAxisId="pnl" domain={["auto", "auto"]} hide />
          <Tooltip
            contentStyle={{ fontSize: 13, background: "var(--surface-color)", border: "1px solid var(--border-color)" }}
            formatter={(value: unknown, name: unknown) => [formatCurrency(Number(value), true), name === "equity" ? "Equity" : "Daily PnL"]}
            labelFormatter={() => ""}
          />
          <Bar yAxisId="pnl" dataKey="pnl" isAnimationActive={false}>
            {data.map((entry, index) => (
              <Cell key={index} fill={entry.pnl >= 0 ? "var(--color-success)" : "var(--color-danger)"} />
            ))}
          </Bar>
          <Line yAxisId="equity" type="monotone" dataKey="equity" stroke="var(--color-primary)" dot={false} strokeWidth={2} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
});

export const BacktestEquityChart = memo(function BacktestEquityChart({
  points
}: {
  points: Array<{ timestamp: string; equity: number; drawdown: number; net_return: number }>;
}) {
  if (!points.length) return <EmptyChart label="No backtest equity curve returned yet." />;

  const data = points.map((point) => ({
    ts: point.timestamp,
    equity: point.equity,
    drawdown: point.drawdown * 100,
  }));
  const last = data.at(-1);
  const minDD = Math.min(-0.01, ...points.map((p) => p.drawdown)) * 100;

  return (
    <div aria-label="Backtest equity curve">
      <div className="chart-flex-header">
        <span className="chart-label">Final equity {formatNumber(last?.equity)}</span>
        <span className="chart-label">Max drawdown {formatPercent(minDD / 100)}</span>
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
          <XAxis dataKey="ts" hide />
          <YAxis yAxisId="equity" domain={["auto", "auto"]} hide />
          <YAxis yAxisId="drawdown" domain={["auto", 0]} hide />
          <Tooltip
            contentStyle={{ fontSize: 13, background: "var(--surface-color)", border: "1px solid var(--border-color)" }}
          />
          <Line yAxisId="equity" type="monotone" dataKey="equity" stroke="var(--color-primary)" dot={false} strokeWidth={2} />
          <Line yAxisId="drawdown" type="monotone" dataKey="drawdown" stroke="var(--color-danger)" dot={false} strokeWidth={2} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
});

export const HorizontalBars = memo(function HorizontalBars({
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
    <div className="bar-list" aria-label="Horizontal bar comparison chart">
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
});

export const LeaderboardBars = memo(function LeaderboardBars({ leaderboard }: { leaderboard: LeaderboardRow[] }) {
  return (
    <div aria-label="Leaderboard ranking chart">
      <HorizontalBars
        valueKind="currency"
        rows={leaderboard.slice(0, 8).map((row) => ({
          label: row.strategy,
          value: row.daily_pnl,
          detail: pipelineLabel(row.pipeline)
        }))}
      />
    </div>
  );
});

export const ExposureBars = memo(function ExposureBars({ strategies }: { strategies: PaperStrategy[] }) {
  return (
    <div aria-label="Market exposure chart">
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
    </div>
  );
});

export const AllocationStrip = memo(function AllocationStrip({ strategies }: { strategies: PaperStrategy[] }) {
  if (!strategies.length) return <EmptyChart label="No allocation state available." />;
  const total = strategies.reduce((sum, strategy) => sum + Math.abs(strategy.equity), 0) || 1;
  let left = 0;
  return (
    <div className="allocation-strip" aria-label="Strategy allocation chart">
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
});

export const RiskReturnMap = memo(function RiskReturnMap({ strategies }: { strategies: PaperStrategy[] }) {
  if (!strategies.length) return <EmptyChart label="Run paper strategies to populate the risk map." />;

  const positive = strategies.filter((s) => s.daily_pnl >= 0).map((s) => ({
    name: s.name, exposure: s.gross_exposure_ratio, pnl: s.daily_pnl, trades: s.trade_count
  }));
  const negative = strategies.filter((s) => s.daily_pnl < 0).map((s) => ({
    name: s.name, exposure: s.gross_exposure_ratio, pnl: s.daily_pnl, trades: s.trade_count
  }));

  return (
    <div aria-label="Risk return scatter chart">
      <div className="chart-subtitle">
        <span className="chart-label">X = exposure | Y = daily PnL | bubble size = trade count</span>
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <ScatterChart>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
          <XAxis dataKey="exposure" tick={{ fontSize: 11 }} />
          <YAxis dataKey="pnl" tick={{ fontSize: 11 }} />
          <ZAxis dataKey="trades" range={[60, 400]} />
          <Tooltip
            contentStyle={{ fontSize: 13, background: "var(--surface-color)", border: "1px solid var(--border-color)" }}
            formatter={(value: unknown, name: unknown) => {
              const v = Number(value);
              if (name === "exposure") return [formatPercent(v), "Exposure"];
              if (name === "pnl") return [formatCurrency(v, true), "Daily PnL"];
              return [String(v), String(name)];
            }}
          />
          <ReferenceLine y={0} stroke="var(--border-color)" strokeDasharray="3 3" />
          <Scatter data={positive} fill="#0f766e" isAnimationActive={false} />
          <Scatter data={negative} fill="#b42318" isAnimationActive={false} />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
});

export const OrderNotionalBars = memo(function OrderNotionalBars({ orders }: { orders: Array<PaperOrder & { strategy?: string }> }) {
  const totals = new Map<string, number>();
  for (const order of orders) {
    const key = String(order.strategy ?? "unknown");
    totals.set(key, (totals.get(key) ?? 0) + orderNotional(order));
  }
  return (
    <div aria-label="Order notional value chart">
      <HorizontalBars
        valueKind="currency"
        rows={Array.from(totals, ([label, value]) => ({ label, value, tone: "neutral" as const })).sort((a, b) => b.value - a.value)}
      />
    </div>
  );
});

export const StrategyConcentrationBars = memo(function StrategyConcentrationBars({ strategy }: { strategy: PaperStrategy | null }) {
  if (!strategy) return <EmptyChart label="Choose a strategy to inspect concentration." />;
  const rows = Object.entries(strategy.target_weights)
    .map(([label, value]) => ({ label, value: Math.abs(toNumber(value)), tone: "neutral" as const }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 12);
  return (
    <div aria-label="Strategy concentration chart">
      <HorizontalBars valueKind="percent" rows={rows} />
    </div>
  );
});

export const SentimentTimelineChart = memo(function SentimentTimelineChart({
  points,
  title = "Sentiment overlay",
  detail
}: {
  points: SentimentDailyPoint[];
  title?: string;
  detail?: string;
}) {
  if (!points.length) return <EmptyChart label="No sentiment points yet. Run the accumulator to build the overlay dataset." />;

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
  const averageSentiment = byDate.reduce((sum, point) => sum + point.sentiment, 0) / Math.max(byDate.length, 1);
  const totalArticles = byDate.reduce((sum, point) => sum + point.article_count, 0);
  const bestPoint = byDate.reduce((best, point) => (point.sentiment > best.sentiment ? point : best), byDate[0]);
  const worstPoint = byDate.reduce((worst, point) => (point.sentiment < worst.sentiment ? point : worst), byDate[0]);
  const latest = byDate.at(-1);

  return (
    <div aria-label="Sentiment timeline chart">
      <div className="chart-flex-header chart-flex-header--compact">
        <div>
          <div className="chart-label">{title}</div>
          <div className="chart-sub-label">{detail ?? "Line = weighted sentiment | bars = articles | dot size = confidence"}</div>
        </div>
        <div className="chart-value-right">
          <div className="chart-label">
            Latest {formatNumber(latest?.sentiment)} | Avg {formatNumber(averageSentiment)}
          </div>
          <div className="chart-sub-label">
            {formatNumber(totalArticles, 0)} articles | high {formatNumber(bestPoint.sentiment)} on {shortDate(bestPoint.date)} | low {formatNumber(worstPoint.sentiment)} on {shortDate(worstPoint.date)}
          </div>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={360}>
        <ComposedChart data={byDate}>
          <defs>
            <linearGradient id="sentimentAreaGradient" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--color-primary)" stopOpacity={0.14} />
              <stop offset="100%" stopColor="var(--color-primary)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11 }}
            interval="preserveStartEnd"
            tickFormatter={(val: string) => shortDate(val)}
          />
          <YAxis yAxisId="sentiment" domain={[-1, 1]} tick={{ fontSize: 11 }} />
          <YAxis yAxisId="articles" domain={[0, "auto"]} hide orientation="right" />
          <Tooltip
            contentStyle={{ fontSize: 13, background: "var(--surface-color)", border: "1px solid var(--border-color)" }}
            formatter={(value: unknown, name: unknown) => {
              const v = Number(value);
              if (name === "sentiment") return [formatNumber(v), "Sentiment"];
              if (name === "article_count") return [formatNumber(v, 0), "Articles"];
              if (name === "confidence_avg") return [formatNumber(v), "Confidence"];
              return [String(v), String(name)];
            }}
          />
          <Bar yAxisId="articles" dataKey="article_count" fill="var(--color-neutral)" opacity={0.5} radius={[4, 4, 0, 0]} />
          <Area yAxisId="sentiment" type="monotone" dataKey="sentiment" fill="url(#sentimentAreaGradient)" stroke="none" />
          <Line yAxisId="sentiment" type="monotone" dataKey="sentiment" stroke="var(--color-primary)" dot={false} strokeWidth={2} />
          <Scatter
            yAxisId="sentiment"
            dataKey="sentiment"
            isAnimationActive={false}
            shape={(props: { cx?: number; cy?: number; payload?: { sentiment?: number; confidence_avg?: number } }) => {
              const cx = props.cx ?? 0;
              const cy = props.cy ?? 0;
              const r = Math.max(5, 5 + (props.payload?.confidence_avg ?? 0) * 6);
              const color = (props.payload?.sentiment ?? 0) >= 0 ? "#0f766e" : "#b42318";
              return <circle cx={cx} cy={cy} r={r} fill={color} opacity={0.7} />;
            }}
          />
          <ReferenceLine yAxisId="sentiment" y={0} stroke="var(--border-color)" strokeDasharray="3 3" />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
});

export const SentimentTickerBars = memo(function SentimentTickerBars({ points }: { points: SentimentDailyPoint[] }) {
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

  return (
    <div aria-label="Sentiment by ticker chart">
      <HorizontalBars valueKind="number" rows={rows} />
    </div>
  );
});

export const SentimentHeatmapChart = memo(function SentimentHeatmapChart({ points }: { points: SentimentDailyPoint[] }) {
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
});

export const SentimentSourceBars = memo(function SentimentSourceBars({ sources }: { sources: SentimentSourceSummary[] }) {
  return (
    <div aria-label="Sentiment by source chart">
      <HorizontalBars
        valueKind="number"
        rows={sources.map((source) => ({
          label: source.source || "unknown",
          value: source.headline_count,
          tone: "neutral" as const
        }))}
      />
    </div>
  );
});

export const TelemetryTimelineChart = memo(function TelemetryTimelineChart({ events }: { events: TelemetryEventRecord[] }) {
  const datedEvents = events
    .map((event) => ({ event, timestamp: telemetryEventTime(event) }))
    .filter((row): row is { event: TelemetryEventRecord; timestamp: number } => row.timestamp !== null)
    .sort((a, b) => a.timestamp - b.timestamp);

  if (!datedEvents.length) return <EmptyChart label="No telemetry events yet. Interact with the app or run a refresh to populate this chart." />;

  const first = datedEvents[0].timestamp;
  const last = datedEvents.at(-1)?.timestamp ?? first;
  const hourly = last - first <= 36 * 60 * 60 * 1000;
  const buckets = Array.from(
    datedEvents.reduce((map, row) => {
      const date = new Date(row.timestamp);
      const key = hourly ? date.toISOString().slice(0, 13) : date.toISOString().slice(0, 10);
      const current = map.get(key) ?? {
        key,
        label: telemetryBucketLabel(row.timestamp, hourly),
        product: 0,
        refresh: 0,
        error: 0,
        engineering: 0,
        other: 0,
        total: 0
      };
      const category = telemetryCategory(row.event);
      if (telemetryIsError(row.event)) current.error += 1;
      else if (category === "product") current.product += 1;
      else if (category === "refresh") current.refresh += 1;
      else if (category === "engineering") current.engineering += 1;
      else current.other += 1;
      current.total += 1;
      map.set(key, current);
      return map;
    }, new Map<string, { key: string; label: string; product: number; refresh: number; error: number; engineering: number; other: number; total: number }>())
  )
    .map(([, value]) => value)
    .sort((a, b) => a.key.localeCompare(b.key))
    .slice(-14);

  const latest = datedEvents.at(-1)?.event;
  const total = buckets.reduce((sum, bucket) => sum + bucket.total, 0);
  const errors = buckets.reduce((sum, bucket) => sum + bucket.error, 0);

  return (
    <div role="img" aria-label="Telemetry events over time">
      <div className="chart-flex-header">
        <div>
          <div className="chart-label">Telemetry event volume</div>
          <div className="chart-sub-label">Stacked by product, refresh, engineering, other, and error events</div>
        </div>
        <div className="chart-value-right">
          <div className="chart-label">{formatNumber(total, 0)} events | {formatNumber(errors, 0)} errors</div>
          <div className="chart-sub-label">
            Latest: {latest ? `${latest.name} at ${formatDateTime(latest.occurred_at_utc)}` : "No latest event"}
          </div>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={320}>
        <BarChart data={buckets}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} interval={0} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip
            contentStyle={{ fontSize: 13, background: "var(--surface-color)", border: "1px solid var(--border-color)" }}
          />
          <Bar stackId="a" dataKey="product" fill="var(--color-info)" />
          <Bar stackId="a" dataKey="refresh" fill="var(--color-success)" />
          <Bar stackId="a" dataKey="engineering" fill="var(--color-warn)" />
          <Bar stackId="a" dataKey="other" fill="var(--color-neutral)" />
          <Bar stackId="a" dataKey="error" fill="var(--color-danger)" />
          <Legend fontSize={11} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
});

export const TelemetryLatencyChart = memo(function TelemetryLatencyChart({ events }: { events: TelemetryEventRecord[] }) {
  const points = events
    .map((event) => ({ event, timestamp: telemetryEventTime(event), latency: telemetryLatencyMs(event) }))
    .filter((row): row is { event: TelemetryEventRecord; timestamp: number; latency: number } => row.timestamp !== null && row.latency !== null)
    .sort((a, b) => a.timestamp - b.timestamp)
    .slice(-40);

  if (!points.length) return <EmptyChart label="No latency fields found yet. Add latency_ms or duration_ms to event properties to monitor performance." />;

  const sortedLatencies = points.map((point) => point.latency).sort((a, b) => a - b);
  const p95 = sortedLatencies[Math.min(sortedLatencies.length - 1, Math.floor(sortedLatencies.length * 0.95))];
  const average = sortedLatencies.reduce((sum, value) => sum + value, 0) / sortedLatencies.length;
  const data = points.map((point) => ({
    ts: point.timestamp,
    latency: point.latency,
    isError: telemetryIsError(point.event),
    name: point.event.name,
  }));
  const maxLatency = Math.max(1, ...points.map((p) => p.latency));

  return (
    <div aria-label="Telemetry latency chart">
      <div className="chart-flex-header">
        <div>
          <div className="chart-label">Latency trail</div>
          <div className="chart-sub-label">
            Reads latency_ms, duration_ms, elapsed_ms, response_ms, or runtime_ms from event properties/context
          </div>
        </div>
        <div className="chart-value-right">
          <div className="chart-label">Avg {formatNumber(average, 0)}ms | P95 {formatNumber(p95, 0)}ms</div>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
          <XAxis dataKey="ts" hide />
          <YAxis yAxisId="latency" domain={[0, "auto"]} tick={{ fontSize: 11 }} tickFormatter={(v: number) => `${formatNumber(v, 0)}ms`} />
          <Tooltip
            contentStyle={{ fontSize: 13, background: "var(--surface-color)", border: "1px solid var(--border-color)" }}
            formatter={(value: unknown) => [formatNumber(Number(value), 0) + "ms", "Latency"]}
            labelFormatter={(label: unknown) => {
              const d = new Date(Number(label));
              return Number.isFinite(d.getTime()) ? formatDateTime(d.toISOString()) : String(label);
            }}
          />
          <Line yAxisId="latency" type="monotone" dataKey="latency" stroke="var(--color-primary)" dot={false} strokeWidth={2} />
          <Scatter
            yAxisId="latency"
            dataKey="latency"
            isAnimationActive={false}
            shape={(props: { cx?: number; cy?: number; payload?: { isError?: boolean } }) => {
              const cx = props.cx ?? 0;
              const cy = props.cy ?? 0;
              const r = props.payload?.isError ? 7 : 5;
              const fill = props.payload?.isError ? "#b42318" : "#94a3b8";
              return <circle cx={cx} cy={cy} r={r} fill={fill} opacity={0.7} />;
            }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
});

export const TelemetryCategoryBars = memo(function TelemetryCategoryBars({ events }: { events: TelemetryEventRecord[] }) {
  const rows = Array.from(
    events.reduce((map, event) => {
      const key = telemetryCategory(event);
      map.set(key, (map.get(key) ?? 0) + 1);
      return map;
    }, new Map<string, number>())
  )
    .map(([label, value]) => ({ label, value, tone: telemetryToneForCategory(label) }))
    .sort((a, b) => b.value - a.value || a.label.localeCompare(b.label));

  return (
    <div aria-label="Telemetry by category chart">
      <HorizontalBars valueKind="number" rows={rows} />
    </div>
  );
});

export const TelemetryConsentBars = memo(function TelemetryConsentBars({ events }: { events: TelemetryEventRecord[] }) {
  const rows = Array.from(
    events.reduce((map, event) => {
      const key = String(event.consent || "unknown").toLowerCase();
      map.set(key, (map.get(key) ?? 0) + 1);
      return map;
    }, new Map<string, number>())
  )
    .map(([label, value]) => ({
      label,
      value,
      tone: label === "denied" ? "bad" as const : label === "granted" || label === "system" ? "good" as const : "neutral" as const
    }))
    .sort((a, b) => b.value - a.value || a.label.localeCompare(b.label));

  return (
    <div aria-label="Telemetry consent status chart">
      <HorizontalBars valueKind="number" rows={rows} />
    </div>
  );
});

export const TelemetryTopEventsBars = memo(function TelemetryTopEventsBars({ events }: { events: TelemetryEventRecord[] }) {
  const rows = Array.from(
    events.reduce((map, event) => {
      const key = event.name || "unknown_event";
      map.set(key, (map.get(key) ?? 0) + 1);
      return map;
    }, new Map<string, number>())
  )
    .map(([label, value]) => ({
      label,
      value,
      detail: `${formatNumber((value / Math.max(events.length, 1)) * 100, 0)}% of visible events`,
      tone: "neutral" as const
    }))
    .sort((a, b) => b.value - a.value || a.label.localeCompare(b.label))
    .slice(0, 8);

  return (
    <div aria-label="Top telemetry events chart">
      <HorizontalBars valueKind="number" rows={rows} />
    </div>
  );
});
