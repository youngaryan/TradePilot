import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import {
  approveStrategySpec,
  archiveMarketplaceListing,
  chatStrategyBuilder,
  createMarketplaceListing,
  getBacktestJob,
  getFinancialEvents,
  getMarketResearchJob,
  getOhlc,
  getHealth,
  getPaperRunJob,
  getPaperSummary,
  getSentimentDataset,
  getSentimentAccumulationJob,
  getStrategyCatalog,
  listBacktestJobs,
  listMarketResearchJobs,
  listMarketplaceListings,
  listMarketplacePublications,
  listMarketplaceSubscriptions,
  listUserStrategies,
  startBacktest,
  startMarketResearchJob,
  startPaperRunJob,
  startSentimentAccumulationJob,
  publishMarketplaceListing,
  subscribeMarketplaceListing,
  unsubscribeMarketplaceListing,
} from "../api/client";
import type {
  BacktestJob,
  FinancialEventRecord,
  MarketResearchJob,
  MarketResearchReport,
  MarketplaceListing,
  MarketplaceSubscription,
  OhlcRow,
  PaperDashboardPayload,
  SentimentAccumulationRequest,
  SentimentDatasetPayload,
  StrategyBuilderMessage,
  StrategyBuilderResponse,
  StrategyCatalogItem,
  StrategySpec,
  UserStrategyRecord,
  WorkspacePayload,
} from "../api/types";
import { formatCurrency, formatNumber, formatPercent, pipelineLabel } from "../utils/format";

/**
 * Apollo — rule-safe trading research dashboard.
 *
 * Production dashboard backed by the authenticated `/api` workspace endpoints.
 * It owns its theming and layout, while all portfolio, strategy, research, and
 * sentiment content comes from the active tenant's API responses.
 *
 * Mounted at the standalone `/apollo` route (see main.tsx) so it renders as its
 * own full-page app shell without nesting inside the existing console.
 */

// ---------------------------------------------------------------------------
// Icons (inline SVG, no icon-font dependency)
// ---------------------------------------------------------------------------
const ICONS: Record<string, string> = {
  search:
    '<svg viewBox="0 0 24 24" fill="none" width="100%" height="100%"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="2"/><line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  bell:
    '<svg viewBox="0 0 24 24" fill="none" width="16" height="16"><path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M13.73 21a2 2 0 01-3.46 0" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  plus:
    '<svg viewBox="0 0 24 24" fill="none" width="100%" height="100%"><line x1="12" y1="5" x2="12" y2="19" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>',
  sparkle:
    '<svg viewBox="0 0 24 24" fill="currentColor" width="100%" height="100%"><path d="M12 2l1.8 5.6L19 9.5l-5.2 1.9L12 17l-1.8-5.6L5 9.5l5.2-1.9L12 2z"/></svg>',
  sun:
    '<svg viewBox="0 0 24 24" fill="none" width="16" height="16"><circle cx="12" cy="12" r="4.5" stroke="currentColor" stroke-width="2"/><g stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="12" y1="1.5" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="22.5"/><line x1="1.5" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="22.5" y2="12"/><line x1="4.2" y1="4.2" x2="6" y2="6"/><line x1="18" y1="18" x2="19.8" y2="19.8"/><line x1="19.8" y1="4.2" x2="18" y2="6"/><line x1="6" y1="18" x2="4.2" y2="19.8"/></g></svg>',
  moon:
    '<svg viewBox="0 0 24 24" fill="none" width="16" height="16"><path d="M21 12.5A9 9 0 1111.5 3a7 7 0 009.5 9.5z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  home:
    '<svg viewBox="0 0 24 24" fill="none" width="17" height="17"><path d="M3 11l9-7 9 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M5 10v10h14V10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  layers:
    '<svg viewBox="0 0 24 24" fill="none" width="17" height="17"><path d="M12 3l9 5-9 5-9-5 9-5z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M3 13l9 5 9-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  flask:
    '<svg viewBox="0 0 24 24" fill="none" width="17" height="17"><path d="M9 2v6.5L3.5 19a1.5 1.5 0 001.3 2.3h14.4a1.5 1.5 0 001.3-2.3L15 8.5V2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><line x1="8" y1="2" x2="16" y2="2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><line x1="6.5" y1="14" x2="17.5" y2="14" stroke="currentColor" stroke-width="2"/></svg>',
  activity:
    '<svg viewBox="0 0 24 24" fill="none" width="17" height="17"><polyline points="2,13 7,13 10,4 14,20 17,13 22,13" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  brain:
    '<svg viewBox="0 0 24 24" fill="none" width="17" height="17"><path d="M9 4a3 3 0 00-3 3v1a3 3 0 00-1 5.8V15a3 3 0 003 3h1M15 4a3 3 0 013 3v1a3 3 0 011 5.8V15a3 3 0 01-3 3h-1M9 4v14M15 4v14" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  news:
    '<svg viewBox="0 0 24 24" fill="none" width="17" height="17"><rect x="3" y="4" width="18" height="16" rx="2" stroke="currentColor" stroke-width="1.8"/><line x1="7" y1="8" x2="17" y2="8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><line x1="7" y1="12" x2="17" y2="12" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><line x1="7" y1="16" x2="13" y2="16" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  chevronDown:
    '<svg viewBox="0 0 24 24" fill="none" width="100%" height="100%"><path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  trendUp:
    '<svg viewBox="0 0 24 24" fill="none" width="100%" height="100%"><polyline points="3,17 9,11 13,15 21,6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><polyline points="15,6 21,6 21,12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  trendDown:
    '<svg viewBox="0 0 24 24" fill="none" width="100%" height="100%"><polyline points="3,7 9,13 13,9 21,18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><polyline points="15,18 21,18 21,12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  candle:
    '<svg viewBox="0 0 24 24" fill="none" width="100%" height="100%"><line x1="6" y1="3" x2="6" y2="21" stroke="currentColor" stroke-width="1.6"/><rect x="3.5" y="8" width="5" height="6" rx="1" stroke="currentColor" stroke-width="1.6"/><line x1="16" y1="2" x2="16" y2="20" stroke="currentColor" stroke-width="1.6"/><rect x="13.5" y="6" width="5" height="8" rx="1" stroke="currentColor" stroke-width="1.6"/></svg>',
  shield:
    '<svg viewBox="0 0 24 24" fill="none" width="100%" height="100%"><path d="M12 3l7 3v6c0 4.5-3 8-7 9-4-1-7-4.5-7-9V6l7-3z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><line x1="12" y1="8" x2="12" y2="13" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="16.2" r="0.9" fill="currentColor"/></svg>',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Parse a CSS declaration string ("a:b; c:d") into a React style object.
 * Splits on ";" only at paren depth 0, so semicolons inside values such as
 * `url('data:image/svg+xml;charset=UTF-8,...')` are preserved intact.
 */
function css(s: string): CSSProperties {
  const out: Record<string, string> = {};
  let depth = 0;
  let start = 0;
  const decls: string[] = [];
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (ch === "(") depth++;
    else if (ch === ")") depth--;
    else if (ch === ";" && depth === 0) {
      decls.push(s.slice(start, i));
      start = i + 1;
    }
  }
  decls.push(s.slice(start));
  decls.forEach((decl) => {
    const i = decl.indexOf(":");
    if (i === -1) return;
    const prop = decl.slice(0, i).trim();
    const val = decl.slice(i + 1).trim();
    if (!prop) return;
    const camel = prop.replace(/-([a-z])/g, (_, c: string) => c.toUpperCase());
    out[camel] = val;
  });
  return out as CSSProperties;
}

/** Render an inline-SVG icon string. */
function Icon({ html, style }: { html: string; style?: CSSProperties }) {
  return <span style={{ display: "inline-flex", ...style }} dangerouslySetInnerHTML={{ __html: html }} />;
}

function buildPath(points: number[], w: number, h: number, min: number, max: number) {
  if (points.length < 2) return "";
  const range = max - min || 1;
  const stepX = w / (points.length - 1);
  return points
    .map((p, i) => {
      const x = i * stepX;
      const y = h - ((p - min) / range) * h;
      return (i === 0 ? "M" : "L") + x.toFixed(1) + "," + y.toFixed(1);
    })
    .join(" ");
}

// Like buildPath but tolerates null gaps (e.g. an SMA-200 line that only
// begins once enough history exists); x stays aligned to the full index.
function buildPathSparse(points: Array<number | null | undefined>, w: number, h: number, min: number, max: number) {
  const range = max - min || 1;
  const stepX = w / ((points.length - 1) || 1);
  let d = "";
  let started = false;
  points.forEach((p, i) => {
    if (p == null || !Number.isFinite(p)) return;
    const x = i * stepX;
    const y = h - ((p - min) / range) * h;
    d += (started ? " L" : "M") + x.toFixed(1) + "," + y.toFixed(1);
    started = true;
  });
  return d;
}

const TOUR_STEPS: Array<{ screen: Screen; title: string; body: string }> = [
  { screen: "home", title: "Welcome to Apollo", body: "This dashboard tracks your simulated portfolio: paper equity, today's P&L, live agents, and your most recent backtests — all fake money, no broker." },
  { screen: "layers", title: "Strategy Library", body: "Browse server-provided strategies, describe your own rule-based idea, or review workspace-authored strategies. Marketplace availability is controlled by the workspace server." },
  { screen: "flask", title: "Backtesting", body: "Pick tickers, dates, and a timeframe, then run a walk-forward backtest. You get an equity-vs-benchmark chart, trade markers, real SEC events, and a plain-English analysis with an overfitting (PBO) check." },
  { screen: "activity", title: "Paper Trading", body: "Deploy validated strategies as fake-money agents. Watch live simulated equity, share holdings, and orders — zero real capital at risk." },
  { screen: "brain", title: "AI Research", body: "Run a multi-analyst committee on any ticker: bull, bear, technical, and risk analysts debate to a BUY / HOLD / SELL / AVOID verdict. Informational only." },
  { screen: "news", title: "News & Sentiment", body: "Scan headlines for your watchlist, score them per ticker, and read the auto-generated overall take. Change the timeframe to zoom in. That's the tour — enjoy!" },
];

// Map a mouse position over a stretched SVG chart to the nearest series index.
function hoverIndexFromEvent(e: React.MouseEvent<SVGSVGElement>, count: number): number | null {
  if (count < 2) return null;
  const rect = e.currentTarget.getBoundingClientRect();
  const ratio = (e.clientX - rect.left) / (rect.width || 1);
  return Math.max(0, Math.min(count - 1, Math.round(ratio * (count - 1))));
}

function intervalLabel(iv: string): string {
  return iv === "1h" ? "1-hour bars" : iv === "4h" ? "4-hour bars" : iv === "1mo" ? "Monthly bars" : "Daily bars";
}

export function boundedBuilderMessages(messages: StrategyBuilderMessage[], next: StrategyBuilderMessage): StrategyBuilderMessage[] {
  return [...messages, next].slice(-20);
}

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

const TERMINAL_JOB_STATUSES = new Set(["completed", "failed", "interrupted"]);

export class JobPollingTimeoutError extends Error {
  constructor(attempts: number) {
    super(`Job did not finish after ${attempts} status checks. Retry or check the job history.`);
    this.name = "JobPollingTimeoutError";
  }
}

function pollingDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Polling cancelled", "AbortError"));
      return;
    }
    const cancel = () => {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", cancel);
      reject(new DOMException("Polling cancelled", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", cancel);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", cancel, { once: true });
  });
}

export async function pollJobUntilTerminal<T extends { status: string }>(
  fetchJob: () => Promise<T>,
  options: {
    signal: AbortSignal;
    maxAttempts?: number;
    initialDelayMs?: number;
    maxDelayMs?: number;
  },
): Promise<T> {
  const maxAttempts = options.maxAttempts ?? 40;
  let delayMs = options.initialDelayMs ?? 1200;
  const maxDelayMs = options.maxDelayMs ?? 5000;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    await pollingDelay(delayMs, options.signal);
    const job = await fetchJob();
    if (TERMINAL_JOB_STATUSES.has(job.status)) return job;
    delayMs = Math.min(maxDelayMs, Math.ceil(delayMs * 1.35));
  }
  throw new JobPollingTimeoutError(maxAttempts);
}

export function buildProductionSentimentRequest(
  rawSymbols: string,
  today = new Date(),
): SentimentAccumulationRequest {
  const symbols = rawSymbols.trim().split(/\s+/).map((s) => s.toUpperCase()).filter(Boolean).slice(0, 8);
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
    .map(([ticker, aggregate]) => {
      return {
        ticker,
        article_count: aggregate.count,
        avg_sentiment: aggregate.sentimentSum / aggregate.weight,
        avg_confidence: aggregate.confidenceSum / aggregate.weight,
        latest_sentiment: aggregate.latest,
      };
    })
    .sort((a, b) => a.ticker.localeCompare(b.ticker));
}

export function sentimentHeadlineKey(row: Record<string, unknown>, index: number): string {
  return [row.timestamp, row.ticker, row.source ?? row.provider_name, row.url, row.headline ?? row.title, index]
    .map((value) => String(value ?? ""))
    .join("|");
}

const ACCENTS: Record<string, string> = {
  cobalt: "oklch(56% 0.19 258)",
  orchid: "oklch(54% 0.18 322)",
  cyan: "oklch(62% 0.11 215)",
  chartreuse: "oklch(72% 0.15 128)",
};

type Screen = "home" | "layers" | "flask" | "activity" | "brain" | "news" | "account";

/** Normalized strategy-card model produced from the active workspace catalog. */
interface StratCard {
  id: string;
  tag: string;
  name: string;
  desc: string;
  badgeText: string;
  badgeColor: string;
  badgeTitle?: string;
  footLabel: string;
  footValue?: string;
  /** Where the strategy came from — built-in library, this user, or the community. */
  origin?: "builtin" | "benchmark" | "user" | "community";
}

