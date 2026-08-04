import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { listBacktestJobs, listMarketResearchJobs, listPaperRunJobs } from "../api/client";

export interface ActivityEntry {
  id: string;
  kind: "Backtest" | "Paper run" | "AI research";
  status: string;
  stage?: string;
  progress?: number;
  updatedAtUtc: string;
  warningCount: number;
  path: string;
  label: string;
}

const NON_TERMINAL = new Set(["queued", "running"]);

/**
 * Background-job activity for the shell.
 *
 * Reads the same tenant-scoped job endpoints the feature screens use, so the
 * activity menu never shows a job the workspace does not actually own. Polls
 * fast only while something is in flight; nothing is fabricated when the calls
 * fail — the menu reports the failure instead.
 */
export function useBackgroundJobs(enabled: boolean, activeOrgId: string | null) {
  const [entries, setEntries] = useState<ActivityEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const version = useRef(0);

  const load = useCallback(async () => {
    if (!enabled) {
      setEntries(null);
      setError(null);
      return;
    }
    const current = ++version.current;
    const [backtests, paper, research] = await Promise.allSettled([
      listBacktestJobs(),
      listPaperRunJobs(),
      listMarketResearchJobs(),
    ]);
    if (current !== version.current) return;

    if (backtests.status === "rejected" && paper.status === "rejected" && research.status === "rejected") {
      setEntries(null);
      setError("Job activity is unavailable right now.");
      return;
    }

    const next: ActivityEntry[] = [];
    if (backtests.status === "fulfilled") {
      for (const job of backtests.value) {
        const request = (job.request ?? {}) as { symbols?: string[] };
        next.push({
          id: job.id,
          kind: "Backtest",
          status: String(job.status),
          stage: job.stage,
          progress: job.progress,
          updatedAtUtc: job.updated_at_utc,
          warningCount: job.warnings?.length ?? 0,
          path: "/backtests",
          label: Array.isArray(request.symbols) && request.symbols.length ? request.symbols.join(" ") : "Validation run",
        });
      }
    }
    if (paper.status === "fulfilled") {
      for (const job of paper.value) {
        next.push({
          id: job.id,
          kind: "Paper run",
          status: String(job.status),
          stage: job.stage,
          progress: job.progress,
          updatedAtUtc: job.updated_at_utc,
          warningCount: 0,
          path: "/paper",
          label: job.message || "Simulated deployment",
        });
      }
    }
    if (research.status === "fulfilled") {
      for (const job of research.value) {
        const request = (job.request ?? {}) as { ticker?: string };
        next.push({
          id: job.id,
          kind: "AI research",
          status: String(job.status),
          stage: job.stage,
          progress: job.progress,
          updatedAtUtc: job.updated_at_utc,
          warningCount: job.warnings?.length ?? 0,
          path: "/research",
          label: String(request.ticker ?? "Research committee"),
        });
      }
    }

    next.sort((a, b) => (b.updatedAtUtc || "").localeCompare(a.updatedAtUtc || ""));
    setEntries(next);
    setError(null);
  }, [enabled]);

  const activeCount = useMemo(
    () => (entries ?? []).filter((entry) => NON_TERMINAL.has(entry.status)).length,
    [entries],
  );

  useEffect(() => {
    version.current += 1;
    setEntries(null);
    setError(null);
    if (!enabled) return undefined;
    void load();
    return () => {
      version.current += 1;
    };
  }, [enabled, activeOrgId, load]);

  useEffect(() => {
    if (!enabled) return undefined;
    const interval = window.setInterval(() => void load(), activeCount > 0 ? 12_000 : 60_000);
    return () => window.clearInterval(interval);
  }, [enabled, activeCount, load]);

  return { entries, error, activeCount, reload: load };
}
