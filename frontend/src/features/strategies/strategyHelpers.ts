import type { StrategyBuilderMessage, StrategyCatalogItem } from "../../api/types";

/**
 * The backend strategy-builder endpoint accepts at most 20 conversation
 * messages, so the client trims before sending rather than letting the request
 * fail server-side.
 */
export function boundedBuilderMessages(
  messages: StrategyBuilderMessage[],
  next: StrategyBuilderMessage,
): StrategyBuilderMessage[] {
  return [...messages, next].slice(-20);
}

/**
 * Turn a catalog entry's `paper_config_example` into ready-to-run backtest
 * defaults, so promoting a strategy to a validation run needs no retyping.
 */
export function catalogBacktestDefaults(item: StrategyCatalogItem): {
  symbols: string[];
  interval: "1d" | "4h" | "1h" | "1mo";
  trainBars: number;
  parameters: Array<{ k: string; v: string }>;
} {
  const example = item.paper_config_example ?? {};
  const rawSymbols = Array.isArray(example.symbols) ? example.symbols : [];
  const symbols = rawSymbols.map((value) => String(value).trim().toUpperCase()).filter(Boolean);
  const rawInterval = String(example.interval ?? "1d");
  const interval = (["1d", "4h", "1h", "1mo"].includes(rawInterval) ? rawInterval : "1d") as "1d" | "4h" | "1h" | "1mo";
  const rawTrainBars = Number(example.train_bars ?? item.required_train_bars ?? 300);
  const trainBars = Number.isFinite(rawTrainBars) && rawTrainBars > 0 ? rawTrainBars : 300;
  const params = example.params && typeof example.params === "object" && !Array.isArray(example.params)
    ? example.params as Record<string, unknown>
    : {};
  const parameters = Object.entries(params).map(([k, value]) => ({ k, v: String(value ?? "") }));
  return { symbols, interval, trainBars, parameters };
}

export type StrategyOrigin = "builtin" | "benchmark" | "user" | "community";

/**
 * Classify a catalog entry's provenance so vetted built-ins, reference
 * benchmarks, workspace-authored strategies, and community listings are never
 * mixed together in one undifferentiated list.
 */
export function strategyOrigin(item: StrategyCatalogItem): StrategyOrigin {
  const isCommunity = Boolean(item.community_strategy) || item.pipeline?.startsWith("marketplace_strategy:");
  if (isCommunity) return "community";
  if (Boolean(item.user_strategy) || item.pipeline?.startsWith("user_strategy:")) return "user";
  if (/buy_and_hold|benchmark/i.test(item.pipeline || "") || /benchmark/i.test(item.name)) return "benchmark";
  return "builtin";
}

export const STRATEGY_ORIGIN_META: Array<{ key: StrategyOrigin; label: string; note: string }> = [
  { key: "builtin", label: "Built-in strategies", note: "Vetted, pre-configured rules shipped with Meridian." },
  { key: "user", label: "Workspace strategies", note: "Authored in this workspace and approved by a member." },
  { key: "community", label: "Community", note: "Published by workspace members through the marketplace." },
  { key: "benchmark", label: "Benchmarks", note: "Reference baselines for comparison, not strategies to deploy." },
];

export function riskTone(risk: string | null | undefined): "good" | "warn" | "bad" | "neutral" {
  const value = String(risk ?? "").toLowerCase();
  if (!value) return "neutral";
  if (value.includes("high")) return "bad";
  if (value.includes("low")) return "good";
  return "warn";
}
