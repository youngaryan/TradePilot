import type { PaperHistoryRow } from "../../api/types";

interface EquitySparklineProps {
  history: PaperHistoryRow[];
}

export function EquitySparkline({ history }: EquitySparklineProps) {
  const values = history
    .map((row) => Number(row.equity_after ?? 0))
    .filter((value) => Number.isFinite(value) && value > 0);

  if (values.length < 2) {
    return <div className="sparkline sparkline--empty" />;
  }

  const width = 420;
  const height = 130;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(max - min, 1);
  const step = width / Math.max(values.length - 1, 1);
  const points = values
    .map((value, index) => {
      const x = index * step;
      const y = height - ((value - min) / range) * (height - 16) - 8;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");

  const last = values[values.length - 1];
  const first = values[0];
  const rising = last >= first;

  return (
    <svg className="sparkline" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Equity history">
      <defs>
        <linearGradient id={`spark-${rising ? "up" : "down"}`} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={rising ? "#1f8a47" : "#c2410c"} stopOpacity="0.28" />
          <stop offset="100%" stopColor={rising ? "#1f8a47" : "#c2410c"} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <polyline
        points={`0,${height} ${points} ${width},${height}`}
        fill={`url(#spark-${rising ? "up" : "down"})`}
        stroke="none"
      />
      <polyline
        points={points}
        fill="none"
        stroke={rising ? "#1f8a47" : "#c2410c"}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="3"
      />
    </svg>
  );
}
