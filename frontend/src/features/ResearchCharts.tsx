import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Panel } from "../components/Cards";

interface PriceChartProps {
  data?: Array<{ date: string; close: number | null; sma20?: number | null; sma50?: number | null }>;
  markers?: Array<{ date: string; price: number; label: string }>;
  ticker?: string;
}

function PriceChartView({ data, markers, ticker }: PriceChartProps) {
  if (!data || data.length === 0) return <div className="empty-state">No price data available</div>;
  const parsed = data.map((d) => ({ ...d, date: d.date.slice(0, 10) }));
  return (
    <div className="chart-container">
      <h4>{ticker ? `${ticker} Price History` : "Price History"}</h4>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={parsed}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
          <YAxis domain={["auto", "auto"]} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          <Line type="monotone" dataKey="close" stroke="var(--color-primary)" dot={false} name="Close" strokeWidth={2} />
          <Line type="monotone" dataKey="sma20" stroke="var(--color-info)" dot={false} name="SMA 20" strokeWidth={1} strokeDasharray="4 4" />
          <Line type="monotone" dataKey="sma50" stroke="var(--color-warn)" dot={false} name="SMA 50" strokeWidth={1} strokeDasharray="4 4" />
          {markers?.map((m, i) => (
            <ReferenceLine key={i} x={m.date.slice(0, 10)} stroke="var(--color-danger)" strokeDasharray="6 3" label={{ value: m.label, position: "top", fontSize: 10 }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

interface SpreadChartProps {
  data?: Array<{ date: string; spread: number; zscore: number }>;
  bands?: { mean: number; std: number; upper_1sigma: number; lower_1sigma: number; upper_2sigma: number; lower_2sigma: number };
  markers?: Array<{ date: string; spread: number; label: string }>;
  pair?: string;
}

function SpreadChartView({ data, bands, markers, pair }: SpreadChartProps) {
  if (!data || data.length === 0) return <div className="empty-state">No spread data available</div>;
  const parsed = data.map((d) => ({ ...d, date: d.date.slice(0, 10) }));
  return (
    <div className="chart-container">
      <h4>{pair ? `${pair} Spread` : "Spread"}</h4>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={parsed}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
          <YAxis domain={["auto", "auto"]} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          {bands ? (
            <>
              <ReferenceArea y1={bands.upper_2sigma} y2={bands.upper_1sigma} fill="var(--color-danger)" fillOpacity={0.1} label={{ value: "+2 sigma", position: "right", fontSize: 10 }} />
              <ReferenceArea y1={bands.lower_1sigma} y2={bands.lower_2sigma} fill="var(--color-danger)" fillOpacity={0.1} label={{ value: "-2 sigma", position: "right", fontSize: 10 }} />
              <ReferenceLine y={bands.mean} stroke="var(--color-neutral)" strokeDasharray="3 3" label={{ value: "Mean", position: "right", fontSize: 10 }} />
              <ReferenceLine y={bands.upper_1sigma} stroke="var(--color-info)" strokeDasharray="3 3" />
              <ReferenceLine y={bands.lower_1sigma} stroke="var(--color-info)" strokeDasharray="3 3" />
            </>
          ) : null}
          <Line type="monotone" dataKey="spread" stroke="var(--color-primary)" dot={false} name="Spread" strokeWidth={2} />
          {markers?.map((m, i) => (
            <ReferenceLine key={i} x={m.date.slice(0, 10)} stroke="var(--color-danger)" strokeDasharray="6 3" label={{ value: m.label, position: "top", fontSize: 10 }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

interface ZScoreChartProps {
  data?: Array<{ date: string; zscore: number }>;
  thresholds?: { upper_entry: number; upper_exit: number; lower_entry: number; lower_exit: number };
  markers?: Array<{ date: string; zscore: number; label: string }>;
  pair?: string;
}

function ZScoreChartView({ data, thresholds, markers, pair }: ZScoreChartProps) {
  if (!data || data.length === 0) return <div className="empty-state">No z-score data available</div>;
  const parsed = data.map((d) => ({ ...d, date: d.date.slice(0, 10) }));
  return (
    <div className="chart-container">
      <h4>{pair ? `${pair} Z-Score` : "Z-Score"}</h4>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={parsed}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
          <YAxis domain={[-3.5, 3.5]} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          {thresholds ? (
            <>
              <ReferenceLine y={thresholds.upper_entry} stroke="var(--color-danger)" strokeDasharray="6 3" label={{ value: "Entry +2", position: "right", fontSize: 10 }} />
              <ReferenceLine y={thresholds.lower_entry} stroke="var(--color-danger)" strokeDasharray="6 3" label={{ value: "Entry -2", position: "right", fontSize: 10 }} />
              <ReferenceLine y={thresholds.upper_exit} stroke="var(--color-success)" strokeDasharray="3 3" label={{ value: "Exit +1", position: "right", fontSize: 10 }} />
              <ReferenceLine y={thresholds.lower_exit} stroke="var(--color-success)" strokeDasharray="3 3" label={{ value: "Exit -1", position: "right", fontSize: 10 }} />
              <ReferenceLine y={0} stroke="var(--color-neutral)" strokeDasharray="2 2" />
            </>
          ) : null}
          <Line type="monotone" dataKey="zscore" stroke="var(--color-primary)" dot={false} name="Z-Score" strokeWidth={2} />
          {markers?.map((m, i) => (
            <ReferenceLine key={i} x={m.date.slice(0, 10)} stroke="var(--color-warn)" strokeDasharray="6 3" label={{ value: m.label, position: "top", fontSize: 10 }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

interface CorrelationChartProps {
  data?: Array<{ date: string; rolling_correlation: number | null }>;
  overall_correlation?: number;
  pair?: string;
}

function CorrelationChartView({ data, overall_correlation, pair }: CorrelationChartProps) {
  if (!data || data.length === 0) return <div className="empty-state">No correlation data available</div>;
  const parsed = data.map((d) => ({ ...d, date: d.date.slice(0, 10) }));
  return (
    <div className="chart-container">
      <h4>{pair ? `${pair} Rolling Correlation (30d)` : "Rolling Correlation"}</h4>
      {overall_correlation !== undefined ? (
        <small>Overall correlation: {overall_correlation.toFixed(4)}</small>
      ) : null}
      <ResponsiveContainer width="100%" height={300}>
        <AreaChart data={parsed}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
          <YAxis domain={[-1, 1]} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          <ReferenceLine y={0} stroke="var(--color-neutral)" strokeDasharray="2 2" />
          <ReferenceLine y={0.5} stroke="var(--color-info)" strokeDasharray="3 3" />
          <Area type="monotone" dataKey="rolling_correlation" fill="var(--color-info)" fillOpacity={0.2} stroke="var(--color-info)" dot={false} name="Rolling Correlation" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

interface ResearchChartsProps {
  charts: Record<string, unknown>;
}

export function ResearchCharts({ charts }: ResearchChartsProps) {
  const [activeTab, setActiveTab] = useState<string | null>(null);

  const chartEntries = useMemo(() => Object.entries(charts), [charts]);

  const tabs = useMemo(() => {
    return chartEntries.map(([key, value]) => {
      const val = value as { type?: string; ticker?: string; pair?: string };
      const type = val?.type ?? "price";
      const label = val?.ticker ?? val?.pair ?? key.replace("price_", "").replace("returns_", "");
      return { key, label: `${type.charAt(0).toUpperCase() + type.slice(1)} - ${label}`, type };
    });
  }, [chartEntries]);

  if (chartEntries.length === 0) return null;

  const active = activeTab ?? tabs[0]?.key ?? chartEntries[0]?.[0];
  const activeChart = charts[active] as Record<string, unknown> | undefined;

  return (
    <Panel title="Charts & Visual Evaluation" subtitle="Historical performance of selected stocks and pairs">
      {tabs.length > 1 ? (
        <div className="chart-tabs">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`ghost-button ${active === tab.key ? "ghost-button--active" : ""}`}
              onClick={() => setActiveTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      ) : null}
      <div className="chart-content">
        {renderChart(active, activeChart)}
      </div>
    </Panel>
  );
}

interface ReturnsChartProps {
  data?: Array<{ date: string; return: number }>;
  benchmark?: { ticker: string; data: Array<{ date: string; return: number }> };
  ticker?: string;
}

function ReturnsChartView({ data, benchmark, ticker }: ReturnsChartProps) {
  if (!data || data.length === 0) return <div className="empty-state">No returns data available</div>;
  const parsed = data.map((d) => ({ ...d, date: d.date.slice(0, 10) }));
  const benchParsed = benchmark?.data?.map((d) => ({ ...d, date: d.date.slice(0, 10) }));
  return (
    <div className="chart-container">
      <h4>{ticker ? `${ticker} Cumulative Returns` : "Cumulative Returns"}</h4>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={parsed}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
          <YAxis domain={["auto", "auto"]} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          <Line type="monotone" dataKey="return" stroke="var(--color-primary)" dot={false} name={ticker || "Return"} strokeWidth={2} />
          {benchmark && benchParsed ? (
            <Line type="monotone" dataKey="return" data={benchParsed} stroke="var(--color-info)" dot={false} name={benchmark.ticker} strokeWidth={1} strokeDasharray="4 4" />
          ) : null}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function renderChart(key: string, chart: Record<string, unknown> | undefined) {
  if (!chart) return <div className="empty-state">No chart data</div>;
  const type = chart.type as string;

  switch (type) {
    case "price":
      return (
        <PriceChartView
          data={(chart.data || []) as PriceChartProps["data"]}
          markers={(chart.markers || []) as PriceChartProps["markers"]}
          ticker={chart.ticker as string}
        />
      );
    case "returns":
      return (
        <ReturnsChartView
          data={(chart.data || []) as ReturnsChartProps["data"]}
          benchmark={chart.benchmark as ReturnsChartProps["benchmark"]}
          ticker={chart.ticker as string}
        />
      );
    case "spread":
      return (
        <SpreadChartView
          data={(chart.data || []) as SpreadChartProps["data"]}
          bands={chart.bands as SpreadChartProps["bands"]}
          markers={(chart.markers || []) as SpreadChartProps["markers"]}
          pair={chart.pair as string}
        />
      );
    case "zscore":
      return (
        <ZScoreChartView
          data={(chart.data || []) as ZScoreChartProps["data"]}
          thresholds={chart.thresholds as ZScoreChartProps["thresholds"]}
          markers={(chart.markers || []) as ZScoreChartProps["markers"]}
          pair={chart.pair as string}
        />
      );
    case "correlation":
      return (
        <CorrelationChartView
          data={(chart.data || []) as CorrelationChartProps["data"]}
          overall_correlation={chart.overall_correlation as number}
          pair={chart.pair as string}
        />
      );
    default:
      return <div className="empty-state">Unknown chart type: {type}</div>;
  }
}
