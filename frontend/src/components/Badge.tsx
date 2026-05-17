import { memo } from "react";

export type Tone = "good" | "bad" | "warn" | "info" | "neutral";

export const Badge = memo(function Badge({
  label,
  tone = "neutral"
}: {
  label: string;
  tone?: Tone;
}) {
  return <span className={`badge badge--${tone}`}>{label}</span>;
});
