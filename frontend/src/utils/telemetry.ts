import type { TelemetryEventRecord } from "../api/types";

export function telemetryEventTime(event: TelemetryEventRecord) {
  const parsed = new Date(event.occurred_at_utc).getTime();
  return Number.isFinite(parsed) ? parsed : null;
}

export function telemetryCategory(event: TelemetryEventRecord) {
  return String(event.category || "unknown").toLowerCase();
}

export function telemetryIsError(event: TelemetryEventRecord) {
  const category = telemetryCategory(event);
  const name = event.name.toLowerCase();
  const status = String(event.properties?.status ?? event.context?.status ?? "").toLowerCase();
  return category === "error" || name.includes("error") || name.includes("failed") || status === "failed";
}

export function telemetryLatencyMs(event: TelemetryEventRecord) {
  const numericKeys = ["latency_ms", "duration_ms", "elapsed_ms", "response_ms", "runtime_ms"];
  const secondKeys = ["latency_seconds", "duration_seconds", "elapsed_seconds", "runtime_seconds"];
  for (const source of [event.properties, event.context]) {
    for (const key of numericKeys) {
      const value = source?.[key];
      if (typeof value === "number" || typeof value === "string") {
        const parsed = Number(value);
        if (Number.isFinite(parsed) && parsed >= 0) return parsed;
      }
    }
    for (const key of secondKeys) {
      const value = source?.[key];
      if (typeof value === "number" || typeof value === "string") {
        const parsed = Number(value);
        if (Number.isFinite(parsed) && parsed >= 0) return parsed * 1000;
      }
    }
  }
  return null;
}

export function telemetryToneForCategory(category: string): "good" | "bad" | "neutral" {
  if (category === "error" || category === "security") return "bad";
  if (category === "refresh" || category === "billing") return "good";
  return "neutral";
}

export function telemetryBucketLabel(timestamp: number, hourly: boolean) {
  const date = new Date(timestamp);
  if (hourly) {
    return date.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit" });
  }
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
