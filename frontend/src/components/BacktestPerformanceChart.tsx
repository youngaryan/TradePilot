import { memo, useEffect, useMemo, useRef, useState } from "react";
import {
  AreaSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type LineData,
  type SeriesMarker,
  type Time
} from "lightweight-charts";

import type { BacktestTradeEvent, BacktestVisualizationPayload } from "../api/types";
import { formatDateTime, formatNumber, formatPercent, toNumber } from "../utils/format";

type ChartSeriesRefs = {
  chart: IChartApi;
  strategy: ISeriesApi<"Line", Time>;
  baseline: ISeriesApi<"Line", Time>;
  close: ISeriesApi<"Line", Time>;
  sma20: ISeriesApi<"Line", Time>;
  sma50: ISeriesApi<"Line", Time>;
  position: ISeriesApi<"Area", Time>;
  forecast: ISeriesApi<"Line", Time>;
  turnover: ISeriesApi<"Histogram", Time>;
  rsi: ISeriesApi<"Line", Time>;
  macd: ISeriesApi<"Histogram", Time>;
  strategyDrawdown: ISeriesApi<"Area", Time>;
  baselineDrawdown: ISeriesApi<"Line", Time>;
  markers: ISeriesMarkersPluginApi<Time>;
};

type HoverRow = {
  timestamp: string;
  strategy?: number | null;
  baseline?: number | null;
  close?: number | null;
  position?: number | null;
  forecast?: number | null;
  rsi?: number | null;
  drawdown?: number | null;
  baselineDrawdown?: number | null;
};

function chartTime(timestamp: string): Time {
  if (!timestamp.includes("T") || timestamp.endsWith("T00:00:00")) return timestamp.slice(0, 10) as Time;
  const parsed = Date.parse(timestamp.endsWith("Z") ? timestamp : `${timestamp}Z`);
  if (!Number.isFinite(parsed)) return timestamp.slice(0, 10) as Time;
  return Math.floor(parsed / 1000) as Time;
}

function timeKey(time: Time): string {
  if (typeof time === "string" || typeof time === "number") return String(time);
  return `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`;
}

function linePoint(time: Time, value: unknown): LineData<Time> | null {
  const number = toNumber(value, Number.NaN);
  if (!Number.isFinite(number)) return null;
  return { time, value: number };
}

function histogramPoint(time: Time, value: unknown, color?: string): HistogramData<Time> | null {
  const number = toNumber(value, Number.NaN);
  if (!Number.isFinite(number)) return null;
  return { time, value: number, color };
}

function compactDate(timestamp: string) {
  if (!timestamp) return "n/a";
  return timestamp.includes("T") ? formatDateTime(timestamp) : timestamp;
}

function markerForEvent(event: BacktestTradeEvent): SeriesMarker<Time> | null {
  const type = String(event.type || "").toLowerCase();
  const side = String(event.side || "").toLowerCase();
  const isExit = type === "exit";
  const isLongEntry = type === "entry" && side !== "short";
  const isShortEntry = type === "entry" && side === "short";
  const isBuy = type === "buy" || isLongEntry;
  const isSell = type === "sell" || isShortEntry;
  const text = isExit ? "Exit" : isShortEntry ? "Short" : isLongEntry ? "Long" : isSell ? "Sell" : "Buy";
  return {
    time: chartTime(event.timestamp),
    position: isBuy && !isExit ? "belowBar" : "aboveBar",
    color: isExit ? "#ca8a04" : isSell ? "#b42318" : "#0f766e",
    shape: isBuy && !isExit ? "arrowUp" : "arrowDown",
    text,
    id: event.id
  };
}

