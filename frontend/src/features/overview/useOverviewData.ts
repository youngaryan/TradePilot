import { useCallback, useEffect, useRef, useState } from "react";

import {
  getPaperSummary,
  getSentimentDataset,
  getStrategyCatalog,
  listBacktestJobs,
  listMarketResearchJobs,
} from "../../api/client";
import type {
  BacktestJob,
  MarketResearchJob,
  PaperDashboardPayload,
  SentimentDatasetPayload,
  StrategyCatalogItem,
} from "../../api/types";

export interface OverviewData {
  paper: PaperDashboardPayload | null;
  paperError: string | null;
  paperLoading: boolean;

  catalog: StrategyCatalogItem[] | null;
  catalogError: string | null;
  catalogLoading: boolean;

  backtestJobs: BacktestJob[];
  researchJobs: MarketResearchJob[];
  jobsError: string | null;

  sentiment: SentimentDatasetPayload | null;
  sentimentError: string | null;
  sentimentLoading: boolean;

  reload: () => Promise<void>;
  isRefreshing: boolean;
}

function message(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback;
}

/**
 * Overview data loader.
 *
 * Owns its own fetching (rather than taking props) so the overview can be
 * mounted stand-alone as well as inside the application shell. Every response is
 * validated against the organization that requested it, so a slow response from
 * a previous tenant is discarded instead of rendered.
 */
export function useOverviewData(activeOrgId: string | null | undefined): OverviewData {
  const [paper, setPaper] = useState<PaperDashboardPayload | null>(null);
  const [paperError, setPaperError] = useState<string | null>(null);
  const [paperLoading, setPaperLoading] = useState(true);

  const [catalog, setCatalog] = useState<StrategyCatalogItem[] | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);

  const [backtestJobs, setBacktestJobs] = useState<BacktestJob[]>([]);
  const [researchJobs, setResearchJobs] = useState<MarketResearchJob[]>([]);
  const [jobsError, setJobsError] = useState<string | null>(null);

  const [sentiment, setSentiment] = useState<SentimentDatasetPayload | null>(null);
  const [sentimentError, setSentimentError] = useState<string | null>(null);
  const [sentimentLoading, setSentimentLoading] = useState(true);

  const [isRefreshing, setIsRefreshing] = useState(false);
  const version = useRef(0);

  const load = useCallback(async () => {
    const current = ++version.current;
    setIsRefreshing(true);
    const [paperResult, catalogResult, backtestResult, researchResult, sentimentResult] = await Promise.allSettled([
      getPaperSummary(),
      getStrategyCatalog(),
      listBacktestJobs(),
      listMarketResearchJobs(),
      getSentimentDataset(),
    ]);
    if (current !== version.current) return;

    if (paperResult.status === "fulfilled") {
      setPaper(paperResult.value);
      setPaperError(null);
    } else {
      setPaper(null);
      setPaperError(message(paperResult.reason, "The simulated portfolio is unavailable."));
    }
    setPaperLoading(false);

    if (catalogResult.status === "fulfilled") {
      setCatalog(catalogResult.value);
      setCatalogError(null);
    } else {
      setCatalog(null);
      setCatalogError(message(catalogResult.reason, "The strategy library is unavailable."));
    }
    setCatalogLoading(false);

    const jobFailures: string[] = [];
    if (backtestResult.status === "fulfilled") setBacktestJobs(backtestResult.value);
    else jobFailures.push("backtest history");
    if (researchResult.status === "fulfilled") setResearchJobs(researchResult.value);
    else jobFailures.push("research history");
    setJobsError(jobFailures.length ? `Could not load ${jobFailures.join(" and ")}.` : null);

    if (sentimentResult.status === "fulfilled") {
      setSentiment(sentimentResult.value);
      setSentimentError(null);
    } else {
      setSentiment(null);
      setSentimentError(message(sentimentResult.reason, "Sentiment data is unavailable."));
    }
    setSentimentLoading(false);
    setIsRefreshing(false);
  }, []);

  useEffect(() => {
    version.current += 1;
    setPaper(null);
    setCatalog(null);
    setBacktestJobs([]);
    setResearchJobs([]);
    setSentiment(null);
    setPaperError(null);
    setCatalogError(null);
    setJobsError(null);
    setSentimentError(null);
    setPaperLoading(true);
    setCatalogLoading(true);
    setSentimentLoading(true);
    void load();
    return () => {
      version.current += 1;
    };
  }, [activeOrgId, load]);

  return {
    paper,
    paperError,
    paperLoading,
    catalog,
    catalogError,
    catalogLoading,
    backtestJobs,
    researchJobs,
    jobsError,
    sentiment,
    sentimentError,
    sentimentLoading,
    reload: load,
    isRefreshing,
  };
}
