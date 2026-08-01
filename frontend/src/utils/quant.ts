import type { PaperAgentConfig, PaperOrder, PaperStrategy } from "../api/types";
import { toNumber } from "./format";

export interface PortfolioPoint {
  timestamp: string;
  equity: number;
  dailyPnl: number;
  turnover: number;
}

export function aggregateEquityHistory(strategies: PaperStrategy[]): PortfolioPoint[] {
  const byTimestamp = new Map<string, PortfolioPoint>();
  for (const strategy of strategies) {
    for (const row of strategy.history) {
      if (!row.timestamp) continue;
      const current = byTimestamp.get(row.timestamp) ?? {
        timestamp: row.timestamp,
        equity: 0,
        dailyPnl: 0,
        turnover: 0
      };
      current.equity += toNumber(row.equity_after);
      current.dailyPnl += toNumber(row.daily_pnl);
      current.turnover += toNumber(row.turnover_notional);
      byTimestamp.set(row.timestamp, current);
    }
  }
  return Array.from(byTimestamp.values()).sort((a, b) => a.timestamp.localeCompare(b.timestamp));
}

export function getAllOrders(strategies: PaperStrategy[]): Array<PaperOrder & { strategy: string }> {
  return strategies.flatMap((strategy) =>
    strategy.latest_orders.map((order) => ({
      ...order,
      strategy: strategy.name
    }))
  );
}

export function orderNotional(order: PaperOrder): number {
  const explicit = toNumber(order.notional, NaN);
  if (Number.isFinite(explicit)) return Math.abs(explicit);
  return Math.abs(toNumber(order.quantity) * toNumber(order.execution_price ?? order.mark_price));
}

export function agentHasSentiment(agent: PaperAgentConfig): boolean {
  return Boolean(
    agent.use_finbert ||
      agent.daily_sentiment_file ||
      agent.news_provider_names?.length ||
      agent.news_files?.length ||
      agent.rss_feed_urls?.length ||
      agent.news_topics?.length
  );
}

export function defaultAgentId(): string {
  if ("crypto" in window && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `agent_${Math.random().toString(36).slice(2)}`;
}

export function parseJsonObject(text: string, label: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(text || "{}");
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new Error("expected a JSON object");
    }
    return parsed as Record<string, unknown>;
  } catch (caught) {
    throw new Error(`${label}: ${caught instanceof Error ? caught.message : "invalid JSON"}`);
  }
}
