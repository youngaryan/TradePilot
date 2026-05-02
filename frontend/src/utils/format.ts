export function toNumber(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  return fallback;
}

export function formatCurrency(value: unknown, compact = false): string {
  const number = toNumber(value);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: compact ? "compact" : "standard",
    maximumFractionDigits: compact ? 1 : 2
  }).format(number);
}

export function formatNumber(value: unknown, digits = 2): string {
  const number = toNumber(value);
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits
  }).format(number);
}

export function formatPercent(value: unknown, digits = 2): string {
  const number = toNumber(value);
  return new Intl.NumberFormat("en-US", {
    style: "percent",
    maximumFractionDigits: digits
  }).format(number);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "Not available";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}

export function toneFromNumber(value: unknown): "good" | "bad" | "neutral" {
  const number = toNumber(value);
  if (number > 0) return "good";
  if (number < 0) return "bad";
  return "neutral";
}

export function statusTone(status: string | null | undefined): "good" | "bad" | "warn" | "neutral" {
  if (status === "completed" || status === "ok" || status === "succeeded" || status === "active" || status === "running_ok") return "good";
  if (status === "failed" || status === "interrupted") return "bad";
  if (status === "queued" || status === "running") return "warn";
  return "neutral";
}

export function pipelineLabel(value: string | null | undefined): string {
  if (!value) return "Unknown";
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
    .replace("Etf", "ETF")
    .replace("Rsi", "RSI")
    .replace("Macd", "MACD")
    .replace("Edgar", "EDGAR");
}

export function splitList(value: string): string[] {
  return value
    .split(/[,\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function splitSymbols(value: string): string[] {
  return splitList(value).map((item) => item.toUpperCase());
}
