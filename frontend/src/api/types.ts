export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export interface PaperTotals {
  equity: number;
  daily_pnl: number;
  rebalance_cost_pnl: number;
  cash: number;
  gross_exposure: number;
  gross_exposure_ratio: number;
  position_count: number;
  trade_count: number;
  turnover: number;
}

export interface LeaderboardRow {
  strategy: string;
  pipeline: string;
  mode: string;
  equity: number;
  return_since_inception: number;
  daily_pnl: number;
  trade_count: number;
  gross_exposure_ratio: number;
}

export interface PaperOrder {
  instrument?: string;
  side?: string;
  quantity?: number;
  mark_price?: number;
  execution_price?: number;
  target_weight?: number;
  commission?: number;
  notional?: number;
  [key: string]: unknown;
}

export interface PaperHistoryRow {
  timestamp: string;
  equity_after?: number;
  daily_pnl?: number;
  rebalance_cost_pnl?: number;
  net_return_since_inception?: number;
  cash_after?: number;
  gross_exposure_notional?: number;
  gross_exposure_ratio?: number;
  position_count?: number;
  trade_count?: number;
  turnover_notional?: number;
  [key: string]: unknown;
}

export interface PaperStrategy {
  name: string;
  pipeline: string;
  mode: string;
  equity: number;
  daily_pnl: number;
  rebalance_cost_pnl: number;
  return_since_inception: number;
  cash: number;
  gross_exposure: number;
  gross_exposure_ratio: number;
  position_count: number;
  trade_count: number;
  turnover: number;
  positions: Record<string, number>;
  target_weights: Record<string, number>;
  latest_orders: PaperOrder[];
  diagnostics: Record<string, unknown>;
  history: PaperHistoryRow[];
}

export interface PaperDashboardPayload {
  asof_date: string | null;
  run_timestamp_utc: string | null;
  totals: PaperTotals;
  leaderboard: LeaderboardRow[];
  strategies: PaperStrategy[];
  visuals: Record<string, unknown>;
}

export interface PaperRunJob {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "interrupted" | string;
  request: Record<string, unknown>;
  created_at_utc: string;
  updated_at_utc: string;
  progress: number;
  stage: string;
  message: string;
  started_at_utc?: string | null;
  finished_at_utc?: string | null;
  result?: (PaperDashboardPayload & { run_sequence?: { dates: Array<string | null>; count: number; deployment_config_path: string } }) | null;
  error?: string | null;
}

export interface PaperExecutionConfig {
  initial_cash: number;
  commission_bps: number;
  slippage_bps: number;
  min_trade_notional: number;
  weight_tolerance: number;
}

export interface PaperAgentConfig {
  id: string;
  name: string;
  pipeline: string;
  symbols: string[];
  interval: string;
  lookback_bars: number;
  sector_map_path?: string | null;
  event_file?: string | null;
  use_sec_companyfacts?: boolean;
  include_sec_filings?: boolean;
  sec_filing_forms?: string[];
  edgar_user_agent?: string | null;
  daily_sentiment_file?: string | null;
  news_provider_names?: string[];
  news_files?: string[];
  rss_feed_urls?: string[];
  newsapi_api_key?: string | null;
  use_finbert?: boolean;
  local_finbert_only?: boolean;
  news_topics?: string[];
  params: Record<string, unknown>;
}

export interface PaperRunRequest {
  deployment_config_path?: string | null;
  deployment_config?: {
    execution: PaperExecutionConfig;
    strategies: Array<Record<string, unknown>>;
  } | null;
  asof_date?: string | null;
  asof_start?: string | null;
  asof_end?: string | null;
}

export interface HealthResponse {
  status: string;
  service: string;
}

export interface SystemMetadata {
  metadata_db_path: string;
  counts: {
    jobs: number;
    deployment_configs: number;
    experiment_runs: number;
    users?: number;
    organizations?: number;
    projects?: number;
    experiments?: number;
    paper_agents?: number;
    datasets?: number;
    api_keys?: number;
    subscriptions?: number;
    telemetry_events?: number;
    refresh_runs?: number;
    refresh_statuses?: number;
  };
}

export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  owner_user_id: string;
  billing_email?: string | null;
  stripe_customer_id?: string | null;
  role?: string;
  created_at_utc: string;
  updated_at_utc: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
  organizations: Organization[];
  active_organization_id: string | null;
}

export interface SaaSProject {
  id: string;
  organization_id: string;
  name: string;
  slug: string;
  description?: string | null;
  created_at_utc: string;
  updated_at_utc: string;
}