export const BacktestPerformanceChart = memo(function BacktestPerformanceChart({
  payload,
  isRunning = false
}: {
  payload: BacktestVisualizationPayload | null | undefined;
  isRunning?: boolean;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const refs = useRef<ChartSeriesRefs | null>(null);
  const [hoverKey, setHoverKey] = useState<string | null>(null);

  const prepared = useMemo(() => {
    const equity = payload?.equity ?? [];
    const price = payload?.price ?? [];
    const indicators = payload?.indicators ?? [];
    const tradeEvents = payload?.trade_events ?? [];
    const priceByTime = new Map(price.map((point) => [point.timestamp, point]));
    const indicatorByTime = new Map(indicators.map((point) => [point.timestamp, point]));
    const rows = equity.map((point) => {
      const time = chartTime(point.timestamp);
      const pricePoint = priceByTime.get(point.timestamp);
      const indicator = indicatorByTime.get(point.timestamp);
      return { point, pricePoint, indicator, time, key: timeKey(time) };
    });

    const hoverRows = new Map<string, HoverRow>();
    for (const row of rows) {
      hoverRows.set(row.key, {
        timestamp: row.point.timestamp,
        strategy: row.point.equity,
        baseline: row.point.baseline_equity,
        close: row.pricePoint?.close,
        position: row.indicator?.position,
        forecast: row.indicator?.forecast,
        rsi: row.indicator?.rsi,
        drawdown: row.point.drawdown,
        baselineDrawdown: row.point.baseline_drawdown
      });
    }

    return {
      hoverRows,
      strategy: rows.map((row) => linePoint(row.time, row.point.equity)).filter((item): item is LineData<Time> => item !== null),
      baseline: rows.map((row) => linePoint(row.time, row.point.baseline_equity)).filter((item): item is LineData<Time> => item !== null),
      close: rows.map((row) => linePoint(row.time, row.pricePoint?.close)).filter((item): item is LineData<Time> => item !== null),
      sma20: rows.map((row) => linePoint(row.time, row.pricePoint?.sma_20)).filter((item): item is LineData<Time> => item !== null),
      sma50: rows.map((row) => linePoint(row.time, row.pricePoint?.sma_50)).filter((item): item is LineData<Time> => item !== null),
      position: rows.map((row) => linePoint(row.time, row.indicator?.position)).filter((item): item is LineData<Time> => item !== null),
      forecast: rows.map((row) => linePoint(row.time, row.indicator?.forecast)).filter((item): item is LineData<Time> => item !== null),
      turnover: rows
        .map((row) => histogramPoint(row.time, row.indicator?.turnover, "rgba(202, 138, 4, 0.42)"))
        .filter((item): item is HistogramData<Time> => item !== null),
      rsi: rows.map((row) => linePoint(row.time, row.indicator?.rsi)).filter((item): item is LineData<Time> => item !== null),
      macd: rows
        .map((row) => {
          const value = toNumber(row.indicator?.macd_histogram, Number.NaN);
          return histogramPoint(row.time, value, value >= 0 ? "rgba(15, 118, 110, 0.48)" : "rgba(180, 35, 24, 0.44)");
        })
        .filter((item): item is HistogramData<Time> => item !== null),
      strategyDrawdown: rows.map((row) => linePoint(row.time, toNumber(row.point.drawdown) * 100)).filter((item): item is LineData<Time> => item !== null),
      baselineDrawdown: rows.map((row) => linePoint(row.time, toNumber(row.point.baseline_drawdown) * 100)).filter((item): item is LineData<Time> => item !== null),
      markers: tradeEvents.map(markerForEvent).filter((item): item is SeriesMarker<Time> => item !== null)
    };
  }, [payload]);

  useEffect(() => {
    if (!containerRef.current || refs.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      height: 820,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#526174",
        fontFamily: "Inter, system-ui, sans-serif"
      },
      rightPriceScale: {
        borderVisible: false
      },
      grid: {
        vertLines: { color: "rgba(143, 161, 184, 0.18)" },
        horzLines: { color: "rgba(143, 161, 184, 0.18)" }
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(15, 92, 173, 0.34)", labelBackgroundColor: "#0b5cad" },
        horzLine: { color: "rgba(15, 92, 173, 0.34)", labelBackgroundColor: "#0b5cad" }
      },
      timeScale: {
        borderVisible: false,
        rightOffset: 4,
        barSpacing: 8,
        lockVisibleTimeRangeOnResize: true
      },
      handleScale: true,
      handleScroll: true
    });

    const strategy = chart.addSeries(LineSeries, { title: "Strategy", color: "#0b5cad", lineWidth: 3 }, 0);
    const baseline = chart.addSeries(LineSeries, { title: "Buy-and-hold", color: "#0f766e", lineWidth: 2, lineStyle: 2 }, 0);
    const close = chart.addSeries(LineSeries, { title: "Price", color: "#27384f", lineWidth: 2 }, 1);
    const sma20 = chart.addSeries(LineSeries, { title: "SMA 20", color: "#ca8a04", lineWidth: 1, lineStyle: 2 }, 1);
    const sma50 = chart.addSeries(LineSeries, { title: "SMA 50", color: "#7c3aed", lineWidth: 1, lineStyle: 2 }, 1);
    const position = chart.addSeries(AreaSeries, {
      title: "Position",
      lineColor: "#0b5cad",
      topColor: "rgba(11, 92, 173, 0.22)",
      bottomColor: "rgba(11, 92, 173, 0.02)",
      lineWidth: 2
    }, 2);
    const forecast = chart.addSeries(LineSeries, { title: "Forecast", color: "#475569", lineWidth: 2, lineStyle: 2 }, 2);
    const turnover = chart.addSeries(HistogramSeries, { title: "Turnover", color: "rgba(202, 138, 4, 0.42)" }, 2);
    const rsi = chart.addSeries(LineSeries, { title: "RSI", color: "#0f766e", lineWidth: 2 }, 3);
    const macd = chart.addSeries(HistogramSeries, { title: "MACD histogram", color: "rgba(15, 118, 110, 0.44)" }, 3);
    const strategyDrawdown = chart.addSeries(AreaSeries, {
      title: "Strategy DD",
      lineColor: "#b42318",
      topColor: "rgba(180, 35, 24, 0.04)",
      bottomColor: "rgba(180, 35, 24, 0.22)",
      lineWidth: 2
    }, 4);
    const baselineDrawdown = chart.addSeries(LineSeries, { title: "Baseline DD", color: "#ca8a04", lineWidth: 2, lineStyle: 2 }, 4);
    const markers = createSeriesMarkers(close, [], { autoScale: false });
    refs.current = { chart, strategy, baseline, close, sma20, sma50, position, forecast, turnover, rsi, macd, strategyDrawdown, baselineDrawdown, markers };

    chart.subscribeCrosshairMove((param) => {
      setHoverKey(param.time ? timeKey(param.time as Time) : null);
    });

    return () => {
      refs.current = null;
      chart.remove();
    };
  }, []);

  useEffect(() => {
    if (!refs.current) return;
    refs.current.strategy.setData(prepared.strategy);
    refs.current.baseline.setData(prepared.baseline);
    refs.current.close.setData(prepared.close);
    refs.current.sma20.setData(prepared.sma20);
    refs.current.sma50.setData(prepared.sma50);
    refs.current.position.setData(prepared.position);
    refs.current.forecast.setData(prepared.forecast);
    refs.current.turnover.setData(prepared.turnover);
    refs.current.rsi.setData(prepared.rsi);
    refs.current.macd.setData(prepared.macd);
    refs.current.strategyDrawdown.setData(prepared.strategyDrawdown);
    refs.current.baselineDrawdown.setData(prepared.baselineDrawdown);
    refs.current.markers.setMarkers(prepared.markers);
    const panes = refs.current.chart.panes();
    panes[0]?.setHeight(230);
    panes[1]?.setHeight(190);
    panes[2]?.setHeight(130);
    panes[3]?.setHeight(130);
    panes[4]?.setHeight(140);
    if (prepared.strategy.length > 0) refs.current.chart.timeScale().fitContent();
  }, [prepared]);

  const latest = payload?.equity.at(-1);
  const latestKey = latest ? timeKey(chartTime(latest.timestamp)) : null;
  const hover = hoverKey ? prepared.hoverRows.get(hoverKey) : undefined;
  const display = hover ?? (latestKey ? prepared.hoverRows.get(latestKey) : undefined);
  const hasData = Boolean(payload?.equity.length);

  if (!hasData) {
    return <div className="empty-state chart-empty">No synchronized backtest stream is available for this job yet.</div>;
  }

  return (
    <div className="backtest-chart-shell">
      <div className="backtest-chart-toolbar">
        <div>
          <strong>{payload?.status === "running" || isRunning ? "Backtest updating" : "Backtest complete"}</strong>
          <span>
            {formatNumber(payload?.completed_folds ?? 0, 0)} / {formatNumber(payload?.total_folds ?? 0, 0)} folds
            {payload?.sampled ? ` | showing ${formatNumber(payload.equity.length, 0)} of ${formatNumber(payload.source_points, 0)} points` : ""}
          </span>
        </div>
        <button type="button" className="secondary-button secondary-button--compact" onClick={() => refs.current?.chart.timeScale().fitContent()}>
          Reset zoom
        </button>
      </div>

      <div className="backtest-legend-grid">
        <span><i className="legend-swatch" style={{ background: "#0b5cad" }} /> Strategy equity {formatNumber(display?.strategy)}</span>
        <span><i className="legend-swatch" style={{ background: "#0f766e" }} /> {payload?.baseline_label ?? "Baseline"} {formatNumber(display?.baseline)}</span>
        <span><i className="legend-swatch" style={{ background: "#27384f" }} /> {payload?.primary_symbol ?? "Price"} {formatNumber(display?.close)}</span>
        <span><i className="legend-swatch" style={{ background: "#b42318" }} /> Strategy DD {formatPercent((display?.drawdown ?? 0))}</span>
        <span><i className="legend-swatch" style={{ background: "#ca8a04" }} /> Baseline DD {formatPercent((display?.baselineDrawdown ?? 0))}</span>
        <span>{display ? compactDate(display.timestamp) : "Hover a chart point"}</span>
      </div>

      <div ref={containerRef} className="backtest-chart-canvas" aria-label="Synchronized backtest chart with equity, price, indicators, and drawdowns" />

      <div className="backtest-pane-key">
        <span>Pane 1: strategy vs baseline equity</span>
        <span>Pane 2: close, SMA 20, SMA 50, trade markers</span>
        <span>Pane 3: position, forecast, turnover</span>
        <span>Pane 4: RSI and MACD histogram</span>
        <span>Pane 5: strategy vs baseline drawdown</span>
      </div>
    </div>
  );
});
