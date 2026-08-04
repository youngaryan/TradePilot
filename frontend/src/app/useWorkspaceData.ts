import { useCallback, useEffect, useRef, useState } from "react";

import {
  getBacktestTemplates,
  getPaperSummary,
  getStrategyCatalog,
  getSystemAdminCounts,
  getSystemMetadata,
  listBacktestJobs,
  listPaperRunJobs,
} from "../api/client";
import type {
  BacktestJob,
  BacktestTemplate,
  PaperDashboardPayload,
  PaperRunJob,
  StrategyCatalogItem,
  SystemMetadata,
} from "../api/types";

export interface WorkspaceDataState {
  paper: PaperDashboardPayload | null;
  catalog: StrategyCatalogItem[] | null;
  templates: BacktestTemplate[];
  paperJobs: PaperRunJob[];
  backtestJobs: BacktestJob[];
  metadata: SystemMetadata | null;
  /** First load in progress for the active tenant. */
  isLoading: boolean;
  /** Background refresh in progress (data on screen stays visible). */
  isRefreshing: boolean;
  /** Per-resource failure messages, so one broken endpoint cannot blank a page. */
  errors: {
    paper: string | null;
    catalog: string | null;
    templates: string | null;
    jobs: string | null;
    metadata: string | null;
  };
  setPaper: (payload: PaperDashboardPayload | null) => void;
  setCatalog: (catalog: StrategyCatalogItem[]) => void;
  setPaperJobs: (jobs: PaperRunJob[]) => void;
  setBacktestJobs: (jobs: BacktestJob[]) => void;
  refresh: () => Promise<void>;
}

function message(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

/**
 * Tenant-scoped application data shared by the overview, strategy, backtest and
 * paper-trading screens.
 *
 * Every request is keyed to the active organization; a response that arrives
 * after the workspace changed is discarded so one tenant's numbers can never be
 * shown under another tenant's header. Failures are reported per resource — the
 * UI never substitutes placeholder content for a failed call.
 */
export function useWorkspaceData(
  activeOrgId: string | null,
  options: { authenticated: boolean; isPlatformAdmin: boolean },
): WorkspaceDataState {
  const { authenticated, isPlatformAdmin } = options;
  const [paper, setPaper] = useState<PaperDashboardPayload | null>(null);
  const [catalog, setCatalog] = useState<StrategyCatalogItem[] | null>(null);
  const [templates, setTemplates] = useState<BacktestTemplate[]>([]);
  const [paperJobs, setPaperJobs] = useState<PaperRunJob[]>([]);
  const [backtestJobs, setBacktestJobs] = useState<BacktestJob[]>([]);
  const [metadata, setMetadata] = useState<SystemMetadata | null>(null);
  const [isLoading, setIsLoading] = useState(authenticated);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [errors, setErrors] = useState<WorkspaceDataState["errors"]>({
    paper: null,
    catalog: null,
    templates: null,
    jobs: null,
    metadata: null,
  });
  const version = useRef(0);
  const initialised = useRef(false);

  const load = useCallback(async (mode: "initial" | "refresh") => {
    if (!authenticated) {
      setIsLoading(false);
      return;
    }
    const current = ++version.current;
    if (mode === "initial") setIsLoading(true);
    else setIsRefreshing(true);

    const [
      paperResult,
      catalogResult,
      templatesResult,
      paperJobsResult,
      backtestJobsResult,
      metadataResult,
      adminCountsResult,
    ] = await Promise.allSettled([
      getPaperSummary(),
      getStrategyCatalog(),
      getBacktestTemplates(),
      listPaperRunJobs(),
      listBacktestJobs(),
      getSystemMetadata(),
      isPlatformAdmin ? getSystemAdminCounts() : Promise.resolve(null),
    ]);

    if (current !== version.current) return;

    const nextErrors: WorkspaceDataState["errors"] = {
      paper: null,
      catalog: null,
      templates: null,
      jobs: null,
      metadata: null,
    };

    if (paperResult.status === "fulfilled") setPaper(paperResult.value);
    else {
      setPaper(null);
      nextErrors.paper = message(paperResult.reason, "The simulated portfolio could not be loaded.");
    }

    if (catalogResult.status === "fulfilled") setCatalog(catalogResult.value);
    else {
      setCatalog(null);
      nextErrors.catalog = message(catalogResult.reason, "The strategy library could not be loaded.");
    }

    if (templatesResult.status === "fulfilled") setTemplates(templatesResult.value);
    else {
      setTemplates([]);
      nextErrors.templates = message(templatesResult.reason, "Backtest templates could not be loaded.");
    }

    if (paperJobsResult.status === "fulfilled") setPaperJobs(paperJobsResult.value);
    if (backtestJobsResult.status === "fulfilled") setBacktestJobs(backtestJobsResult.value);
    if (paperJobsResult.status === "rejected" || backtestJobsResult.status === "rejected") {
      nextErrors.jobs = "Some job history could not be loaded.";
    }

    if (metadataResult.status === "fulfilled") {
      const counts = adminCountsResult.status === "fulfilled" ? adminCountsResult.value : null;
      setMetadata(counts ? { ...metadataResult.value, counts: counts.counts } : metadataResult.value);
    } else {
      setMetadata(null);
      nextErrors.metadata = message(metadataResult.reason, "Backend metadata could not be loaded.");
    }

    setErrors(nextErrors);
    setIsLoading(false);
    setIsRefreshing(false);
  }, [authenticated, isPlatformAdmin]);

  useEffect(() => {
    version.current += 1;
    if (!authenticated) {
      setPaper(null);
      setCatalog(null);
      setTemplates([]);
      setPaperJobs([]);
      setBacktestJobs([]);
      setMetadata(null);
      setIsLoading(false);
      return undefined;
    }
    // Clear tenant data immediately so nothing from the previous workspace is
    // visible while the new workspace loads.
    setPaper(null);
    setCatalog(null);
    setTemplates([]);
    setPaperJobs([]);
    setBacktestJobs([]);
    void load(initialised.current ? "refresh" : "initial");
    initialised.current = true;
    return () => {
      version.current += 1;
    };
  }, [authenticated, activeOrgId, load]);

  return {
    paper,
    catalog,
    templates,
    paperJobs,
    backtestJobs,
    metadata,
    isLoading,
    isRefreshing,
    errors,
    setPaper,
    setCatalog,
    setPaperJobs,
    setBacktestJobs,
    refresh: () => load("refresh"),
  };
}
