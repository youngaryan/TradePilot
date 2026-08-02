import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "../../api/client";
import type { PaperDashboardPayload, SentimentDatasetPayload } from "../../api/types";
import {
  ApolloDashboard,
  JobPollingTimeoutError,
  buildProductionSentimentRequest,
  buildSentimentNewsMatrix,
  pollJobUntilTerminal,
  sentimentHeadlineKey,
  sentimentWindowCutoff,
} from "../ApolloDashboard";

vi.mock("../../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api/client")>();
  return {
    ...original,
    getHealth: vi.fn().mockResolvedValue({ status: "ok", service: "test" }),
    getStrategyCatalog: vi.fn().mockResolvedValue([]),
    getPaperSummary: vi.fn(),
    listBacktestJobs: vi.fn().mockResolvedValue([]),
    listMarketResearchJobs: vi.fn().mockResolvedValue([]),
    getSentimentDataset: vi.fn().mockResolvedValue({
      headlines: [], scored_headlines: [], daily_points: [], ticker_summary: [], source_summary: [], source_group_summary: [],
    }),
    listUserStrategies: vi.fn().mockResolvedValue([]),
  };
});

const emptyPaper = (name?: string): PaperDashboardPayload => ({
  asof_date: "2026-08-01",
  run_timestamp_utc: "2026-08-01T12:00:00Z",
  totals: {
    equity: name ? 100_000 : 0,
    daily_pnl: 0,
    rebalance_cost_pnl: 0,
    cash: name ? 100_000 : 0,
    gross_exposure: 0,
    gross_exposure_ratio: 0,
    position_count: 0,
    trade_count: 0,
    turnover: 0,
  },
  leaderboard: name ? [{ strategy: name, pipeline: "buy_and_hold", mode: "daily", equity: 100_000, return_since_inception: 0, daily_pnl: 0, trade_count: 0, gross_exposure_ratio: 0 }] : [],
  strategies: name ? [{
    name,
    pipeline: "buy_and_hold",
    mode: "daily",
    equity: 100_000,
    daily_pnl: 0,
    rebalance_cost_pnl: 0,
    return_since_inception: 0,
    cash: 100_000,
    gross_exposure: 0,
    gross_exposure_ratio: 0,
    position_count: 0,
    trade_count: 0,
    turnover: 0,
    positions: {},
    target_weights: {},
    latest_orders: [],
    diagnostics: {},
    history: [],
  }] : [],
  visuals: {},
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => { resolve = next; });
  return { promise, resolve };
}

beforeEach(() => {
  window.localStorage.setItem("apollo.tour_done", "1");
  vi.mocked(api.getPaperSummary).mockReset().mockResolvedValue(emptyPaper());
});

describe("Apollo production data behavior", () => {
  it("does not render synthetic portfolio, strategy, or headline content for empty APIs", async () => {
    render(<ApolloDashboard activeOrgId="org-empty" hasPremium />);

    expect(await screen.findByText("No paper agents running yet")).toBeInTheDocument();
    expect(screen.getByText("No strategies are available in this workspace.")).toBeInTheDocument();
    expect(screen.queryByText("Golden Cross (50/200 SMA)")).not.toBeInTheDocument();
    expect(screen.queryByText(/Fed signals steady rates/i)).not.toBeInTheDocument();
  });

  it("renders the empty state when the backend returns a bare payload with no paper runs yet", async () => {
    vi.mocked(api.getPaperSummary).mockResolvedValueOnce({} as PaperDashboardPayload);

    render(<ApolloDashboard activeOrgId="org-bare-payload" hasPremium />);

    expect(await screen.findByText("No paper agents running yet")).toBeInTheDocument();
  });

  it("shows API failures instead of synthetic fallback content", async () => {
    vi.mocked(api.getPaperSummary).mockRejectedValueOnce(new Error("paper offline"));
    vi.mocked(api.getStrategyCatalog).mockRejectedValueOnce(new Error("catalog offline"));
    vi.mocked(api.getSentimentDataset).mockRejectedValueOnce(new Error("sentiment offline"));

    render(<ApolloDashboard activeOrgId="org-failure" hasPremium />);

    expect(await screen.findByText("Portfolio unavailable")).toBeInTheDocument();
    expect(await screen.findByText(/Strategy library unavailable/)).toBeInTheDocument();
    expect(screen.queryByText("Golden Cross (50/200 SMA)")).not.toBeInTheDocument();
    expect(screen.queryByText(/Fed signals steady rates/i)).not.toBeInTheDocument();
  });

  it("ignores a previous tenant response after the active organization changes", async () => {
    const firstTenant = deferred<PaperDashboardPayload>();
    vi.mocked(api.getPaperSummary)
      .mockImplementationOnce(() => firstTenant.promise)
      .mockResolvedValueOnce(emptyPaper("Tenant B agent"));

    const view = render(<ApolloDashboard activeOrgId="org-a" hasPremium />);
    view.rerender(<ApolloDashboard activeOrgId="org-b" hasPremium />);

    expect(await screen.findByText("Tenant B agent")).toBeInTheDocument();
    await act(async () => { firstTenant.resolve(emptyPaper("Tenant A stale agent")); });

    await waitFor(() => expect(screen.queryByText("Tenant A stale agent")).not.toBeInTheDocument());
    expect(screen.getByText("Tenant B agent")).toBeInTheDocument();
  });
});

