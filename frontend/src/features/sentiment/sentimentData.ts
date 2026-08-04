import type { SentimentAccumulationRequest, SentimentDatasetPayload } from "../../api/types";

/**
 * Sentiment dataset helpers.
 *
 * These deliberately anchor every window to dates observed in the dataset rather
 * than to "today", so a historical dataset is never silently reported as empty.
 */

export function buildProductionSentimentRequest(
  rawSymbols: string,
  today = new Date(),
): SentimentAccumulationRequest {
  const symbols = rawSymbols.trim().split(/\s+/).map((symbol) => symbol.toUpperCase()).filter(Boolean).slice(0, 8);
  const iso = (offsetDays: number) => new Date(today.getTime() + offsetDays * 86400000).toISOString().slice(0, 10);
  return {
    symbols: symbols.length ? symbols : ["AAPL", "MSFT", "NVDA", "GLD"],
    start: iso(-30),
    end: iso(0),
    providers: ["rss", "local_web"],
    rss_feed_urls: [],
    local_web_search_urls: [],
    local_web_refresh_minutes: 60,
    local_web_max_pages_per_source: 30,
    web_research_urls: [],
    web_research_domains: [],
    web_research_query_terms: "",
    web_research_max_articles: 4,
    web_research_fetch_article_text: true,
    news_files: [],
    newsapi_api_key: null,
    alphavantage_api_key: null,
    benzinga_api_key: null,
    stocktwits_access_token: null,
    stocktwits_max_pages: 20,
    use_finbert: false,
    local_finbert_only: true,
  };
}

export type SentimentNewsWindow = "7" | "30" | "90" | "all";

function validDateKey(value: unknown): string | null {
  const key = String(value ?? "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return null;
  const parsed = new Date(`${key}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === key ? key : null;
}

export function sentimentDatasetAnchorDate(dataset: SentimentDatasetPayload | null): string | null {
  if (!dataset) return null;
  const observed = [
    ...(dataset.daily_points ?? []).map((point) => validDateKey(point.date)),
    ...(dataset.scored_headlines ?? []).map((row) => validDateKey(row.timestamp)),
    ...(dataset.headlines ?? []).map((row) => validDateKey(row.timestamp)),
  ].filter((value): value is string => Boolean(value));
  if (observed.length) return observed.sort().at(-1) ?? null;
  return validDateKey(dataset.metadata?.end);
}

export function sentimentWindowCutoff(
  dataset: SentimentDatasetPayload | null,
  window: SentimentNewsWindow,
): string | null {
  if (window === "all") return null;
  const anchor = sentimentDatasetAnchorDate(dataset);
  if (!anchor) return null;
  const cutoff = new Date(`${anchor}T00:00:00Z`);
  cutoff.setUTCDate(cutoff.getUTCDate() - (Number(window) - 1));
  return cutoff.toISOString().slice(0, 10);
}

export function buildSentimentNewsMatrix(dataset: SentimentDatasetPayload | null, cutoff: string | null) {
  const points = dataset?.daily_points ?? [];
  if (!points.length) return dataset?.ticker_summary ?? [];
  const aggregates = new Map<string, { count: number; weight: number; sentimentSum: number; confidenceSum: number; latestDate: string; latest: number }>();
  for (const point of points) {
    const pointDate = validDateKey(point.date);
    if (!pointDate || (cutoff && pointDate < cutoff)) continue;
    const ticker = String(point.ticker).toUpperCase();
    const articleCount = Math.max(0, Number(point.article_count) || 0);
    const weight = articleCount || 1;
    const sentimentScore = Number(point.sentiment_score) || 0;
    const confidence = Math.max(0, Math.min(1, Number(point.confidence) || 0));
    const current = aggregates.get(ticker) ?? { count: 0, weight: 0, sentimentSum: 0, confidenceSum: 0, latestDate: "", latest: 0 };
    current.count += articleCount;
    current.weight += weight;
    current.sentimentSum += sentimentScore * weight;
    current.confidenceSum += confidence * weight;
    if (pointDate >= current.latestDate) {
      current.latestDate = pointDate;
      current.latest = sentimentScore;
    }
    aggregates.set(ticker, current);
  }
  return Array.from(aggregates.entries())
    .map(([ticker, aggregate]) => ({
      ticker,
      article_count: aggregate.count,
      avg_sentiment: aggregate.sentimentSum / aggregate.weight,
      avg_confidence: aggregate.confidenceSum / aggregate.weight,
      latest_sentiment: aggregate.latest,
    }))
    .sort((a, b) => a.ticker.localeCompare(b.ticker));
}

/** Stable key for syndicated headlines that share text but differ by timestamp. */
export function sentimentHeadlineKey(row: Record<string, unknown>, index: number): string {
  return [row.timestamp, row.ticker, row.source ?? row.provider_name, row.url, row.headline ?? row.title, index]
    .map((value) => String(value ?? ""))
    .join("|");
}
