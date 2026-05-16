import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { PaperAgentConfig, StrategyCatalogItem } from "../../api/types";

import { AgentEditor } from "../AgentEditor";

const mockAgent: PaperAgentConfig = {
  id: "test-1",
  name: "test_agent",
  pipeline: "etf_trend",
  symbols: ["SPY", "QQQ"],
  interval: "1d",
  lookback_bars: 500,
  params: { top_n: 3 }
};

const mockCatalog: StrategyCatalogItem[] = [
  {
    id: "etf_trend",
    name: "ETF Trend",
    pipeline: "etf_trend",
    family: "Directional",
    difficulty: "Beginner",
    summary: "Trend following on ETFs",
    how_it_works: "Trend",
    best_for: "Directional",
    watch_out: "Whipsaws",
    key_parameters: ["top_n"],
    example_cli: "--pipeline etf_trend",
    paper_config_example: { symbols: ["SPY"], params: { top_n: 2 } }
  }
];

describe("AgentEditor", () => {
  it("renders agent name", () => {
    render(
      <AgentEditor
        agent={mockAgent}
        catalog={mockCatalog}
        paramsText='{"top_n": 3}'
        onChange={() => undefined}
        onParamsChange={() => undefined}
        onClone={() => undefined}
        onRemove={() => undefined}
      />
    );
    expect(screen.getByText("test_agent")).toBeInTheDocument();
  });

  it("renders the method select with catalog options", () => {
    render(
      <AgentEditor
        agent={mockAgent}
        catalog={mockCatalog}
        paramsText='{"top_n": 3}'
        onChange={() => undefined}
        onParamsChange={() => undefined}
        onClone={() => undefined}
        onRemove={() => undefined}
      />
    );
    expect(screen.getByText("ETF Trend")).toBeInTheDocument();
  });

  it("renders clone and remove buttons", () => {
    render(
      <AgentEditor
        agent={mockAgent}
        catalog={mockCatalog}
        paramsText='{"top_n": 3}'
        onChange={() => undefined}
        onParamsChange={() => undefined}
        onClone={() => undefined}
        onRemove={() => undefined}
      />
    );
    expect(screen.getByText("Clone")).toBeInTheDocument();
    expect(screen.getByText("Remove")).toBeInTheDocument();
  });

  it("renders lookback bars badge", () => {
    render(
      <AgentEditor
        agent={mockAgent}
        catalog={mockCatalog}
        paramsText='{"top_n": 3}'
        onChange={() => undefined}
        onParamsChange={() => undefined}
        onClone={() => undefined}
        onRemove={() => undefined}
      />
    );
    expect(screen.getByText("500 bars")).toBeInTheDocument();
  });
});