export interface ProjectCreateRequest {
  name: string;
  description?: string | null;
}

export interface SubscriptionRecord {
  id: string;
  organization_id: string;
  plan: string;
  status: string;
  stripe_customer_id?: string | null;
  stripe_subscription_id?: string | null;
  current_period_end_utc?: string | null;
  usage: Record<string, unknown>;
  created_at_utc: string;
  updated_at_utc: string;
}

export interface DatasetRecord {
  id: string;
  organization_id: string;
  project_id?: string | null;
  name: string;
  kind: string;
  path: string;
  provider: Record<string, unknown>;
  schema: Record<string, unknown>;
  row_count: number;
  created_at_utc: string;
  updated_at_utc: string;
}

export interface ApiKeyRecord {
  id: string;
  organization_id: string;
  name: string;
  provider: string;
  masked_value: string;
  secret_ref?: string | null;
  status: string;
  created_at_utc: string;
  updated_at_utc: string;
}

export interface ApiKeyCreateRequest {
  name: string;
  provider: string;
  secret?: string | null;
  secret_ref?: string | null;
}

export interface ReadinessCheck {
  name: string;
  value?: number | string | null;
  passed: boolean;
  target: string;
}

export interface ExperimentRecord {
  id: string;
  organization_id: string;
  project_id?: string | null;
  job_id?: string | null;
  name: string;
  pipeline: string;
  status: string;
  artifact_dir?: string | null;
  summary: Record<string, unknown>;
  validation: Record<string, unknown>;
  lineage: Record<string, unknown>;
  readiness: {
    score?: number;
    verdict?: string;
    passed_checks?: number;
    total_checks?: number;
    checks?: ReadinessCheck[];
  };
  trades: Array<Record<string, unknown>>;
  sentiment: Record<string, unknown>;
  artifact_files?: string[];
  equity_curve_points?: Array<Record<string, unknown>>;
  fold_metrics?: Array<Record<string, unknown>>;
  diagnostics?: unknown;
  created_at_utc: string;
  updated_at_utc: string;
}

export interface PaperAgentRecord {
  id: string;
  organization_id: string;
  project_id?: string | null;
  name: string;
  pipeline: string;
  status: string;
  fake_cash: number;
  config: Record<string, unknown>;
  latest_payload: Partial<PaperStrategy> & Record<string, unknown>;
  warnings: string[];
  created_at_utc: string;
  updated_at_utc: string;
}

export interface WorkspacePayload {
  organization_id: string;
  projects: SaaSProject[];
  subscription: SubscriptionRecord | null;
  datasets: DatasetRecord[];
  api_keys: ApiKeyRecord[];
  experiments: ExperimentRecord[];
  paper_agents: PaperAgentRecord[];
  onboarding: {
    complete_count: number;
    total_count: number;
    steps: Array<{ id: string; label: string; complete: boolean }>;
  };
}

export interface TelemetryEventRequest {
  name: string;
  category?: string;
  properties?: Record<string, unknown>;
  context?: Record<string, unknown>;
  consent?: "granted" | "denied" | "system" | "unknown" | string;
  anonymous_id?: string | null;
  occurred_at_utc?: string | null;
}

export interface TelemetryEventRecord {
  id: string;
  organization_id?: string | null;
  user_id?: string | null;
  name: string;
  category: string;
  properties: Record<string, unknown>;
  context: Record<string, unknown>;
  consent: string;
  occurred_at_utc: string;
}

export interface RefreshRunRecord {
  id: string;
  idempotency_key: string;
  user_id: string;
  organization_id: string;
  status: string;
  attempt: number;
  max_attempts: number;
  started_at_utc?: string | null;
  finished_at_utc?: string | null;
  locked_until_utc?: string | null;
  summary: Record<string, unknown>;
  error?: string | null;
  created_at_utc: string;
  updated_at_utc: string;
}

export interface RefreshStatusRecord {
  user_id: string;
  organization_id: string;
  status: string;
  last_success_at_utc?: string | null;
  last_attempt_at_utc?: string | null;
  next_due_at_utc: string;
  latest_run_id?: string | null;
  last_error?: string | null;
  updated_at_utc: string;
}

export interface RefreshStatusPayload {
  interval_hours: number;
  max_attempts: number;
  scheduler_enabled: boolean;
  statuses: RefreshStatusRecord[];
  recent_runs: RefreshRunRecord[];
}

export interface BillingCheckoutRequest {
  plan: string;
  price_id?: string | null;
}

