import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  createChart,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";

import { getOhlc } from "../api/client";
import type { OhlcRow } from "../api/types";
import { Button, Disclosure, InlineNotice, LoadingBlock } from "../ui";
import { formatNumber } from "../utils/format";

export interface CandlestickPanelProps {
  symbol: string;
  start: string;
  end: string;
  interval?: string;
  /** Rendered above the chart to say which run these bars belong to. */
  caption?: string;
}

function chartTime(timestamp: string): Time {
  return (timestamp.slice(0, 10) || timestamp) as Time;
}

/**
 * Candlestick view of the primary symbol for a backtest window.
 *
 * Bars are fetched on demand from `/api/backtests/ohlc` so opening the panel is
 * the only thing that costs a request. A tabular alternative is always available
 * for readers who cannot use the canvas.
 */
export function CandlestickPanel({ symbol, start, end, interval = "1d", caption }: CandlestickPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<{ chart: IChartApi; candles: ISeriesApi<"Candlestick", Time>; volume: ISeriesApi<"Histogram", Time> } | null>(null);
  const [rows, setRows] = useState<OhlcRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === "dark");

  useEffect(() => {
    const observer = new MutationObserver(() => setDark(document.documentElement.dataset.theme === "dark"));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await getOhlc({ symbol, start, end, interval });
      setRows(payload.rows ?? []);
    } catch (caught) {
      setRows(null);
      setError(caught instanceof Error ? caught.message : "Price bars are unavailable for this window.");
    } finally {
      setLoading(false);
    }
  }, [symbol, start, end, interval]);

  useEffect(() => {
    setRows(null);
    setError(null);
    void load();
  }, [load]);

  const candles = useMemo<CandlestickData<Time>[]>(
    () => (rows ?? [])
      .filter((row) => row.open != null && row.high != null && row.low != null && Number.isFinite(row.close))
      .map((row) => ({
        time: chartTime(row.timestamp),
        open: Number(row.open),
        high: Number(row.high),
        low: Number(row.low),
        close: Number(row.close),
      })),
    [rows],
  );

  const volume = useMemo<HistogramData<Time>[]>(
    () => (rows ?? [])
      .filter((row) => row.volume != null && Number.isFinite(row.volume))
      .map((row) => ({ time: chartTime(row.timestamp), value: Number(row.volume) })),
    [rows],
  );

  useEffect(() => {
    if (!containerRef.current || candles.length === 0) return undefined;
    const ink = dark ? "#e8ecf3" : "#232b36";
    const grid = dark ? "rgba(255,255,255,0.07)" : "rgba(20,28,40,0.07)";
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: ink,
        fontFamily: "'Inter', sans-serif",
        panes: { separatorColor: grid },
      },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: grid },
      timeScale: { borderColor: grid, rightOffset: 4 },
      crosshair: { mode: CrosshairMode.Normal },
      height: 340,
      autoSize: true,
    });
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: dark ? "#4bbf8a" : "#1c7d55",
      downColor: dark ? "#e4756a" : "#b3392c",
      borderVisible: false,
      wickUpColor: dark ? "#4bbf8a" : "#1c7d55",
      wickDownColor: dark ? "#e4756a" : "#b3392c",
    }, 0);
    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: dark ? "rgba(120,150,190,0.45)" : "rgba(80,110,150,0.35)",
      priceLineVisible: false,
      lastValueVisible: false,
    }, 1);
    chartRef.current = { chart, candles: candleSeries, volume: volumeSeries };
    candleSeries.setData(candles);
    volumeSeries.setData(volume);
    chart.panes()[0]?.setStretchFactor(72);
    chart.panes()[1]?.setStretchFactor(28);
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [candles, volume, dark]);

  return (
    <div className="ui-chart">
      <div className="ui-chart__head">
        <span className="ui-chart__title">Price candles — {symbol}</span>
        <div className="ui-btn-row">
          <Button size="sm" variant="ghost" onClick={() => chartRef.current?.chart.timeScale().fitContent()}>
            Reset zoom
          </Button>
          <Button size="sm" variant="secondary" onClick={() => void load()} disabled={loading}>
            {loading ? "Loading…" : "Reload bars"}
          </Button>
        </div>
      </div>

      {caption ? <p className="ui-chart__caption">{caption}</p> : null}

      {loading && rows == null ? <LoadingBlock label={`Loading ${symbol} price bars`} lines={3} /> : null}

      {error ? (
        <InlineNotice tone="warn" title="Price bars unavailable">
          {error} The equity and drawdown views above are unaffected.
        </InlineNotice>
      ) : null}

      {!loading && !error && candles.length === 0 && rows != null ? (
        <div className="chart-empty" role="status">
          The provider returned no open/high/low/close bars for {symbol} between {start} and {end}.
        </div>
      ) : null}

      {candles.length > 0 ? (
        <>
          <div
            ref={containerRef}
            className="backtest-chart-canvas"
            role="img"
            aria-label={`Candlestick chart of ${symbol} from ${start} to ${end}, ${candles.length} bars, with a volume pane beneath.`}
          />
          <p className="ui-chart__caption">
            Upper pane: open, high, low and close per bar. Lower pane: traded volume. {formatNumber(candles.length, 0)}{" "}
            bars at {interval} resolution.
          </p>
          <Disclosure summary={`Show the last 60 bars as a table (${formatNumber(candles.length, 0)} total)`}>
            <div className="ui-table-scroll">
              <table className="ui-table ui-table--stack">
                <caption className="ui-sr-only">{symbol} price bars</caption>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col" data-align="right">Open</th>
                    <th scope="col" data-align="right">High</th>
                    <th scope="col" data-align="right">Low</th>
                    <th scope="col" data-align="right">Close</th>
                  </tr>
                </thead>
                <tbody>
                  {candles.slice(-60).map((bar) => (
                    <tr key={String(bar.time)}>
                      <td data-label="Date">{String(bar.time)}</td>
                      <td data-align="right" data-label="Open">{formatNumber(bar.open)}</td>
                      <td data-align="right" data-label="High">{formatNumber(bar.high)}</td>
                      <td data-align="right" data-label="Low">{formatNumber(bar.low)}</td>
                      <td data-align="right" data-label="Close">{formatNumber(bar.close)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Disclosure>
        </>
      ) : null}
    </div>
  );
}

export default CandlestickPanel;