export interface ApolloDashboardProps {
  accent?: keyof typeof ACCENTS;
  /** Signed-in identity supplied by the authenticated application shell. */
  userName?: string;
  userInitials?: string;
  workspaceLabel?: string;
  organizations?: Array<{ id: string; name: string }>;
  activeOrgId?: string | null;
  onSwitchOrg?: (id: string) => void;
  /** undefined = don't show the badge; true/false = backend health. */
  backendOnline?: boolean;
  hasPremium?: boolean;
  /** When provided, the user row becomes an interactive menu with sign-out. */
  onLogout?: () => void;
  /** Signed-in user id — used to distinguish "my" strategies in the community marketplace. */
  userId?: string;
  userEmail?: string;
  userRole?: string;
  planName?: string;
  planStatus?: string | null;
  capabilities?: WorkspacePayload["capabilities"];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export function ApolloDashboard(props: ApolloDashboardProps = {}) {
  const {
    accent = "cyan",
    userName = "Jordan Marsh",
    workspaceLabel = "Sandbox workspace",
    organizations = [],
    activeOrgId = null,
    onSwitchOrg,
    backendOnline,
    hasPremium,
    onLogout,
    userId,
    userEmail,
    userRole,
    planName,
    planStatus,
    capabilities,
  } = props;
  const builderIsLlm = capabilities?.strategy_builder_mode === "llm";
  const builderDesignLabel = builderIsLlm ? "Design with AI" : "Design with rules";
  const builderBusyLabel = builderIsLlm ? "Thinking…" : "Parsing…";
  const builderGenerationLabel = builderIsLlm ? "AI-assisted" : "rule-generated";
  const builderProviderLabel = builderIsLlm && capabilities?.strategy_builder_provider
    ? `${capabilities.strategy_builder_provider}${capabilities.strategy_builder_model ? ` / ${capabilities.strategy_builder_model}` : ""}`
    : "deterministic rules";
  const activeOrgRef = useRef(activeOrgId);
  activeOrgRef.current = activeOrgId;
  const userInitials =
    props.userInitials ||
    userName
      .split(/\s+/)
      .slice(0, 2)
      .map((w) => w[0]?.toUpperCase() ?? "")
      .join("") ||
    "?";

  // Live viewport width so inline styles can respond to breakpoints.
  const [viewportW, setViewportW] = useState(() => window.innerWidth);
  useEffect(() => {
    const onResize = () => setViewportW(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const isNarrow = viewportW < 1000;
  const isMobile = viewportW < 720;
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  const [theme, setTheme] = useState<"dark" | "light">(() =>
    window.localStorage.getItem("apollo.theme") === "dark" ? "dark" : "light",
  );
  useEffect(() => { window.localStorage.setItem("apollo.theme", theme); }, [theme]);

  // Live "data connected" status: an honest "updated Xm ago" rather than a
  // static "Online" label that never actually re-checks anything.
  const [lastConnectedAt, setLastConnectedAt] = useState<Date | null>(null);
  const [nowTick, setNowTick] = useState(() => new Date());
  useEffect(() => {
    let alive = true;
    const check = () => {
      getHealth()
        .then(() => { if (alive) setLastConnectedAt(new Date()); })
        .catch(() => undefined);
    };
    check();
    const healthTimer = window.setInterval(check, 45000);
    const clockTimer = window.setInterval(() => setNowTick(new Date()), 15000);
    return () => { alive = false; window.clearInterval(healthTimer); window.clearInterval(clockTimer); };
  }, []);
  const connectionStatus = (() => {
    if (backendOnline === false) return { label: "Offline", dot: false };
    if (!lastConnectedAt) return { label: "Connecting…", dot: false };
    const mins = Math.max(0, Math.round((nowTick.getTime() - lastConnectedAt.getTime()) / 60000));
    return { label: `Data connected · updated ${mins === 0 ? "just now" : `${mins}m ago`}`, dot: true };
  })();

  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [screen, setScreen] = useState<Screen>("home");

  // -- First-run product tour ---------------------------------------------------
  const [tourStep, setTourStep] = useState<number | null>(null);
  useEffect(() => {
    if (!window.localStorage.getItem("apollo.tour_done")) setTourStep(0);
  }, []);
  const endTour = () => {
    window.localStorage.setItem("apollo.tour_done", "1");
    setTourStep(null);
  };

  // -- Command palette + notifications ------------------------------------------
  const [bellOpen, setBellOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen((v) => !v);
        setSearchQuery("");
      } else if (e.key === "Escape") {
        setSearchOpen(false);
        setBellOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const [openNavKey, setOpenNavKey] = useState<Screen | null>("home");
  const [category, setCategory] = useState("All Categories");
  const [universeFilter, setUniverseFilter] = useState("All Universes");
  const [range, setRange] = useState("3M");
  const [selectedStrategy, setSelectedStrategy] = useState("Golden Cross (50/200 SMA)");
  // Manual backtest configuration.
  const [backtestSymbols, setBacktestSymbols] = useState<string[]>(["SPY"]);
  const [symbolInput, setSymbolInput] = useState("");
  const [backtestStart, setBacktestStart] = useState("2018-01-01");
  const [backtestEnd, setBacktestEnd] = useState("2023-12-31");
  const [backtestInterval, setBacktestInterval] = useState<"1d" | "4h" | "1h" | "1mo">("1d");
  // Strategy parameter overrides sent to the engine (blank value = engine default).
  const [btParamRows, setBtParamRows] = useState<Array<{ k: string; v: string }>>([]);
  const [showParams, setShowParams] = useState(false);
  const [chartMode, setChartMode] = useState<"equity" | "price" | "candles">("equity");
  // Real OHLC bars for the active backtest's primary symbol (candlestick mode).
  const [btOhlc, setBtOhlc] = useState<OhlcRow[]>([]);
  const [btOhlcError, setBtOhlcError] = useState<string | null>(null);
  // Chart hover tracking (crosshair + timeline readout) per chart.
  const [btHoverIdx, setBtHoverIdx] = useState<number | null>(null);
  const [paHoverIdx, setPaHoverIdx] = useState<number | null>(null);
  const [dashHoverIdx, setDashHoverIdx] = useState<number | null>(null);
  // Backtest chart analysis tools: drag-to-zoom window (full-series indices),
  // in-progress drag selection (view indices), indicator sub-chart, SMA toggles.
  const [btZoom, setBtZoom] = useState<[number, number] | null>(null);
  const [btDrag, setBtDrag] = useState<[number, number] | null>(null);
  const [subChart, setSubChart] = useState<"none" | "rsi" | "drawdown">("none");
  const [smaVis, setSmaVis] = useState({ s20: true, s50: true, s200: true });

  const addSymbol = (raw: string) => {
    const t = raw.trim().toUpperCase();
    if (!t) return;
    setBacktestSymbols((prev) => (prev.includes(t) ? prev : [...prev, t]));
    setSymbolInput("");
  };
  const removeSymbol = (t: string) => setBacktestSymbols((prev) => prev.filter((s) => s !== t));

  // Live strategy catalog. Authenticated views never substitute demo content.
  const [catalog, setCatalog] = useState<StrategyCatalogItem[] | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const catalogLoadVersion = useRef(0);
  const reloadCatalog = useCallback(async () => {
    const version = ++catalogLoadVersion.current;
    setCatalogLoading(true);
    setCatalogError(null);
    try {
      const next = await getStrategyCatalog();
      if (version === catalogLoadVersion.current) setCatalog(next);
    } catch (error) {
      if (version === catalogLoadVersion.current) {
        setCatalog(null);
        setCatalogError(error instanceof Error ? error.message : "Strategy library is unavailable.");
      }
    } finally {
      if (version === catalogLoadVersion.current) setCatalogLoading(false);
    }
  }, [activeOrgId]);
  useEffect(() => {
    setCatalog(null);
    void reloadCatalog();
    return () => { catalogLoadVersion.current += 1; };
  }, [reloadCatalog]);
  const strategyCount = catalog?.length ?? 0;

  // Human-readable name for a raw pipeline id. Built-in pipelines format cleanly
  // via pipelineLabel (e.g. "bollinger_reversion_shadow" -> "Bollinger Reversion
  // Shadow"), but user-created/community strategies are addressed internally as
  // "user_strategy:<id>" — pipelineLabel alone would leak that internal id
  // straight into the UI, so look up the real catalog name first.
  const strategyDisplayName = (pipeline: string | null | undefined): string => {
    if (!pipeline) return "Unknown strategy";
    const known = catalog?.find((c) => c.pipeline === pipeline)?.name;
    if (known) return known;
    if (pipeline.startsWith("user_strategy:")) return "Custom strategy";
    return pipelineLabel(pipeline);
  };

  // -- Live read data (paper portfolio, backtest jobs, research jobs) ----------
  const [paper, setPaper] = useState<PaperDashboardPayload | null>(null);
  const [btJobs, setBtJobs] = useState<BacktestJob[]>([]);
  const [researchJobs, setResearchJobs] = useState<MarketResearchJob[]>([]);
  const [tenantDataLoading, setTenantDataLoading] = useState(true);
  const [tenantDataError, setTenantDataError] = useState<string | null>(null);

  const reloadJobs = useCallback(async () => {
    const [b, r] = await Promise.allSettled([listBacktestJobs(), listMarketResearchJobs()]);
    if (activeOrgRef.current !== activeOrgId) return;
    if (b.status === "fulfilled") setBtJobs(b.value);
    if (r.status === "fulfilled") setResearchJobs(r.value);
    if (b.status === "rejected" || r.status === "rejected") setTenantDataError("Some workspace job history could not be loaded.");
  }, [activeOrgId]);

  useEffect(() => {
    let alive = true;
    setPaper(null);
    setBtJobs([]);
    setResearchJobs([]);
    setTenantDataLoading(true);
    setTenantDataError(null);
    Promise.allSettled([getPaperSummary(), listBacktestJobs(), listMarketResearchJobs()]).then(
      ([p, b, r]) => {
        if (!alive) return;
        if (p.status === "fulfilled") setPaper(p.value);
        if (b.status === "fulfilled") setBtJobs(b.value);
        if (r.status === "fulfilled") setResearchJobs(r.value);
        if (p.status === "rejected" || b.status === "rejected" || r.status === "rejected") {
          setTenantDataError("Some workspace data could not be loaded. Retry to restore the full dashboard.");
        }
        setTenantDataLoading(false);
      },
    );
    return () => { alive = false; };
  }, [activeOrgId]);

  // Map a display strategy name to its pipeline id via the live catalog.
  const pipelineFor = useCallback(
    (name: string): string => catalog?.find((c) => c.name === name)?.pipeline ?? catalog?.find((c) => c.id === name)?.pipeline ?? "",
    [catalog],
  );

  // -- Backtest run action (start + poll to completion) -----------------------
  const [btRunning, setBtRunning] = useState(false);
  const [btError, setBtError] = useState<string | null>(null);
  const [btResult, setBtResult] = useState<BacktestJob | null>(null);
  const backtestPoll = useRef<AbortController | null>(null);

  const runBacktest = useCallback(async () => {
    if (!hasPremium) { setBtError("Upgrade to Pro to run live backtests."); return; }
    const symbols = backtestSymbols.length ? backtestSymbols : ["SPY"];
    if (backtestStart >= backtestEnd) { setBtError("Start date must be before end date."); return; }
    const pipeline = pipelineFor(selectedStrategy);
    if (!pipeline) { setBtError("Select a strategy from the loaded catalog before running a backtest."); return; }
    backtestPoll.current?.abort();
    const controller = new AbortController();
    backtestPoll.current = controller;
    setBtError(null);
    setBtResult(null);
    setBtRunning(true);
    // Intraday intervals run in the engine's short-term mode; daily/monthly use the daily path.
    const tradingMode = backtestInterval === "1h" || backtestInterval === "4h" ? "short_term" : "daily";
    // Only explicitly-filled parameters override engine defaults.
    const parameters: Record<string, unknown> = {};
    for (const row of btParamRows) {
      const key = row.k.trim();
      const value = row.v.trim();
      if (!key || !value) continue;
      const num = Number(value);
      parameters[key] = value === "true" ? true : value === "false" ? false : Number.isFinite(num) ? num : value;
    }
    const selectedCatalogItem = catalog?.find((item) => item.name === selectedStrategy || item.id === selectedStrategy);
    const configuredTrainBars = selectedCatalogItem ? catalogBacktestDefaults(selectedCatalogItem).trainBars : 300;
    try {
      const job = await startBacktest({
        pipeline,
        symbols,
        start: backtestStart,
        end: backtestEnd,
        interval: backtestInterval,
        trading_mode: tradingMode,
        experiment_name: "apollo_backtest",
        train_bars: configuredTrainBars,
        parameters,
      } as Parameters<typeof startBacktest>[0]);
      const fresh = await pollJobUntilTerminal(() => getBacktestJob(job.id), { signal: controller.signal });
      if (controller.signal.aborted) return;
      setBtResult(fresh);
      if (fresh.status !== "completed") setBtError(fresh.error || `Backtest ${fresh.status}.`);
      void reloadJobs();
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      setBtError(e instanceof Error ? e.message : "Failed to start backtest.");
    } finally {
      if (backtestPoll.current === controller) {
        backtestPoll.current = null;
        setBtRunning(false);
      }
    }
  }, [backtestSymbols, backtestStart, backtestEnd, backtestInterval, btParamRows, selectedStrategy, pipelineFor, reloadJobs, hasPremium, catalog]);

  useEffect(() => {
    backtestPoll.current?.abort();
    setBtRunning(false);
    setBtResult(null);
    setBtError(null);
    return () => backtestPoll.current?.abort();
  }, [activeOrgId]);

  // -- AI research run action -------------------------------------------------
  const [researchTicker, setResearchTicker] = useState("NVDA");
  const [researchRunning, setResearchRunning] = useState(false);
  const [researchError, setResearchError] = useState<string | null>(null);
  const [researchReport, setResearchReport] = useState<MarketResearchReport | null>(null);
  const researchPoll = useRef<AbortController | null>(null);

  const runResearch = useCallback(async () => {
    if (!hasPremium) { setResearchError("Upgrade to Pro to run the AI research committee."); return; }
    setResearchError(null);
    setResearchReport(null);
    setResearchRunning(true);
    researchPoll.current?.abort();
    const controller = new AbortController();
    researchPoll.current = controller;
    try {
      const job = await startMarketResearchJob({ ticker: researchTicker.trim().toUpperCase() || "NVDA", horizon: "swing" });
      const fresh = await pollJobUntilTerminal(() => getMarketResearchJob(job.id), { signal: controller.signal, initialDelayMs: 1500 });
      if (controller.signal.aborted) return;
      if (fresh.status === "completed" && fresh.result && "decision" in fresh.result) {
        setResearchReport(fresh.result as MarketResearchReport);
      } else {
        setResearchError(fresh.error || `Research ${fresh.status}.`);
      }
      void reloadJobs();
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      setResearchError(e instanceof Error ? e.message : "Failed to start research job.");
    } finally {
      if (researchPoll.current === controller) {
        researchPoll.current = null;
        setResearchRunning(false);
      }
    }
  }, [researchTicker, reloadJobs, hasPremium]);

  useEffect(() => {
    researchPoll.current?.abort();
    setResearchRunning(false);
    setResearchReport(null);
    setResearchError(null);
    return () => researchPoll.current?.abort();
  }, [activeOrgId]);

  // -- Reload paper portfolio -------------------------------------------------
  const reloadPaper = useCallback(async () => {
    try {
      setTenantDataError(null);
      const next = await getPaperSummary();
      if (activeOrgRef.current === activeOrgId) setPaper(next);
    } catch (error) {
      if (activeOrgRef.current === activeOrgId) setTenantDataError(error instanceof Error ? error.message : "Paper portfolio is unavailable.");
    }
  }, [activeOrgId]);

  // -- News & sentiment (dataset + scan action) -------------------------------
  const [sentiment, setSentiment] = useState<SentimentDatasetPayload | null>(null);
  const [sentimentTicker, setSentimentTicker] = useState("AAPL MSFT NVDA GLD");
  const [newsWindow, setNewsWindow] = useState<SentimentNewsWindow>("all");
  const [newsPage, setNewsPage] = useState(1);
  const [scanRunning, setScanRunning] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [sentimentLoading, setSentimentLoading] = useState(true);
  const [sentimentLoadError, setSentimentLoadError] = useState<string | null>(null);
  const sentimentPoll = useRef<AbortController | null>(null);
  const sentimentLoadVersion = useRef(0);

  const reloadSentiment = useCallback(async () => {
    const version = ++sentimentLoadVersion.current;
    setSentimentLoading(true);
    setSentimentLoadError(null);
    try {
      const next = await getSentimentDataset();
      if (version === sentimentLoadVersion.current) setSentiment(next);
    } catch (error) {
      if (version === sentimentLoadVersion.current) {
        setSentiment(null);
        setSentimentLoadError(error instanceof Error ? error.message : "Sentiment data is unavailable.");
      }
    } finally {
      if (version === sentimentLoadVersion.current) setSentimentLoading(false);
    }
  }, [activeOrgId]);

  useEffect(() => {
    setSentiment(null);
    void reloadSentiment();
    return () => { sentimentLoadVersion.current += 1; };
  }, [reloadSentiment]);

  useEffect(() => {
    setNewsPage(1);
  }, [sentiment?.dataset_id, sentiment?.scored_headlines?.length, sentiment?.headlines?.length]);

  const runSentimentScan = useCallback(async () => {
    if (!hasPremium) { setScanError("Upgrade to Pro to run sentiment scans."); return; }
    sentimentPoll.current?.abort();
    const controller = new AbortController();
    sentimentPoll.current = controller;
    setScanError(null);
    setScanRunning(true);
    const request = { ...buildProductionSentimentRequest(sentimentTicker), idempotency_key: `sentiment:${crypto.randomUUID()}` };
    try {
      const job = await startSentimentAccumulationJob(request);
      const fresh = await pollJobUntilTerminal(() => getSentimentAccumulationJob(job.id), { signal: controller.signal, initialDelayMs: 1500 });
      if (controller.signal.aborted) return;
      if (fresh.status === "completed" && fresh.result) {
        setSentiment(fresh.result);
        setSentimentLoadError(null);
      }
      else setScanError(fresh.error || `Scan ${fresh.status}.`);
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      setScanError(e instanceof Error ? e.message : "Failed to start sentiment scan.");
    } finally {
      if (sentimentPoll.current === controller) {
        sentimentPoll.current = null;
        setScanRunning(false);
      }
    }
  }, [sentimentTicker, hasPremium]);

  useEffect(() => {
    sentimentPoll.current?.abort();
    setScanRunning(false);
    setScanError(null);
    return () => sentimentPoll.current?.abort();
  }, [activeOrgId]);

  // -- Deploy paper agents ------------------------------------------------------
  // Deployment is gated behind an actual, completed backtest — no more firing a
  // generic default deployment disconnected from anything the user validated.
  const [deployRunning, setDeployRunning] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);
  const deployPoll = useRef<AbortController | null>(null);
  const [deployJobId, setDeployJobId] = useState<string | null>(null);

  const completedBtJobs = useMemo(
    () =>
      [...btJobs]
        .filter((j) => j.status === "completed" && j.result)
        .sort((a, b) => (b.created_at_utc || "").localeCompare(a.created_at_utc || "")),
    [btJobs],
  );
  useEffect(() => {
    if (!completedBtJobs.length) { setDeployJobId(null); return; }
    if (!deployJobId || !completedBtJobs.some((j) => j.id === deployJobId)) {
      setDeployJobId(completedBtJobs[0].id);
    }
  }, [completedBtJobs, deployJobId]);

  const deployPaper = useCallback(async () => {
    if (!hasPremium) { setDeployError("Upgrade to Pro to deploy paper agents."); return; }
    const chosen = completedBtJobs.find((j) => j.id === deployJobId);
    if (!chosen) { setDeployError("Select a validated backtest to deploy first."); return; }
    const req = chosen.request as { pipeline?: string; symbols?: string[]; interval?: string; trading_mode?: string };
    if (!req.pipeline) { setDeployError("That backtest is missing a strategy pipeline — pick a different run."); return; }
    setDeployError(null);
    setDeployRunning(true);
    deployPoll.current?.abort();
    const controller = new AbortController();
    deployPoll.current = controller;
    try {
      const job = await startPaperRunJob({
        deployment_config: {
          execution: { initial_cash: 100000, commission_bps: 0.5, slippage_bps: 1.0, min_trade_notional: 100.0, weight_tolerance: 0.0025 },
          strategies: [{
            name: strategyDisplayName(req.pipeline),
            pipeline: req.pipeline,
            symbols: req.symbols ?? [],
            interval: req.interval ?? "1d",
            trading_mode: req.trading_mode ?? "daily",
          }],
        },
      });
      const fresh = await pollJobUntilTerminal(() => getPaperRunJob(job.id), { signal: controller.signal, initialDelayMs: 1500 });
      if (controller.signal.aborted) return;
      if (fresh.status === "completed") await reloadPaper();
      else setDeployError(fresh.error || `Deploy ${fresh.status}.`);
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      setDeployError(e instanceof Error ? e.message : "Failed to deploy paper agents.");
    } finally {
      if (deployPoll.current === controller) {
        deployPoll.current = null;
        setDeployRunning(false);
      }
    }
  }, [hasPremium, reloadPaper, completedBtJobs, deployJobId, strategyDisplayName]);

  useEffect(() => {
    deployPoll.current?.abort();
    setDeployRunning(false);
    setDeployError(null);
    return () => deployPoll.current?.abort();
  }, [activeOrgId]);

  // -- Strategy builder (describe an idea → validated, backtestable spec) ------
  const [userStrategies, setUserStrategies] = useState<UserStrategyRecord[]>([]);
  const reloadUserStrategies = useCallback(async () => {
    try {
      const next = await listUserStrategies();
      if (activeOrgRef.current === activeOrgId) setUserStrategies(next);
    } catch {
      if (activeOrgRef.current === activeOrgId) setUserStrategies([]);
    }
  }, [activeOrgId]);
  useEffect(() => {
    setUserStrategies([]);
    void reloadUserStrategies();
  }, [reloadUserStrategies]);

  const [builderMode, setBuilderMode] = useState<"browse" | "design" | "community">("browse");
  const [builderMessages, setBuilderMessages] = useState<StrategyBuilderMessage[]>([]);
  const [builderInput, setBuilderInput] = useState("");
  const [builderResp, setBuilderResp] = useState<StrategyBuilderResponse | null>(null);
  const [builderDraft, setBuilderDraft] = useState<StrategySpec | null>(null);
  const [builderBusy, setBuilderBusy] = useState(false);
  const [builderError, setBuilderError] = useState<string | null>(null);
  const [builderApproved, setBuilderApproved] = useState<StrategyCatalogItem | null>(null);

  useEffect(() => {
    setBuilderMessages([]);
    setBuilderResp(null);
    setBuilderDraft(null);
    setBuilderApproved(null);
    setBuilderError(null);
    setBuilderBusy(false);
  }, [activeOrgId]);

  const sendBuilderMessage = useCallback(async () => {
    const text = builderInput.trim();
    if (!text || builderBusy) return;
    const nextMessages = boundedBuilderMessages(builderMessages, { role: "user", content: text });
    setBuilderMessages(nextMessages);
    setBuilderInput("");
    setBuilderBusy(true);
    setBuilderError(null);
    setBuilderApproved(null);
    // Do not display clarification questions from the previous turn while the
    // new request is still being evaluated.
    setBuilderResp(null);
    const orgAtStart = activeOrgId;
    try {
      const resp = await chatStrategyBuilder(nextMessages, (builderDraft as unknown as Record<string, unknown> | null) ?? null);
      if (activeOrgRef.current !== orgAtStart) return;
      setBuilderResp(resp);
      setBuilderDraft(resp.draft_spec ?? (resp.state === "needs_clarification" ? builderDraft : null));
      setBuilderMessages((prev) => boundedBuilderMessages(prev, { role: "assistant", content: resp.assistant_message }));
    } catch (e) {
      if (activeOrgRef.current === orgAtStart) setBuilderError(e instanceof Error ? e.message : "Strategy builder failed.");
    } finally {
      if (activeOrgRef.current === orgAtStart) setBuilderBusy(false);
    }
  }, [builderInput, builderBusy, builderMessages, builderDraft, activeOrgId]);

  const approveBuilderDraft = useCallback(async () => {
    if (!builderDraft || builderBusy) return;
    setBuilderBusy(true);
    setBuilderError(null);
    const orgAtStart = activeOrgId;
    try {
      const resp = await approveStrategySpec(builderDraft as unknown as Record<string, unknown>, `Approved ${builderDraft.name} from Apollo.`, builderResp?.provenance_token);
      if (activeOrgRef.current !== orgAtStart) return;
      setBuilderApproved(resp.catalog_item);
      // Refresh the catalog so the new strategy appears in the library immediately.
      await Promise.all([reloadCatalog(), reloadUserStrategies()]);
      // Reset the conversation for the next idea.
      setBuilderMessages([]);
      setBuilderDraft(null);
      setBuilderResp(null);
    } catch (e) {
      if (activeOrgRef.current === orgAtStart) setBuilderError(e instanceof Error ? e.message : "Approval failed.");
    } finally {
      if (activeOrgRef.current === orgAtStart) setBuilderBusy(false);
    }
  }, [builderDraft, builderBusy, builderResp?.provenance_token, activeOrgId, reloadCatalog, reloadUserStrategies]);

  const marketplaceEnabled = capabilities?.marketplace_enabled === true;
  const [marketListings, setMarketListings] = useState<MarketplaceListing[]>([]);
  const [marketPublications, setMarketPublications] = useState<MarketplaceListing[]>([]);
  const [marketSubscriptions, setMarketSubscriptions] = useState<MarketplaceSubscription[]>([]);
  const [marketplaceBusy, setMarketplaceBusy] = useState<string | null>(null);
  const [marketplaceError, setMarketplaceError] = useState<string | null>(null);
  const reloadMarketplace = useCallback(async () => {
    if (!marketplaceEnabled) {
      setMarketListings([]);
      setMarketPublications([]);
      setMarketSubscriptions([]);
      return;
    }
    try {
      const [listings, publications, subscriptions] = await Promise.all([
        listMarketplaceListings(),
        listMarketplacePublications(),
        listMarketplaceSubscriptions(),
      ]);
      if (activeOrgRef.current !== activeOrgId) return;
      setMarketListings(listings);
      setMarketPublications(publications);
      setMarketSubscriptions(subscriptions);
      setMarketplaceError(null);
    } catch (error) {
      if (activeOrgRef.current === activeOrgId) setMarketplaceError(error instanceof Error ? error.message : "Marketplace data is unavailable.");
    }
  }, [activeOrgId, marketplaceEnabled]);
  useEffect(() => { void reloadMarketplace(); }, [reloadMarketplace]);

  const marketPub = useMemo(() => Object.fromEntries(
    marketPublications.filter((listing) => listing.source_strategy_id).map((listing) => [listing.source_strategy_id as string, listing])
  ) as Record<string, MarketplaceListing>, [marketPublications]);
  const marketSubs = useMemo(() => Object.fromEntries(
    marketSubscriptions.map((subscription) => [subscription.listing_id, subscription.status === "active"])
  ) as Record<string, boolean>, [marketSubscriptions]);
  const marketSubscriptionByListing = useMemo(() => Object.fromEntries(
    marketSubscriptions.map((subscription) => [subscription.listing_id, subscription])
  ) as Record<string, MarketplaceSubscription>, [marketSubscriptions]);

  const runMarketplaceMutation = useCallback(async (key: string, operation: () => Promise<unknown>) => {
    setMarketplaceBusy(key);
    setMarketplaceError(null);
    try {
      await operation();
      await reloadMarketplace();
    } catch (error) {
      setMarketplaceError(error instanceof Error ? error.message : "Marketplace operation failed.");
    } finally {
      setMarketplaceBusy(null);
    }
  }, [reloadMarketplace]);
  const [specUpload, setSpecUpload] = useState("");
  const [specUploadBusy, setSpecUploadBusy] = useState(false);
  const [specUploadMsg, setSpecUploadMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [showAdvancedImport, setShowAdvancedImport] = useState(false);

  const uploadSpec = useCallback(async () => {
    const raw = specUpload.trim();
    if (!raw || specUploadBusy) return;
    setSpecUploadBusy(true);
    setSpecUploadMsg(null);
    const orgAtStart = activeOrgId;
    try {
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      const resp = await approveStrategySpec(parsed, "Uploaded via the Apollo community marketplace.");
      if (activeOrgRef.current !== orgAtStart) return;
      setSpecUploadMsg({ ok: true, text: `Added “${resp.catalog_item?.name ?? String(parsed.name ?? "strategy")}” to your workspace.` });
      setSpecUpload("");
      await reloadUserStrategies();
      await reloadCatalog();
    } catch (e) {
      if (activeOrgRef.current === orgAtStart) setSpecUploadMsg({
        ok: false,
        text: e instanceof SyntaxError
          ? "That isn't valid JSON — paste a full strategy spec (schema strategy_spec/v1). Tip: design one with AI and copy its spec, or export one from the classic console."
          : e instanceof Error ? e.message : "Upload failed.",
      });
    } finally {
      if (activeOrgRef.current === orgAtStart) setSpecUploadBusy(false);
    }
  }, [specUpload, specUploadBusy, reloadUserStrategies, reloadCatalog, activeOrgId]);

  useEffect(() => {
    if (!catalog?.length) {
      setSelectedStrategy("");
      return;
    }
    if (!catalog.some((item) => item.name === selectedStrategy || item.id === selectedStrategy)) {
      setSelectedStrategy(catalog[0].name);
    }
  }, [catalog, selectedStrategy]);

  // Prefill the parameter editor for the selected strategy: user strategies get
  // their spec's editable parameters with defaults; built-ins get their named
  // key parameters with blank values (engine defaults apply until filled).
  useEffect(() => {
    const item = (catalog ?? []).find((c) => c.name === selectedStrategy);
    if (!item) { setBtParamRows([]); return; }
    if (item.pipeline?.startsWith("user_strategy:")) {
      const id = item.pipeline.split(":")[1];
      const spec = userStrategies.find((s) => s.id === id)?.spec;
      setBtParamRows((spec?.editable_parameters ?? []).map((p) => ({ k: p.name, v: String(p.default ?? "") })));
      return;
    }
    const defaults = catalogBacktestDefaults(item);
    setBtParamRows(defaults.parameters.length ? defaults.parameters : (item.key_parameters ?? []).slice(0, 8).map((k) => ({ k, v: "" })));
  }, [selectedStrategy, catalog, userStrategies]);

  const dark = theme === "dark";
  const accentColor = ACCENTS[accent] || ACCENTS.cyan;
  const colors = dark
    ? {
        bg: "oklch(16.5% 0.013 250)",
        surface: "oklch(20.5% 0.014 250)",
        surfaceRaised: "oklch(24% 0.015 250)",
        border: "oklch(31% 0.015 250)",
        text: "oklch(95% 0.004 250)",
        textFaint: "oklch(68% 0.013 250)",
        accent: accentColor,
        gain: "oklch(68% 0.16 148)",
        loss: "oklch(65% 0.19 25)",
      }
    : {
        bg: "oklch(98% 0.002 250)",
        surface: "oklch(99.4% 0.002 250)",
        surfaceRaised: "oklch(100% 0 0)",
        border: "oklch(90% 0.006 250)",
        text: "oklch(20% 0.012 250)",
        textFaint: "oklch(48% 0.012 250)",
        accent: accentColor,
        gain: "oklch(56% 0.15 148)",
        loss: "oklch(56% 0.19 25)",
      };

  const font = "'Inter', -apple-system, sans-serif";
  const grotesk = "'Space Grotesk', sans-serif";
  // Same display face as the APOLLO wordmark on the entry page — reserved for
  // the brand mark itself, not general headings (it's too heavy for body text).
  const black = "'Archivo Black', 'Space Grotesk', sans-serif";
  const chevronColor = dark ? "9aa3b5" : "6b7280";

  const navigateTo = (key: Screen) => {
    setScreen(key);
    setOpenNavKey(key);
    setMobileNavOpen(false);
  };
  const toggleNavOpen = (key: Screen, e: React.MouseEvent) => {
    e.stopPropagation();
    setOpenNavKey((cur) => (cur === key ? null : key));
  };
  const runBacktestFor = (name: string) => {
    const item = catalog?.find((candidate) => candidate.name === name || candidate.id === name);
    if (item) {
      const defaults = catalogBacktestDefaults(item);
      if (defaults.symbols.length) setBacktestSymbols(defaults.symbols);
      setBacktestInterval(defaults.interval);
      setBtParamRows(defaults.parameters.length ? defaults.parameters : (item.key_parameters ?? []).slice(0, 8).map((k) => ({ k, v: "" })));
    }
    setSelectedStrategy(name);
    navigateTo("flask");
  };

  const cardBase = `background:${colors.surface}; border:1px solid ${colors.border}; border-radius:14px; padding:18px;`;
  const panelStyle = `background:${colors.surface}; border:1px solid ${colors.border}; border-radius:14px; padding:20px; box-sizing:border-box;`;
  // Responsive: inline styles can't carry media queries, so switch on a live
  // viewport width instead. Two-column rows stack and fixed grids become
  // auto-fit below the tablet breakpoint.
  const rowStyle = isNarrow
    ? "display:flex; flex-direction:column; gap:14px; margin-top:14px; align-items:stretch;"
    : "display:flex; gap:14px; margin-top:14px; align-items:stretch;";
  const statGridStyle = `display:grid; grid-template-columns:repeat(${isNarrow ? "auto-fit, minmax(150px, 1fr)" : "4, 1fr"}); gap:14px;`;
  const strategyGridStyle = `display:grid; grid-template-columns:repeat(${isNarrow ? "auto-fit, minmax(230px, 1fr)" : "4, 1fr"}); gap:14px;`;
  const pillGain = `font-size:11.5px; font-weight:700; padding:3px 8px; border-radius:20px; background:oklch(from ${colors.gain} l c h / 0.14); color:${colors.gain};`;
  const pillLoss = `font-size:11.5px; font-weight:700; padding:3px 8px; border-radius:20px; background:oklch(from ${colors.loss} l c h / 0.14); color:${colors.loss};`;
  const pillNeutral = `font-size:11.5px; font-weight:700; padding:3px 8px; border-radius:20px; background:${colors.surfaceRaised}; color:${colors.textFaint};`;
  const ghostLinkBtnStyle = `display:block; width:100%; text-align:center; margin-top:12px; padding:9px; border-radius:9px; background:transparent; border:1px solid ${colors.border}; color:${colors.text}; font-size:12.5px; font-weight:600; font-family:${font}; cursor:pointer;`;
  const newAgentBtnStyle = `display:flex; align-items:center; gap:7px; background:${colors.accent}; color:white; border:none; border-radius:10px; padding:0 16px; height:36px; font-size:13px; font-weight:600; font-family:${font}; cursor:pointer;`;
  const selectStyle = `appearance:none; -webkit-appearance:none; font-family:${font}; font-size:12.5px; font-weight:600; color:${colors.text}; background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:9px; padding:8px 30px 8px 12px; cursor:pointer; background-image:url('data:image/svg+xml;charset=UTF-8,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 24 24%22 fill=%22none%22%3E%3Cpath d=%22M6 9l6 6 6-6%22 stroke=%22%23${chevronColor}%22 stroke-width=%222%22 stroke-linecap=%22round%22 stroke-linejoin=%22round%22/%3E%3C/svg%3E'); background-repeat:no-repeat; background-position:right 10px center; background-size:13px;`;
  const tableHeaderRowStyle = `display:flex; padding:0 10px 10px 10px; border-bottom:1px solid ${colors.border}; font-size:11.5px; font-weight:600; color:${colors.textFaint}; text-transform:uppercase; letter-spacing:.04em;`;
  const tableRowStyle = `display:flex; align-items:center; padding:12px 10px; border-bottom:1px solid ${colors.border};`;
  const verdictCardStyle = `background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:12px; padding:14px;`;
  const verdictPillStyle = "font-size:10.5px; font-weight:700; text-transform:uppercase; letter-spacing:.04em; padding:3px 9px; border-radius:20px; background:oklch(70% 0.15 65 / 0.16); color:oklch(55% 0.15 65);";
  const newsRowStyle = "display:flex; align-items:flex-start; gap:9px;";

  const pboSymbolFor = (n: number) => (n <= 20 ? "●" : n <= 40 ? "▲" : "⚠");
  const pboColorFor = (n: number) => (n <= 20 ? colors.gain : n <= 40 ? "oklch(70% 0.15 80)" : colors.loss);

  // -- Nav --------------------------------------------------------------------
  const navDefs: Array<{ key: Screen; label: string; badge?: string }> = [
    { key: "home", label: "Dashboard" },
    { key: "layers", label: "Strategy Library" },
    { key: "flask", label: "Backtesting" },
    { key: "activity", label: "Paper Trading", badge: paper?.strategies?.length ? String(paper.strategies.length) : undefined },
    { key: "brain", label: "AI Research" },
    { key: "news", label: "News & Sentiment" },
  ];
  const navSummaries: Record<Screen, string> = {
    home: "Portfolio equity, today’s P&L, active agents, and recent backtests at a glance.",
    layers: `${strategyCount} pre-built strategies across trend, momentum, stat-arb, event & volatility families.`,
    flask: btJobs.length ? `${btJobs.length} recent run${btJobs.length === 1 ? "" : "s"} with return, max drawdown, and overfitting (PBO) scores.` : "Run a backtest to see return, max drawdown, and overfitting (PBO) scores.",
    activity: paper?.strategies?.length
      ? `${paper.strategies.length} live agent${paper.strategies.length === 1 ? "" : "s"} running simulated ${formatCurrency(paper?.totals?.equity ?? 0)} in paper equity.`
      : "Deploy a paper agent to start tracking simulated equity here.",
    brain: "Multi-analyst synthesis weighing bull, bear, technical, and risk evidence per ticker.",
    news: "Latest headlines scored bullish or bearish across your watchlist.",
    account: "Your profile, workspace, plan, and data scope.",
  };

  const nowDate = new Date();
  const greetWord = nowDate.getHours() < 12 ? "Good morning" : nowDate.getHours() < 18 ? "Good afternoon" : "Good evening";
  const firstName = userName.split(/\s+/)[0] || userName;
  const dateLabel = nowDate.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  const titleMap: Record<Screen, [string, string]> = {
    home: [`${greetWord}, ${firstName}`, dateLabel],
    layers: ["Strategy Library", `${strategyCount} pre-built rules, ready to backtest`],
    flask: [
      "Backtest · " + selectedStrategy,
      `${backtestSymbols.join(", ") || "—"} · ${backtestStart} → ${backtestEnd} · ${intervalLabel(backtestInterval)}`,
    ],
    activity: ["Paper Trading", "Running simulations · paper capital only"],
    brain: [`AI Research · ${researchTicker.toUpperCase()}`, "Simulated analyst debate · informational only, not financial advice"],
    news: ["News & Sentiment", "Latest headlines scored across your watchlist"],
    account: ["Account", `${userEmail ?? userName} · ${workspaceLabel}`],
  };
  const [screenTitle, screenSubtitle] = titleMap[screen];

  // -- Live-derived view models -----------------------------------------------
  const paperStrategies = paper?.strategies ?? [];
  const leaderboard = paper?.leaderboard ?? [];
  const hasPaperData = paperStrategies.length > 0;
  const equityVal = paper?.totals?.equity ?? 0;
  const dailyPnl = paper?.totals?.daily_pnl ?? 0;
  const portfolioReturn = (() => {
    const denom = leaderboard.reduce((s, r) => s + (r.equity || 0), 0);
    if (!denom) return 0;
    return leaderboard.reduce((s, r) => s + (r.return_since_inception || 0) * (r.equity || 0), 0) / denom;
  })();
  const dailyPct = equityVal - dailyPnl !== 0 ? dailyPnl / (equityVal - dailyPnl) : 0;
  const pipelinesSub = (() => {
    const counts: Record<string, number> = {};
    leaderboard.forEach((r) => { const k = (r.pipeline || "mixed").split("_")[0]; counts[k] = (counts[k] || 0) + 1; });
    return Object.entries(counts).slice(0, 3).map(([k, v]) => `${v} ${k}`).join(" · ") || "No agents yet";
  })();
  const completedBt = btJobs.filter((j) => j.status === "completed").length;
  const statDefs = [
    { label: "Paper Portfolio Equity", value: formatCurrency(equityVal), delta: `${portfolioReturn >= 0 ? "+" : ""}${formatPercent(portfolioReturn)}`, sub: `Across ${paperStrategies.length} paper ${paperStrategies.length === 1 ? "agent" : "agents"}`, kind: portfolioReturn >= 0 ? "gain" : "loss" },
    { label: "Today's Simulated P&L", value: `${dailyPnl >= 0 ? "+" : ""}${formatCurrency(dailyPnl)}`, delta: `${dailyPct >= 0 ? "+" : ""}${formatPercent(dailyPct)}`, sub: paper?.asof_date ? `As of ${paper.asof_date}` : "Latest run", kind: dailyPnl >= 0 ? "gain" : "loss" },
    { label: "Active Agents", value: String(paperStrategies.length), delta: "live", sub: pipelinesSub, kind: "neutral" },
    { label: "Backtests Saved", value: String(btJobs.length), delta: btJobs.length ? `${completedBt} done` : "none", sub: "This workspace", kind: "neutral" },
  ];
  const pillForKind = (k: string) => (k === "gain" ? pillGain : k === "loss" ? pillLoss : pillNeutral);

  const agentDefs = leaderboard.map((r) => ({
    name: r.strategy,
    strategy: strategyDisplayName(r.pipeline),
    pnl: (r.return_since_inception || 0) * 100,
    points: (paperStrategies.find((s) => s.name === r.strategy || s.pipeline === r.pipeline)?.history ?? [])
      .map((h) => Number(h.equity_after))
      .filter(Number.isFinite),
    equityLabel: formatCurrency(r.equity),
  }));

  const riskTone = (r: string) => {
    const s = r.toLowerCase();
    if (s.includes("high")) return colors.loss;
    if (s.includes("low")) return colors.gain;
    return "oklch(70% 0.15 80)"; // moderate / medium
  };
  const realCards: StratCard[] = (catalog ?? []).map((item) => {
    const hasRisk = Boolean(item.risk_level);
    const paramCount = item.key_parameters?.length ?? 0;
    // Distinguish provenance: benchmarks are reference baselines, user strategies
    // were authored here, and published user strategies are community listings.
    const isCommunity = Boolean(item.community_strategy) || item.pipeline?.startsWith("marketplace_strategy:");
    const isUser = !isCommunity && (Boolean(item.user_strategy) || item.pipeline?.startsWith("user_strategy:"));
    const isBenchmark = /buy_and_hold|benchmark/i.test(item.pipeline || "") || /benchmark/i.test(item.name);
    return {
      id: item.id,
      tag: item.family,
      name: item.name,
      desc: item.summary,
      badgeText: hasRisk ? `${item.risk_level} risk` : item.difficulty || "Strategy",
      badgeColor: hasRisk ? riskTone(item.risk_level as string) : colors.textFaint,
      badgeTitle: hasRisk ? "Relative risk level" : "Difficulty",
      // Avoid repeating difficulty in both slots: show it in the footer only when
      // the badge is showing risk instead.
      footLabel: hasRisk ? "Difficulty" : "Parameters",
      footValue: hasRisk ? item.difficulty || "—" : String(paramCount),
      origin: isCommunity ? "community" : isUser ? "user" : isBenchmark ? "benchmark" : "builtin",
    } as StratCard;
  });
  const allCards = realCards;
  const categoryOptions = ["All Categories", ...Array.from(new Set(allCards.map((s) => s.tag)))];
  const filteredCards = category === "All Categories" ? allCards : allCards.filter((s) => s.tag === category);
  // Group by provenance so reference baselines and community work aren't mixed
  // in with the vetted built-in library.
  const ORIGIN_META: Array<{ key: NonNullable<StratCard["origin"]>; label: string; note: string }> = [
    { key: "builtin", label: "Built-in strategies", note: "Vetted, pre-configured rules shipped with Apollo" },
    { key: "user", label: "Your strategies", note: "Authored in this workspace" },
    { key: "community", label: "Community", note: "Published by workspace members · Beta" },
    { key: "benchmark", label: "Benchmarks", note: "Reference baselines for comparison, not strategies to deploy" },
  ];
  const groupedCards = ORIGIN_META.map((g) => ({ ...g, cards: filteredCards.filter((s) => (s.origin ?? "builtin") === g.key) })).filter((g) => g.cards.length);

  // -- Sprint 4: validation evidence per user strategy -------------------------
  // A listing's credibility comes from its validation record, not its headline
  // return. Robustness scores checks-passed and out-of-sample breadth; PBO
  // penalises overfitting. Publishing requires a completed, validated run.
  interface Robustness { runs: number; checksPassed: number; checksTotal: number; pbo: number | null; folds: number; score: number; label: string; tone: string; publishable: boolean }
  const robustnessFor = useCallback(
    (strategyId: string): Robustness => {
      const pipeline = `user_strategy:${strategyId}`;
      const runs = btJobs.filter((j) => j.status === "completed" && (j.request as { pipeline?: string })?.pipeline === pipeline);
      const best = runs
        .map((j) => {
          const s = (j.result?.summary ?? {}) as Record<string, unknown>;
          const d = j.result?.decision;
          const pboRaw = typeof s.pbo === "number" ? s.pbo * 100 : null;
          return {
            checksPassed: d?.passed_checks ?? 0,
            checksTotal: d?.total_checks ?? 0,
            pbo: pboRaw,
            folds: typeof s.folds === "number" ? s.folds : 0,
          };
        })
        .sort((a, b) => b.checksPassed - a.checksPassed)[0];
      if (!best) {
        return { runs: 0, checksPassed: 0, checksTotal: 0, pbo: null, folds: 0, score: 0, label: "Unvalidated", tone: colors.textFaint, publishable: false };
      }
      const checkRatio = best.checksTotal ? best.checksPassed / best.checksTotal : 0;
      const foldCredit = Math.min(1, best.folds / 12);
      const pboPenalty = best.pbo == null ? 0.25 : Math.min(1, best.pbo / 100);
      const score = Math.round(Math.max(0, checkRatio * 60 + foldCredit * 25 + (1 - pboPenalty) * 15));
      const label = score >= 70 ? "Robust" : score >= 45 ? "Moderate" : "Weak evidence";
      const tone = score >= 70 ? colors.gain : score >= 45 ? "oklch(70% 0.15 80)" : colors.loss;
      // Gate: needs a completed run, a majority of checks passed, and real OOS breadth.
      const publishable = best.checksTotal > 0 && checkRatio >= 0.5 && best.folds >= 3;
      return { runs: runs.length, checksPassed: best.checksPassed, checksTotal: best.checksTotal, pbo: best.pbo, folds: best.folds, score, label, tone, publishable };
    },
    [btJobs, colors.textFaint, colors.gain, colors.loss],
  );
  // Rank listings by validation robustness, not by simulated returns.
  const rankedUserStrategies = [...userStrategies]
    .map((s) => ({ s, r: robustnessFor(s.id) }))
    .sort((a, b) => b.r.score - a.r.score || a.s.name.localeCompare(b.s.name));

  const verdictToStep = (verdict: string, pbo: number | null) => {
    const v = (verdict || "").toLowerCase();
    if (v.includes("deploy") || v.includes("pass") || v.includes("promote")) return "Deploy to paper";
    if (v.includes("reject") || v.includes("fail")) return "Reject / archive";
    if (pbo != null && pbo > 40) return "Inspect overfit risk";
    return "Review results";
  };
  interface BtRow { strategy: string; universe: string; ret: number; dd: string; pbo: number | null; nextStep: string; jobId: string; actionable: boolean; }
  const btDefs: BtRow[] = btJobs.map((job) => {
    const req = (job.request || {}) as { pipeline?: string; symbols?: string[] };
    const s = (job.result?.summary || {}) as Record<string, unknown>;
    const pboNum = s.pbo == null ? null : Math.round(Number(s.pbo) * 100);
    return {
      strategy: strategyDisplayName(req.pipeline || (s.strategy as string) || "strategy"),
      universe: Array.isArray(req.symbols) && req.symbols.length ? req.symbols.join(" ") : "—",
      ret: Number(s.total_return ?? 0) * 100,
      dd: s.max_drawdown != null ? formatPercent(s.max_drawdown) : "—",
      pbo: pboNum,
      nextStep:
        job.status === "completed"
          ? verdictToStep((job.result?.decision?.verdict as string) || "", pboNum)
          : job.status === "queued" || job.status === "running"
            ? "Running…"
            : "Failed",
      jobId: job.id,
      // Completed runs can be opened for review; in-flight/failed ones can't.
      actionable: job.status === "completed",
    };
  });
  const universeOptions = ["All Universes", ...Array.from(new Set(btDefs.map((b) => b.universe)))];
  const filteredBtDefs = universeFilter === "All Universes" ? btDefs : btDefs.filter((b) => b.universe === universeFilter);

  const relTime = (ts?: string) => {
    if (!ts) return "recent";
    const d = new Date(ts).getTime();
    if (Number.isNaN(d)) return "recent";
    const mins = Math.max(0, Math.round((Date.now() - d) / 60000));
    if (mins < 60) return `${mins}m`;
    const hrs = Math.round(mins / 60);
    return hrs < 24 ? `${hrs}h` : `${Math.round(hrs / 24)}d`;
  };
  const sentimentWord = (score?: number) => (score == null ? "neutral" : score > 0.1 ? "bull" : score < -0.1 ? "bear" : "neutral");
  const headlineRows = (sentiment?.scored_headlines?.length ? sentiment.scored_headlines : sentiment?.headlines) ?? [];
  const realNews = headlineRows.map((h, index) => ({
    key: sentimentHeadlineKey(h, index),
    headline: String(h.headline || h.title || h.summary || h.label || "Untitled"),
    source: `${h.ticker ? `${h.ticker} · ` : ""}${String(h.source || h.source_group_label || "news")}`,
    time: relTime(h.timestamp),
    sentiment: sentimentWord(typeof h.score === "number" ? h.score : undefined),
  }));
  const newsDefs = realNews;
  const newsPageCount = Math.max(1, Math.ceil(newsDefs.length / 8));
  const safeNewsPage = Math.min(newsPage, newsPageCount);
  const pagedNews = newsDefs.slice((safeNewsPage - 1) * 8, safeNewsPage * 8);
  const tickerMatrix = sentiment?.ticker_summary ?? [];

  // Timeframe-aware sentiment matrix: recompute per-ticker aggregates from the
  // scored daily points when a window is selected; fall back to the server's
  // all-time ticker_summary when daily points are unavailable.
  const newsCutoff = sentimentWindowCutoff(sentiment, newsWindow);
  const newsMatrix = buildSentimentNewsMatrix(sentiment, newsCutoff);

  const newsSources = sentiment?.source_group_summary?.length ? sentiment.source_group_summary : sentiment?.source_summary ?? [];

  // News heatmap matrix (ticker × date), ported from the classic sentiment lab.
  // Color encodes the daily sentiment score; the inner dot encodes article volume.
  const heatmap = (() => {
    const pts = (sentiment?.daily_points ?? []).filter((p) => !newsCutoff || String(p.date).slice(0, 10) >= newsCutoff);
    if (!pts.length) return null;
    const dates = Array.from(new Set(pts.map((p) => String(p.date).slice(0, 10)))).sort();
    const cell = new Map<string, { s: number; conf: number; arts: number }>();
    const latest = new Map<string, { t: number; s: number; arts: number }>();
    let maxArts = 1;
    for (const p of pts) {
      const tk = String(p.ticker).toUpperCase();
      const d = String(p.date).slice(0, 10);
      const s = Number(p.sentiment_score) || 0;
      const arts = Number(p.article_count) || 0;
      maxArts = Math.max(maxArts, arts);
      cell.set(`${tk}|${d}`, { s, conf: Number(p.confidence) || 0, arts });
      const cur = latest.get(tk) ?? { t: -Infinity, s: 0, arts: 0 };
      const t = new Date(d).getTime();
      if (t >= cur.t) { cur.t = t; cur.s = s; }
      cur.arts += arts;
      latest.set(tk, cur);
    }
    const tickers = Array.from(latest.entries())
      .sort((a, b) => Math.abs(b[1].s) - Math.abs(a[1].s) || a[0].localeCompare(b[0]))
      .map(([tk]) => tk);
    // Show at most the most-recent ~60 dates so the grid stays readable.
    const shownDates = dates.slice(-60);
    return { tickers, dates: shownDates, cell, maxArts };
  })();
  const heatColor = (s: number) => {
    const mag = Math.min(1, Math.abs(s));
    const alpha = (0.12 + mag * 0.8).toFixed(2);
    if (mag < 0.02) return colors.surfaceRaised;
    return `oklch(from ${s >= 0 ? colors.gain : colors.loss} l c h / ${alpha})`;
  };

  // Deterministic overall narrative for the selected window.
  const newsNarrative = (() => {
    if (!newsMatrix.length) return null;
    const windowLabel = newsWindow === "all" ? "across the full scanned range" : `over the last ${newsWindow} days`;
    const totalArticles = newsMatrix.reduce((s, r) => s + (r.article_count || 0), 0);
    const wavg = totalArticles ? newsMatrix.reduce((s, r) => s + r.avg_sentiment * (r.article_count || 0), 0) / totalArticles : 0;
    const tone = wavg > 0.1 ? "bullish" : wavg > 0.03 ? "mildly bullish" : wavg < -0.1 ? "bearish" : wavg < -0.03 ? "mildly bearish" : "neutral";
    const sorted = [...newsMatrix].sort((a, b) => b.avg_sentiment - a.avg_sentiment);
    const best = sorted[0];
    const worst = sorted[sorted.length - 1];
    const parts = [
      `Coverage is ${tone} ${windowLabel}: ${Math.round(totalArticles)} scored articles across ${newsMatrix.length} ${newsMatrix.length === 1 ? "ticker" : "tickers"} average ${wavg >= 0 ? "+" : ""}${wavg.toFixed(2)}.`,
    ];
    if (newsMatrix.length > 1 && best.ticker !== worst.ticker) {
      parts.push(
        `${best.ticker} screens most positive (${best.avg_sentiment >= 0 ? "+" : ""}${best.avg_sentiment.toFixed(2)} over ${Math.round(best.article_count)} articles) while ${worst.ticker} is the laggard (${worst.avg_sentiment >= 0 ? "+" : ""}${worst.avg_sentiment.toFixed(2)}).`,
      );
    }
    const cooling = newsMatrix.filter((r) => r.latest_sentiment < r.avg_sentiment - 0.1).map((r) => r.ticker);
    const heating = newsMatrix.filter((r) => r.latest_sentiment > r.avg_sentiment + 0.1).map((r) => r.ticker);
    if (heating.length) parts.push(`Latest readings are improving for ${heating.join(", ")}.`);
    if (cooling.length) parts.push(`Latest readings are cooling for ${cooling.join(", ")}.`);
    return parts.join(" ");
  })();
  const sentimentDot = (s: string) =>
    `width:7px; height:7px; border-radius:50%; margin-top:5px; flex-shrink:0; background:${
      s === "bull" ? colors.gain : s === "bear" ? colors.loss : colors.textFaint
    };`;

  // Backtest detail — prefer the just-run job, else a completed job for this pipeline.
  const activeBtJob: BacktestJob | null =
    btResult ??
    btJobs.find((j) => j.status === "completed" && (j.request as { pipeline?: string })?.pipeline === pipelineFor(selectedStrategy)) ??
    null;
  const detailSummary = (activeBtJob?.result?.summary ?? null) as Record<string, unknown> | null;
  const hasBtDetail = Boolean(detailSummary);

  // Financial events (earnings reports, SEC filings) inside the backtest window,
  // fetched once per job and overlaid on the chart at their timestamps.
  const [btEvents, setBtEvents] = useState<FinancialEventRecord[]>([]);
  const btJobId = activeBtJob?.id ?? "";
  const btJobReq = (activeBtJob?.request ?? null) as { symbols?: string[]; start?: string; end?: string } | null;
  useEffect(() => {
    let alive = true;
    setBtEvents([]);
    setBtOhlc([]);
    setBtOhlcError(null);
    // New job → new timeline; clear any zoom/drag from the previous chart.
    setBtZoom(null);
    setBtDrag(null);
    if (!btJobId || !btJobReq?.symbols?.length || !btJobReq.start || !btJobReq.end) return;
    getFinancialEvents({ symbols: btJobReq.symbols, start: btJobReq.start, end: btJobReq.end, limit: 60 })
      .then((p) => { if (alive) setBtEvents(p.events ?? []); })
      .catch(() => undefined);
    const jobInterval = String((activeBtJob?.request as { interval?: string } | null)?.interval || "1d");
    getOhlc({ symbol: btJobReq.symbols[0], start: btJobReq.start, end: btJobReq.end, interval: jobInterval })
      .then((p) => { if (alive) setBtOhlc(p.rows ?? []); })
      .catch((e) => { if (alive) setBtOhlcError(e instanceof Error ? e.message : "OHLC unavailable"); });
    return () => { alive = false; };
    // Key off the job id: the request object is a fresh identity each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [btJobId]);
  const selectedDetail = {
    cagr: detailSummary ? formatPercent(detailSummary.cagr ?? 0) : "—",
    sharpe: detailSummary ? formatNumber(detailSummary.sharpe ?? 0, 2) : "—",
    maxDd: detailSummary?.max_drawdown != null ? formatPercent(detailSummary.max_drawdown) : "—",
    // hit_rate is the trade-level win rate (% of closed trades that were profitable).
    // The backend's win_rate field is a bar/time-in-position stat and reads as an
    // implausibly high number (90%+) even for mediocre strategies — not what a
    // user means by "win rate".
    winRate: detailSummary?.hit_rate != null ? formatPercent(detailSummary.hit_rate) : "—",
  };
  const selectedPbo = detailSummary?.pbo == null ? null : Math.round(Number(detailSummary.pbo) * 100);
  const pboRisk = selectedPbo == null ? "unknown" : selectedPbo < 20 ? "low" : selectedPbo < 40 ? "medium" : "high";
  const pboRiskLabel = selectedPbo == null ? "Validation incomplete" : pboRisk === "low" ? "Low risk" : pboRisk === "medium" ? "Moderate risk" : "High risk";
  const pboRiskColor = pboRisk === "low" ? colors.gain : pboRisk === "medium" ? "oklch(70% 0.15 80)" : pboRisk === "high" ? colors.loss : colors.textFaint;
  const pboNote =
    selectedPbo == null
      ? "PBO not computed for this run, so overfitting risk can't be assessed yet — treat the result as unvalidated. As a rule of thumb, below 20% is generally trustworthy and above 50% suggests the result is overfit."
      : `${selectedPbo}% of walk-forward folds ranked this configuration best out-of-sample` +
        (pboRisk === "low"
          ? " — unlikely to be curve-fit luck."
          : pboRisk === "medium"
            ? " — within a generally acceptable range, but worth monitoring."
            : " — this backtest is likely overfit to historical noise.") +
        " Below 20% is generally considered trustworthy; above 50% means the result is probably overfit.";

  // -- Auto-generated backtest narrative --------------------------------------
  // Deterministic plain-English analysis composed from the real result metrics
  // and the backend's decision engine (verdict + per-check messages). No LLM
  // call, so it is always available and never hallucinates a number.
  const btDecision = activeBtJob?.result?.decision ?? null;
  const btNarrative: string[] = (() => {
    const s = detailSummary;
    if (!s) return [];
    const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? (v as number) : null);
    const pct = (v: unknown) => { const n = num(v); return n == null ? "—" : `${(n * 100).toFixed(1)}%`; };
    const cagr = num(s.cagr), baseCagr = num(s.baseline_cagr);
    const sharpe = num(s.sharpe), baseSharpe = num(s.baseline_sharpe);
    const dd = num(s.max_drawdown), hit = num(s.hit_rate);
    const totRet = num(s.total_return), baseRet = num(s.baseline_total_return);
    const pbo = num(s.pbo);
    const paras: string[] = [];
    if (btDecision?.headline) paras.push(`Verdict: ${btDecision.headline}.`);
    const beat = totRet != null && baseRet != null ? totRet > baseRet : null;
    paras.push(
      `Over ${backtestStart} to ${backtestEnd}, ${selectedStrategy} on ${backtestSymbols.join(", ") || "the selected universe"} ` +
      `${totRet != null ? `returned ${pct(totRet)}` : "ran"}${baseRet != null ? ` versus ${pct(baseRet)} for buy-and-hold` : ""}` +
      `${beat === true ? " — beating the benchmark" : beat === false ? " — trailing the benchmark" : ""}. ` +
      `It compounded at ${pct(cagr)} a year${baseCagr != null ? ` (benchmark ${pct(baseCagr)})` : ""} with a Sharpe ratio of ${sharpe != null ? sharpe.toFixed(2) : "—"}${baseSharpe != null ? ` against the benchmark's ${baseSharpe.toFixed(2)}` : ""}.`,
    );
    paras.push(
      `The deepest peak-to-trough drawdown was ${pct(dd)}${hit != null ? `, and ${pct(hit)} of closed trades were profitable` : ""}. ` +
      (pbo != null
        ? `Overfitting risk (PBO) is ${(pbo * 100).toFixed(0)}% — ${pbo < 0.2 ? "low, so the edge is unlikely to be curve-fit to history" : pbo < 0.4 ? "moderate, so treat the edge with some caution" : "high, meaning this result is probably overfit to historical noise"}.`
        : "An overfitting (PBO) score was not reported for this run."),
    );
    return paras;
  })();

  // -- Walk-forward fold metrics (out-of-sample per-window results) ------------
  interface FoldRow { fold: number; testStart: string; testEnd: string; trainStart: string; trainEnd: string; ret: number; sharpe: number; dd: number; hit: number; turnover: number }
  const foldRows: FoldRow[] = (() => {
    const raw = activeBtJob?.result?.fold_metrics_tail;
    if (!Array.isArray(raw)) return [];
    return raw
      .map((r) => {
        const o = r as Record<string, unknown>;
        const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : 0);
        return {
          fold: num(o.fold),
          testStart: String(o.test_start ?? "").slice(0, 10),
          testEnd: String(o.test_end ?? "").slice(0, 10),
          trainStart: String(o.train_start ?? "").slice(0, 10),
          trainEnd: String(o.train_end ?? "").slice(0, 10),
          ret: num(o.total_return) * 100,
          sharpe: num(o.sharpe),
          dd: num(o.max_drawdown) * 100,
          hit: num(o.hit_rate) * 100,
          turnover: num(o.avg_turnover),
        };
      })
      .sort((a, b) => a.fold - b.fold);
  })();
  const foldStats = foldRows.length
    ? {
        positive: foldRows.filter((f) => f.ret > 0).length,
        total: foldRows.length,
        best: Math.max(...foldRows.map((f) => f.ret)),
        worst: Math.min(...foldRows.map((f) => f.ret)),
        median: (() => {
          const s = [...foldRows.map((f) => f.ret)].sort((a, b) => a - b);
          const m = Math.floor(s.length / 2);
          return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
        })(),
      }
    : null;
  const wfConfig = (detailSummary?.walk_forward_config ?? null) as Record<string, unknown> | null;
  const totalFolds = detailSummary?.folds != null ? Number(detailSummary.folds) : foldRows.length;

  // -- Execution & cost reality (Sprint 2 tiles) ------------------------------
  const costModel = (detailSummary?.cost_model ?? null) as Record<string, unknown> | null;
  const execStats = detailSummary
    ? (() => {
        const n = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
        return {
          trades: n(detailSummary.closed_trade_count),
          fills: n(detailSummary.fill_count),
          turnover: n(detailSummary.turnover),
          avgTurnover: n(detailSummary.avg_turnover),
          exposure: n(detailSummary.exposure_time),
          benchDelta: n(detailSummary.benchmark_relative_return),
          benchTotal: n(detailSummary.benchmark_total_return),
          calmar: n(detailSummary.calmar),
          sortino: n(detailSummary.sortino),
          alpha: n(detailSummary.alpha),
          beta: n(detailSummary.beta),
        };
      })()
    : null;

  // Charts — aggregate paper equity across agents when live data exists.
  const dashEquityPtsFull = (() => {
    if (!hasPaperData) return [];
    const histories = paperStrategies.map((s) => s.history || []);
    const maxLen = Math.max(0, ...histories.map((h) => h.length));
    if (maxLen < 2) return [];
    const pts: number[] = [];
    for (let i = 0; i < maxLen; i++) {
      let sum = 0;
      for (const h of histories) sum += Number((h[i] ?? h[h.length - 1])?.equity_after ?? 0);
      pts.push(sum);
    }
    return pts;
  })();
  // Timeline labels for the dashboard chart come from the longest agent history.
  const dashTsFull: string[] = (() => {
    if (!hasPaperData) return [];
    const histories = paperStrategies.map((s) => s.history || []);
    const longest = histories.reduce((a, b) => (b.length > a.length ? b : a), histories[0] ?? []);
    return longest.map((h) => String(h.timestamp ?? "").slice(0, 10));
  })();
  // Range tabs slice the tail of the series by trading-day counts (21/63/252).
  const rangeCount = range === "1M" ? 21 : range === "3M" ? 63 : range === "1Y" ? 252 : Infinity;
  const rangeStart = Number.isFinite(rangeCount) ? Math.max(0, dashEquityPtsFull.length - rangeCount) : 0;
  const dashEquityPts = dashEquityPtsFull.slice(rangeStart);
  const dashTs = dashTsFull.slice(rangeStart);
  const equityMin = dashEquityPts.length ? Math.min(...dashEquityPts) : 0;
  const equityMax = dashEquityPts.length ? Math.max(...dashEquityPts) : 1;
  const equityLine = buildPath(dashEquityPts, 760, 190, equityMin, equityMax);
  const equityArea = equityLine + " L760,190 L0,190 Z";

  // Rich chart data from the job's full visualization payload: real strategy
  // AND real baseline equity (no more synthesized benchmark), plus a price
  // series with moving averages and entry/exit trade markers.
  const viz = activeBtJob?.result?.visualization ?? null;
  const vizEqFull = viz?.equity ?? [];
  const hasViz = vizEqFull.length > 1;
  const nBtFull = vizEqFull.length;

  // Zoom window: btZoom holds full-series indices; everything below renders the
  // sliced view so hover, markers, and events all stay index-aligned.
  const [vi0, vi1] = btZoom && hasViz
    ? [Math.max(0, Math.min(btZoom[0], nBtFull - 2)), Math.min(nBtFull - 1, Math.max(btZoom[1], btZoom[0] + 1))]
    : [0, Math.max(0, nBtFull - 1)];
  const vizEq = vizEqFull.slice(vi0, vi1 + 1);
  const nBt = vizEq.length;
  const tsIndex = new Map<string, number>();
  vizEqFull.forEach((p, i) => tsIndex.set(p.timestamp, i));

  // Equity mode — rebased to cumulative % return from the FULL series start so
  // values stay consistent while zooming (like a fixed y-axis, windowed x-axis).
  const eqStart = vizEqFull[0]?.equity || 1;
  const baseStart = vizEqFull[0]?.baseline_equity || eqStart;
  const stratPts = hasViz ? vizEq.map((p) => (p.equity / eqStart - 1) * 100) : [];
  const bhPts = hasViz ? vizEq.map((p) => ((p.baseline_equity ?? p.equity) / baseStart - 1) * 100) : [];

  // Price mode — close plus moving averages (nulls tolerated for early bars).
  const vizPriceFull = viz?.price ?? [];
  const vizPrice = vizPriceFull.slice(vi0, vi1 + 1);
  const closePts = vizPrice.map((p) => (typeof p.close === "number" ? p.close : null));
  const sma20Pts = vizPrice.map((p) => (typeof p.sma_20 === "number" ? p.sma_20 : null));
  const sma50Pts = vizPrice.map((p) => (typeof p.sma_50 === "number" ? p.sma_50 : null));
  const sma200Pts = vizPrice.map((p) => (typeof p.sma_200 === "number" ? p.sma_200 : null));
  const priceMode = chartMode === "price" && hasViz && vizPriceFull.length > 1;

  const activeVals: number[] = priceMode
    ? [
        ...closePts,
        ...(smaVis.s20 ? sma20Pts : []),
        ...(smaVis.s50 ? sma50Pts : []),
        ...(smaVis.s200 ? sma200Pts : []),
      ].filter((v): v is number => typeof v === "number")
    : stratPts.concat(bhPts);
  const btMin = activeVals.length ? Math.min(...activeVals) : 0;
  const btMax = activeVals.length ? Math.max(...activeVals) : 1;

  const stratPath = buildPath(stratPts, 700, 200, btMin, btMax);
  const bhPath = buildPath(bhPts, 700, 200, btMin, btMax);
  const closePath = buildPathSparse(closePts, 700, 200, btMin, btMax);
  const sma20Path = buildPathSparse(sma20Pts, 700, 200, btMin, btMax);
  const sma50Path = buildPathSparse(sma50Pts, 700, 200, btMin, btMax);
  const sma200Path = buildPathSparse(sma200Pts, 700, 200, btMin, btMax);
  const yBt = (v: number) => 200 - ((v - btMin) / ((btMax - btMin) || 1)) * 200;

  // -- Candlestick mode: real OHLC bars bucketed to a readable candle count ----
  const candleMode = chartMode === "candles" && btOhlc.length > 1;
  // Window the OHLC series to the same date range as the current zoom view.
  const viewStartTs = hasViz ? vizEq[0].timestamp : null;
  const viewEndTs = hasViz ? vizEq[nBt - 1].timestamp.slice(0, 10) + "T23:59:59" : null;
  const ohlcView = candleMode
    ? btOhlc.filter((r) => (!viewStartTs || r.timestamp >= viewStartTs) && (!viewEndTs || r.timestamp <= viewEndTs))
    : [];
  interface Candle { t0: string; t1: string; o: number; h: number; l: number; c: number; v: number; lastIdx: number }
  const candles: Candle[] = (() => {
    if (ohlcView.length < 2) return [];
    const bSize = Math.max(1, Math.ceil(ohlcView.length / 160));
    const out: Candle[] = [];
    for (let i = 0; i < ohlcView.length; i += bSize) {
      const chunk = ohlcView.slice(i, i + bSize);
      const opens = chunk.map((r) => r.open ?? r.close);
      const highs = chunk.map((r) => r.high ?? r.close);
      const lows = chunk.map((r) => r.low ?? r.close);
      out.push({
        t0: chunk[0].timestamp,
        t1: chunk[chunk.length - 1].timestamp,
        o: opens[0],
        h: Math.max(...highs),
        l: Math.min(...lows),
        c: chunk[chunk.length - 1].close,
        v: chunk.reduce((s, r) => s + (r.volume ?? 0), 0),
        lastIdx: btOhlc.indexOf(chunk[chunk.length - 1]),
      });
    }
    return out;
  })();
  // Client-side rolling means over the full real close series (standard indicator
  // math over real data), sampled at each candle's last underlying bar.
  const rollingMean = (values: number[], window: number): Array<number | null> => {
    const out: Array<number | null> = new Array(values.length).fill(null);
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
      sum += values[i];
      if (i >= window) sum -= values[i - window];
      if (i >= window - 1) out[i] = sum / window;
    }
    return out;
  };
  const ohlcCloses = btOhlc.map((r) => r.close);
  const cSma20Full = candleMode ? rollingMean(ohlcCloses, 20) : [];
  const cSma50Full = candleMode ? rollingMean(ohlcCloses, 50) : [];
  const cSma200Full = candleMode ? rollingMean(ohlcCloses, 200) : [];
  const cSma20 = candles.map((cd) => cSma20Full[cd.lastIdx] ?? null);
  const cSma50 = candles.map((cd) => cSma50Full[cd.lastIdx] ?? null);
  const cSma200 = candles.map((cd) => cSma200Full[cd.lastIdx] ?? null);
  const candleVals: number[] = candles.length
    ? [
        ...candles.flatMap((cd) => [cd.h, cd.l]),
        ...(smaVis.s20 ? cSma20.filter((v): v is number => v != null) : []),
        ...(smaVis.s50 ? cSma50.filter((v): v is number => v != null) : []),
        ...(smaVis.s200 ? cSma200.filter((v): v is number => v != null) : []),
      ]
    : [];
  const cMin = candleVals.length ? Math.min(...candleVals) : 0;
  const cMax = candleVals.length ? Math.max(...candleVals) : 1;
  const yC = (v: number) => 200 - ((v - cMin) / ((cMax - cMin) || 1)) * 200;
  const cSma20Path = candles.length ? buildPathSparse(cSma20, 700, 200, cMin, cMax) : "";
  const cSma50Path = candles.length ? buildPathSparse(cSma50, 700, 200, cMin, cMax) : "";
  const cSma200Path = candles.length ? buildPathSparse(cSma200, 700, 200, cMin, cMax) : "";
  // Interactions (hover/drag) run in candle space when candles are showing.
  const chartCount = candleMode && candles.length ? candles.length : nBt;
  // Financial events mapped into candle-slot space.
  const candleEventMarkers = candleMode && candles.length
    ? (btEvents
        .slice(0, 40)
        .map((ev) => {
          if (ev.date < candles[0].t0.slice(0, 10) || ev.date > candles[candles.length - 1].t1.slice(0, 10)) return null;
          const ci = candles.findIndex((cd) => cd.t1.slice(0, 10) >= ev.date);
          if (ci < 0) return null;
          return { x: ((ci + 0.5) / candles.length) * 700, ev };
        })
        .filter(Boolean) as Array<{ x: number; ev: FinancialEventRecord }>)
    : [];

  // Indicator sub-chart series (RSI / drawdown), sliced to the same view.
  const vizInd = (viz?.indicators ?? []).slice(vi0, vi1 + 1);
  const rsiPts = vizInd.map((p) => (typeof p.rsi === "number" ? p.rsi : null));
  const hasRsi = rsiPts.some((v) => v != null);
  const ddPts = vizEq.map((p) => (p.drawdown ?? 0) * 100);
  const ddMin = ddPts.length ? Math.min(...ddPts, 0) : 0;
  const rsiPath = buildPathSparse(rsiPts, 700, 70, 0, 100);
  const ddPath = buildPath(ddPts, 700, 70, ddMin, 0);
  const ddArea = ddPath ? ddPath + " L700,0 L0,0 Z" : "";

  // Window summary stats for the visible range (Trading-212-style readout).
  const btWindowStats = hasViz && nBt > 1 ? (() => {
    const d0 = vizEq[0].timestamp.slice(0, 10);
    const d1 = vizEq[nBt - 1].timestamp.slice(0, 10);
    if (candleMode && candles.length) {
      const first = candles[0];
      const last = candles[candles.length - 1];
      const chg = first.o ? (last.c / first.o - 1) * 100 : null;
      return { d0: first.t0.slice(0, 10), d1: last.t1.slice(0, 10), main: chg == null ? "—" : `${chg >= 0 ? "+" : ""}${chg.toFixed(1)}%`, mainLabel: "Price", hi: `High ${formatCurrency(Math.max(...candles.map((cd) => cd.h)))}`, lo: `Low ${formatCurrency(Math.min(...candles.map((cd) => cd.l)))}` };
    }
    if (priceMode) {
      const vals = closePts.filter((v): v is number => v != null);
      const first = closePts.find((v) => v != null) ?? null;
      const last = [...closePts].reverse().find((v) => v != null) ?? null;
      const chg = first != null && last != null && first !== 0 ? ((last / first) - 1) * 100 : null;
      return { d0, d1, main: chg == null ? "—" : `${chg >= 0 ? "+" : ""}${chg.toFixed(1)}%`, mainLabel: "Close", hi: vals.length ? formatCurrency(Math.max(...vals)) : "—", lo: vals.length ? formatCurrency(Math.min(...vals)) : "—" };
    }
    const chg = ((1 + stratPts[nBt - 1] / 100) / (1 + stratPts[0] / 100) - 1) * 100;
    const bchg = ((1 + bhPts[nBt - 1] / 100) / (1 + bhPts[0] / 100) - 1) * 100;
    return { d0, d1, main: `${chg >= 0 ? "+" : ""}${chg.toFixed(1)}%`, mainLabel: "Strategy", hi: `B&H ${bchg >= 0 ? "+" : ""}${bchg.toFixed(1)}%`, lo: `Max DD ${Math.min(...ddPts).toFixed(1)}%` };
  })() : null;

  // Entry/exit markers, thinned so a 250-fill backtest doesn't drown the chart.
  const rawTrades = viz?.trade_events ?? [];
  const markerStride = Math.max(1, Math.ceil(rawTrades.length / 60));
  const btMarkers = hasViz
    ? (rawTrades
        .filter((_, i) => i % markerStride === 0)
        .map((t) => {
          const idx = tsIndex.get(t.timestamp);
          if (idx == null || idx < vi0 || idx > vi1) return null;
          const vIdx = idx - vi0;
          const val = priceMode ? (typeof t.price === "number" ? t.price : null) : stratPts[vIdx];
          if (val == null || !Number.isFinite(val)) return null;
          const range = (btMax - btMin) || 1;
          return {
            x: (vIdx / (nBt - 1 || 1)) * 700,
            y: 200 - ((val - btMin) / range) * 200,
            isBuy: String(t.type).toLowerCase().includes("buy") || String(t.side).toLowerCase() === "long",
          };
        })
        .filter(Boolean) as Array<{ x: number; y: number; isBuy: boolean }>)
    : [];

  // Financial-event markers: place each event at the first chart bar on/after
  // its date (events can fall on non-trading days).
  const btEventMarkers = hasViz
    ? (btEvents
        .slice(0, 40)
        .map((ev) => {
          const idx = vizEqFull.findIndex((p) => p.timestamp.slice(0, 10) >= ev.date);
          if (idx < vi0 || idx > vi1) return null;
          return { x: ((idx - vi0) / (nBt - 1 || 1)) * 700, ev };
        })
        .filter(Boolean) as Array<{ x: number; ev: FinancialEventRecord }>)
    : [];
  const eventTone = (dir: string) => (dir === "positive" ? colors.gain : dir === "negative" ? colors.loss : colors.textFaint);

  // Prefer an agent that actually has activity (open positions or a non-zero
  // return) so the detail panel isn't arbitrarily stuck on a freshly-deployed,
  // all-zero sleeve when more interesting agents exist.
  const focusAgent =
    paperStrategies.find((s) => Object.keys(s.positions || {}).length > 0) ??
    [...paperStrategies].sort((a, b) => Math.abs(b.return_since_inception || 0) - Math.abs(a.return_since_inception || 0))[0] ??
    paperStrategies[0];
  const paHist = focusAgent?.history ?? [];
  const hasPaHistory = paHist.length > 1;
  const paPts = hasPaHistory ? paHist.map((h) => Number(h.equity_after ?? 0)) : [];
  const paMin = paPts.length ? Math.min(...paPts) : 0;
  const paMax = paPts.length ? Math.max(...paPts) : 0;
  const paPath = hasPaHistory ? buildPath(paPts, 900, 150, paMin, paMax) : "";
  const paArea = hasPaHistory ? paPath + " L900,150 L0,150 Z" : "";

  const rpt = researchReport;
  // The backend's bear_thesis already folds in the trend + momentum technical
  // signals verbatim, so pulling technical_signals[0] here would just repeat
  // the Bear Analyst card. Use the consolidated composite signal instead,
  // which reports a distinct weighted-consensus view.
  const technicalComposite = rpt?.technical_signals?.find((s) => s.label === "technical_composite");
  const analystDefsFull = [
    { title: "Bull Analyst", icon: "trendUp", color: colors.gain, text: rpt?.bull_thesis || "Data-center demand and gross margins keep expanding faster than consensus estimates." },
    { title: "Bear Analyst", icon: "trendDown", color: colors.loss, text: rpt?.bear_thesis || "Valuation prices in years of flawless execution; export restrictions remain a tail risk." },
    { title: "Technical Analyst", icon: "candle", color: colors.text, text: technicalComposite?.rationale || rpt?.summary || "Price holding above the 50-day average with rising volume on up days." },
    { title: "Risk Analyst", icon: "shield", color: colors.text, text: rpt?.risk_assessment?.summary || "Position sizing matters here — implied volatility is elevated ahead of earnings." },
  ];
  // Evidence-oriented framing instead of raw BUY/SELL/HOLD/AVOID action labels —
  // this is a research synthesis, not a trade instruction.
  const evidenceLabel = (decision: string | null | undefined): string => {
    const d = String(decision || "").toUpperCase();
    if (d === "BUY") return "Bullish";
    if (d === "SELL") return "Bearish";
    if (d === "AVOID") return "Weak / avoid";
    return "Mixed";
  };
  const researchVerdict = rpt ? evidenceLabel(rpt.decision) : "No verdict yet";
  const latestReport = researchJobs
    .map((j) => j.result)
    .find((r): r is MarketResearchReport => Boolean(r) && typeof r === "object" && "decision" in (r as object));

  // -- Sprint 3: research auditability ----------------------------------------
  // Every claim should be traceable: which signals drove it, what raw evidence
  // backs each one, where the data came from, how fresh it is, and what's missing.
  const allSignals = rpt
    ? [
        ...(rpt.technical_signals ?? []).map((s) => ({ ...s, group: "Technical" })),
        ...(rpt.fundamental_signals ?? []).map((s) => ({ ...s, group: "Fundamental" })),
        ...(rpt.news_sentiment_signals ?? []).map((s) => ({ ...s, group: "News & sentiment" })),
      ]
    : [];
  const signalTone = (dir: string) =>
    dir === "bullish" ? colors.gain : dir === "bearish" ? colors.loss : dir === "mixed" ? "oklch(70% 0.15 80)" : colors.textFaint;
  const freshnessRows: Array<{ key: string; date: string | null; conf: number | null }> = rpt
    ? Object.entries(rpt.data_freshness ?? {}).map(([k, v]) => ({
        key: k.replace(/_/g, " "),
        date: typeof v === "string" ? v : null,
        conf: typeof rpt.confidence_levels?.[k] === "number" ? (rpt.confidence_levels[k] as number) : null,
      }))
    : [];
  // De-duplicate source references (the backend can repeat the same id).
  const sourceRefs = rpt
    ? Array.from(new Map((rpt.source_references ?? []).map((s) => [`${s.id}|${s.title}`, s])).values()).slice(0, 12)
    : [];
  const agentOutputs = (rpt?.raw_agent_outputs ?? []).filter((a) => a.display_name);

  // No seed fallback here: an agent with zero real orders should say so,
  // not display fabricated fills that never happened.
  const focusOrders = focusAgent?.latest_orders ?? [];
  const orderDefs = focusOrders.slice(0, 6).map((o) => ({
    side: String(o.side || "").toUpperCase() || "—",
    desc: `${o.instrument ?? ""} · ${o.quantity != null ? `${o.quantity} sh` : ""} @ ${o.execution_price != null ? formatCurrency(o.execution_price) : "market"}`,
    time: o.commission != null ? `comm ${formatCurrency(o.commission)}` : "",
    gain: String(o.side || "").toLowerCase() === "buy",
  }));

  const rangeTabs = ["1M", "3M", "1Y", "ALL"];
  const dashboardHasData = hasPaperData;

  // -- Reusable renderers -----------------------------------------------------
  const StrategyCard = (s: StratCard) => (
    <div key={s.id} style={css(`background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:12px; padding:16px; cursor:pointer;`)}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}>
        <div style={css(`font-size:10.5px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:${colors.accent}; background:oklch(from ${colors.accent} l c h / 0.14); padding:3px 8px; border-radius:6px;`)}>{s.tag}</div>
        <div style={{ fontSize: "11px", fontWeight: 700, color: s.badgeColor, whiteSpace: "nowrap" }} title={s.badgeTitle}>
          {s.badgeText}
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "7px", marginTop: "10px", flexWrap: "wrap" }}>
        <div style={{ fontSize: "14.5px", fontWeight: 600, color: colors.text }}>{s.name}</div>
        {s.origin && s.origin !== "builtin" ? (
          <span style={css(`font-size:9.5px; font-weight:700; letter-spacing:.05em; text-transform:uppercase; padding:2px 6px; border-radius:5px; border:1px solid ${colors.border}; color:${colors.textFaint};`)}>
            {s.origin === "user" ? "Yours" : s.origin === "community" ? "Community" : "Benchmark"}
          </span>
        ) : null}
      </div>
      <div style={{ fontSize: "12px", color: colors.textFaint, marginTop: "4px", lineHeight: 1.45 }}>{s.desc}</div>
      <div style={css(`display:flex; align-items:center; justify-content:space-between; gap:10px; margin-top:14px; padding-top:12px; border-top:1px solid ${colors.border};`)}>
        <div style={{ fontSize: "12px", color: colors.textFaint, whiteSpace: "nowrap" }}>
          {s.footLabel} {s.footValue ? <span style={{ color: colors.text, fontWeight: 700 }}>{s.footValue}</span> : null}
        </div>
        <div style={{ fontSize: "12.5px", fontWeight: 600, color: colors.accent, cursor: "pointer", whiteSpace: "nowrap" }} onClick={() => runBacktestFor(s.name)}>
          Run backtest →
        </div>
      </div>
    </div>
  );

  const NewsList = (items: typeof newsDefs, big = false) =>
    items.map((n) => (
      <div key={n.key} style={css(newsRowStyle)}>
        <div style={css(sentimentDot(n.sentiment))} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: big ? "13.5px" : "12.5px", fontWeight: 600, color: colors.text, lineHeight: 1.4 }}>{n.headline}</div>
          <div style={{ fontSize: big ? "12px" : "11px", color: colors.textFaint, marginTop: "3px" }}>
            {n.source} · {n.time}
          </div>
        </div>
      </div>
    ));

  // ---------------------------------------------------------------------------
  return (
    <div style={css(`display:flex; min-height:100vh; width:100%; background:${colors.bg}; font-family:${font}; color:${colors.text}; box-sizing:border-box;`)}>
      <style>{`@keyframes apollo-pulse { 0%,100% { opacity:1; } 50% { opacity:.35; } }`}</style>

      {/* Mobile nav scrim */}
      {isMobile && mobileNavOpen ? (
        <div onClick={() => setMobileNavOpen(false)} style={css("position:fixed; inset:0; z-index:45; background:oklch(15% 0.01 250 / 0.45);")} />
      ) : null}

      {/* SIDEBAR — off-canvas drawer on mobile, static column otherwise */}
      <aside
        style={css(
          isMobile
            ? `position:fixed; top:0; left:0; bottom:0; z-index:46; width:250px; transform:translateX(${mobileNavOpen ? "0" : "-105%"}); transition:transform .22s ease; background:${colors.surface}; border-right:1px solid ${colors.border}; display:flex; flex-direction:column; padding:20px 14px; box-sizing:border-box; gap:14px; overflow-y:auto;`
            : `width:236px; flex-shrink:0; background:${colors.surface}; border-right:1px solid ${colors.border}; display:flex; flex-direction:column; padding:20px 14px; box-sizing:border-box; gap:14px;`,
        )}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "11px", padding: "0 8px 22px 8px" }}>
          <div style={css(`width:30px; height:30px; border-radius:8px; background:${colors.accent}; color:white; display:flex; align-items:center; justify-content:center; flex-shrink:0;`)}>
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none">
              <circle cx="12" cy="12" r="4.5" fill="white" />
              <g stroke="white" strokeWidth="2" strokeLinecap="round">
                <line x1="12" y1="1" x2="12" y2="4.5" /><line x1="12" y1="19.5" x2="12" y2="23" />
                <line x1="1" y1="12" x2="4.5" y2="12" /><line x1="19.5" y1="12" x2="23" y2="12" />
                <line x1="4.2" y1="4.2" x2="6.6" y2="6.6" /><line x1="17.4" y1="17.4" x2="19.8" y2="19.8" />
                <line x1="19.8" y1="4.2" x2="17.4" y2="6.6" /><line x1="6.6" y1="17.4" x2="4.2" y2="19.8" />
              </g>
            </svg>
          </div>
          <div>
            <div style={{ fontFamily: black, fontSize: "16px", letterSpacing: ".01em", color: colors.text, lineHeight: 1.1 }}>
              APOLLO
            </div>
            <div style={{ fontSize: "10px", fontWeight: 600, letterSpacing: ".07em", textTransform: "uppercase", color: colors.textFaint, marginTop: "3px" }}>Strategy Research &amp; Simulation</div>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "2px", flex: 1 }}>
          {navDefs.map((n) => {
            const isOpen = openNavKey === n.key;
            const isActive = n.key === screen;
            return (
              <div key={n.key} style={css(`display:flex; flex-direction:column; padding:6px 10px; border-radius:9px; background:${isActive ? colors.surfaceRaised : "transparent"}; box-shadow:${isActive ? "0 0 0 1px " + colors.border : "none"};`)}>
                <div style={css(`display:flex; align-items:center; gap:11px; cursor:pointer; color:${isActive ? colors.text : colors.textFaint};`)} onClick={() => navigateTo(n.key)}>
                  <Icon html={ICONS[n.key]} style={css(`width:17px; height:17px; align-items:center; justify-content:center; color:${isActive ? colors.accent : "currentColor"}; flex-shrink:0;`)} />
                  <div style={{ flex: 1, fontSize: "14px", fontWeight: isActive ? 700 : 500, letterSpacing: "-0.01em" }}>{n.label}</div>
                  {n.badge ? (
                    <div style={css(`background:${colors.accent}; color:white; font-size:10.5px; font-weight:700; border-radius:20px; min-width:17px; height:17px; display:flex; align-items:center; justify-content:center; padding:0 5px;`)}>{n.badge}</div>
                  ) : null}
                  <div
                    style={css(`width:20px; height:20px; display:flex; align-items:center; justify-content:center; color:${colors.textFaint}; flex-shrink:0; border-radius:6px; transform:rotate(${isOpen ? 180 : 0}deg); transition:transform .15s ease;`)}
                    onClick={(e) => (n.key !== screen ? navigateTo(n.key) : toggleNavOpen(n.key, e))}
                  >
                    <Icon html={ICONS.chevronDown} style={{ width: "100%", height: "100%" }} />
                  </div>
                </div>
                {isOpen ? (
                  <div style={css(`margin:6px 2px 4px 28px; padding:10px; border-radius:9px; background:${colors.surface}; border:1px solid ${colors.border};`)}>
                    <div style={css(`font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:${colors.textFaint}; margin-bottom:5px;`)}>On this page</div>
                    <div style={{ fontSize: "12px", color: colors.text, lineHeight: 1.45 }}>{navSummaries[n.key]}</div>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>

        {hasPremium ? (
          <div style={css(`background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:12px; padding:14px; margin-top:auto;`)}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div style={{ fontSize: "12px", fontWeight: 600, textTransform: "uppercase", letterSpacing: ".06em", color: colors.textFaint }}>Pro Plan</div>
              <div style={css(`font-size:10.5px; font-weight:700; padding:2px 8px; border-radius:20px; background:oklch(from ${colors.accent} l c h / 0.14); color:${colors.accent};`)}>Active</div>
            </div>
            <div style={{ fontSize: "11.5px", color: colors.textFaint, marginTop: "8px", lineHeight: 1.4 }}>Full access to compute &amp; paper agents.</div>
          </div>
        ) : (
          <div style={css(`background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:12px; padding:14px; margin-top:auto;`)}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div style={{ fontSize: "12px", fontWeight: 600, textTransform: "uppercase", letterSpacing: ".06em", color: colors.textFaint }}>Free Plan</div>
              <div style={{ fontSize: "11px", fontWeight: 700, color: colors.accent }}>62%</div>
            </div>
            <div style={css(`height:5px; border-radius:4px; background:${colors.border}; margin-top:9px; overflow:hidden;`)}>
              <div style={css(`height:100%; width:62%; background:${colors.accent}; border-radius:4px;`)} />
            </div>
            <div style={{ fontSize: "11.5px", color: colors.textFaint, marginTop: "6px", lineHeight: 1.4 }}>31 / 50 backtests used this month</div>
            <button style={css(`width:100%; margin-top:11px; padding:8px; border-radius:8px; border:none; background:${colors.accent}; color:white; font-size:12.5px; font-weight:600; font-family:${font}; cursor:pointer;`)}>Upgrade to Pro</button>
          </div>
        )}

        <div style={{ position: "relative" }}>
          {userMenuOpen && onLogout ? (
            <div style={css(`position:absolute; bottom:100%; left:0; right:0; margin-bottom:8px; background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:12px; padding:8px; box-shadow:0 10px 30px rgba(0,0,0,0.22); z-index:20;`)}>
              {organizations.length > 1 ? (
                <>
                  <div style={css(`font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:${colors.textFaint}; padding:6px 8px 4px;`)}>Workspace</div>
                  {organizations.map((o) => (
                    <button
                      key={o.id}
                      onClick={() => { onSwitchOrg?.(o.id); setUserMenuOpen(false); }}
                      style={css(`display:block; width:100%; padding:8px; border:none; background:${o.id === activeOrgId ? colors.surface : "transparent"}; color:${colors.text}; border-radius:8px; font-size:12.5px; font-weight:600; font-family:${font}; cursor:pointer; text-align:left;`)}
                    >
                      {o.id === activeOrgId ? "● " : ""}{o.name}
                    </button>
                  ))}
                  <div style={{ height: "1px", background: colors.border, margin: "6px 0" }} />
                </>
              ) : null}
              <button
                onClick={() => { setUserMenuOpen(false); navigateTo("account"); }}
                style={css(`display:block; width:100%; padding:8px; border:none; background:transparent; color:${colors.text}; border-radius:8px; font-size:12.5px; font-weight:600; font-family:${font}; cursor:pointer; text-align:left;`)}
              >
                Account &amp; plan
              </button>
              <a
                href="/classic"
                style={css(`display:block; width:100%; padding:8px; color:${colors.textFaint}; border-radius:8px; font-size:12.5px; font-weight:600; font-family:${font}; cursor:pointer; text-align:left; text-decoration:none; box-sizing:border-box;`)}
              >
                Billing &amp; admin console ↗
              </a>
              <button
                onClick={() => { setUserMenuOpen(false); navigateTo("home"); setTourStep(0); }}
                style={css(`display:block; width:100%; padding:8px; border:none; background:transparent; color:${colors.text}; border-radius:8px; font-size:12.5px; font-weight:600; font-family:${font}; cursor:pointer; text-align:left;`)}
              >
                Restart product tour
              </button>
              <button
                onClick={() => { setUserMenuOpen(false); onLogout(); }}
                style={css(`display:block; width:100%; padding:8px; border:none; background:transparent; color:${colors.loss}; border-radius:8px; font-size:12.5px; font-weight:600; font-family:${font}; cursor:pointer; text-align:left;`)}
              >
                Sign out
              </button>
            </div>
          ) : null}
          <div
            onClick={() => { if (onLogout) setUserMenuOpen((v) => !v); }}
            style={css(`display:flex; align-items:center; gap:10px; padding:8px; border-top:1px solid ${colors.border}; padding-top:14px; cursor:${onLogout ? "pointer" : "default"};`)}
          >
            <div style={css(`width:32px; height:32px; border-radius:9px; background:${colors.border}; color:${colors.text}; display:flex; align-items:center; justify-content:center; font-size:12px; font-weight:700; flex-shrink:0;`)}>{userInitials}</div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: "13px", fontWeight: 600, color: colors.text, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{userName}</div>
              <div style={{ fontSize: "11.5px", color: colors.textFaint }}>{workspaceLabel}</div>
            </div>
            {onLogout ? (
              <div style={css(`width:16px; height:16px; color:${colors.textFaint}; flex-shrink:0; transform:rotate(${userMenuOpen ? 180 : 0}deg); transition:transform .15s ease;`)}>
                <Icon html={ICONS.chevronDown} style={{ width: "100%", height: "100%" }} />
              </div>
            ) : null}
          </div>
        </div>
      </aside>

      {/* MAIN */}
      <main style={css(`flex:1; min-width:0; padding:${isMobile ? "16px 16px 32px 16px" : isNarrow ? "20px 22px 36px 22px" : "26px 34px 40px 34px"}; box-sizing:border-box; display:flex; flex-direction:column;`)}>
        {/* TOPBAR */}
        <div style={css(`display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:22px; ${isNarrow ? "flex-wrap:wrap;" : ""}`)}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: "10px", minWidth: 0 }}>
            {isMobile ? (
              <button
                onClick={() => setMobileNavOpen(true)}
                aria-label="Open navigation"
                style={css(`flex-shrink:0; width:36px; height:36px; border-radius:10px; background:${colors.surface}; border:1px solid ${colors.border}; color:${colors.text}; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:3.5px; cursor:pointer;`)}
              >
                {[0, 1, 2].map((i) => <span key={i} style={css(`width:15px; height:1.8px; background:${colors.text}; border-radius:2px; display:block;`)} />)}
              </button>
            ) : null}
            <div style={{ minWidth: 0 }}>
              <div style={{ fontFamily: grotesk, fontSize: isMobile ? "18px" : "22px", fontWeight: 700, letterSpacing: "-0.02em", color: colors.text }}>{screenTitle}</div>
              <div style={{ fontSize: isMobile ? "12px" : "13px", color: colors.textFaint, marginTop: "2px" }}>{screenSubtitle}</div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            {!isNarrow ? (
              <div onClick={() => { setSearchOpen(true); setSearchQuery(""); }} style={css(`display:flex; align-items:center; gap:9px; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:10px; padding:9px 12px; width:250px; cursor:pointer;`)}>
                <Icon html={ICONS.search} style={{ width: "15px", height: "15px", opacity: 0.5, flexShrink: 0 }} />
                <div style={{ fontSize: "13.5px", color: colors.textFaint }}>Search tickers, strategies…</div>
                <div style={css(`margin-left:auto; font-size:10.5px; color:${colors.textFaint}; background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:5px; padding:1px 5px; font-family:'JetBrains Mono',monospace;`)}>⌘K</div>
              </div>
            ) : (
              <button
                onClick={() => { setSearchOpen(true); setSearchQuery(""); }}
                aria-label="Search"
                style={css(`width:36px; height:36px; border-radius:10px; background:${colors.surface}; border:1px solid ${colors.border}; color:${colors.text}; display:flex; align-items:center; justify-content:center; cursor:pointer;`)}
              >
                <Icon html={ICONS.search} style={{ width: "15px", height: "15px" }} />
              </button>
            )}
            {backendOnline !== undefined ? (
              <div style={css(`display:flex; align-items:center; gap:6px; font-size:12px; font-weight:600; color:${colors.textFaint}; padding:0 6px; white-space:nowrap;`)} title="Live backend health check, refreshed every 45s">
                <span style={css(`width:7px; height:7px; border-radius:50%; flex-shrink:0; background:${connectionStatus.dot ? colors.gain : colors.loss};`)} />
                {connectionStatus.label}
              </div>
            ) : null}
            <button style={css(`width:36px; height:36px; border-radius:10px; background:${colors.surface}; border:1px solid ${colors.border}; color:${colors.text}; display:flex; align-items:center; justify-content:center; cursor:pointer;`)} onClick={() => setTheme(dark ? "light" : "dark")}>
              <Icon html={dark ? ICONS.sun : ICONS.moon} />
            </button>
            <div style={{ position: "relative" }}>
              <button style={css(`width:36px; height:36px; border-radius:10px; background:${colors.surface}; border:1px solid ${colors.border}; color:${colors.text}; display:flex; align-items:center; justify-content:center; cursor:pointer;`)} onClick={() => setBellOpen((v) => !v)}>
                <Icon html={ICONS.bell} />
              </button>
              {bellOpen ? (
                <div style={css(`position:absolute; right:0; top:44px; z-index:50; width:320px; background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:12px; padding:12px; box-shadow:0 14px 36px oklch(20% 0.01 250 / 0.3);`)}>
                  <div style={{ fontSize: "12px", fontWeight: 700, color: colors.text, marginBottom: "8px" }}>Recent activity</div>
                  {btJobs.length === 0 && researchJobs.length === 0 ? (
                    <div style={{ fontSize: "12px", color: colors.textFaint }}>Nothing yet — run a backtest or research job and it will show up here.</div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "7px" }}>
                      {btJobs.slice(0, 4).map((j) => (
                        <div key={j.id} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px" }}>
                          <span style={css(`width:7px; height:7px; border-radius:50%; flex-shrink:0; background:${j.status === "completed" ? colors.gain : j.status === "failed" ? colors.loss : colors.textFaint};`)} />
                          <span style={{ color: colors.text, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>Backtest · {strategyDisplayName(((j.request as { pipeline?: string })?.pipeline) || "strategy")}</span>
                          <span style={{ color: colors.textFaint, flexShrink: 0 }}>{j.status}</span>
                        </div>
                      ))}
                      {researchJobs.slice(0, 3).map((j) => (
                        <div key={j.id} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px" }}>
                          <span style={css(`width:7px; height:7px; border-radius:50%; flex-shrink:0; background:${j.status === "completed" ? colors.gain : j.status === "failed" ? colors.loss : colors.textFaint};`)} />
                          <span style={{ color: colors.text, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>Research · {String((j.request as { ticker?: string })?.ticker || "").toUpperCase() || "multi"}</span>
                          <span style={{ color: colors.textFaint, flexShrink: 0 }}>{j.status}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : null}
            </div>
            {screen === "home" ? (
              <button style={css(`${ghostLinkBtnStyle} width:auto; margin:0; padding:0 14px; height:36px; display:flex; align-items:center;`)} onClick={() => navigateTo("activity")}>Deploy validated strategy</button>
            ) : null}
            <button style={css(newAgentBtnStyle)} onClick={() => navigateTo("flask")}>
              <Icon html={ICONS.plus} style={{ width: "14px", height: "14px" }} />
              Run Backtest
            </button>
          </div>
        </div>

        {/* DASHBOARD */}
        {screen === "home" ? (
          <>
            <div style={css(`display:flex; align-items:center; gap:7px; font-size:12px; font-weight:600; color:${colors.textFaint}; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:9px; padding:8px 12px; margin-bottom:14px; width:fit-content;`)}>
              <Icon html={ICONS.shield} style={{ width: "13px", height: "13px", color: colors.textFaint }} /> Simulated environment · No broker connected
            </div>
            {tenantDataError && paper ? (
              <div style={css(`font-size:12.5px; color:${colors.loss}; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:9px; padding:10px 12px; margin-bottom:14px;`)}>
                {tenantDataError} <button onClick={() => { void reloadPaper(); void reloadJobs(); }} style={css(`border:none; background:transparent; color:${colors.accent}; font:inherit; cursor:pointer;`)}>Retry</button>
              </div>
            ) : null}

            {tenantDataLoading ? (
              <div style={css(`background:${colors.surface}; border:1px solid ${colors.border}; border-radius:14px; padding:24px; margin-bottom:16px; color:${colors.textFaint}; font-size:13px;`)}>Loading workspace portfolio…</div>
            ) : tenantDataError && !paper ? (
              <div style={css(`background:${colors.surface}; border:1px solid ${colors.border}; border-radius:14px; padding:24px; margin-bottom:16px;`)}>
                <div style={{ fontFamily: grotesk, fontSize: "18px", fontWeight: 700, color: colors.text }}>Portfolio unavailable</div>
                <div style={{ fontSize: "13px", color: colors.loss, marginTop: "6px" }}>{tenantDataError}</div>
                <button style={css(`${ghostLinkBtnStyle} width:auto;`)} onClick={() => { void reloadPaper(); void reloadJobs(); }}>Retry workspace data</button>
              </div>
            ) : dashboardHasData ? (
              <>
                {/* STAT CARDS */}
                <div style={css(statGridStyle)}>
                  {statDefs.map((c) => (
                    <div key={c.label} style={css(cardBase)}>
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                        <div style={{ fontSize: "12.5px", fontWeight: 600, color: colors.textFaint, textTransform: "uppercase", letterSpacing: ".05em" }}>{c.label}</div>
                        <div style={css(pillForKind(c.kind))}>{c.delta}</div>
                      </div>
                      <div style={{ fontFamily: grotesk, fontSize: "28px", fontWeight: 700, letterSpacing: "-0.02em", color: colors.text, marginTop: "10px" }}>{c.value}</div>
                      <div style={{ fontSize: "12px", color: colors.textFaint, marginTop: "4px" }}>{c.sub}</div>
                    </div>
                  ))}
                </div>

                {/* EQUITY + AGENTS */}
                <div style={css(rowStyle)}>
                  <div style={css(`${panelStyle} flex:1.65;`)}>
                    <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: "4px" }}>
                      <div>
                        <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text }}>Paper Portfolio Equity</div>
                        <div style={{ fontSize: "12.5px", color: colors.textFaint, marginTop: "2px" }}>Aggregated across {paperStrategies.length} active {paperStrategies.length === 1 ? "agent" : "agents"} · paper money</div>
                      </div>
                      <div style={{ display: "flex", gap: "6px" }}>
                        {rangeTabs.map((r) => (
                          <div key={r} style={css(`font-size:12px; font-weight:600; padding:5px 10px; border-radius:7px; cursor:pointer; color:${r === range ? colors.text : colors.textFaint}; background:${r === range ? colors.surfaceRaised : "transparent"}; box-shadow:${r === range ? "0 0 0 1px " + colors.border : "none"};`)} onClick={() => setRange(r)}>{r}</div>
                        ))}
                      </div>
                    </div>
                    <div style={{ display: "flex", alignItems: "baseline", gap: "10px", margin: "10px 0 6px 0" }}>
                      <div style={css(portfolioReturn >= 0 ? pillGain : pillLoss)}>{portfolioReturn >= 0 ? "▲" : "▼"} {formatPercent(portfolioReturn)} all-time</div>
                    </div>
                    <div style={{ position: "relative" }}>
                    {dashEquityPts.length < 2 ? (
                      <div style={{ position: "absolute", inset: 0, zIndex: 2, display: "flex", alignItems: "center", justifyContent: "center", color: colors.textFaint, fontSize: "12.5px", textAlign: "center", padding: "20px" }}>No portfolio history has been recorded for these agents yet.</div>
                    ) : null}
                    <svg
                      viewBox="0 0 760 190"
                      style={{ width: "100%", height: "190px", marginTop: "4px", display: "block", cursor: dashEquityPts.length > 1 ? "crosshair" : "default" }}
                      preserveAspectRatio="none"
                      onMouseMove={(e) => { if (dashEquityPts.length > 1) setDashHoverIdx(hoverIndexFromEvent(e, dashEquityPts.length)); }}
                      onMouseLeave={() => setDashHoverIdx(null)}
                    >
                      <defs>
                        <linearGradient id="apolloEqFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor={colors.accent} stopOpacity={0.28} />
                          <stop offset="100%" stopColor={colors.accent} stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <path d={equityArea} fill="url(#apolloEqFill)" stroke="none" />
                      <path d={equityLine} fill="none" stroke={colors.accent} strokeWidth={2.2} strokeLinejoin="round" strokeLinecap="round" />
                      {hasPaperData && dashHoverIdx != null && dashHoverIdx < dashEquityPts.length ? (() => {
                        const hx = (dashHoverIdx / (dashEquityPts.length - 1 || 1)) * 760;
                        const hy = 190 - ((dashEquityPts[dashHoverIdx] - equityMin) / ((equityMax - equityMin) || 1)) * 190;
                        return (
                          <g pointerEvents="none">
                            <line x1={hx} x2={hx} y1={0} y2={190} stroke={colors.text} strokeWidth={1} strokeDasharray="3,3" opacity={0.5} />
                            <circle cx={hx} cy={hy} r={3.4} fill={colors.accent} stroke={colors.surfaceRaised} strokeWidth={1.4} />
                          </g>
                        );
                      })() : null}
                    </svg>
                    {hasPaperData && dashHoverIdx != null && dashHoverIdx < dashEquityPts.length ? (() => {
                      const pct = (dashHoverIdx / (dashEquityPts.length - 1 || 1)) * 100;
                      const ts = dashTs[dashHoverIdx] ?? "";
                      return (
                        <div style={{ position: "absolute", top: "8px", left: `${pct}%`, transform: pct > 55 ? "translateX(calc(-100% - 12px))" : "translateX(12px)", pointerEvents: "none", zIndex: 5, background: colors.surfaceRaised, border: `1px solid ${colors.border}`, borderRadius: "9px", padding: "8px 11px", boxShadow: "0 8px 22px oklch(20% 0.01 250 / 0.25)", whiteSpace: "nowrap" }}>
                          {ts ? <div style={{ fontSize: "11px", fontWeight: 700, color: colors.text, fontFamily: "'JetBrains Mono',monospace" }}>{ts}</div> : null}
                          <div style={{ fontSize: "11.5px", color: colors.accent, marginTop: "3px", fontWeight: 600 }}>Portfolio {formatCurrency(dashEquityPts[dashHoverIdx])}</div>
                        </div>
                      );
                    })() : null}
                    </div>
                    <div style={{ fontSize: "11.5px", color: colors.textFaint, marginTop: "8px" }}>Paper money · no real capital at risk</div>
                  </div>

                  <div style={css(`${panelStyle} flex:1;`)}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "2px" }}>
                      <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text }}>Agent Performance</div>
                      <div style={{ fontSize: "12.5px", color: colors.accent, fontWeight: 600 }}>{agentDefs.length} running</div>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: "2px", marginTop: "8px" }}>
                      {agentDefs.map((a) => {
                        const mn = a.points.length ? Math.min(...a.points) : 0;
                        const mx = a.points.length ? Math.max(...a.points) : 1;
                        const path = buildPath(a.points, 64, 24, mn, mx);
                        const gain = a.pnl >= 0;
                        const equity = a.equityLabel;
                        return (
                          <div key={a.name} style={css("display:flex; align-items:center; gap:10px; padding:9px 6px; border-radius:9px;")}>
                            <div style={{ width: "8px", display: "flex", alignItems: "center", justifyContent: "center" }}>
                              <div style={css(`width:7px; height:7px; border-radius:50%; background:${colors.gain}; animation:apollo-pulse 2s ease-in-out infinite;`)} />
                            </div>
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ fontSize: "13.5px", fontWeight: 600, color: colors.text, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{a.name}</div>
                              <div style={{ fontSize: "11.5px", color: colors.textFaint, marginTop: "1px" }}>{a.strategy}</div>
                            </div>
                            {path ? <svg viewBox="0 0 64 24" style={{ width: "52px", height: "20px", flexShrink: 0 }}><path d={path} fill="none" stroke={gain ? colors.gain : colors.loss} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" /></svg> : <div style={{ width: "52px", fontSize: "10px", color: colors.textFaint, textAlign: "center" }}>No history</div>}
                            <div style={{ textAlign: "right", minWidth: "58px" }}>
                              <div style={{ fontSize: "13px", fontWeight: 700, color: gain ? colors.gain : colors.loss }}>{gain ? "▲" : "▼"} {(gain ? "+" : "") + a.pnl.toFixed(1)}%</div>
                              <div style={{ fontSize: "11px", color: colors.textFaint }}>{equity}</div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    <button style={css(ghostLinkBtnStyle)} onClick={() => navigateTo("activity")}>View all agents →</button>
                  </div>
                </div>
              </>
            ) : (
              <div style={css(`display:flex; flex-direction:column; align-items:flex-start; background:${colors.surface}; border:1px dashed ${colors.border}; border-radius:14px; padding:24px; margin-bottom:16px;`)}>
                <div style={{ fontFamily: grotesk, fontSize: "18px", fontWeight: 700, color: colors.text }}>No paper agents running yet</div>
                <div style={{ fontSize: "13px", color: colors.textFaint, marginTop: "6px", maxWidth: "460px", lineHeight: 1.5 }}>Backtest a strategy below, then deploy it as a paper agent to track live simulated performance here.</div>
                <button style={css(`${newAgentBtnStyle} margin-top:14px;`)} onClick={() => navigateTo("layers")}>
                  <Icon html={ICONS.plus} style={{ width: "14px", height: "14px" }} /> Browse Strategy Library
                </button>
              </div>
            )}

            {/* STRATEGY LIBRARY PREVIEW */}
                <div style={css(`${panelStyle} margin-top:16px;`)}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "10px" }}>
                    <div>
                      <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text }}>Strategy Library</div>
                      <div style={{ fontSize: "12.5px", color: colors.textFaint, marginTop: "2px" }}>{strategyCount} pre-built rules, ready to backtest</div>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      <select style={css(selectStyle)} value={category} onChange={(e) => setCategory(e.target.value)}>
                        {categoryOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                      </select>
                      <button style={css(`${ghostLinkBtnStyle} margin:0;`)} onClick={() => navigateTo("layers")}>Browse all →</button>
                    </div>
                  </div>
                  {catalogLoading ? <div style={{ fontSize: "12.5px", color: colors.textFaint, padding: "12px 0" }}>Loading strategy library…</div>
                    : catalogError ? <div style={{ fontSize: "12.5px", color: colors.loss, padding: "12px 0" }}>Strategy library unavailable. <button onClick={() => void reloadCatalog()} style={css(`border:none; background:transparent; color:${colors.accent}; font:inherit; cursor:pointer;`)}>Retry</button></div>
                    : filteredCards.length ? <div style={css(strategyGridStyle)}>{filteredCards.slice(0, 4).map(StrategyCard)}</div>
                    : <div style={{ fontSize: "12.5px", color: colors.textFaint, padding: "12px 0" }}>No strategies are available in this workspace.</div>}
                </div>

                <div style={css(`display:flex; align-items:center; gap:7px; font-size:12px; color:${colors.textFaint}; margin-top:10px;`)}>
                  <Icon html={ICONS.shield} style={{ width: "13px", height: "13px", color: colors.accent }} /> Walk-forward testing · PBO checks · realistic costs
                </div>

                {/* BACKTESTS + RESEARCH */}
                <div style={css(`${rowStyle} margin-top:16px; align-items:stretch;`)}>
                  <div style={css(`${panelStyle} flex:1.4;`)}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px" }}>
                      <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text }}>Recent Backtests</div>
                      <select style={css(selectStyle)} value={universeFilter} onChange={(e) => setUniverseFilter(e.target.value)}>
                        {universeOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                      </select>
                    </div>
                    <div style={css(tableHeaderRowStyle)}>
                      <div style={{ flex: 1.6 }}>Strategy</div>
                      <div style={{ flex: 1 }}>Universe</div>
                      <div style={{ flex: 1, textAlign: "right" }}>Return</div>
                      <div style={{ flex: 1, textAlign: "right" }}>Max DD</div>
                      <div style={{ flex: 0.9, textAlign: "right" }} title="Probability of Backtest Overfitting — lower is better">PBO</div>
                      <div style={{ flex: 1.4, textAlign: "right" }}>Next Step</div>
                    </div>
                    {filteredBtDefs.length === 0 ? (
                      <div style={{ padding: "18px 10px", fontSize: "12.5px", color: colors.textFaint }}>No backtests yet — run one from the Strategy Library.</div>
                    ) : null}
                    {filteredBtDefs.map((b, i) => {
                      const pc = b.pbo == null ? colors.textFaint : pboColorFor(b.pbo);
                      const nextColor = b.nextStep === "Reject / archive" || b.nextStep === "Failed" ? colors.loss : b.nextStep === "Deploy to paper" ? colors.gain : colors.textFaint;
                      const nextSym = b.nextStep === "Reject / archive" || b.nextStep === "Failed" ? "⚠" : b.nextStep === "Deploy to paper" ? "✓" : "•";
                      return (
                        <div key={`${b.strategy}-${i}`} style={css(tableRowStyle)}>
                          <div style={{ flex: 1.6, fontSize: "13px", fontWeight: 600, color: colors.text }}>{b.strategy}</div>
                          <div style={{ flex: 1, fontSize: "12.5px", color: colors.textFaint }}>{b.universe}</div>
                          <div style={{ flex: 1, fontSize: "13px", fontWeight: 600, textAlign: "right", color: b.ret >= 0 ? colors.gain : colors.loss }}>{b.ret >= 0 ? "▲" : "▼"} {(b.ret >= 0 ? "+" : "") + b.ret.toFixed(1)}%</div>
                          <div style={{ flex: 1, fontSize: "13px", textAlign: "right", color: colors.textFaint }}>{b.dd}</div>
                          <div style={{ flex: 0.9, textAlign: "right" }}>
                            <span style={css(`font-size:11px; font-weight:700; padding:3px 8px; border-radius:20px; background:oklch(from ${pc} l c h / 0.14); color:${pc};`)} title="Probability of Backtest Overfitting — lower is better">{b.pbo == null ? "Not computed" : `${pboSymbolFor(b.pbo)} ${b.pbo}%`}</span>
                          </div>
                          <div style={{ flex: 1.4, display: "flex", justifyContent: "flex-end" }}>
                            {b.actionable ? (
                              <button
                                onClick={() => { setSelectedStrategy(b.strategy); setUniverseFilter("All Universes"); navigateTo("flask"); }}
                                title={`Open this run: ${b.nextStep}`}
                                style={css(`display:inline-flex; align-items:center; gap:5px; background:transparent; border:1px solid ${colors.border}; border-radius:7px; padding:4px 10px; font-size:11.5px; font-weight:600; font-family:${font}; cursor:pointer; color:${nextColor}; white-space:nowrap;`)}
                              >
                                {nextSym} {b.nextStep} →
                              </button>
                            ) : (
                              <span style={{ fontSize: "12px", fontWeight: 600, color: nextColor, whiteSpace: "nowrap" }}>{nextSym} {b.nextStep}</span>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  <div style={css(`${panelStyle} flex:1; display:flex; flex-direction:column; gap:14px;`)}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text }}>Research Signals</div>
                      <Icon html={ICONS.sparkle} style={{ width: "15px", height: "15px", color: colors.accent }} />
                    </div>
                    <div style={css(verdictCardStyle)}>
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                          <div style={{ fontFamily: grotesk, fontSize: "15px", fontWeight: 700, color: colors.text }}>{latestReport?.ticker ?? "—"}</div>
                          <div style={css(verdictPillStyle)}>{latestReport ? evidenceLabel(latestReport.decision) : "No runs"}</div>
                        </div>
                        <div style={{ fontSize: "12px", fontWeight: 600, color: colors.accent, cursor: "pointer" }} onClick={() => navigateTo("brain")}>{latestReport ? "Read report →" : "Run research →"}</div>
                      </div>
                      <div style={{ fontSize: "12px", color: colors.textFaint, marginTop: "6px", lineHeight: 1.5 }}>{latestReport?.summary || "No research reports yet. Run a synthesis on a ticker to see a weighted evidence balance across bull, bear, technical, and risk signals."}</div>
                    </div>
                    <div style={css(`border-top:1px solid ${colors.border}; padding-top:14px; flex:1; display:flex; flex-direction:column; min-height:0;`)}>
                      <div style={{ fontSize: "13px", fontWeight: 600, color: colors.text, marginBottom: "10px" }}>News &amp; Sentiment</div>
                      {sentimentLoading ? <div style={{ fontSize: "12px", color: colors.textFaint }}>Loading headlines…</div>
                        : sentimentLoadError ? <div style={{ fontSize: "12px", color: colors.loss }}>Headlines unavailable. <button onClick={() => void reloadSentiment()} style={css(`border:none; background:transparent; color:${colors.accent}; font:inherit; cursor:pointer;`)}>Retry</button></div>
                        : newsDefs.length ? <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>{NewsList(newsDefs.slice(0, 3))}</div>
                        : <div style={{ fontSize: "12px", color: colors.textFaint }}>No headlines were returned for this workspace.</div>}
                    </div>
                  </div>
                </div>
          </>
        ) : null}

        {/* LIBRARY */}
        {screen === "layers" ? (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "16px", flexWrap: "wrap" }}>
              <div style={{ display: "flex", gap: "3px", background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: "9px", padding: "3px" }}>
                {(["browse", "design", "community"] as const).map((m) => (
                  <button key={m} onClick={() => setBuilderMode(m)} style={css(`padding:7px 14px; border:none; border-radius:7px; font-size:13px; font-weight:600; font-family:${font}; cursor:pointer; background:${builderMode === m ? colors.accent : "transparent"}; color:${builderMode === m ? "white" : colors.textFaint};`)}>
                    {m === "browse" ? "Browse library" : m === "design" ? `✨ ${builderDesignLabel}` : "🌐 Community"}
                  </button>
                ))}
              </div>
              {builderMode === "browse" ? (
                <select style={css(selectStyle)} value={category} onChange={(e) => setCategory(e.target.value)}>
                  {categoryOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : null}
            </div>

            {builderMode === "browse" ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
                {catalogLoading ? <div style={css(panelStyle)}>Loading strategy library…</div> : null}
                {!catalogLoading && catalogError ? <div style={css(panelStyle)}><div style={{ color: colors.loss, fontSize: "13px" }}>Strategy library unavailable: {catalogError}</div><button onClick={() => void reloadCatalog()} style={css(`${ghostLinkBtnStyle} width:auto;`)}>Retry</button></div> : null}
                {!catalogLoading && !catalogError && groupedCards.length === 0 ? <div style={css(panelStyle)}><div style={{ color: colors.textFaint, fontSize: "13px" }}>No strategies are available in this workspace.</div></div> : null}
                {!catalogLoading && !catalogError ? groupedCards.map((g) => (
                  <div key={g.key}>
                    <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginBottom: "10px", flexWrap: "wrap" }}>
                      <div style={{ fontSize: "13.5px", fontWeight: 700, color: colors.text }}>{g.label}</div>
                      <div style={{ fontSize: "11.5px", color: colors.textFaint }}>{g.cards.length} · {g.note}</div>
                    </div>
                    <div style={css(strategyGridStyle)}>{g.cards.map(StrategyCard)}</div>
                  </div>
                )) : null}
              </div>
            ) : builderMode === "community" ? (
              <>
                <div style={css(`display:flex; align-items:center; gap:7px; font-size:12px; font-weight:600; color:${colors.textFaint}; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:9px; padding:8px 12px; margin-bottom:14px; width:fit-content;`)}>
                  <Icon html={ICONS.shield} style={{ width: "13px", height: "13px", color: colors.textFaint }} />
                  {marketplaceEnabled
                    ? "Marketplace preview · immutable versions · paper-research use only"
                    : "Marketplace is disabled by this workspace · private workspace strategies remain available"}
                </div>
                {marketplaceError ? <div style={css(`${panelStyle} color:${colors.loss}; font-size:12px; margin-bottom:14px;`)}>Marketplace error: {marketplaceError} <button onClick={() => void reloadMarketplace()} style={css(ghostLinkBtnStyle)}>Retry</button></div> : null}
                <div style={css(`${rowStyle} align-items:stretch;`)}>
                  {/* LISTINGS */}
                  <div style={css(`${panelStyle} flex:1.5; display:flex; flex-direction:column;`)}>
                    <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text, marginBottom: "2px" }}>Community strategies</div>
                    <div style={{ fontSize: "12px", color: colors.textFaint, marginBottom: "12px" }}>Published entries are pinned to reviewed immutable versions. Validation status and risk are shown before subscription.</div>
                    {marketplaceEnabled && marketListings.length ? marketListings.map((listing) => {
                      const subscribed = Boolean(marketSubs[listing.id]);
                      const subscription = marketSubscriptionByListing[listing.id];
                      const upgradeAvailable = Boolean(
                        subscribed && listing.current_version_id && subscription?.pinned_listing_version_id !== listing.current_version_id
                      );
                      const mine = marketPublications.some((item) => item.id === listing.id);
                      return (
                        <div key={listing.id} style={css(`border:1px solid ${colors.border}; border-radius:12px; padding:14px; margin-bottom:10px;`)}>
                          <div style={{ display: "flex", justifyContent: "space-between", gap: "12px" }}>
                            <div>
                              <div style={{ fontSize: "14px", fontWeight: 700, color: colors.text }}>{listing.title}</div>
                              <div style={{ fontSize: "12px", color: colors.textFaint, marginTop: "4px", lineHeight: 1.45 }}>{listing.summary}</div>
                            </div>
                            <div style={{ fontSize: "11px", color: colors.textFaint, flexShrink: 0 }}>v{listing.version ?? "?"} · {listing.risk_level ?? "unknown"} risk</div>
                          </div>
                          <div style={{ fontSize: "11px", color: colors.textFaint, marginTop: "8px" }}>
                            {listing.validation_summary.validated ? "Validated" : "Validation unavailable"} · {listing.validation_summary.warning_count} warnings · dry run {listing.validation_summary.dry_run_status ?? "unknown"}
                          </div>
                          <div style={{ marginTop: "10px" }}>
                            {mine ? <span style={{ fontSize: "12px", color: colors.textFaint }}>Published by your organization</span> : (
                              <button
                                disabled={marketplaceBusy === listing.id}
                                onClick={() => void runMarketplaceMutation(listing.id, () => upgradeAvailable
                                  ? subscribeMarketplaceListing(listing.id, listing.current_version_id as string)
                                  : subscribed ? unsubscribeMarketplaceListing(listing.id) : subscribeMarketplaceListing(listing.id))}
                                style={css(newAgentBtnStyle)}
                              >{marketplaceBusy === listing.id ? "Saving…" : upgradeAvailable ? `Upgrade explicitly to v${listing.version ?? "new"}` : subscribed ? "Unsubscribe" : "Subscribe to this version"}</button>
                            )}
                          </div>
                        </div>
                      );
                    }) : null}
                    {marketplaceEnabled && !marketListings.length ? <div style={{ fontSize: "12.5px", color: colors.textFaint, marginBottom: "12px" }}>No public listings have been published.</div> : null}
                    {rankedUserStrategies.length === 0 ? (
                      <div style={{ fontSize: "12.5px", color: colors.textFaint, lineHeight: 1.55 }}>No community strategies yet. Design one with AI or import a spec, then validate and publish it here.</div>
                    ) : (
                      rankedUserStrategies.map(({ s, r }) => {
                        const mine = userId ? s.owner_user_id === userId : true;
                        const pub = marketPub[s.id];
                        const catItem = (catalog ?? []).find((c) => c.pipeline === `user_strategy:${s.id}`);
                        const deployed = paperStrategies.filter((p) => p.pipeline === `user_strategy:${s.id}`);
                        return (
                          <div key={s.id} style={css(`border:1px solid ${colors.border}; border-radius:12px; padding:14px; margin-bottom:10px;`)}>
                            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "10px", flexWrap: "wrap" }}>
                              <div style={{ minWidth: 0 }}>
                                <div style={{ fontSize: "14px", fontWeight: 700, color: colors.text }}>{s.name}{mine ? <span style={{ color: colors.textFaint, fontWeight: 600 }}> · by you</span> : null}</div>
                                {/* Reproducibility metadata: version lineage + audit trail */}
                                <div style={{ fontSize: "11.5px", color: colors.textFaint, marginTop: "3px", fontFamily: "'JetBrains Mono',monospace" }}>
                                  v{s.version} · {s.risk_level} risk · {s.status}
                                  {s.version > 1 ? " · revision" : ""}
                                  {s.updated_at_utc ? ` · updated ${String(s.updated_at_utc).slice(0, 10)}` : ""}
                                </div>
                                <div style={{ fontSize: "11px", color: colors.textFaint, marginTop: "3px" }}>
                                  {r.runs} validated {r.runs === 1 ? "run" : "runs"}
                                  {r.checksTotal ? ` · ${r.checksPassed}/${r.checksTotal} checks` : ""}
                                  {r.folds ? ` · ${r.folds} folds` : ""}
                                  {r.pbo != null ? ` · PBO ${r.pbo.toFixed(0)}%` : " · PBO not computed"}
                                  {deployed.length ? ` · ${deployed.length} paper ${deployed.length === 1 ? "deployment" : "deployments"}` : ""}
                                </div>
                              </div>
                              <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "5px", flexShrink: 0 }}>
                                <div style={css(`font-size:11px; font-weight:700; padding:3px 10px; border-radius:20px; background:oklch(from ${r.tone} l c h / 0.16); color:${r.tone};`)} title="Validation robustness: checks passed, out-of-sample breadth, and overfitting risk">
                                  {r.label}{r.runs ? ` · ${r.score}` : ""}
                                </div>
                                {pub ? <div style={css(`font-size:10.5px; font-weight:700; padding:2px 9px; border-radius:20px; background:oklch(from ${colors.accent} l c h / 0.14); color:${colors.accent};`)}>Listing v{pub.version ?? "draft"} · {pub.status}</div> : null}
                              </div>
                            </div>
                            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "12px", flexWrap: "wrap" }}>
                              {mine ? (
                                pub ? (
                                  <>
                                    <button disabled={marketplaceBusy === pub.id} onClick={() => void runMarketplaceMutation(pub.id, () => pub.status === "published" ? archiveMarketplaceListing(pub.id) : publishMarketplaceListing(pub.id))} style={css(`background:transparent; border:1px solid ${colors.border}; color:${colors.text}; border-radius:8px; padding:6px 12px; font-size:12px; font-weight:600; font-family:${font}; cursor:pointer;`)}>{marketplaceBusy === pub.id ? "Saving…" : pub.status === "published" ? "Archive listing" : "Publish version"}</button>
                                  </>
                                ) : r.publishable ? (
                                  <>
                                    <button disabled={!marketplaceEnabled || marketplaceBusy === s.id} title={marketplaceEnabled ? "Create and publish an immutable marketplace version" : "Marketplace disabled by workspace"} onClick={() => void runMarketplaceMutation(s.id, async () => {
                                      const listing = await createMarketplaceListing({ source_strategy_id: s.id, title: s.name, summary: `Validated paper-research strategy: ${s.name}. Review its immutable specification and risk metadata before use.` });
                                      await publishMarketplaceListing(listing.id);
                                    })} style={css(`background:${colors.surfaceRaised}; border:1px solid ${colors.border}; color:${marketplaceEnabled ? colors.text : colors.textFaint}; border-radius:8px; padding:6px 14px; font-size:12px; font-weight:600; font-family:${font}; cursor:${marketplaceEnabled ? "pointer" : "not-allowed"};`)}>{marketplaceBusy === s.id ? "Publishing…" : marketplaceEnabled ? "Publish reviewed version" : "Marketplace disabled"}</button>
                                    {catItem ? <button onClick={() => { setSelectedStrategy(catItem.name); navigateTo("flask"); }} style={css(`background:transparent; border:1px solid ${colors.border}; color:${colors.text}; border-radius:8px; padding:6px 12px; font-size:12px; font-weight:600; font-family:${font}; cursor:pointer;`)}>Backtest →</button> : null}
                                  </>
                                ) : (
                                  <>
                                    <span style={{ fontSize: "12px", color: colors.textFaint }}>
                                      {r.runs === 0
                                        ? "Not publishable yet — run a backtest to build a validation record."
                                        : `Not publishable yet — needs a majority of checks passed across 3+ folds (currently ${r.checksPassed}/${r.checksTotal} checks, ${r.folds} folds).`}
                                    </span>
                                    {catItem ? (
                                      <button onClick={() => { setSelectedStrategy(catItem.name); navigateTo("flask"); }} style={css(`background:${colors.accent}; border:none; color:white; border-radius:8px; padding:6px 14px; font-size:12px; font-weight:600; font-family:${font}; cursor:pointer;`)}>Validate now →</button>
                                    ) : null}
                                  </>
                                )
                              ) : null}
                              {catItem && mine && pub ? (
                                <button onClick={() => { setSelectedStrategy(catItem.name); navigateTo("flask"); }} style={css(`background:transparent; border:1px solid ${colors.border}; color:${colors.text}; border-radius:8px; padding:6px 12px; font-size:12px; font-weight:600; font-family:${font}; cursor:pointer;`)}>Backtest →</button>
                              ) : null}
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>

                  {/* UPLOAD + MARKETPLACE POLICY */}
                  <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "14px", minWidth: 0 }}>
                    <div style={css(panelStyle)}>
                      <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text, marginBottom: "2px" }}>Add a strategy</div>
                      <div style={{ fontSize: "12px", color: colors.textFaint, marginBottom: "12px", lineHeight: 1.5 }}>The recommended path is the {builderIsLlm ? "AI-assisted" : "rule-based"} designer — describe your idea in plain English and review the validated spec.</div>
                      <button onClick={() => setBuilderMode("design")} style={css(newAgentBtnStyle)}>
                        <Icon html={ICONS.sparkle} style={{ width: "14px", height: "14px" }} /> {builderDesignLabel}
                      </button>
                      {/* Raw JSON import is an expert path — kept out of the default flow. */}
                      <button
                        onClick={() => setShowAdvancedImport((v) => !v)}
                        style={css(`display:block; margin-top:14px; background:transparent; border:none; color:${colors.textFaint}; font-size:11.5px; font-weight:600; font-family:${font}; cursor:pointer; padding:0;`)}
                      >
                        {showAdvancedImport ? "▾" : "▸"} Advanced: import spec JSON
                      </button>
                      {showAdvancedImport ? (
                        <div style={css(`margin-top:10px; border-top:1px solid ${colors.border}; padding-top:12px;`)}>
                          <div style={{ fontSize: "11.5px", color: colors.textFaint, marginBottom: "8px", lineHeight: 1.5 }}>Paste a spec (schema <code>strategy_spec/v1</code>). Validated server-side — only safe rule blocks, never executable code.</div>
                          <textarea value={specUpload} onChange={(e) => setSpecUpload(e.target.value)} placeholder='{"schema_version":"strategy_spec/v1", "name":"…", …}' rows={5} style={css(`width:100%; box-sizing:border-box; font-family:'JetBrains Mono',monospace; font-size:11px; color:${colors.text}; background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:9px; padding:9px; resize:vertical;`)} />
                          <button disabled={specUploadBusy || !specUpload.trim()} onClick={() => void uploadSpec()} style={css(`${newAgentBtnStyle} margin-top:10px; ${specUploadBusy || !specUpload.trim() ? "opacity:0.5; cursor:default;" : ""}`)}>{specUploadBusy ? "Validating…" : "Validate & add"}</button>
                          {specUploadMsg ? <div style={{ fontSize: "12px", color: specUploadMsg.ok ? colors.gain : colors.loss, marginTop: "8px", lineHeight: 1.45 }}>{specUploadMsg.text}</div> : null}
                        </div>
                      ) : null}
                    </div>
                    <div style={css(panelStyle)}>
                      <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text, marginBottom: "8px" }}>Marketplace policy</div>
                      <div style={{ fontSize: "12.5px", color: colors.textFaint, lineHeight: 1.55 }}>Listings and subscriptions are for fake-money research only. Creator credits are disabled pending separate legal and product approval; there is no cash value, withdrawal, transfer, or performance-fee entitlement.</div>
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <div style={css(`${rowStyle} align-items:stretch;`)}>
                {/* Conversation */}
                <div style={css(`${panelStyle} flex:1; display:flex; flex-direction:column; min-height:440px;`)}>
                  <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text, marginBottom: "2px" }}>Describe your idea</div>
                  <div style={{ fontSize: "12px", color: colors.textFaint, marginBottom: "12px", lineHeight: 1.5 }}>Explain your strategy in plain English — universe, entry &amp; exit rules, timeframe, sizing, stop, and costs. The {builderGenerationLabel} builder ({builderProviderLabel}) returns a structured hypothesis for review (no executable code or investment advice).</div>

                  <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "10px", overflowY: "auto", marginBottom: "12px" }}>
                    {builderMessages.length === 0 ? (
                      <div>
                        <div style={{ fontSize: "12px", fontWeight: 600, color: colors.textFaint, marginBottom: "8px" }}>Try an example:</div>
                        {["Buy SPY when the 50-day SMA crosses above the 200-day, sell when it crosses below. Long only, daily bars. Equal weight. No stop. 5 bps cost.", "Buy QQQ when RSI drops below 30 and sell when RSI rises above 60. Daily bars, max 100% per name, no stop, 3 bps cost."].map((ex) => (
                          <div key={ex} onClick={() => setBuilderInput(ex)} style={css(`font-size:12.5px; color:${colors.text}; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:9px; padding:9px 11px; margin-bottom:8px; cursor:pointer; line-height:1.45;`)}>{ex}</div>
                        ))}
                      </div>
                    ) : null}
                    {builderMessages.map((m, i) => (
                      <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
                        <div style={css(`max-width:88%; font-size:12.5px; line-height:1.5; padding:9px 12px; border-radius:11px; ${m.role === "user" ? `background:oklch(from ${colors.accent} l c h / 0.14); color:${colors.text};` : `background:${colors.surface}; border:1px solid ${colors.border}; color:${colors.textFaint};`}`)}>{m.content}</div>
                      </div>
                    ))}
                    {builderResp?.state === "needs_clarification" && builderResp.questions.length ? (
                      <div style={css(`background:${colors.surface}; border:1px solid ${colors.border}; border-radius:11px; padding:11px 13px;`)}>
                        <div style={{ fontSize: "12px", fontWeight: 600, color: colors.text, marginBottom: "6px" }}>A few specifics needed:</div>
                        {builderResp.questions.map((q, i) => (
                          <div key={i} style={{ fontSize: "12px", color: colors.textFaint, lineHeight: 1.5, marginBottom: "4px" }}>• {q}</div>
                        ))}
                      </div>
                    ) : null}
                    {builderResp?.state === "rejected" ? (
                      <div style={{ fontSize: "12px", color: colors.loss, lineHeight: 1.5 }}>{(builderResp.validation.errors || []).join(" ")}</div>
                    ) : null}
                    {builderResp?.interpreted_intent ? (
                      <details style={css(`background:${colors.surface}; border:1px solid ${colors.border}; border-radius:11px; padding:10px 12px;`)}>
                        <summary style={{ fontSize: "12px", fontWeight: 700, color: colors.text, cursor: "pointer" }}>How the AI interpreted this request</summary>
                        <div style={{ fontSize: "12px", color: colors.textFaint, lineHeight: 1.45, marginTop: "7px" }}>{builderResp.interpreted_intent.objective}</div>
                        {builderResp.interpreted_intent.requirement_trace.map((item, i) => (
                          <div key={`${item.requirement}-${i}`} style={{ fontSize: "11.5px", color: colors.textFaint, lineHeight: 1.4, marginTop: "6px" }}>
                            <b style={{ color: item.disposition === "unsupported" || item.disposition === "missing" ? colors.loss : colors.text }}>{item.disposition.toUpperCase()}:</b> {item.requirement} — {item.handling}
                          </div>
                        ))}
                        {builderResp.semantic_repair_count ? <div style={{ fontSize: "11px", color: colors.textFaint, marginTop: "7px" }}>The engine validator corrected this draft through one bounded AI repair pass.</div> : null}
                      </details>
                    ) : null}
                    {builderApproved ? (
                      <div style={css(`background:oklch(from ${colors.gain} l c h / 0.12); border:1px solid oklch(from ${colors.gain} l c h / 0.4); border-radius:11px; padding:11px 13px;`)}>
                        <div style={{ fontSize: "12.5px", fontWeight: 600, color: colors.text }}>✓ Added “{builderApproved.name}” to your library.</div>
                        <button style={css(`${newAgentBtnStyle} margin-top:10px;`)} onClick={() => {
                          const defaults = catalogBacktestDefaults(builderApproved);
                          setSelectedStrategy(builderApproved.name);
                          if (defaults.symbols.length) setBacktestSymbols(defaults.symbols);
                          setBacktestInterval(defaults.interval);
                          setBtParamRows(defaults.parameters);
                          setBuilderMode("browse");
                          navigateTo("flask");
                        }}>
                          <Icon html={ICONS.plus} style={{ width: "14px", height: "14px" }} /> Backtest it now
                        </button>
                      </div>
                    ) : null}
                  </div>

                  {builderError ? <div style={{ fontSize: "12px", color: colors.loss, marginBottom: "8px" }}>{builderError}</div> : null}
                  <div style={{ display: "flex", gap: "8px" }}>
                    <input
                      value={builderInput}
                      onChange={(e) => setBuilderInput(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void sendBuilderMessage(); } }}
                      placeholder={builderBusy ? builderBusyLabel : "Describe or answer here…"}
                      disabled={builderBusy}
                      style={css(`flex:1; font-family:${font}; font-size:13px; color:${colors.text}; background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:10px; padding:10px 13px; outline:none;`)}
                    />
                    <button disabled={builderBusy || !builderInput.trim()} style={css(`${newAgentBtnStyle} ${builderBusy || !builderInput.trim() ? "opacity:0.5; cursor:default;" : ""}`)} onClick={() => void sendBuilderMessage()}>
                      {builderBusy ? "…" : "Send"}
                    </button>
                    <button
                      disabled={builderBusy || (!builderMessages.length && !builderDraft)}
                      onClick={() => { setBuilderMessages([]); setBuilderInput(""); setBuilderResp(null); setBuilderDraft(null); setBuilderApproved(null); setBuilderError(null); }}
                      style={css(`background:transparent; border:1px solid ${colors.border}; color:${colors.text}; border-radius:10px; padding:0 12px; font-size:12px; font-weight:600; font-family:${font}; cursor:pointer;`)}
                    >Start over</button>
                  </div>
                </div>

                {/* Draft spec */}
                <div style={css(`${panelStyle} flex:1.05; display:flex; flex-direction:column;`)}>
                  {!builderDraft ? (
                    <div style={{ margin: "auto", textAlign: "center", maxWidth: "260px" }}>
                      <div style={{ fontFamily: grotesk, fontSize: "16px", fontWeight: 700, color: colors.text }}>Your strategy blueprint</div>
                      <div style={{ fontSize: "12.5px", color: colors.textFaint, marginTop: "8px", lineHeight: 1.5 }}>As you describe your idea, a validated spec — universe, rules, sizing, and tunable parameters — appears here, ready to approve and backtest.</div>
                    </div>
                  ) : (
                    <>
                      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "10px", marginBottom: "4px" }}>
                        <div>
                          <div style={{ fontFamily: grotesk, fontSize: "16px", fontWeight: 700, color: colors.text }}>{builderDraft.name}</div>
                          <div style={{ fontSize: "12px", color: colors.textFaint, marginTop: "2px", lineHeight: 1.4 }}>{builderDraft.summary}</div>
                        </div>
                        {(() => {
                          const st = builderResp?.state ?? "draft";
                          const tone = st === "ready_for_approval" ? colors.gain : st === "rejected" ? colors.loss : "oklch(70% 0.15 80)";
                          const label = st === "ready_for_approval" ? "Ready" : st === "rejected" ? "Rejected" : "Needs detail";
                          return <div style={css(`flex-shrink:0; font-size:11px; font-weight:700; padding:3px 10px; border-radius:20px; background:oklch(from ${tone} l c h / 0.16); color:${tone};`)}>{label}</div>;
                        })()}
                      </div>
                      <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", margin: "12px 0", fontSize: "12px" }}>
                        <div><span style={{ color: colors.textFaint }}>Universe </span><b style={{ color: colors.text }}>{builderDraft.asset_universe.symbols.join(", ")}</b></div>
                        <div><span style={{ color: colors.textFaint }}>Timeframe </span><b style={{ color: colors.text }}>{builderDraft.timeframe === "short_term" ? "1-hour execution + 4-hour confirmation" : intervalLabel("1d")}</b></div>
                        <div><span style={{ color: colors.textFaint }}>Side </span><b style={{ color: colors.text }}>{builderDraft.side.replace("_", " ")}</b></div>
                      </div>
                      {builderResp?.generation_summary ? (
                        <div style={css(`border:1px solid ${colors.border}; border-radius:10px; padding:10px 12px; margin-bottom:10px; background:${colors.surfaceRaised};`)}>
                          <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", letterSpacing: ".05em", color: colors.textFaint, marginBottom: "4px" }}>AI summary</div>
                          <div style={{ fontSize: "12.5px", color: colors.text, lineHeight: 1.5 }}>{builderResp.generation_summary}</div>
                        </div>
                      ) : null}
                      {builderResp?.risk_analysis ? (
                        <div style={css(`border:1px solid oklch(from ${colors.loss} l c h / 0.35); border-radius:10px; padding:10px 12px; margin-bottom:10px; background:oklch(from ${colors.loss} l c h / 0.06);`)}>
                          <div style={{ display: "flex", justifyContent: "space-between", gap: "8px", marginBottom: "5px" }}>
                            <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", letterSpacing: ".05em", color: colors.textFaint }}>Pre-backtest risk analysis</div>
                            <div style={{ fontSize: "11px", fontWeight: 700, color: colors.loss }}>{builderResp.risk_analysis.overall_risk.toUpperCase()}</div>
                          </div>
                          <div style={{ fontSize: "12px", color: colors.text, lineHeight: 1.45 }}>{builderResp.risk_analysis.overview}</div>
                          <div style={{ fontSize: "11.5px", color: colors.textFaint, marginTop: "6px", lineHeight: 1.45 }}>
                            <b style={{ color: colors.text }}>Key risks:</b> {builderResp.risk_analysis.key_risks.join(" ")}
                          </div>
                          <div style={{ fontSize: "11.5px", color: colors.textFaint, marginTop: "4px", lineHeight: 1.45 }}>
                            <b style={{ color: colors.text }}>Mitigations:</b> {builderResp.risk_analysis.mitigations.join(" ")}
                          </div>
                          <div style={{ fontSize: "11.5px", color: colors.textFaint, marginTop: "4px", lineHeight: 1.45 }}>
                            <b style={{ color: colors.text }}>Validate:</b> {builderResp.risk_analysis.validation_priorities.join(" ")}
                          </div>
                        </div>
                      ) : null}
                      <div style={css(`border-top:1px solid ${colors.border}; padding-top:10px;`)}>
                        {[["Entry", builderDraft.entry_rules], ["Exit", builderDraft.exit_rules]].map(([grp, rules]) => (
                          <div key={grp as string} style={{ marginBottom: "8px" }}>
                            <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", letterSpacing: ".05em", color: colors.textFaint, marginBottom: "3px" }}>{grp as string} rules</div>
                            {(rules as StrategySpec["entry_rules"]).map((r, i) => (
                              <div key={i} style={{ fontSize: "12.5px", color: colors.text, lineHeight: 1.45 }}>• {r.description || r.kind}</div>
                            ))}
                          </div>
                        ))}
                      </div>
                      {builderDraft.editable_parameters.length ? (
                        <div style={css(`border-top:1px solid ${colors.border}; padding-top:10px; margin-top:2px;`)}>
                          <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", letterSpacing: ".05em", color: colors.textFaint, marginBottom: "5px" }}>Tunable parameters</div>
                          {builderDraft.editable_parameters.map((p) => (
                            <div key={p.name} style={{ display: "flex", justifyContent: "space-between", fontSize: "12.5px", padding: "2px 0" }}>
                              <span style={{ color: colors.textFaint }}>{p.name}</span>
                              <span style={{ color: colors.text, fontWeight: 600 }}>{String(p.default)}{p.min != null && p.max != null ? ` (${p.min}–${p.max})` : ""}</span>
                            </div>
                          ))}
                        </div>
                      ) : null}
                      {builderResp?.validation?.warnings?.length ? (
                        <div style={{ fontSize: "11.5px", color: "oklch(70% 0.15 80)", marginTop: "10px", lineHeight: 1.4 }}>⚠ {builderResp.validation.warnings.join(" ")}</div>
                      ) : null}
                      <details style={{ marginTop: "10px" }}>
                        <summary style={{ fontSize: "11.5px", fontWeight: 700, color: colors.textFaint, cursor: "pointer" }}>Full validated StrategySpec JSON</summary>
                        <pre style={css(`max-height:240px; overflow:auto; white-space:pre-wrap; font-size:10.5px; color:${colors.text}; background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:9px; padding:9px;`)}>{JSON.stringify(builderDraft, null, 2)}</pre>
                      </details>
                      {builderResp ? <div style={{ fontSize: "10.5px", color: colors.textFaint, marginTop: "7px" }}>{builderResp.generation_mode === "llm" ? "AI-assisted" : "Rule-generated"} · {builderResp.generation_path ?? builderResp.prompt_version}{builderResp.model ? ` · ${builderResp.model}` : ""}</div> : null}
                      <button
                        style={css(`${newAgentBtnStyle} margin-top:14px; justify-content:center; ${builderResp?.state !== "ready_for_approval" || builderBusy ? "opacity:0.5; cursor:default;" : ""}`)}
                        onClick={() => { if (builderResp?.state === "ready_for_approval" && !builderBusy) void approveBuilderDraft(); }}
                      >
                        {builderBusy ? "Working…" : "Approve & add to library"}
                      </button>
                      <div style={{ fontSize: "10.5px", color: colors.textFaint, marginTop: "8px", lineHeight: 1.4 }}>Educational, fake-money research only. Not financial advice. {builderDraft.limitations?.[0]}</div>
                    </>
                  )}
                </div>
              </div>
            )}
          </>
        ) : null}

        {/* BACKTEST */}
        {screen === "flask" ? (
          <>
            {/* Manual configuration: universe, date range, timeframe */}
            <div style={{ display: "flex", alignItems: "flex-start", gap: "10px", marginBottom: "10px", flexWrap: "wrap" }}>
              <div style={css(`display:flex; align-items:center; gap:6px; flex-wrap:wrap; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:10px; padding:5px 8px; min-height:38px; box-sizing:border-box; max-width:340px;`)}>
                {backtestSymbols.map((s) => (
                  <span key={s} style={css(`display:inline-flex; align-items:center; gap:5px; font-size:12.5px; font-weight:600; color:${colors.text}; background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:7px; padding:3px 7px;`)}>
                    {s}
                    <span onClick={() => removeSymbol(s)} title={`Remove ${s}`} style={{ cursor: "pointer", color: colors.textFaint, fontWeight: 700, lineHeight: 1 }}>×</span>
                  </span>
                ))}
                <input
                  value={symbolInput}
                  onChange={(e) => setSymbolInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addSymbol(symbolInput); } }}
                  onBlur={() => addSymbol(symbolInput)}
                  placeholder={backtestSymbols.length ? "Add ticker…" : "Add tickers e.g. SPY"}
                  style={css(`font-family:${font}; font-size:12.5px; color:${colors.text}; background:transparent; border:none; outline:none; min-width:88px; flex:1;`)}
                />
              </div>
              <input type="date" value={backtestStart} onChange={(e) => setBacktestStart(e.target.value)} style={css(`font-family:${font}; font-size:13px; font-weight:600; color:${colors.text}; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:10px; padding:8px 10px; box-sizing:border-box;`)} />
              <span style={{ fontSize: "13px", color: colors.textFaint, alignSelf: "center" }}>→</span>
              <input type="date" value={backtestEnd} onChange={(e) => setBacktestEnd(e.target.value)} style={css(`font-family:${font}; font-size:13px; font-weight:600; color:${colors.text}; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:10px; padding:8px 10px; box-sizing:border-box;`)} />
              <select style={css(selectStyle)} value={backtestInterval} onChange={(e) => setBacktestInterval(e.target.value as typeof backtestInterval)}>
                <option value="1d">Daily bars</option>
                <option value="1h">1-hour bars</option>
                <option value="1mo">Monthly bars</option>
              </select>
              <button
                onClick={() => setShowParams((v) => !v)}
                style={css(`background:${showParams ? colors.surfaceRaised : "transparent"}; border:1px solid ${colors.border}; color:${colors.text}; border-radius:10px; padding:0 14px; height:38px; font-size:13px; font-weight:600; font-family:${font}; cursor:pointer;`)}
              >
                ⚙ Parameters{btParamRows.filter((r) => r.v.trim()).length ? ` (${btParamRows.filter((r) => r.v.trim()).length})` : ""}
              </button>
              <button
                style={css(`${newAgentBtnStyle} ${btRunning ? "opacity:0.6; cursor:default;" : ""}`)}
                onClick={() => { if (!btRunning) void runBacktest(); }}
              >
                <Icon html={ICONS.plus} style={{ width: "14px", height: "14px" }} /> {btRunning ? "Running…" : "Run robust backtest"}
              </button>
            </div>
            {showParams ? (
              <div style={css(`${panelStyle} margin-bottom:14px;`)}>
                <div style={{ fontSize: "13px", fontWeight: 600, color: colors.text, marginBottom: "2px" }}>Strategy parameters · {selectedStrategy}</div>
                <div style={{ fontSize: "11.5px", color: colors.textFaint, marginBottom: "10px" }}>Blank values use the engine's defaults. Filled values are passed to the strategy for this run.</div>
                {btParamRows.map((row, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "7px" }}>
                    <input
                      value={row.k}
                      onChange={(e) => setBtParamRows((prev) => prev.map((r, j) => (j === i ? { ...r, k: e.target.value } : r)))}
                      placeholder="parameter_name"
                      style={css(`flex:1; max-width:220px; font-family:'JetBrains Mono',monospace; font-size:12px; color:${colors.text}; background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:8px; padding:7px 10px;`)}
                    />
                    <input
                      value={row.v}
                      onChange={(e) => setBtParamRows((prev) => prev.map((r, j) => (j === i ? { ...r, v: e.target.value } : r)))}
                      placeholder="engine default"
                      style={css(`flex:1; max-width:160px; font-family:'JetBrains Mono',monospace; font-size:12px; color:${colors.text}; background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:8px; padding:7px 10px;`)}
                    />
                    <span onClick={() => setBtParamRows((prev) => prev.filter((_, j) => j !== i))} title="Remove" style={{ cursor: "pointer", color: colors.textFaint, fontWeight: 700 }}>×</span>
                  </div>
                ))}
                <button onClick={() => setBtParamRows((prev) => [...prev, { k: "", v: "" }])} style={css(`background:transparent; border:1px dashed ${colors.border}; color:${colors.textFaint}; border-radius:8px; padding:6px 12px; font-size:12px; font-weight:600; font-family:${font}; cursor:pointer;`)}>+ Add parameter</button>
              </div>
            ) : null}
            <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "16px", flexWrap: "wrap", minHeight: "16px" }}>
              {btRunning ? <div style={{ fontSize: "12px", color: colors.textFaint }}>Training &amp; validating across walk-forward folds…</div> : null}
              {btError ? <div style={{ fontSize: "12px", color: colors.loss }}>{btError}</div> : null}
              {(backtestInterval === "1h" || backtestInterval === "4h") && !btRunning ? <div style={{ fontSize: "12px", color: colors.textFaint }}>Intraday data is typically only available for the last ~2 years — narrow the dates if the run fails.</div> : null}
              {!hasBtDetail && !btRunning && !btError ? <div style={{ fontSize: "12px", color: colors.textFaint }}>Run a backtest to populate live metrics for {selectedStrategy}.</div> : null}
            </div>

            {/* VALIDATION SUMMARY STRIP — the headline trust verdict, above the chart */}
            {hasBtDetail ? (() => {
              const passed = btDecision?.passed_checks ?? null;
              const totalChecks = btDecision?.total_checks ?? null;
              const allPassed = passed != null && totalChecks != null && passed === totalChecks;
              const state = selectedPbo == null
                ? { label: "Validation incomplete", tone: "oklch(70% 0.15 80)" }
                : allPassed && pboRisk === "low"
                  ? { label: "Validated", tone: colors.gain }
                  : pboRisk === "high"
                    ? { label: "Likely overfit", tone: colors.loss }
                    : { label: "Needs more evidence", tone: "oklch(70% 0.15 80)" };
              const items: Array<[string, string, string]> = [
                ["Out-of-sample folds", totalFolds ? String(totalFolds) : "—", foldStats ? `${foldStats.positive}/${foldStats.total} profitable` : "not reported"],
                ["PBO", selectedPbo == null ? "not computed" : `${selectedPbo}%`, pboRiskLabel],
                ["Checks passed", passed != null && totalChecks != null ? `${passed}/${totalChecks}` : "—", btDecision?.verdict ? String(btDecision.verdict).replace(/_/g, " ") : ""],
                ["vs benchmark", execStats?.benchDelta != null ? `${execStats.benchDelta >= 0 ? "+" : ""}${formatPercent(execStats.benchDelta)}` : "—", execStats?.benchTotal != null ? `B&H ${formatPercent(execStats.benchTotal)}` : ""],
              ];
              return (
                <div style={css(`${panelStyle} margin-bottom:14px; border-left:3px solid ${state.tone};`)}>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginBottom: "12px" }}>
                    <div style={css(`font-size:11.5px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; padding:4px 11px; border-radius:20px; background:oklch(from ${state.tone} l c h / 0.16); color:${state.tone};`)}>{state.label}</div>
                    <div style={{ fontSize: "12.5px", color: colors.textFaint }}>{btDecision?.headline ? String(btDecision.headline) : "Validation summary for this run"}</div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "12px" }}>
                    {items.map(([k, v, sub]) => (
                      <div key={k}>
                        <div style={{ fontSize: "10.5px", fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase", color: colors.textFaint }}>{k}</div>
                        <div style={{ fontFamily: grotesk, fontSize: "17px", fontWeight: 700, color: colors.text, marginTop: "4px" }}>{v}</div>
                        {sub ? <div style={{ fontSize: "11px", color: colors.textFaint, marginTop: "1px" }}>{sub}</div> : null}
                      </div>
                    ))}
                  </div>
                </div>
              );
            })() : null}

            <div style={css(`${panelStyle} margin-bottom:14px;`)}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", marginBottom: "6px", flexWrap: "wrap" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "14px", flexWrap: "wrap" }}>
                  <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text }}>{candleMode ? `Candlesticks · ${btJobReq?.symbols?.[0] ?? ""}` : priceMode ? "Price & moving averages" : "Cumulative return vs. buy-and-hold"}</div>
                  {candleMode ? (
                    <>
                      <span style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "11.5px", color: colors.textFaint }}><span style={{ width: "7px", height: "10px", borderRadius: "2px", background: colors.gain }} /> Up</span>
                      <span style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "11.5px", color: colors.textFaint }}><span style={{ width: "7px", height: "10px", borderRadius: "2px", background: colors.loss }} /> Down</span>
                      {([["SMA 20", colors.accent, "s20"], ["SMA 50", "oklch(70% 0.15 80)", "s50"], ["SMA 200", "oklch(62% 0.2 300)", "s200"]] as const).map(([lbl, c, key]) => (
                        <div
                          key={lbl}
                          onClick={() => setSmaVis((prev) => ({ ...prev, [key]: !prev[key] }))}
                          title={`Click to ${smaVis[key] ? "hide" : "show"} ${lbl}`}
                          style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer", opacity: smaVis[key] ? 1 : 0.35 }}
                        >
                          <div style={{ width: "10px", height: "2.5px", background: c, borderRadius: "2px" }} />
                          <div style={{ fontSize: "11.5px", color: colors.textFaint, textDecoration: smaVis[key] ? "none" : "line-through" }}>{lbl}</div>
                        </div>
                      ))}
                    </>
                  ) : priceMode ? (
                    <>
                      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        <div style={{ width: "10px", height: "2.5px", background: colors.text, borderRadius: "2px" }} />
                        <div style={{ fontSize: "11.5px", color: colors.textFaint }}>Close</div>
                      </div>
                      {([["SMA 20", colors.accent, "s20"], ["SMA 50", "oklch(70% 0.15 80)", "s50"], ["SMA 200", colors.loss, "s200"]] as const).map(([lbl, c, key]) => (
                        <div
                          key={lbl}
                          onClick={() => setSmaVis((prev) => ({ ...prev, [key]: !prev[key] }))}
                          title={`Click to ${smaVis[key] ? "hide" : "show"} ${lbl}`}
                          style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer", opacity: smaVis[key] ? 1 : 0.35 }}
                        >
                          <div style={{ width: "10px", height: "2.5px", background: c, borderRadius: "2px" }} />
                          <div style={{ fontSize: "11.5px", color: colors.textFaint, textDecoration: smaVis[key] ? "none" : "line-through" }}>{lbl}</div>
                        </div>
                      ))}
                    </>
                  ) : (
                    <>
                      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        <div style={{ width: "10px", height: "2.5px", background: colors.accent, borderRadius: "2px" }} />
                        <div style={{ fontSize: "11.5px", color: colors.textFaint }}>Strategy</div>
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        <div style={{ width: "10px", height: "2.5px", background: colors.textFaint, borderRadius: "2px" }} />
                        <div style={{ fontSize: "11.5px", color: colors.textFaint }}>Buy &amp; hold</div>
                      </div>
                    </>
                  )}
                  {btMarkers.length ? (
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <span style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "11.5px", color: colors.textFaint }}><span style={{ width: "7px", height: "7px", borderRadius: "50%", background: colors.gain }} /> Buy</span>
                      <span style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "11.5px", color: colors.textFaint }}><span style={{ width: "7px", height: "7px", borderRadius: "50%", background: colors.loss }} /> Sell</span>
                    </div>
                  ) : null}
                  {btEventMarkers.length ? (
                    <span style={{ display: "flex", alignItems: "center", gap: "5px", fontSize: "11.5px", color: colors.textFaint }} title="Earnings reports and SEC filings — hover a diamond on the chart for details"><span style={{ width: "7px", height: "7px", transform: "rotate(45deg)", background: colors.textFaint, borderRadius: "2px" }} /> Events ({btEventMarkers.length})</span>
                  ) : null}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                  {btZoom ? (
                    <button onClick={() => { setBtZoom(null); setBtDrag(null); }} style={css(`padding:5px 12px; border:1px solid ${colors.border}; border-radius:8px; font-size:12px; font-weight:600; font-family:${font}; cursor:pointer; background:transparent; color:${colors.accent};`)}>⤺ Reset zoom</button>
                  ) : null}
                  {hasViz ? (
                    <div style={{ display: "flex", gap: "3px", background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: "9px", padding: "3px" }} title="Indicator panel below the chart">
                      {(["none", "rsi", "drawdown"] as const).map((s) => (
                        <button
                          key={s}
                          onClick={() => setSubChart(s)}
                          disabled={s === "rsi" && !hasRsi}
                          style={css(`padding:5px 10px; border:none; border-radius:7px; font-size:11.5px; font-weight:600; font-family:${font}; cursor:${s === "rsi" && !hasRsi ? "default" : "pointer"}; opacity:${s === "rsi" && !hasRsi ? 0.4 : 1}; background:${subChart === s ? colors.accent : "transparent"}; color:${subChart === s ? "white" : colors.textFaint};`)}
                        >
                          {s === "none" ? "—" : s === "rsi" ? "RSI" : "DD"}
                        </button>
                      ))}
                    </div>
                  ) : null}
                  <div style={{ display: "flex", gap: "3px", background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: "9px", padding: "3px" }}>
                    {(["equity", "price", "candles"] as const).map((m) => {
                      const disabled = (m === "price" && !hasViz) || (m === "candles" && btOhlc.length < 2);
                      const hint = m === "price" && !hasViz
                        ? "Run a backtest to see the price chart"
                        : m === "candles" && btOhlc.length < 2
                          ? (btOhlcError || "Run a backtest to load real OHLC candles")
                          : "";
                      return (
                        <button
                          key={m}
                          onClick={() => setChartMode(m)}
                          disabled={disabled}
                          title={hint}
                          style={css(`padding:5px 12px; border:none; border-radius:7px; font-size:12px; font-weight:600; font-family:${font}; cursor:${disabled ? "default" : "pointer"}; opacity:${disabled ? 0.5 : 1}; background:${chartMode === m ? colors.accent : "transparent"}; color:${chartMode === m ? "white" : colors.textFaint};`)}
                        >
                          {m === "equity" ? "Equity" : m === "price" ? "Price" : "Candles"}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
              {btWindowStats ? (
                <div style={{ display: "flex", alignItems: "center", gap: "14px", flexWrap: "wrap", fontSize: "11.5px", color: colors.textFaint, marginBottom: "2px" }}>
                  <span style={{ fontFamily: "'JetBrains Mono',monospace" }}>{btWindowStats.d0} → {btWindowStats.d1}</span>
                  <span>{btWindowStats.mainLabel} <b style={{ color: btWindowStats.main.startsWith("-") ? colors.loss : colors.gain }}>{btWindowStats.main}</b></span>
                  <span>{btWindowStats.hi}</span>
                  <span>{btWindowStats.lo}</span>
                  <span style={{ opacity: 0.7 }}>· drag to zoom, double-click to reset</span>
                </div>
              ) : null}
              <div style={{ position: "relative" }}>
              {!hasViz ? (
                <div style={{ position: "absolute", inset: 0, zIndex: 2, display: "flex", alignItems: "center", justifyContent: "center", color: colors.textFaint, fontSize: "12.5px", textAlign: "center", padding: "20px" }}>
                  {activeBtJob?.status === "completed" ? "This run completed without chart visualization data. Retry the backtest or inspect the saved job details." : "Run or select a completed backtest to view its chart."}
                </div>
              ) : null}
              <svg
                viewBox="0 0 700 200"
                style={{ width: "100%", height: "200px", marginTop: "8px", display: "block", cursor: hasViz ? "crosshair" : "default", userSelect: "none" }}
                preserveAspectRatio="none"
                onMouseDown={(e) => {
                  if (!hasViz) return;
                  e.preventDefault();
                  const idx = hoverIndexFromEvent(e, chartCount);
                  if (idx != null) setBtDrag([idx, idx]);
                }}
                onMouseMove={(e) => {
                  if (!hasViz) return;
                  const idx = hoverIndexFromEvent(e, chartCount);
                  setBtHoverIdx(idx);
                  if (btDrag && idx != null) setBtDrag([btDrag[0], idx]);
                }}
                onMouseUp={() => {
                  if (!btDrag) return;
                  const lo = Math.min(btDrag[0], btDrag[1]);
                  const hi = Math.max(btDrag[0], btDrag[1]);
                  if (candleMode && candles.length) {
                    // Candle drag → date range → nearest full-series equity indices.
                    if (hi - lo >= 2) {
                      const t0 = candles[lo].t0;
                      const t1 = candles[hi].t1;
                      let i0 = vizEqFull.findIndex((p) => p.timestamp >= t0);
                      if (i0 < 0) i0 = 0;
                      let i1 = vizEqFull.findIndex((p) => p.timestamp > t1);
                      i1 = i1 < 0 ? nBtFull - 1 : Math.max(i0 + 2, i1 - 1);
                      if (i1 - i0 >= 2 && i1 < nBtFull) setBtZoom([i0, i1]);
                    }
                  } else if (hi - lo >= 3) {
                    setBtZoom([vi0 + lo, vi0 + hi]);
                  }
                  setBtDrag(null);
                }}
                onDoubleClick={() => { setBtZoom(null); setBtDrag(null); }}
                onMouseLeave={() => { setBtHoverIdx(null); setBtDrag(null); }}
              >
                {candleMode ? (
                  <>
                    {smaVis.s200 ? <path d={cSma200Path} fill="none" stroke="oklch(62% 0.2 300)" strokeWidth={1.4} strokeLinecap="round" opacity={0.8} /> : null}
                    {smaVis.s50 ? <path d={cSma50Path} fill="none" stroke="oklch(70% 0.15 80)" strokeWidth={1.4} strokeLinecap="round" opacity={0.8} /> : null}
                    {smaVis.s20 ? <path d={cSma20Path} fill="none" stroke={colors.accent} strokeWidth={1.4} strokeLinecap="round" opacity={0.8} /> : null}
                    {candles.map((cd, i) => {
                      const slotW = 700 / candles.length;
                      const cx = (i + 0.5) * slotW;
                      const bodyW = Math.max(1.2, slotW * 0.62);
                      const up = cd.c >= cd.o;
                      const tone = up ? colors.gain : colors.loss;
                      const yO = yC(cd.o);
                      const yCl = yC(cd.c);
                      return (
                        <g key={`cd-${i}`}>
                          <line x1={cx} x2={cx} y1={yC(cd.h)} y2={yC(cd.l)} stroke={tone} strokeWidth={Math.min(1.2, bodyW * 0.18)} />
                          <rect x={cx - bodyW / 2} y={Math.min(yO, yCl)} width={bodyW} height={Math.max(1, Math.abs(yO - yCl))} fill={tone} rx={bodyW > 4 ? 1 : 0} />
                        </g>
                      );
                    })}
                  </>
                ) : priceMode ? (
                  <>
                    {smaVis.s200 ? <path d={sma200Path} fill="none" stroke={colors.loss} strokeWidth={1.4} strokeLinecap="round" opacity={0.7} /> : null}
                    {smaVis.s50 ? <path d={sma50Path} fill="none" stroke="oklch(70% 0.15 80)" strokeWidth={1.4} strokeLinecap="round" opacity={0.8} /> : null}
                    {smaVis.s20 ? <path d={sma20Path} fill="none" stroke={colors.accent} strokeWidth={1.4} strokeLinecap="round" opacity={0.8} /> : null}
                    <path d={closePath} fill="none" stroke={colors.text} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
                  </>
                ) : (
                  <>
                    <path d={bhPath} fill="none" stroke={colors.textFaint} strokeWidth={1.8} strokeDasharray="5,4" strokeLinecap="round" />
                    <path d={stratPath} fill="none" stroke={colors.accent} strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" />
                  </>
                )}
                {(candleMode ? candleEventMarkers : btEventMarkers).map((m, i) => (
                  <g key={`ev-${i}`}>
                    <line x1={m.x} x2={m.x} y1={14} y2={200} stroke={eventTone(m.ev.event_direction)} strokeWidth={1} strokeDasharray="2,5" opacity={0.3} />
                    <rect x={-3.5} y={-3.5} width={7} height={7} transform={`translate(${m.x},8) rotate(45)`} fill={eventTone(m.ev.event_direction)} opacity={0.9}>
                      <title>{`${m.ev.date} · ${m.ev.ticker} · ${m.ev.event_type_label || m.ev.event_type}${m.ev.event_title ? ` — ${m.ev.event_title}` : ""}${m.ev.beat_miss && m.ev.beat_miss !== "not_available" ? ` (${m.ev.beat_miss})` : ""}`}</title>
                    </rect>
                  </g>
                ))}
                {!candleMode ? btMarkers.map((m, i) => (
                  <circle key={i} cx={m.x} cy={m.y} r={2.4} fill={m.isBuy ? colors.gain : colors.loss} opacity={0.7} />
                )) : null}
                {btDrag ? (() => {
                  const lo = Math.min(btDrag[0], btDrag[1]);
                  const hi = Math.max(btDrag[0], btDrag[1]);
                  const x0 = candleMode ? (lo / chartCount) * 700 : (lo / (chartCount - 1 || 1)) * 700;
                  const x1 = candleMode ? ((hi + 1) / chartCount) * 700 : (hi / (chartCount - 1 || 1)) * 700;
                  return <rect x={x0} y={0} width={Math.max(1, x1 - x0)} height={200} fill={colors.accent} opacity={0.12} stroke={colors.accent} strokeWidth={1} strokeDasharray="4,3" pointerEvents="none" />;
                })() : null}
                {hasViz && btHoverIdx != null && btHoverIdx < chartCount ? (() => {
                  const hx = candleMode ? ((btHoverIdx + 0.5) / chartCount) * 700 : (btHoverIdx / (chartCount - 1 || 1)) * 700;
                  return (
                    <g pointerEvents="none">
                      <line x1={hx} x2={hx} y1={0} y2={200} stroke={colors.text} strokeWidth={1} strokeDasharray="3,3" opacity={0.5} />
                      {candleMode ? (
                        candles[btHoverIdx] ? (() => {
                          const slotW = 700 / chartCount;
                          return <rect x={btHoverIdx * slotW} y={0} width={slotW} height={200} fill={colors.text} opacity={0.06} />;
                        })() : null
                      ) : priceMode ? (
                        closePts[btHoverIdx] != null ? <circle cx={hx} cy={yBt(closePts[btHoverIdx] as number)} r={3.4} fill={colors.text} stroke={colors.surfaceRaised} strokeWidth={1.4} /> : null
                      ) : (
                        <>
                          <circle cx={hx} cy={yBt(stratPts[btHoverIdx])} r={3.4} fill={colors.accent} stroke={colors.surfaceRaised} strokeWidth={1.4} />
                          <circle cx={hx} cy={yBt(bhPts[btHoverIdx])} r={3} fill={colors.textFaint} stroke={colors.surfaceRaised} strokeWidth={1.4} />
                        </>
                      )}
                    </g>
                  );
                })() : null}
              </svg>
              {hasViz && btHoverIdx != null && btHoverIdx < chartCount ? (() => {
                const pct = candleMode ? ((btHoverIdx + 0.5) / chartCount) * 100 : (btHoverIdx / (chartCount - 1 || 1)) * 100;
                const fmtPct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
                const cd = candleMode ? candles[btHoverIdx] : null;
                const date = cd
                  ? cd.t0.slice(0, 10) === cd.t1.slice(0, 10) ? cd.t0.slice(0, 10) : `${cd.t0.slice(0, 10)} → ${cd.t1.slice(0, 10)}`
                  : btHoverIdx < nBt ? vizEq[btHoverIdx].timestamp.slice(0, 10) : "";
                return (
                  <div style={{ position: "absolute", top: "10px", left: `${pct}%`, transform: pct > 55 ? "translateX(calc(-100% - 12px))" : "translateX(12px)", pointerEvents: "none", zIndex: 5, background: colors.surfaceRaised, border: `1px solid ${colors.border}`, borderRadius: "9px", padding: "8px 11px", boxShadow: "0 8px 22px oklch(20% 0.01 250 / 0.25)", whiteSpace: "nowrap" }}>
                    <div style={{ fontSize: "11px", fontWeight: 700, color: colors.text, fontFamily: "'JetBrains Mono',monospace" }}>{date}</div>
                    {cd ? (
                      <>
                        <div style={{ fontSize: "11.5px", color: cd.c >= cd.o ? colors.gain : colors.loss, marginTop: "3px", fontWeight: 600 }}>
                          O {formatNumber(cd.o, 2)} · H {formatNumber(cd.h, 2)} · L {formatNumber(cd.l, 2)} · C {formatNumber(cd.c, 2)}
                        </div>
                        <div style={{ fontSize: "11px", color: colors.textFaint, marginTop: "1px" }}>
                          {fmtPct(cd.o ? (cd.c / cd.o - 1) * 100 : 0)}{cd.v ? ` · Vol ${formatCurrency(cd.v, true).replace("$", "")}` : ""}
                        </div>
                      </>
                    ) : priceMode && btHoverIdx < nBt ? (
                      <>
                        <div style={{ fontSize: "11.5px", color: colors.text, marginTop: "3px" }}>Close {closePts[btHoverIdx] != null ? formatCurrency(closePts[btHoverIdx]) : "—"}</div>
                        <div style={{ fontSize: "11px", color: colors.textFaint, marginTop: "1px" }}>
                          SMA20 {sma20Pts[btHoverIdx] != null ? formatNumber(sma20Pts[btHoverIdx], 2) : "—"} · SMA50 {sma50Pts[btHoverIdx] != null ? formatNumber(sma50Pts[btHoverIdx], 2) : "—"} · SMA200 {sma200Pts[btHoverIdx] != null ? formatNumber(sma200Pts[btHoverIdx], 2) : "—"}
                        </div>
                      </>
                    ) : btHoverIdx < nBt ? (
                      <>
                        <div style={{ fontSize: "11.5px", color: colors.accent, marginTop: "3px", fontWeight: 600 }}>Strategy {fmtPct(stratPts[btHoverIdx])}</div>
                        <div style={{ fontSize: "11.5px", color: colors.textFaint, marginTop: "1px" }}>Buy &amp; hold {fmtPct(bhPts[btHoverIdx])}</div>
                      </>
                    ) : null}
                  </div>
                );
              })() : null}
              </div>
              {subChart !== "none" && hasViz ? (
                <div style={css(`margin-top:8px; border-top:1px solid ${colors.border}; padding-top:8px;`)}>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "2px" }}>
                    <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", letterSpacing: ".05em", color: colors.textFaint }}>{subChart === "rsi" ? "RSI (14)" : "Strategy drawdown"}</div>
                    {!candleMode && btHoverIdx != null && btHoverIdx < nBt ? (
                      <div style={{ fontSize: "11px", fontWeight: 600, color: colors.text, fontFamily: "'JetBrains Mono',monospace" }}>
                        {subChart === "rsi"
                          ? (rsiPts[btHoverIdx] != null ? Number(rsiPts[btHoverIdx]).toFixed(1) : "—")
                          : `${ddPts[btHoverIdx].toFixed(1)}%`}
                      </div>
                    ) : null}
                  </div>
                  <svg viewBox="0 0 700 70" style={{ width: "100%", height: "70px", display: "block" }} preserveAspectRatio="none">
                    {subChart === "rsi" ? (
                      <>
                        <line x1={0} x2={700} y1={70 - (70 * 70) / 100} y2={70 - (70 * 70) / 100} stroke={colors.loss} strokeWidth={0.8} strokeDasharray="3,4" opacity={0.5} />
                        <line x1={0} x2={700} y1={70 - (30 * 70) / 100} y2={70 - (30 * 70) / 100} stroke={colors.gain} strokeWidth={0.8} strokeDasharray="3,4" opacity={0.5} />
                        <path d={rsiPath} fill="none" stroke={colors.accent} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
                      </>
                    ) : (
                      <>
                        <path d={ddArea} fill={`oklch(from ${colors.loss} l c h / 0.14)`} stroke="none" />
                        <path d={ddPath} fill="none" stroke={colors.loss} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
                      </>
                    )}
                    {!candleMode && btHoverIdx != null && btHoverIdx < nBt ? (
                      <line x1={(btHoverIdx / (nBt - 1 || 1)) * 700} x2={(btHoverIdx / (nBt - 1 || 1)) * 700} y1={0} y2={70} stroke={colors.text} strokeWidth={1} strokeDasharray="3,3" opacity={0.5} pointerEvents="none" />
                    ) : null}
                  </svg>
                </div>
              ) : null}
              {btEventMarkers.length ? (
                <div style={css("margin-top:10px; display:flex; gap:8px; overflow-x:auto; padding-bottom:4px;")}>
                  {btEvents.slice(0, 12).map((ev) => (
                    <div key={ev.id} title={ev.summary || ev.event_title} style={css(`flex-shrink:0; display:flex; align-items:center; gap:7px; font-size:11.5px; color:${colors.textFaint}; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:8px; padding:6px 10px;`)}>
                      <span style={css(`width:7px; height:7px; border-radius:2px; transform:rotate(45deg); background:${eventTone(ev.event_direction)}; flex-shrink:0;`)} />
                      <span style={{ fontWeight: 600, color: colors.text }}>{ev.ticker}</span>
                      <span>{ev.date}</span>
                      <span>{ev.event_type_label || ev.event_type}</span>
                      {ev.beat_miss && ev.beat_miss !== "not_available" ? (
                        <span style={css(`font-weight:700; color:${ev.beat_miss === "beat" ? colors.gain : ev.beat_miss === "miss" ? colors.loss : colors.textFaint};`)}>{ev.beat_miss}</span>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>

            <div style={css(`${statGridStyle} margin-bottom:14px;`)}>
              {[
                { label: "CAGR", value: selectedDetail.cagr, valueColor: colors.text },
                { label: "Sharpe", value: selectedDetail.sharpe, valueColor: colors.text },
                { label: "Max Drawdown", value: selectedDetail.maxDd, valueColor: colors.loss },
                { label: "Win Rate", value: selectedDetail.winRate, valueColor: colors.text },
              ].map((c) => (
                <div key={c.label} style={css(cardBase)}>
                  <div style={{ fontSize: "12.5px", fontWeight: 600, color: colors.textFaint, textTransform: "uppercase", letterSpacing: ".05em" }}>{c.label}</div>
                  <div style={{ fontFamily: grotesk, fontSize: "24px", fontWeight: 700, letterSpacing: "-0.02em", color: c.valueColor, marginTop: "10px" }}>{c.value}</div>
                </div>
              ))}
            </div>

            <div style={css(panelStyle)}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "6px" }}>
                <div style={{ fontSize: "14px", fontWeight: 600, color: colors.text }}>Overfitting score (PBO): {selectedPbo == null ? "not computed" : `${selectedPbo}%`}</div>
                <div style={css(`font-size:11.5px; font-weight:700; padding:3px 10px; border-radius:20px; background:oklch(from ${pboRiskColor} l c h / 0.16); color:${pboRiskColor};`)}>{pboRiskLabel}</div>
              </div>
              <div style={{ fontSize: "12.5px", color: colors.textFaint, lineHeight: 1.5 }}>{pboNote}</div>
            </div>

            {/* OUT-OF-SAMPLE FOLDS — per-window walk-forward results */}
            {foldRows.length && foldStats ? (
              <div style={css(`${panelStyle} margin-top:14px;`)}>
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "12px", flexWrap: "wrap", marginBottom: "12px" }}>
                  <div>
                    <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text }}>Out-of-sample folds</div>
                    <div style={{ fontSize: "11.5px", color: colors.textFaint, marginTop: "2px" }}>
                      Each bar is one unseen test window. Consistency across folds matters more than any single number.
                      {wfConfig ? ` Train ${String(wfConfig.train_bars)} bars → test ${String(wfConfig.test_bars)} bars, step ${String(wfConfig.step_bars)}, purge ${String(wfConfig.purge_bars)}.` : ""}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "16px", flexWrap: "wrap" }}>
                    {[
                      ["Profitable", `${foldStats.positive}/${foldStats.total}`],
                      ["Median", `${foldStats.median >= 0 ? "+" : ""}${foldStats.median.toFixed(1)}%`],
                      ["Best", `+${foldStats.best.toFixed(1)}%`],
                      ["Worst", `${foldStats.worst.toFixed(1)}%`],
                    ].map(([k, v]) => (
                      <div key={k} style={{ textAlign: "right" }}>
                        <div style={{ fontSize: "10px", fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase", color: colors.textFaint }}>{k}</div>
                        <div style={{ fontSize: "13.5px", fontWeight: 700, color: String(v).startsWith("-") ? colors.loss : colors.text, marginTop: "2px" }}>{v}</div>
                      </div>
                    ))}
                  </div>
                </div>
                {(() => {
                  const maxAbs = Math.max(1, ...foldRows.map((f) => Math.abs(f.ret)));
                  return (
                    <div style={{ display: "flex", alignItems: "stretch", gap: "6px", height: "130px", marginBottom: "10px" }}>
                      {foldRows.map((f) => {
                        const up = f.ret >= 0;
                        const h = (Math.abs(f.ret) / maxAbs) * 50;
                        return (
                          <div
                            key={f.fold}
                            title={`Fold ${f.fold}\ntest ${f.testStart} → ${f.testEnd}\ntrained ${f.trainStart} → ${f.trainEnd}\nreturn ${f.ret >= 0 ? "+" : ""}${f.ret.toFixed(2)}% · sharpe ${f.sharpe.toFixed(2)} · maxDD ${f.dd.toFixed(1)}% · hit ${f.hit.toFixed(0)}%`}
                            style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", alignItems: "center", cursor: "default" }}
                          >
                            <div style={{ flex: 1, width: "100%", display: "flex", flexDirection: "column", justifyContent: "flex-end", alignItems: "center" }}>
                              {up ? <div style={{ width: "72%", height: `${h}%`, background: colors.gain, borderRadius: "3px 3px 0 0", minHeight: "2px" }} /> : null}
                            </div>
                            <div style={css(`width:100%; height:1px; background:${colors.border};`)} />
                            <div style={{ flex: 1, width: "100%", display: "flex", flexDirection: "column", justifyContent: "flex-start", alignItems: "center" }}>
                              {!up ? <div style={{ width: "72%", height: `${h}%`, background: colors.loss, borderRadius: "0 0 3px 3px", minHeight: "2px" }} /> : null}
                            </div>
                            <div style={{ fontSize: "9.5px", color: colors.textFaint, marginTop: "3px", fontFamily: "'JetBrains Mono',monospace" }}>{f.fold}</div>
                          </div>
                        );
                      })}
                    </div>
                  );
                })()}
                <div style={{ overflowX: "auto" }}>
                  <div style={css(tableHeaderRowStyle)}>
                    <div style={{ flex: 0.6 }}>Fold</div>
                    <div style={{ flex: 1.6 }}>Test window (unseen)</div>
                    <div style={{ flex: 1, textAlign: "right" }}>Return</div>
                    <div style={{ flex: 0.9, textAlign: "right" }}>Sharpe</div>
                    <div style={{ flex: 1, textAlign: "right" }}>Max DD</div>
                    <div style={{ flex: 0.9, textAlign: "right" }}>Hit rate</div>
                  </div>
                  {foldRows.map((f) => (
                    <div key={`row-${f.fold}`} style={css(tableRowStyle)}>
                      <div style={{ flex: 0.6, fontSize: "12.5px", fontWeight: 700, color: colors.text }}>{f.fold}</div>
                      <div style={{ flex: 1.6, fontSize: "12px", color: colors.textFaint, fontFamily: "'JetBrains Mono',monospace" }}>{f.testStart} → {f.testEnd}</div>
                      <div style={{ flex: 1, fontSize: "12.5px", fontWeight: 600, textAlign: "right", color: f.ret >= 0 ? colors.gain : colors.loss }}>{f.ret >= 0 ? "+" : ""}{f.ret.toFixed(2)}%</div>
                      <div style={{ flex: 0.9, fontSize: "12.5px", textAlign: "right", color: colors.text }}>{f.sharpe.toFixed(2)}</div>
                      <div style={{ flex: 1, fontSize: "12.5px", textAlign: "right", color: colors.textFaint }}>{f.dd.toFixed(1)}%</div>
                      <div style={{ flex: 0.9, fontSize: "12.5px", textAlign: "right", color: colors.textFaint }}>{f.hit.toFixed(0)}%</div>
                    </div>
                  ))}
                </div>
                {totalFolds > foldRows.length ? (
                  <div style={{ fontSize: "11px", color: colors.textFaint, marginTop: "10px" }}>Showing the most recent {foldRows.length} of {totalFolds} folds reported by the engine.</div>
                ) : null}
              </div>
            ) : null}

            {/* EXECUTION, COSTS & BENCHMARK */}
            {execStats ? (
              <div style={css(`${panelStyle} margin-top:14px;`)}>
                <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text, marginBottom: "2px" }}>Execution &amp; costs</div>
                <div style={{ fontSize: "11.5px", color: colors.textFaint, marginBottom: "12px" }}>All returns above are net of the modelled costs below — turnover is what you pay for.</div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(132px, 1fr))", gap: "14px" }}>
                  {([
                    ["Closed trades", execStats.trades != null ? formatNumber(execStats.trades, 0) : "—", execStats.fills != null ? `${formatNumber(execStats.fills, 0)} fills` : ""],
                    ["Total turnover", execStats.turnover != null ? `${execStats.turnover.toFixed(2)}×` : "—", execStats.avgTurnover != null ? `${(execStats.avgTurnover * 100).toFixed(2)}% / bar` : ""],
                    ["Time in market", execStats.exposure != null ? formatPercent(execStats.exposure) : "—", "exposure"],
                    ["vs benchmark", execStats.benchDelta != null ? `${execStats.benchDelta >= 0 ? "+" : ""}${formatPercent(execStats.benchDelta)}` : "—", "excess return"],
                    ["Calmar", execStats.calmar != null ? execStats.calmar.toFixed(2) : "—", "return / max DD"],
                    ["Sortino", execStats.sortino != null ? execStats.sortino.toFixed(2) : "—", "downside-adjusted"],
                    ["Alpha", execStats.alpha != null ? formatPercent(execStats.alpha) : "—", execStats.beta != null ? `beta ${execStats.beta.toFixed(2)}` : ""],
                  ] as Array<[string, string, string]>).map(([k, v, sub]) => (
                    <div key={k}>
                      <div style={{ fontSize: "10.5px", fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase", color: colors.textFaint }}>{k}</div>
                      <div style={{ fontFamily: grotesk, fontSize: "17px", fontWeight: 700, color: v.startsWith("-") ? colors.loss : colors.text, marginTop: "4px" }}>{v}</div>
                      {sub ? <div style={{ fontSize: "11px", color: colors.textFaint, marginTop: "1px" }}>{sub}</div> : null}
                    </div>
                  ))}
                </div>
                {costModel ? (
                  <div style={css(`border-top:1px solid ${colors.border}; margin-top:14px; padding-top:12px;`)}>
                    <div style={{ fontSize: "10.5px", fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase", color: colors.textFaint, marginBottom: "8px" }}>Cost model applied</div>
                    <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                      {([
                        ["Commission", costModel.commission_bps, "bps"],
                        ["Spread", costModel.spread_bps, "bps"],
                        ["Slippage", costModel.slippage_bps, "bps"],
                        ["Market impact", costModel.market_impact_bps, "bps"],
                        ["Borrow", costModel.borrow_bps_annual, "bps/yr"],
                      ] as Array<[string, unknown, string]>)
                        .filter(([, v]) => typeof v === "number")
                        .map(([k, v, unit]) => (
                          <div key={k} style={css(`font-size:11.5px; color:${colors.textFaint}; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:8px; padding:5px 10px;`)}>
                            {k} <b style={{ color: colors.text }}>{String(v)}</b> {unit}
                          </div>
                        ))}
                      {costModel.execution_mode ? (
                        <div style={css(`font-size:11.5px; color:${colors.textFaint}; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:8px; padding:5px 10px;`)}>
                          Fill <b style={{ color: colors.text }}>{String(costModel.execution_mode).replace(/_/g, " ")}</b>
                          {costModel.delay_bars != null ? ` · ${String(costModel.delay_bars)} bar delay` : ""}
                        </div>
                      ) : null}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}

            {/* Auto-generated analysis */}
            {btNarrative.length ? (
              <div style={css(`${panelStyle} margin-top:14px;`)}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "10px" }}>
                  <Icon html={ICONS.sparkle} style={{ width: "16px", height: "16px", color: colors.accent }} />
                  <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text }}>Analysis</div>
                  <div style={{ fontSize: "11px", color: colors.textFaint }}>· auto-generated from your results</div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "9px" }}>
                  {btNarrative.map((p, i) => (
                    <div key={i} style={{ fontSize: "13px", color: i === 0 ? colors.text : colors.textFaint, lineHeight: 1.55, fontWeight: i === 0 ? 600 : 400 }}>{p}</div>
                  ))}
                </div>
                {btDecision?.checks?.length ? (
                  <div style={css(`margin-top:12px; border-top:1px solid ${colors.border}; padding-top:12px;`)}>
                    <div style={{ fontSize: "12px", fontWeight: 600, color: colors.textFaint, marginBottom: "8px" }}>
                      Validation checks — passed {btDecision.passed_checks} of {btDecision.total_checks}
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                      {btDecision.checks.map((c) => (
                        <div key={c.name} style={{ display: "flex", alignItems: "flex-start", gap: "8px", fontSize: "12px", lineHeight: 1.45 }}>
                          <span style={{ color: c.passed ? colors.gain : colors.loss, fontWeight: 700, flexShrink: 0 }}>{c.passed ? "✓" : "✕"}</span>
                          <span style={{ color: colors.textFaint }}><b style={{ color: colors.text }}>{c.name}:</b> {c.message}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}
          </>
        ) : null}

        {/* PAPER TRADING */}
        {screen === "activity" ? (
          paperStrategies.length === 0 ? (
            <div style={css(`display:flex; flex-direction:column; align-items:flex-start; background:${colors.surface}; border:1px dashed ${colors.border}; border-radius:14px; padding:28px;`)}>
              <div style={{ fontFamily: grotesk, fontSize: "18px", fontWeight: 700, color: colors.text }}>No paper agents yet</div>
              {completedBtJobs.length === 0 ? (
                <>
                  <div style={{ fontSize: "13px", color: colors.textFaint, marginTop: "6px", maxWidth: "480px", lineHeight: 1.5 }}>Paper deployment is gated behind a validated backtest — run one first so there's an evidenced basis for what gets deployed.</div>
                  <button style={css(`${newAgentBtnStyle} margin-top:14px;`)} onClick={() => navigateTo("layers")}>
                    <Icon html={ICONS.plus} style={{ width: "14px", height: "14px" }} /> Browse Strategy Library
                  </button>
                </>
              ) : (
                <>
                  <div style={{ fontSize: "13px", color: colors.textFaint, marginTop: "6px", maxWidth: "480px", lineHeight: 1.5 }}>Pick a validated backtest to deploy as a fake-money paper agent — its exact strategy, universe, and timeframe carry over.</div>
                  <select
                    value={deployJobId ?? ""}
                    onChange={(e) => setDeployJobId(e.target.value)}
                    style={css(`${selectStyle} margin-top:12px; max-width:420px;`)}
                  >
                    {completedBtJobs.map((j) => {
                      const req = j.request as { pipeline?: string; symbols?: string[] };
                      const s = (j.result?.summary || {}) as Record<string, unknown>;
                      const ret = Number(s.total_return ?? 0) * 100;
                      const verdict = j.result?.decision?.headline ? ` · ${j.result.decision.headline}` : "";
                      return (
                        <option key={j.id} value={j.id}>
                          {strategyDisplayName(req.pipeline)} · {(req.symbols ?? []).join(", ") || "—"} · {ret >= 0 ? "+" : ""}{ret.toFixed(1)}%{verdict}
                        </option>
                      );
                    })}
                  </select>
                  <div style={{ display: "flex", gap: "10px", marginTop: "14px", flexWrap: "wrap" }}>
                    <button style={css(`${newAgentBtnStyle} ${deployRunning || !deployJobId ? "opacity:0.6; cursor:default;" : ""}`)} onClick={() => { if (!deployRunning && deployJobId) void deployPaper(); }}>
                      <Icon html={ICONS.plus} style={{ width: "14px", height: "14px" }} /> {deployRunning ? "Deploying…" : "Deploy paper agents"}
                    </button>
                    <button style={css(`background:transparent; border:1px solid ${colors.border}; color:${colors.text}; border-radius:10px; padding:0 16px; height:36px; font-size:13px; font-weight:600; font-family:${font}; cursor:pointer;`)} onClick={() => navigateTo("layers")}>Browse Strategy Library</button>
                  </div>
                </>
              )}
              {deployRunning ? <div style={{ fontSize: "12px", color: colors.textFaint, marginTop: "10px" }}>Deploying the selected strategy…</div> : null}
              {deployError ? <div style={{ fontSize: "12px", color: colors.loss, marginTop: "10px" }}>{deployError}</div> : null}
            </div>
          ) : (
          <>
            <div style={css(`${panelStyle} margin-bottom:14px;`)}>
              <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: "4px" }}>
                <div>
                  <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text }}>{strategyDisplayName(focusAgent.pipeline)} · {focusAgent.name}</div>
                  <div style={{ fontSize: "12.5px", color: colors.textFaint, marginTop: "2px" }}>{formatCurrency(focusAgent.equity)} · {focusAgent.position_count} open positions</div>
                </div>
                <div style={css(focusAgent.return_since_inception >= 0 ? pillGain : pillLoss)}>{focusAgent.return_since_inception >= 0 ? "+" : ""}{formatPercent(focusAgent.return_since_inception)}</div>
              </div>
              {hasPaHistory ? (
                <div style={{ position: "relative" }}>
                <svg
                  viewBox="0 0 900 150"
                  style={{ width: "100%", height: "150px", marginTop: "8px", display: "block", cursor: "crosshair" }}
                  preserveAspectRatio="none"
                  onMouseMove={(e) => setPaHoverIdx(hoverIndexFromEvent(e, paPts.length))}
                  onMouseLeave={() => setPaHoverIdx(null)}
                >
                  <defs>
                    <linearGradient id="apolloPaFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={colors.gain} stopOpacity={0.22} />
                      <stop offset="100%" stopColor={colors.gain} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <path d={paArea} fill="url(#apolloPaFill)" stroke="none" />
                  <path d={paPath} fill="none" stroke={colors.gain} strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" />
                  {paHoverIdx != null && paHoverIdx < paPts.length ? (() => {
                    const hx = (paHoverIdx / (paPts.length - 1 || 1)) * 900;
                    const hy = 150 - ((paPts[paHoverIdx] - paMin) / ((paMax - paMin) || 1)) * 150;
                    return (
                      <g pointerEvents="none">
                        <line x1={hx} x2={hx} y1={0} y2={150} stroke={colors.text} strokeWidth={1} strokeDasharray="3,3" opacity={0.5} />
                        <circle cx={hx} cy={hy} r={3.4} fill={colors.gain} stroke={colors.surfaceRaised} strokeWidth={1.4} />
                      </g>
                    );
                  })() : null}
                </svg>
                {paHoverIdx != null && paHoverIdx < paPts.length ? (() => {
                  const pct = (paHoverIdx / (paPts.length - 1 || 1)) * 100;
                  const ts = String(paHist[paHoverIdx]?.timestamp ?? "").slice(0, 10);
                  return (
                    <div style={{ position: "absolute", top: "10px", left: `${pct}%`, transform: pct > 55 ? "translateX(calc(-100% - 12px))" : "translateX(12px)", pointerEvents: "none", zIndex: 5, background: colors.surfaceRaised, border: `1px solid ${colors.border}`, borderRadius: "9px", padding: "8px 11px", boxShadow: "0 8px 22px oklch(20% 0.01 250 / 0.25)", whiteSpace: "nowrap" }}>
                      {ts ? <div style={{ fontSize: "11px", fontWeight: 700, color: colors.text, fontFamily: "'JetBrains Mono',monospace" }}>{ts}</div> : null}
                      <div style={{ fontSize: "11.5px", color: colors.gain, marginTop: "3px", fontWeight: 600 }}>Equity {formatCurrency(paPts[paHoverIdx])}</div>
                    </div>
                  );
                })() : null}
                </div>
              ) : (
                <div style={{ fontSize: "12.5px", color: colors.textFaint, marginTop: "14px" }}>Not enough history yet to chart an equity curve — check back after this agent has run for a few sessions.</div>
              )}
            </div>
            <div style={css(`${rowStyle} align-items:stretch;`)}>
              <div style={css(`${panelStyle} flex:1;`)}>
                <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text, marginBottom: "12px" }}>Holdings</div>
                {Object.keys(focusAgent.positions || {}).length === 0 ? (
                  <div style={{ fontSize: "12.5px", color: colors.textFaint }}>No open positions.</div>
                ) : (
                  <>
                    <div style={css(tableHeaderRowStyle)}>
                      <div style={{ flex: 1 }}>Ticker</div>
                      <div style={{ flex: 1, textAlign: "right" }}>Shares</div>
                      <div style={{ flex: 1, textAlign: "right" }}>Target wt.</div>
                    </div>
                    {Object.entries(focusAgent.positions).map(([tk, sh]) => (
                      <div key={tk} style={css(tableRowStyle)}>
                        <div style={{ flex: 1, fontSize: "13px", fontWeight: 700, color: colors.text }}>{tk}</div>
                        <div style={{ flex: 1, fontSize: "13px", textAlign: "right", color: colors.text }}>{formatNumber(sh, 2)}</div>
                        <div style={{ flex: 1, fontSize: "13px", textAlign: "right", color: colors.textFaint }}>{formatPercent(focusAgent.target_weights?.[tk] ?? 0)}</div>
                      </div>
                    ))}
                  </>
                )}
              </div>

              <div style={css(`${panelStyle} flex:1;`)}>
                <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text, marginBottom: "12px" }}>Latest Orders</div>
                {orderDefs.length === 0 ? (
                  <div style={{ fontSize: "12.5px", color: colors.textFaint }}>No orders placed yet.</div>
                ) : null}
                {orderDefs.map((o, i) => (
                  <div key={`${o.desc}-${i}`} style={css(`display:flex; align-items:center; gap:14px; padding:11px 6px; border-bottom:1px solid ${colors.border};`)}>
                    <div style={{ width: "52px", fontSize: "12px", fontWeight: 700, color: o.gain ? colors.gain : colors.loss }}>{o.side}</div>
                    <div style={{ flex: 1, fontSize: "13px", color: colors.text }}>{o.desc}</div>
                    <div style={{ fontSize: "12px", color: colors.textFaint }}>{o.time}</div>
                  </div>
                ))}
              </div>
            </div>
          </>
          )
        ) : null}

        {/* RESEARCH */}
        {screen === "brain" ? (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "16px", flexWrap: "wrap" }}>
              <input
                value={researchTicker}
                onChange={(e) => setResearchTicker(e.target.value)}
                placeholder="Ticker e.g. NVDA"
                style={css(`font-family:${font}; font-size:13px; font-weight:600; color:${colors.text}; background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:9px; padding:9px 12px; width:170px; text-transform:uppercase;`)}
              />
              <button style={css(`${newAgentBtnStyle} ${researchRunning ? "opacity:0.6; cursor:default;" : ""}`)} onClick={() => { if (!researchRunning) void runResearch(); }}>
                <Icon html={ICONS.sparkle} style={{ width: "14px", height: "14px" }} /> {researchRunning ? "Analyzing…" : "Generate research synthesis"}
              </button>
              {researchRunning ? <div style={{ fontSize: "12px", color: colors.textFaint }}>Running multi-analyst debate…</div> : null}
              {researchError ? <div style={{ fontSize: "12px", color: colors.loss }}>{researchError}</div> : null}
            </div>
            <div style={css(`${strategyGridStyle} margin-bottom:14px;`)}>
              {analystDefsFull.map((a) => (
                <div key={a.title} style={css(panelStyle)}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
                    <Icon html={ICONS[a.icon]} style={css(`width:26px; height:26px; border-radius:8px; background:oklch(from ${a.color} l c h / 0.14); color:${a.color}; align-items:center; justify-content:center; padding:5px; flex-shrink:0;`)} />
                    <div style={{ fontSize: "14px", fontWeight: 600, color: colors.text }}>{a.title}</div>
                  </div>
                  <div style={{ fontSize: "12.5px", color: colors.textFaint, lineHeight: 1.5 }}>{a.text}</div>
                </div>
              ))}
            </div>
            <div style={css(`${panelStyle} display:flex; align-items:center; justify-content:space-between;`)}>
              <div>
                <div style={{ fontSize: "12px", color: colors.textFaint, marginBottom: "4px" }}>Evidence balance{rpt ? ` · ${rpt.ticker}` : ""}</div>
                <div style={css(`${verdictPillStyle} font-size:14px;`)}>{researchVerdict}{rpt ? ` · ${Math.round(rpt.confidence || 0)}% conf` : ""}</div>
              </div>
              <div style={{ fontSize: "11px", color: colors.textFaint, maxWidth: "280px", textAlign: "right", lineHeight: 1.4 }}>For research and educational purposes only. Not financial advice.</div>
            </div>

            {/* SIGNAL EVIDENCE — every signal with its raw supporting data */}
            {allSignals.length ? (
              <div style={css(`${panelStyle} margin-top:14px;`)}>
                <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text, marginBottom: "2px" }}>Signal evidence</div>
                <div style={{ fontSize: "11.5px", color: colors.textFaint, marginBottom: "12px" }}>Each signal with the raw values behind it and where that data came from — nothing here is asserted without a source.</div>
                <div style={{ display: "flex", flexDirection: "column", gap: "9px" }}>
                  {allSignals.map((s, i) => (
                    <div key={`${s.label}-${i}`} style={css(`border:1px solid ${colors.border}; border-radius:10px; padding:11px 13px;`)}>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                        <span style={css(`font-size:10.5px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; padding:2px 8px; border-radius:20px; background:oklch(from ${signalTone(s.direction)} l c h / 0.16); color:${signalTone(s.direction)};`)}>{s.direction}</span>
                        <span style={{ fontSize: "13px", fontWeight: 600, color: colors.text }}>{String(s.label).replace(/_/g, " ")}</span>
                        <span style={{ fontSize: "11px", color: colors.textFaint }}>{s.group}</span>
                        {typeof s.strength === "number" ? (
                          <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "6px" }}>
                            <span style={css(`width:56px; height:4px; border-radius:3px; background:${colors.border}; overflow:hidden; display:block;`)}>
                              <span style={css(`display:block; height:100%; width:${Math.min(100, Math.max(0, s.strength))}%; background:${signalTone(s.direction)};`)} />
                            </span>
                            <span style={{ fontSize: "11px", color: colors.textFaint, fontFamily: "'JetBrains Mono',monospace" }}>{Math.round(s.strength)}</span>
                          </span>
                        ) : null}
                      </div>
                      {s.rationale ? <div style={{ fontSize: "12.5px", color: colors.textFaint, lineHeight: 1.5, marginTop: "7px" }}>{s.rationale}</div> : null}
                      {(s.evidence?.length ?? 0) > 0 ? (
                        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginTop: "8px" }}>
                          {s.evidence.map((e) => (
                            <span key={e} style={css(`font-size:10.5px; font-family:'JetBrains Mono',monospace; color:${colors.textFaint}; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:6px; padding:3px 7px;`)}>{e}</span>
                          ))}
                        </div>
                      ) : null}
                      {(s.provenance?.length ?? 0) > 0 ? (
                        <div style={{ fontSize: "10.5px", color: colors.textFaint, marginTop: "7px" }}>Source: {s.provenance.join(", ").replace(/_/g, " ")}</div>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {/* DATA PROVENANCE: freshness, confidence, gaps, sources, analysts */}
            {rpt ? (
              <div style={css(`${rowStyle} align-items:stretch; margin-top:14px;`)}>
                <div style={css(`${panelStyle} flex:1;`)}>
                  <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text, marginBottom: "12px" }}>Data freshness &amp; confidence</div>
                  {freshnessRows.length ? (
                    <>
                      <div style={css(tableHeaderRowStyle)}>
                        <div style={{ flex: 1.3 }}>Input</div>
                        <div style={{ flex: 1 }}>As of</div>
                        <div style={{ flex: 1, textAlign: "right" }}>Confidence</div>
                      </div>
                      {freshnessRows.map((r) => {
                        const tone = r.conf == null ? colors.textFaint : r.conf >= 0.6 ? colors.gain : r.conf >= 0.3 ? "oklch(70% 0.15 80)" : colors.loss;
                        return (
                          <div key={r.key} style={css(tableRowStyle)}>
                            <div style={{ flex: 1.3, fontSize: "12.5px", color: colors.text, textTransform: "capitalize" }}>{r.key}</div>
                            <div style={{ flex: 1, fontSize: "12px", color: colors.textFaint, fontFamily: "'JetBrains Mono',monospace" }}>{r.date ?? "not available"}</div>
                            <div style={{ flex: 1, fontSize: "12.5px", fontWeight: 600, textAlign: "right", color: tone }}>{r.conf == null ? "—" : `${Math.round(r.conf * 100)}%`}</div>
                          </div>
                        );
                      })}
                    </>
                  ) : (
                    <div style={{ fontSize: "12.5px", color: colors.textFaint }}>No freshness metadata reported for this run.</div>
                  )}
                  {(rpt.missing_data_indicators?.length ?? 0) > 0 ? (
                    <div style={css(`border-top:1px solid ${colors.border}; margin-top:12px; padding-top:12px;`)}>
                      <div style={{ fontSize: "10.5px", fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase", color: colors.loss, marginBottom: "7px" }}>Missing inputs</div>
                      <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                        {(rpt.missing_data_indicators ?? []).map((m) => (
                          <span key={m} style={css(`font-size:11px; color:${colors.loss}; background:oklch(from ${colors.loss} l c h / 0.1); border-radius:6px; padding:3px 8px; text-transform:capitalize;`)}>{m.replace(/_/g, " ")}</span>
                        ))}
                      </div>
                      <div style={{ fontSize: "11px", color: colors.textFaint, marginTop: "8px", lineHeight: 1.45 }}>
                        Treat the verdict as partial — these inputs were unavailable, so the committee could not weigh them.
                      </div>
                    </div>
                  ) : null}
                  {(rpt.data_quality_notes?.length ?? 0) > 0 ? (
                    <div style={css(`border-top:1px solid ${colors.border}; margin-top:12px; padding-top:12px;`)}>
                      <div style={{ fontSize: "10.5px", fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase", color: colors.textFaint, marginBottom: "6px" }}>Caveats</div>
                      {rpt.data_quality_notes.slice(0, 4).map((n) => (
                        <div key={n} style={{ fontSize: "11.5px", color: colors.textFaint, lineHeight: 1.45, marginBottom: "4px" }}>• {n}</div>
                      ))}
                    </div>
                  ) : null}
                </div>

                <div style={css(`${panelStyle} flex:1;`)}>
                  <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text, marginBottom: "12px" }}>Sources &amp; analysts</div>
                  {agentOutputs.length ? (
                    <div style={{ marginBottom: "14px" }}>
                      <div style={{ fontSize: "10.5px", fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase", color: colors.textFaint, marginBottom: "7px" }}>Committee ({agentOutputs.length})</div>
                      <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                        {agentOutputs.map((a) => (
                          <span key={a.agent_name} title={a.summary} style={css(`font-size:11px; color:${colors.textFaint}; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:6px; padding:4px 8px;`)}>
                            {a.display_name}{typeof a.confidence === "number" ? <b style={{ color: colors.text }}> {Math.round(a.confidence)}%</b> : null}
                            {(a.warnings?.length ?? 0) > 0 ? <span style={{ color: colors.loss }}> ⚠</span> : null}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  <div style={{ fontSize: "10.5px", fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase", color: colors.textFaint, marginBottom: "7px" }}>
                    Referenced sources{sourceRefs.length ? ` (${sourceRefs.length})` : ""}
                  </div>
                  {sourceRefs.length === 0 ? (
                    <div style={{ fontSize: "12.5px", color: colors.textFaint }}>No external sources were cited for this run.</div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "8px", maxHeight: "260px", overflowY: "auto" }}>
                      {sourceRefs.map((s) => (
                        <div key={`${s.id}-${s.title}`} style={{ display: "flex", gap: "8px", alignItems: "flex-start" }}>
                          <span
                            title={s.verified ? "Verified source" : "Unverified source"}
                            style={css(`flex-shrink:0; margin-top:4px; width:7px; height:7px; border-radius:50%; background:${s.verified ? colors.gain : colors.textFaint};`)}
                          />
                          <div style={{ minWidth: 0 }}>
                            {s.url ? (
                              <a href={s.url} target="_blank" rel="noreferrer" style={{ fontSize: "12px", color: colors.accent, textDecoration: "none", lineHeight: 1.4 }}>{s.title}</a>
                            ) : (
                              <div style={{ fontSize: "12px", color: colors.text, lineHeight: 1.4 }}>{s.title}</div>
                            )}
                            <div style={{ fontSize: "10.5px", color: colors.textFaint, marginTop: "2px" }}>
                              {s.provider} · {s.source}
                              {s.observed_at_utc ? ` · ${String(s.observed_at_utc).slice(0, 10)}` : ""}
                              {typeof s.confidence === "number" ? ` · ${Math.round(s.confidence * 100)}% conf` : ""}
                              {s.verified ? "" : " · unverified"}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : null}
          </>
        ) : null}

        {/* NEWS */}
        {screen === "news" ? (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "16px", flexWrap: "wrap" }}>
              <input
                value={sentimentTicker}
                onChange={(e) => setSentimentTicker(e.target.value)}
                placeholder="Tickers e.g. AAPL MSFT NVDA"
                style={css(`font-family:${font}; font-size:13px; font-weight:600; color:${colors.text}; background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:9px; padding:9px 12px; width:260px;`)}
              />
              <button style={css(`${newAgentBtnStyle} ${scanRunning ? "opacity:0.6; cursor:default;" : ""}`)} onClick={() => { if (!scanRunning) void runSentimentScan(); }}>
                <Icon html={ICONS.news} style={{ width: "14px", height: "14px" }} /> {scanRunning ? "Scanning…" : "Run sentiment scan"}
              </button>
              {scanRunning ? <div style={{ fontSize: "12px", color: colors.textFaint }}>Fetching &amp; scoring headlines…</div> : null}
              {scanError ? <div style={{ fontSize: "12px", color: colors.loss }}>{scanError}</div> : null}
            </div>

            {/*
              The matrix/headlines below reflect whatever dataset is currently
              loaded on the server — which may be from a previous scan (by this
              user or a teammate in the same org) and can silently disagree with
              whatever is typed in the ticker box above until "Run" is clicked.
              Surface that explicitly instead of letting the two look in sync.
            */}
            {tickerMatrix.length > 0 ? (
              <div style={css(`display:flex; align-items:center; gap:7px; font-size:12px; font-weight:600; color:${colors.textFaint}; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:9px; padding:8px 12px; margin-bottom:14px; width:fit-content;`)}>
                <Icon html={ICONS.shield} style={{ width: "13px", height: "13px", color: colors.textFaint }} />
                Showing last scanned results for {tickerMatrix.map((r) => r.ticker).join(", ")}
                {typeof sentiment?.metadata.start === "string" && typeof sentiment?.metadata.end === "string" ? ` · ${sentiment.metadata.start} → ${sentiment.metadata.end}` : ""}
                {" "}— may not match the ticker box until you run a new scan.
              </div>
            ) : null}

            {newsNarrative ? (
              <div style={css(`${panelStyle} margin-bottom:14px;`)}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
                  <Icon html={ICONS.sparkle} style={{ width: "15px", height: "15px", color: colors.accent }} />
                  <div style={{ fontSize: "14px", fontWeight: 600, color: colors.text }}>Overall read</div>
                  <div style={{ fontSize: "11px", color: colors.textFaint }}>· auto-generated from scored coverage</div>
                </div>
                <div style={{ fontSize: "13px", color: colors.textFaint, lineHeight: 1.55 }}>{newsNarrative}</div>
              </div>
            ) : null}

            <div style={css(`${panelStyle} margin-bottom:14px;`)}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "10px", marginBottom: "12px", flexWrap: "wrap" }}>
                  <div>
                    <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text }}>News Matrix</div>
                    <div style={{ fontSize: "11.5px", color: colors.textFaint, marginTop: "2px" }}>Daily sentiment by ticker · color = score · dot = article volume · hover a cell for the day</div>
                  </div>
                  {heatmap ? (
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "11px", color: colors.textFaint }}>
                      <span>−1</span>
                      <span style={css(`width:14px; height:12px; border-radius:2px; background:${heatColor(-1)};`)} />
                      <span style={css(`width:14px; height:12px; border-radius:2px; background:${colors.surfaceRaised};`)} />
                      <span style={css(`width:14px; height:12px; border-radius:2px; background:${heatColor(1)};`)} />
                      <span>+1</span>
                    </div>
                  </div>
                  ) : null}
                </div>
                {!heatmap ? (
                  <div style={{ fontSize: "12.5px", color: colors.textFaint, lineHeight: 1.5, padding: "4px 0" }}>
                    {tickerMatrix.length
                      ? `No daily points in the last ${newsWindow === "all" ? "range" : newsWindow + " days"} — widen the window above.`
                      : "No sentiment scanned in this workspace yet. Use “Run sentiment scan” above to build the matrix."}
                  </div>
                ) : (
                <div style={{ overflowX: "auto", paddingBottom: "6px" }}>
                  {(() => {
                    const { tickers, dates, cell, maxArts } = heatmap;
                    const step = Math.max(1, Math.ceil(dates.length / 10));
                    const g: React.ReactNode[] = [];
                    g.push(<div key="corner" />);
                    dates.forEach((d, i) => g.push(
                      <div key={`h-${d}`} style={{ fontSize: "9px", color: colors.textFaint, fontFamily: "'JetBrains Mono',monospace", height: "13px", whiteSpace: "nowrap", overflow: "visible" }}>{i % step === 0 ? d.slice(5) : ""}</div>,
                    ));
                    tickers.forEach((tk) => {
                      g.push(<div key={`l-${tk}`} style={{ fontSize: "12px", fontWeight: 700, color: colors.text, paddingRight: "8px", whiteSpace: "nowrap", display: "flex", alignItems: "center" }}>{tk}</div>);
                      dates.forEach((d) => {
                        const cd = cell.get(`${tk}|${d}`);
                        if (!cd) { g.push(<div key={`${tk}-${d}`} style={{ height: "20px", borderRadius: "3px", background: colors.bg }} />); return; }
                        const dot = Math.max(0, Math.round((cd.arts / maxArts) * 7));
                        g.push(
                          <div
                            key={`${tk}-${d}`}
                            title={`${tk} · ${d}\nsentiment ${cd.s >= 0 ? "+" : ""}${cd.s.toFixed(2)} · ${Math.round(cd.arts)} articles · ${Math.round(cd.conf * 100)}% conf`}
                            style={{ height: "20px", borderRadius: "3px", background: heatColor(cd.s), display: "flex", alignItems: "center", justifyContent: "center", boxShadow: cd.conf > 0.6 ? `inset 0 0 0 1px oklch(from ${colors.text} l c h / 0.25)` : "none" }}
                          >
                            {dot > 0 ? <span style={{ width: `${dot}px`, height: `${dot}px`, borderRadius: "50%", background: `oklch(from ${cd.s >= 0 ? colors.gain : colors.loss} l c h / 0.9)` }} /> : null}
                          </div>,
                        );
                      });
                    });
                    return (
                      <div style={{ display: "grid", gridTemplateColumns: `64px repeat(${dates.length}, 20px)`, gap: "2px", alignItems: "center", width: "max-content" }}>
                        {g}
                      </div>
                    );
                  })()}
                </div>
                )}
              </div>

            <div style={css(`${rowStyle} align-items:stretch;`)}>
              <div style={css(`${panelStyle} flex:1;`)}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "10px", marginBottom: "12px", flexWrap: "wrap" }}>
                  <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text }}>Sentiment Matrix</div>
                  <div style={{ display: "flex", gap: "3px", background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: "8px", padding: "2px" }}>
                    {(["7", "30", "90", "all"] as const).map((w) => (
                      <button key={w} onClick={() => setNewsWindow(w)} style={css(`padding:4px 10px; border:none; border-radius:6px; font-size:11.5px; font-weight:600; font-family:${font}; cursor:pointer; background:${newsWindow === w ? colors.accent : "transparent"}; color:${newsWindow === w ? "white" : colors.textFaint};`)}>
                        {w === "all" ? "All" : `${w}D`}
                      </button>
                    ))}
                  </div>
                </div>
                {newsMatrix.length === 0 ? (
                  <div style={{ fontSize: "12.5px", color: colors.textFaint }}>
                    {tickerMatrix.length ? `No scored articles in the last ${newsWindow} days — widen the window or run a fresh scan.` : "Run a scan to score per-ticker sentiment."}
                  </div>
                ) : (
                  <>
                    <div style={css(tableHeaderRowStyle)}>
                      <div style={{ flex: 1 }}>Ticker</div>
                      <div style={{ flex: 1, textAlign: "right" }}>Articles</div>
                      <div style={{ flex: 1.2, textAlign: "right" }}>Avg</div>
                      <div style={{ flex: 1, textAlign: "right" }}>Confidence</div>
                      <div style={{ flex: 1, textAlign: "right" }}>Latest</div>
                    </div>
                    {newsMatrix.map((r) => {
                      const c = r.avg_sentiment > 0.1 ? colors.gain : r.avg_sentiment < -0.1 ? colors.loss : colors.textFaint;
                      const lc = r.latest_sentiment > 0.1 ? colors.gain : r.latest_sentiment < -0.1 ? colors.loss : colors.textFaint;
                      return (
                        <div key={r.ticker} style={css(tableRowStyle)}>
                          <div style={{ flex: 1, fontSize: "13px", fontWeight: 700, color: colors.text }}>{r.ticker}</div>
                          <div style={{ flex: 1, fontSize: "13px", textAlign: "right", color: colors.textFaint }}>{Math.round(r.article_count)}</div>
                          <div style={{ flex: 1.2, fontSize: "13px", fontWeight: 600, textAlign: "right", color: c }}>{r.avg_sentiment >= 0 ? "+" : ""}{r.avg_sentiment.toFixed(2)}</div>
                          <div style={{ flex: 1, fontSize: "13px", textAlign: "right", color: colors.textFaint }}>{Math.round(r.avg_confidence * 100)}%</div>
                          <div style={{ flex: 1, fontSize: "13px", textAlign: "right", color: lc }}>{r.latest_sentiment >= 0 ? "+" : ""}{r.latest_sentiment.toFixed(2)}</div>
                        </div>
                      );
                    })}
                  </>
                )}
                {newsSources.length ? (
                  <div style={css(`border-top:1px solid ${colors.border}; margin-top:12px; padding-top:12px;`)}>
                    <div style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", letterSpacing: ".05em", color: colors.textFaint, marginBottom: "8px" }}>Coverage by source</div>
                    <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                      {newsSources.slice(0, 8).map((s) => (
                        <div key={s.source} title={s.description || s.source} style={css(`display:flex; align-items:center; gap:6px; font-size:11.5px; color:${colors.textFaint}; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:8px; padding:5px 9px;`)}>
                          <span style={{ fontWeight: 600, color: colors.text }}>{s.source_group_label || s.source}</span>
                          <span>{s.headline_count} articles</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>

              <div style={css(`${panelStyle} flex:1;`)}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "10px", marginBottom: "12px" }}>
                  <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text }}>Latest Headlines</div>
                  {newsDefs.length > 8 ? (
                    <div style={{ display: "flex", alignItems: "center", gap: "7px", fontSize: "11.5px", color: colors.textFaint }}>
                      <button disabled={safeNewsPage <= 1} onClick={() => setNewsPage((page) => Math.max(1, page - 1))} style={css(`${ghostLinkBtnStyle} width:auto; opacity:${safeNewsPage <= 1 ? 0.45 : 1};`)}>Previous</button>
                      <span>{safeNewsPage} / {newsPageCount}</span>
                      <button disabled={safeNewsPage >= newsPageCount} onClick={() => setNewsPage((page) => Math.min(newsPageCount, page + 1))} style={css(`${ghostLinkBtnStyle} width:auto; opacity:${safeNewsPage >= newsPageCount ? 0.45 : 1};`)}>Next</button>
                    </div>
                  ) : null}
                </div>
                {sentimentLoading ? <div style={{ fontSize: "12.5px", color: colors.textFaint }}>Loading headlines…</div>
                  : sentimentLoadError ? <div style={{ fontSize: "12.5px", color: colors.loss }}>Headlines unavailable: {sentimentLoadError}<button onClick={() => void reloadSentiment()} style={css(`${ghostLinkBtnStyle} width:auto;`)}>Retry</button></div>
                  : newsDefs.length ? <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>{NewsList(pagedNews, true)}</div>
                  : <div style={{ fontSize: "12.5px", color: colors.textFaint }}>No headlines were returned. Run a sentiment scan to collect current coverage.</div>}
              </div>
            </div>
          </>
        ) : null}

        {/* ACCOUNT */}
        {screen === "account" ? (
          <div style={css(`${rowStyle} margin-top:0;`)}>
            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "14px", minWidth: 0 }}>
              <div style={css(panelStyle)}>
                <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text, marginBottom: "12px" }}>Profile</div>
                {([
                  ["Name", userName],
                  ["Email", userEmail ?? "—"],
                  ["Role", userRole ? userRole.charAt(0).toUpperCase() + userRole.slice(1) : "Member"],
                  ["User ID", userId ?? "—"],
                ] as Array<[string, string]>).map(([k, v]) => (
                  <div key={k} style={css(`display:flex; justify-content:space-between; gap:12px; padding:8px 0; border-bottom:1px solid ${colors.border};`)}>
                    <span style={{ fontSize: "12.5px", color: colors.textFaint }}>{k}</span>
                    <span style={{ fontSize: "12.5px", fontWeight: 600, color: colors.text, fontFamily: k === "User ID" ? "'JetBrains Mono',monospace" : font, wordBreak: "break-all", textAlign: "right" }}>{v}</span>
                  </div>
                ))}
                <div style={{ fontSize: "11.5px", color: colors.textFaint, marginTop: "12px", lineHeight: 1.5 }}>
                  Password changes and email verification are handled in the secure console.
                </div>
                <a href="/classic" style={css(`display:inline-block; margin-top:10px; background:transparent; border:1px solid ${colors.border}; color:${colors.text}; border-radius:8px; padding:8px 14px; font-size:12.5px; font-weight:600; text-decoration:none;`)}>Open security settings ↗</a>
              </div>

              <div style={css(panelStyle)}>
                <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text, marginBottom: "4px" }}>Workspaces</div>
                <div style={{ fontSize: "11.5px", color: colors.textFaint, marginBottom: "10px" }}>All research, strategies, and paper agents are scoped to the active workspace.</div>
                {(organizations.length ? organizations : [{ id: "current", name: workspaceLabel }]).map((o) => {
                  const active = o.id === activeOrgId || organizations.length === 0;
                  return (
                    <div key={o.id} style={css(`display:flex; align-items:center; justify-content:space-between; gap:10px; padding:9px 11px; border:1px solid ${active ? colors.accent : colors.border}; border-radius:9px; margin-bottom:8px;`)}>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontSize: "13px", fontWeight: 600, color: colors.text }}>{o.name}</div>
                        <div style={{ fontSize: "10.5px", color: colors.textFaint, fontFamily: "'JetBrains Mono',monospace" }}>{o.id}</div>
                      </div>
                      {active ? (
                        <span style={css(`flex-shrink:0; font-size:10.5px; font-weight:700; padding:3px 9px; border-radius:20px; background:oklch(from ${colors.accent} l c h / 0.14); color:${colors.accent};`)}>Active</span>
                      ) : onSwitchOrg ? (
                        <button onClick={() => onSwitchOrg(o.id)} style={css(`flex-shrink:0; background:transparent; border:1px solid ${colors.border}; color:${colors.text}; border-radius:7px; padding:5px 11px; font-size:11.5px; font-weight:600; font-family:${font}; cursor:pointer;`)}>Switch</button>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>

            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "14px", minWidth: 0 }}>
              <div style={css(panelStyle)}>
                <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text, marginBottom: "12px" }}>Plan</div>
                <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                  <div style={{ fontFamily: grotesk, fontSize: "22px", fontWeight: 700, color: colors.text, textTransform: "capitalize" }}>{planName ?? "free"}</div>
                  <span style={css(`font-size:10.5px; font-weight:700; padding:3px 9px; border-radius:20px; background:oklch(from ${hasPremium ? colors.gain : colors.textFaint} l c h / 0.14); color:${hasPremium ? colors.gain : colors.textFaint};`)}>
                    {hasPremium ? "Full access" : "Limited"}{planStatus ? ` · ${planStatus}` : ""}
                  </span>
                </div>
                <div style={{ fontSize: "12.5px", color: colors.textFaint, marginTop: "10px", lineHeight: 1.55 }}>
                  {hasPremium
                    ? "Backtests, paper deployment, AI research synthesis, and sentiment scans are all available on this workspace."
                    : "Backtests, paper deployment, research synthesis, and sentiment scans need a paid plan on this workspace. Browsing, reading past runs, and the strategy designer stay available."}
                </div>
                <div style={css(`border-top:1px solid ${colors.border}; margin-top:12px; padding-top:12px;`)}>
                  <div style={{ fontSize: "10.5px", fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase", color: colors.textFaint, marginBottom: "8px" }}>Compute-gated actions</div>
                  {([
                    ["Run backtests", hasPremium],
                    ["Deploy paper agents", hasPremium],
                    ["AI research synthesis", hasPremium],
                    ["Sentiment scans", hasPremium],
                    ["Strategy designer", true],
                    ["Browse & read results", true],
                  ] as Array<[string, boolean]>).map(([k, ok]) => (
                    <div key={k} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12.5px", padding: "3px 0" }}>
                      <span style={{ color: ok ? colors.gain : colors.textFaint, fontWeight: 700 }}>{ok ? "✓" : "—"}</span>
                      <span style={{ color: ok ? colors.text : colors.textFaint }}>{k}</span>
                    </div>
                  ))}
                </div>
                {!hasPremium ? (
                  <a href="/classic" style={css(`display:inline-block; margin-top:14px; background:${colors.accent}; border:none; color:white; border-radius:8px; padding:9px 16px; font-size:13px; font-weight:600; text-decoration:none;`)}>View plans ↗</a>
                ) : null}
              </div>

              <div style={css(panelStyle)}>
                <div style={{ fontSize: "15px", fontWeight: 600, color: colors.text, marginBottom: "10px" }}>This workspace</div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: "12px" }}>
                  {([
                    ["Strategies", String(strategyCount)],
                    ["Yours", String(userStrategies.length)],
                    ["Backtests", String(btJobs.length)],
                    ["Paper agents", String(paperStrategies.length)],
                    ["Research runs", String(researchJobs.length)],
                  ] as Array<[string, string]>).map(([k, v]) => (
                    <div key={k}>
                      <div style={{ fontSize: "10.5px", fontWeight: 700, letterSpacing: ".05em", textTransform: "uppercase", color: colors.textFaint }}>{k}</div>
                      <div style={{ fontFamily: grotesk, fontSize: "18px", fontWeight: 700, color: colors.text, marginTop: "3px" }}>{v}</div>
                    </div>
                  ))}
                </div>
                <div style={css(`border-top:1px solid ${colors.border}; margin-top:14px; padding-top:12px; display:flex; gap:8px; flex-wrap:wrap;`)}>
                  <button onClick={() => { navigateTo("home"); setTourStep(0); }} style={css(`background:transparent; border:1px solid ${colors.border}; color:${colors.text}; border-radius:8px; padding:8px 13px; font-size:12.5px; font-weight:600; font-family:${font}; cursor:pointer;`)}>Replay product tour</button>
                  <button onClick={() => setTheme(dark ? "light" : "dark")} style={css(`background:transparent; border:1px solid ${colors.border}; color:${colors.text}; border-radius:8px; padding:8px 13px; font-size:12.5px; font-weight:600; font-family:${font}; cursor:pointer;`)}>Switch to {dark ? "light" : "dark"} theme</button>
                  {onLogout ? (
                    <button onClick={onLogout} style={css(`background:transparent; border:1px solid ${colors.border}; color:${colors.loss}; border-radius:8px; padding:8px 13px; font-size:12.5px; font-weight:600; font-family:${font}; cursor:pointer;`)}>Sign out</button>
                  ) : null}
                </div>
                <div style={{ fontSize: "11px", color: colors.textFaint, marginTop: "12px", lineHeight: 1.5 }}>
                  Apollo runs on simulated capital only. No broker is connected and no real orders are ever placed.
                </div>
              </div>
            </div>
          </div>
        ) : null}
      </main>

      {/* COMMAND PALETTE */}
      {searchOpen ? (() => {
        const q = searchQuery.trim();
        const ql = q.toLowerCase();
        const qUpper = q.toUpperCase();
        const isTickerish = /^[A-Z][A-Z.\-]{0,5}$/.test(qUpper);
        type PAction = { group: string; label: string; hint?: string; run: () => void };
        const actions: PAction[] = [];
        if (isTickerish) {
          actions.push(
            { group: "Ticker actions", label: `Research ${qUpper} with the AI committee`, run: () => { setResearchTicker(qUpper); navigateTo("brain"); } },
            { group: "Ticker actions", label: `Backtest ${selectedStrategy} on ${qUpper}`, run: () => { setBacktestSymbols([qUpper]); navigateTo("flask"); } },
            { group: "Ticker actions", label: `Scan news sentiment for ${qUpper}`, run: () => { setSentimentTicker(qUpper); navigateTo("news"); } },
          );
        }
        allCards
          .filter((c) => !ql || c.name.toLowerCase().includes(ql) || c.tag.toLowerCase().includes(ql))
          .slice(0, ql ? 6 : 3)
          .forEach((c) => actions.push({ group: "Strategies", label: c.name, hint: c.tag, run: () => { setSelectedStrategy(c.name); navigateTo("flask"); } }));
        navDefs
          .filter((n) => !ql || n.label.toLowerCase().includes(ql))
          .forEach((n) => actions.push({ group: "Go to", label: n.label, run: () => navigateTo(n.key) }));
        const runAction = (a: PAction) => { a.run(); setSearchOpen(false); setSearchQuery(""); };
        let lastGroup = "";
        return (
          <div onClick={() => setSearchOpen(false)} style={css("position:fixed; inset:0; z-index:70; background:oklch(15% 0.01 250 / 0.45); display:flex; justify-content:center; align-items:flex-start; padding-top:12vh;")}>
            <div onClick={(e) => e.stopPropagation()} style={css(`width:540px; max-width:calc(100vw - 40px); background:${colors.surfaceRaised}; border:1px solid ${colors.border}; border-radius:14px; overflow:hidden; box-shadow:0 24px 60px oklch(10% 0.01 250 / 0.45);`)}>
              <div style={css(`display:flex; align-items:center; gap:10px; padding:13px 16px; border-bottom:1px solid ${colors.border};`)}>
                <Icon html={ICONS.search} style={{ width: "16px", height: "16px", opacity: 0.5, flexShrink: 0 }} />
                <input
                  autoFocus
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && actions.length) runAction(actions[0]); }}
                  placeholder="Search tickers, strategies, screens…"
                  style={css(`flex:1; border:none; outline:none; background:transparent; font-family:${font}; font-size:14px; color:${colors.text};`)}
                />
                <span style={css(`font-size:10.5px; color:${colors.textFaint}; background:${colors.surface}; border:1px solid ${colors.border}; border-radius:5px; padding:1px 6px; font-family:'JetBrains Mono',monospace;`)}>esc</span>
              </div>
              <div style={{ maxHeight: "46vh", overflowY: "auto", padding: "8px" }}>
                {actions.length === 0 ? (
                  <div style={{ fontSize: "12.5px", color: colors.textFaint, padding: "14px 10px" }}>No matches. Try a ticker like AAPL, a strategy name, or a screen.</div>
                ) : (
                  actions.map((a, i) => {
                    const showGroup = a.group !== lastGroup;
                    lastGroup = a.group;
                    return (
                      <div key={`${a.group}-${a.label}`}>
                        {showGroup ? <div style={{ fontSize: "10.5px", fontWeight: 700, textTransform: "uppercase", letterSpacing: ".06em", color: colors.textFaint, padding: "8px 10px 4px 10px" }}>{a.group}</div> : null}
                        <div onClick={() => runAction(a)} style={css(`display:flex; align-items:center; gap:10px; padding:9px 10px; border-radius:9px; cursor:pointer; background:${i === 0 ? `oklch(from ${colors.accent} l c h / 0.1)` : "transparent"};`)}>
                          <span style={{ fontSize: "13px", fontWeight: 600, color: colors.text, flex: 1 }}>{a.label}</span>
                          {a.hint ? <span style={{ fontSize: "11.5px", color: colors.textFaint }}>{a.hint}</span> : null}
                          {i === 0 ? <span style={css(`font-size:10.5px; color:${colors.textFaint}; font-family:'JetBrains Mono',monospace;`)}>↵</span> : null}
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </div>
        );
      })() : null}

      {/* PRODUCT TOUR */}
      {tourStep != null && TOUR_STEPS[tourStep] ? (
        <div style={css(`position:fixed; right:26px; bottom:26px; z-index:60; width:340px; max-width:calc(100vw - 40px); background:${colors.surfaceRaised}; border:1px solid ${colors.accent}; border-radius:14px; padding:18px; box-shadow:0 18px 44px oklch(20% 0.01 250 / 0.35);`)}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
            <div style={{ fontSize: "11px", fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: colors.accent }}>Tour · {tourStep + 1} of {TOUR_STEPS.length}</div>
            <div style={{ display: "flex", gap: "4px" }}>
              {TOUR_STEPS.map((_, i) => (
                <span key={i} style={css(`width:6px; height:6px; border-radius:50%; background:${i === tourStep ? colors.accent : colors.border};`)} />
              ))}
            </div>
          </div>
          <div style={{ fontFamily: grotesk, fontSize: "16px", fontWeight: 700, color: colors.text }}>{TOUR_STEPS[tourStep].title}</div>
          <div style={{ fontSize: "12.5px", color: colors.textFaint, lineHeight: 1.55, marginTop: "6px" }}>{TOUR_STEPS[tourStep].body}</div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "14px" }}>
            <button onClick={endTour} style={css(`background:transparent; border:none; color:${colors.textFaint}; font-size:12.5px; font-weight:600; font-family:${font}; cursor:pointer; padding:0;`)}>Skip tour</button>
            <button
              onClick={() => {
                const next = tourStep + 1;
                if (next >= TOUR_STEPS.length) { endTour(); return; }
                setTourStep(next);
                navigateTo(TOUR_STEPS[next].screen);
              }}
              style={css(`${newAgentBtnStyle} margin:0;`)}
            >
              {tourStep + 1 >= TOUR_STEPS.length ? "Finish" : "Next →"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default ApolloDashboard;
