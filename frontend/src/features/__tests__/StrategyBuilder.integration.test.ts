import { describe, expect, it } from "vitest";

import type { StrategyBuilderMessage, StrategyCatalogItem } from "../../api/types";
import { boundedBuilderMessages, catalogBacktestDefaults } from "../ApolloDashboard";
import { appendBuilderMessage } from "../BacktestLab";

function messages(count: number): StrategyBuilderMessage[] {
  return Array.from({ length: count }, (_, index) => ({
    role: index % 2 ? "assistant" : "user",
    content: `message-${index}`,
  }));
}

describe("strategy-builder frontend integration helpers", () => {
  it("keeps both builder conversations inside the backend 20-message limit", () => {
    const next = { role: "user", content: "latest" } as const;

    const classic = appendBuilderMessage(messages(20), next);
    const apollo = boundedBuilderMessages(messages(20), next);

    expect(classic).toHaveLength(20);
    expect(apollo).toHaveLength(20);
    expect(classic.at(-1)).toEqual(next);
    expect(apollo.at(-1)).toEqual(next);
    expect(classic[0].content).toBe("message-1");
    expect(apollo[0].content).toBe("message-1");
  });

  it("maps approved catalog metadata into immediate backtest defaults", () => {
    const item = {
      id: "custom-1",
      name: "Custom SMA",
      family: "User-created",
      difficulty: "Medium",
      pipeline: "user_strategy:custom-1",
      summary: "A custom moving-average strategy.",
      how_it_works: "Uses two moving averages.",
      best_for: "Trend research.",
      watch_out: "Can whipsaw.",
      required_train_bars: 600,
      key_parameters: ["fast_window", "slow_window"],
      example_cli: "UI only",
      paper_config_example: {
        symbols: ["spy", "qqq"],
        interval: "1h",
        train_bars: 600,
        params: { fast_window: 50, slow_window: 200 },
      },
    } as StrategyCatalogItem;

    expect(catalogBacktestDefaults(item)).toEqual({
      symbols: ["SPY", "QQQ"],
      interval: "1h",
      trainBars: 600,
      parameters: [
        { k: "fast_window", v: "50" },
        { k: "slow_window", v: "200" },
      ],
    });
  });
});
