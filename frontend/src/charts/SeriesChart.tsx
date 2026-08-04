import { useId, useMemo, useState } from "react";

import { Disclosure } from "../ui";

export interface SeriesPoint {
  label: string;
  value: number;
  /** Optional comparison value, e.g. a benchmark. */
  baseline?: number | null;
}

export interface SeriesChartProps {
  points: SeriesPoint[];
  /** Accessible name, e.g. "Simulated equity over time". */
  title: string;
  /** Plain-language description of what the reader is looking at. */
  caption?: string;
  seriesLabel: string;
  baselineLabel?: string;
  format: (value: number) => string;
  height?: number;
  /** Adds a zero reference line (for return / drawdown series). */
  zeroLine?: boolean;
  tone?: "brand" | "positive" | "negative" | "auto";
}

/**
 * Compact, accessible line chart used for simulated equity and return series.
 *
 * - The SVG has `role="img"` and a generated summary so it is not silent to
 *   assistive technology.
 * - A tabular alternative is always available behind a disclosure, satisfying
 *   the "charts need a text equivalent" requirement without cluttering the page.
 * - Hover/focus produces a readout instead of relying on a tooltip only.
 */
export function SeriesChart({
  points,
  title,
  caption,
  seriesLabel,
  baselineLabel,
  format,
  height = 132,
  zeroLine,
  tone = "auto",
}: SeriesChartProps) {
  const gradientId = useId();
  const [hover, setHover] = useState<number | null>(null);

  const geometry = useMemo(() => {
    const values = points.flatMap((point) => [point.value, ...(point.baseline == null ? [] : [point.baseline])]);
    if (!values.length) return null;
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (zeroLine) {
      min = Math.min(min, 0);
      max = Math.max(max, 0);
    }
    if (min === max) {
      min -= Math.abs(min || 1) * 0.02;
      max += Math.abs(max || 1) * 0.02;
    }
    const width = 600;
    const pad = 6;
    const usable = height - pad * 2;
    const stepX = points.length > 1 ? width / (points.length - 1) : 0;
    const y = (value: number) => pad + usable - ((value - min) / (max - min)) * usable;
    const path = (accessor: (point: SeriesPoint) => number | null | undefined) => {
      let d = "";
      let started = false;
      points.forEach((point, index) => {
        const value = accessor(point);
        if (value == null || !Number.isFinite(value)) return;
        d += `${started ? " L" : "M"}${(index * stepX).toFixed(1)},${y(value).toFixed(1)}`;
        started = true;
      });
      return d;
    };
    const line = path((point) => point.value);
    const baseline = points.some((point) => point.baseline != null) ? path((point) => point.baseline) : "";
    const area = line ? `${line} L${((points.length - 1) * stepX).toFixed(1)},${height - pad} L0,${height - pad} Z` : "";
    return { width, height, min, max, stepX, y, line, baseline, area, pad };
  }, [points, height, zeroLine]);

  const first = points[0]?.value;
  const last = points[points.length - 1]?.value;
  const direction = first != null && last != null ? (last >= first ? "up" : "down") : "flat";
  const strokeVar =
    tone === "positive" ? "var(--positive)"
      : tone === "negative" ? "var(--negative)"
        : tone === "brand" ? "var(--chart-1)"
          : direction === "down" ? "var(--negative)" : "var(--chart-3)";

  const summary = points.length
    ? `${seriesLabel} from ${format(first ?? 0)} on ${points[0].label} to ${format(last ?? 0)} on ${points[points.length - 1].label}, across ${points.length} observations.`
    : `${seriesLabel}: no observations recorded.`;

  if (!geometry) {
    return (
      <div className="chart-empty" role="status">
        Not enough history to draw {seriesLabel.toLowerCase()} yet.
      </div>
    );
  }

  const active = hover != null ? points[hover] : points[points.length - 1];

  return (
    <div className="ui-chart">
      <div className="ui-chart__head">
        <span className="ui-chart__title">{title}</span>
        <span className="ui-chart__legend">
          <span className="ui-chart__legend-item">
            <span className="ui-chart__swatch" style={{ background: strokeVar }} />
            {seriesLabel}
          </span>
          {geometry.baseline && baselineLabel ? (
            <span className="ui-chart__legend-item">
              <span className="ui-chart__swatch" style={{ background: "var(--chart-baseline)" }} />
              {baselineLabel}
            </span>
          ) : null}
        </span>
      </div>

      <svg
        viewBox={`0 0 ${geometry.width} ${geometry.height}`}
        width="100%"
        height={height}
        role="img"
        aria-label={`${title}. ${summary}`}
        preserveAspectRatio="none"
        style={{ display: "block", cursor: "crosshair" }}
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const ratio = (event.clientX - rect.left) / (rect.width || 1);
          setHover(Math.max(0, Math.min(points.length - 1, Math.round(ratio * (points.length - 1)))));
        }}
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={strokeVar} stopOpacity="0.16" />
            <stop offset="100%" stopColor={strokeVar} stopOpacity="0" />
          </linearGradient>
        </defs>
        {zeroLine && geometry.min < 0 && geometry.max > 0 ? (
          <line
            x1="0"
            x2={geometry.width}
            y1={geometry.y(0)}
            y2={geometry.y(0)}
            stroke="var(--chart-grid)"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        ) : null}
        {geometry.area ? <path d={geometry.area} fill={`url(#${gradientId})`} /> : null}
        {geometry.baseline ? (
          <path
            d={geometry.baseline}
            fill="none"
            stroke="var(--chart-baseline)"
            strokeWidth="1.4"
            strokeDasharray="4 3"
            vectorEffect="non-scaling-stroke"
          />
        ) : null}
        <path d={geometry.line} fill="none" stroke={strokeVar} strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
        {hover != null ? (
          <line
            x1={hover * geometry.stepX}
            x2={hover * geometry.stepX}
            y1={geometry.pad}
            y2={geometry.height - geometry.pad}
            stroke="var(--chart-axis)"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        ) : null}
      </svg>

      <div className="chart-stat-strip">
        <span>
          <span className="chart-label">{hover == null ? "Latest" : "At cursor"}</span>
          <br />
          <span className="ui-num">{active ? format(active.value) : "—"}</span>
        </span>
        <span>
          <span className="chart-label">Observation</span>
          <br />
          {active?.label ?? "—"}
        </span>
        {active?.baseline != null && baselineLabel ? (
          <span>
            <span className="chart-label">{baselineLabel}</span>
            <br />
            <span className="ui-num">{format(active.baseline)}</span>
          </span>
        ) : null}
      </div>

      {caption ? <p className="ui-chart__caption">{caption}</p> : null}

      <Disclosure summary={`Show ${seriesLabel.toLowerCase()} as a table (${points.length} rows)`}>
        <div className="ui-table-scroll">
          <table className="ui-table">
            <caption className="ui-sr-only">{title} — tabular values</caption>
            <thead>
              <tr>
                <th scope="col">Observation</th>
                <th scope="col" data-align="right">{seriesLabel}</th>
                {geometry.baseline && baselineLabel ? <th scope="col" data-align="right">{baselineLabel}</th> : null}
              </tr>
            </thead>
            <tbody>
              {points.map((point, index) => (
                <tr key={`${point.label}-${index}`}>
                  <td data-label="Observation">{point.label}</td>
                  <td data-align="right" data-label={seriesLabel}>{format(point.value)}</td>
                  {geometry.baseline && baselineLabel ? (
                    <td data-align="right" data-label={baselineLabel}>
                      {point.baseline == null ? "Not available" : format(point.baseline)}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Disclosure>
    </div>
  );
}
