import type { PaperOrder, PaperStrategy } from "../../api/types";

export function pipelineLabel(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function getAllOrders(strategies: PaperStrategy[]) {
  return strategies.flatMap((strategy) =>
    strategy.latest_orders.map((order) => ({
      strategy: strategy.name,
      ...order
    }))
  );
}

export function toNumber(value: unknown, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

export function aggregateEquityHistory(strategies: PaperStrategy[]) {
  const maxLength = Math.max(0, ...strategies.map((strategy) => strategy.history.length));
  return Array.from({ length: maxLength }, (_, index) => {
    let equity = 0;
    let dailyPnl = 0;
    let grossExposure = 0;
    let timestamp = "";
    for (const strategy of strategies) {
      const row = strategy.history[Math.min(index, Math.max(strategy.history.length - 1, 0))];
      if (!row) continue;
      equity += toNumber(row.equity_after);
      dailyPnl += toNumber(row.daily_pnl);
      grossExposure += toNumber(row.gross_exposure_notional);
      timestamp = timestamp || row.timestamp;
    }
    return {
      index,
      timestamp,
      equity,
      dailyPnl,
      grossExposure,
      grossRatio: equity ? grossExposure / equity : 0
    };
  });
}

export function orderNotional(order: PaperOrder) {
  return Math.abs(toNumber(order.notional));
}
