import type { ReactNode } from "react";

interface MetricTileProps {
  label: string;
  value: string;
  detail?: string;
  tone?: "neutral" | "positive" | "negative" | "warning";
  icon?: ReactNode;
}

export function MetricTile({ label, value, detail, tone = "neutral", icon }: MetricTileProps) {
  return (
    <section className={`metric-tile metric-tile--${tone}`}>
      <div className="metric-tile__topline">
        <span>{label}</span>
        {icon ? <span className="metric-tile__icon">{icon}</span> : null}
      </div>
      <strong>{value}</strong>
      {detail ? <small>{detail}</small> : null}
    </section>
  );
}