export interface BillingResponse {
  mode: "demo" | "stripe" | string;
  checkout_url?: string | null;
  portal_url?: string | null;
  message?: string;
  stripe_session?: Record<string, unknown>;
}

export interface StrategyCatalogItem {
  id: string;
  name: string;
  family: string;
  difficulty: string;
  pipeline: string;
  summary: string;
  how_it_works: string;
  best_for: string;
  watch_out: string;
  key_parameters: string[];
  example_cli: string;
  paper_config_example: Record<string, unknown>;
}

export interface BacktestTemplate {
  id: string;
  name: string;
  pipeline: string;
  symbols: string[];
  start: string;
  end: string;
  sector_map_path?: string | null;
  event_file?: string | null;
  parameters: Record<string, unknown>;
  description: string;
  objective?: string;
  risk_level?: string;
  validation_focus?: string;
}

export interface BacktestRunRequest {
  pipeline: string;
  symbols: string[];
  start: string;
  end: string;
  interval: string;
  experiment_name?: string | null;
  sector_map_path?: string | null;
  event_file?: string | null;
  use_sec_companyfacts?: boolean;
  include_sec_filings?: boolean;
  sec_filing_forms?: string[];
  edgar_user_agent?: string | null;
  train_bars: number;
  test_bars: number;
  step_bars: number;
  bars_per_year: number;
  purge_bars: number;
  embargo_bars: number;
  pbo_partitions: number;
  parameters: Record<string, unknown>;
}

export interface BacktestJobResult {
  summary: Record<string, unknown>;
  validation: Record<string, unknown>;
  visuals: Record<string, unknown>;
  artifact_dir: string | null;
  fold_metrics_tail: JsonValue;
  equity_curve_tail: JsonValue;
  equity_curve_points: Array<{
    timestamp: string;
    equity: number;
    drawdown: number;
    net_return: number;
  }>;
  decision: {
    verdict: string;
    headline: string;
    passed_checks: number;
    total_checks: number;
    checks: Array<{
      name: string;
      value: number | null;
      passed: boolean;
      message: string;
    }>;
  };
}

export interface BacktestJob {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "interrupted" | string;
  request: BacktestRunRequest | Record<string, unknown>;
  created_at_utc: string;
  updated_at_utc: string;
  progress?: number;
  stage?: string;
  message?: string;
  warnings?: string[];
  started_at_utc?: string | null;
  finished_at_utc?: string | null;
  result?: BacktestJobResult | null;
  error?: string | null;
}

export interface SentimentAccumulationRequest {
  symbols: string[];
  start: string;
  end: string;
  providers: string[];
  rss_feed_urls: string[];
  news_files: string[];
  newsapi_api_key?: string | null;
  alphavantage_api_key?: string | null;
  benzinga_api_key?: string | null;
  output_dir?: string | null;
  use_finbert: boolean;
  local_finbert_only: boolean;
}

export interface SentimentDailyPoint {
  date: string;
  ticker: string;
  sentiment_score: number;
  sentiment_abs: number;
  confidence: number;
  article_count: number;
  positive_prob: number;
  negative_prob: number;
  neutral_prob: number;
  [key: string]: unknown;
}

export interface SentimentTickerSummary {
  ticker: string;
  article_count: number;
  avg_sentiment: number;
  avg_confidence: number;
  latest_sentiment: number;
}

export interface SentimentSourceSummary {
  source: string;
  headline_count: number;
}

export interface SentimentHeadline {
  timestamp?: string;
  ticker?: string;
  headline?: string;
  title?: string;
  summary?: string;
  source?: string;
  url?: string;
  relevance?: number;
  score?: number;
  confidence?: number;
  label?: string;
  [key: string]: unknown;
}

export interface SentimentDatasetPayload {
  output_dir: string;
  raw_headlines_path: string;
  scored_headlines_path: string;
  daily_sentiment_path: string;
  metadata_path: string;
  metadata: Record<string, unknown>;
  warnings: string[];
  summary: {
    headline_count: number;
    scored_headline_count: number;
    returned_headline_count?: number;
    returned_scored_headline_count?: number;
    table_row_limit?: number;
    table_rows_per_last_run_ticker?: number;
    headline_rows_truncated?: boolean;
    scored_headline_rows_truncated?: boolean;
    daily_rows: number;
    ticker_count: number;
    source_count: number;
  };
  daily_points: SentimentDailyPoint[];
  ticker_summary: SentimentTickerSummary[];
  source_summary: SentimentSourceSummary[];
  headlines: SentimentHeadline[];
  scored_headlines: SentimentHeadline[];
}