describe("production request and polling helpers", () => {
  it("builds a provider-backed sentiment request without local sample files", () => {
    const request = buildProductionSentimentRequest("aapl msft", new Date("2026-08-01T12:00:00Z"));
    expect(request.symbols).toEqual(["AAPL", "MSFT"]);
    expect(request.providers).toEqual(["rss", "local_web"]);
    expect(request.news_files).toEqual([]);
    expect(request.start).toBe("2026-07-02");
    expect(request.end).toBe("2026-08-01");
    expect(request.stocktwits_max_pages).toBe(20);
  });

  it("anchors sentiment windows to historical dataset dates and weights confidence", () => {
    const dataset = {
      metadata: { end: "2024-01-10" },
      daily_points: [
        { date: "2024-01-01", ticker: "AAA", article_count: 5, sentiment_score: -0.5, confidence: 0.2 },
        { date: "2024-01-04", ticker: "AAA", article_count: 2, sentiment_score: 0.2, confidence: 0.4 },
        { date: "2024-01-10", ticker: "AAA", article_count: 6, sentiment_score: 0.6, confidence: 0.8 },
      ],
      headlines: [], scored_headlines: [], ticker_summary: [], source_summary: [], warnings: [], summary: {},
    } as unknown as SentimentDatasetPayload;

    const cutoff = sentimentWindowCutoff(dataset, "7");
    const matrix = buildSentimentNewsMatrix(dataset, cutoff);

    expect(cutoff).toBe("2024-01-04");
    expect(matrix).toHaveLength(1);
    expect(matrix[0].article_count).toBe(8);
    expect(matrix[0].avg_sentiment).toBeCloseTo(0.5);
    expect(matrix[0].avg_confidence).toBeCloseTo(0.7);
    expect(matrix[0].latest_sentiment).toBe(0.6);
  });

  it("gives duplicate syndicated headlines stable distinct keys", () => {
    const first = sentimentHeadlineKey({ headline: "Same wire story", timestamp: "2026-08-01T10:00:00Z", source: "wire" }, 0);
    const second = sentimentHeadlineKey({ headline: "Same wire story", timestamp: "2026-08-01T10:05:00Z", source: "wire" }, 1);
    expect(first).not.toBe(second);
  });

  it("bounds polling and cancels pending timers", async () => {
    vi.useFakeTimers();
    const fetchJob = vi.fn().mockResolvedValue({ status: "running" });
    const controller = new AbortController();
    const timedOut = pollJobUntilTerminal(fetchJob, { signal: controller.signal, maxAttempts: 2, initialDelayMs: 1, maxDelayMs: 1 });
    const timeoutAssertion = expect(timedOut).rejects.toBeInstanceOf(JobPollingTimeoutError);
    await vi.advanceTimersByTimeAsync(5);
    await timeoutAssertion;
    expect(fetchJob).toHaveBeenCalledTimes(2);

    const cancelledFetch = vi.fn().mockResolvedValue({ status: "running" });
    const cancelledController = new AbortController();
    const cancelled = pollJobUntilTerminal(cancelledFetch, { signal: cancelledController.signal, initialDelayMs: 1000 });
    const cancelledAssertion = expect(cancelled).rejects.toMatchObject({ name: "AbortError" });
    cancelledController.abort();
    await cancelledAssertion;
    expect(cancelledFetch).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
