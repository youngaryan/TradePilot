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
  local_web_search_urls?: string[];
  local_web_refresh_minutes?: number;
  local_web_max_pages_per_source?: number;
  web_research_urls?: string[];
  web_research_domains?: string[];
  web_research_query_terms?: string;
  web_research_max_articles?: number;
  web_research_fetch_article_text?: boolean;
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
  app_env: string;
  job_backend?: string;
  storage_provider?: string;
  telemetry_enabled?: boolean;
  counts?: SystemCounts;
}

export interface SystemCounts {
  jobs: number;
  deployment_configs: number;
  experiment_runs: number;
  artifacts?: number;
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
}

export interface SystemAdminCounts {
  counts: SystemCounts;
}

export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  role: "admin" | "user" | string;
  status: "active" | "inactive" | string;
  email_verified_at_utc?: string | null;
  mfa_enabled?: boolean;
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
  expires_at_utc?: string;
  user: AuthUser;
  organizations: Organization[];
  active_organization_id: string | null;
}

export interface SignupRequest {
  email: string;
  password: string;
  display_name: string;
  organization_name: string;
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
  scopes?: string[];
  token?: string;
  message?: string;
  status: string;
  created_at_utc: string;
  updated_at_utc: string;
}

export interface ApiKeyCreateRequest {
  name: string;
  provider: string;
  secret?: string | null;
  secret_ref?: string | null;
  scopes?: string[];
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
}

export interface BillingResponse {
  mode: "demo" | "stripe" | string;
  checkout_url?: string | null;
  portal_url?: string | null;
  message?: string;
  stripe_session?: Record<string, unknown>;
}

export interface PricingPlan {
  id: string;
  name: string;
  price_monthly: number;
  currency: string;
  description: string;
  features: string[];
  premium: boolean;
  recommended?: boolean;
  cta: string;
}

export interface PricingPayload {
  plans: PricingPlan[];
  subscription?: SubscriptionRecord | null;
}

export interface BillingStatusPayload {
  subscription: SubscriptionRecord | null;
  premium: boolean;
  access?: "admin" | "subscription" | string;
  pricing: PricingPlan[];
}

export interface AdminUserRecord {
  id: string;
  email: string;
  display_name: string;
  role: "admin" | "user" | string;
  status: "active" | "inactive" | string;
  created_at_utc: string;
  updated_at_utc: string;
  last_login_at_utc?: string | null;
  organization_id?: string | null;
  organization_name?: string | null;
  organization_role?: string | null;
  plan?: string | null;
  subscription_status?: string | null;
  current_period_end_utc?: string | null;
}

export interface AdminOverviewPayload {
  counts: SystemCounts;
  metrics: {
    users_total: number;
    users_active: number;
    admins_active: number;
    signups_7d: number;
    signups_30d: number;
    active_users_7d: number;
    subscriptions_by_status: Record<string, number>;
    plans: Record<string, number>;
  };
  landing_analytics: {
    totals: {
      landing_page_visits: number;
      pricing_views: number;
      features_views: number;
      examples_views: number;
      faq_views: number;
      login_views: number;
      signup_views: number;
      cta_clicks: number;
      login_starts: number;
      login_completions: number;
      signup_starts: number;
      signup_completions: number;
    };
    conversion_rates: Record<string, number>;
    visitors_by_country: Record<string, number>;
    section_views: Record<string, number>;
    cta_clicks: Record<string, number>;
    traffic_trend: Array<{ date: string; visits: number }>;
    recent_events: TelemetryEventRecord[];
  };
  telemetry: TelemetryEventRecord[];
  refresh_statuses: RefreshStatusRecord[];
  recent_refresh_runs: RefreshRunRecord[];
  recent_jobs: Record<string, Array<Record<string, unknown>>>;
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
  user_strategy?: boolean;
  owner_user_id?: string | null;
  status?: string;
  version?: number;
  risk_level?: string;
}

export interface StrategyBuilderMessage {
  role: "user" | "assistant";
  content: string;
}

export interface StrategySpec {
  schema_version: "strategy_spec/v1";
  name: string;
  summary: string;
  asset_universe: { type: string; symbols: string[] };
  timeframe: string;
  side: "long_only" | "short_only" | "long_short";
  required_indicators: Array<{ name: string; kind: string; parameters: Record<string, unknown> }>;
  entry_rules: Array<{ kind: string; parameters: Record<string, unknown>; description?: string | null }>;
  exit_rules: Array<{ kind: string; parameters: Record<string, unknown>; description?: string | null }>;
  position_sizing: Record<string, unknown>;
  risk_controls: Record<string, unknown>;
  rebalancing: Record<string, unknown>;
  costs: Record<string, unknown>;
  assumptions: string[];
  limitations: string[];
  editable_parameters: Array<{ name: string; default: unknown; min?: number | null; max?: number | null; description: string }>;
  compatibility: Record<string, unknown>;
}

export interface UserStrategyRecord {
  id: string;
  organization_id: string;
  owner_user_id: string;
  owner_email?: string | null;
  owner_name?: string | null;
  root_strategy_id: string;
  version: number;
  name: string;
  status: string;
  risk_level: string;
  spec: StrategySpec;
  validation: Record<string, unknown>;
  approval: Record<string, unknown>;
  created_at_utc: string;
  updated_at_utc: string;
  approved_at_utc?: string | null;
  disabled_at_utc?: string | null;
  deleted_at_utc?: string | null;
  backtest_count: number;
}

export interface StrategyBuilderResponse {
  state: "needs_clarification" | "ready_for_approval" | "rejected" | string;
  assistant_message: string;
  questions: string[];
  draft_spec?: StrategySpec | null;
  validation: {
    ok: boolean;
    errors: string[];
    warnings: string[];
  };
}

export interface StrategyBuilderApprovalResponse {
  strategy: UserStrategyRecord;
  catalog_item: StrategyCatalogItem;
  validation: Record<string, unknown>;
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
  visualization?: BacktestVisualizationPayload;
  trade_events?: BacktestTradeEvent[];
  trade_summary?: BacktestTradeSummary[];
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

export interface BacktestEquityPoint {
  timestamp: string;
  equity: number;
  drawdown: number;
  net_return: number;
  baseline_equity?: number | null;
  baseline_drawdown?: number | null;
  baseline_return?: number | null;
}

export interface BacktestPricePoint {
  timestamp: string;
  close?: number | null;
  sma_20?: number | null;
  sma_50?: number | null;
  sma_200?: number | null;
}

export interface BacktestIndicatorPoint {
  timestamp: string;
  forecast?: number | null;
  signal?: number | null;
  position?: number | null;
  turnover?: number | null;
  gross_exposure?: number | null;
  risk_scale?: number | null;
  rsi?: number | null;
  macd?: number | null;
  macd_signal?: number | null;
  macd_histogram?: number | null;
  realized_volatility?: number | null;
  strategy_drawdown?: number | null;
  baseline_drawdown?: number | null;
  sentiment_strength?: number | null;
  sentiment_confidence?: number | null;
}

export interface BacktestTradeEvent {
  id: string;
  timestamp: string;
  type: "entry" | "exit" | "buy" | "sell" | string;
  side: "long" | "short" | "flat" | string;
  label: string;
  exposure?: number | null;
  exposure_change?: number | null;
  price?: number | null;
  strategy_equity?: number | null;
  baseline_equity?: number | null;
  pnl?: number | null;
  return_pct?: number | null;
  quantity?: number | null;
  commission?: number | null;
}

export interface BacktestTradeSummary {
  id: string;
  symbol?: string | null;
  side: "long" | "short" | string;
  entry_timestamp: string;
  exit_timestamp?: string | null;
  entry_price?: number | null;
  exit_price?: number | null;
  entry_equity?: number | null;
  exit_equity?: number | null;
  quantity?: number | null;
  pnl?: number | null;
  return_pct?: number | null;
  holding_period_bars: number;
  status: "open" | "closed" | string;
  entry_commission?: number | null;
  exit_commission?: number | null;
}

export interface BacktestVisualizationPayload {
  status: "running" | "completed" | string;
  completed_folds: number;
  total_folds: number;
  primary_symbol?: string | null;
  baseline_label: string;
  equity: BacktestEquityPoint[];
  price: BacktestPricePoint[];
  indicators: BacktestIndicatorPoint[];
  trade_events: BacktestTradeEvent[];
  trade_summary: BacktestTradeSummary[];
  metrics: Record<string, unknown>;
  sampled: boolean;
  source_points: number;
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
  progress_snapshot?: BacktestVisualizationPayload | null;
  started_at_utc?: string | null;
  finished_at_utc?: string | null;
  result?: BacktestJobResult | null;
  error?: string | null;
}

export type MarketResearchDecision = "BUY" | "HOLD" | "SELL" | "AVOID" | string;
export type MarketResearchHorizon = "intraday" | "swing" | "long-term" | string;

export interface MarketResearchRunRequest {
  ticker: string;
  analysis_date?: string | null;
  horizon: MarketResearchHorizon;
  provider?: string;
  model?: string;
  options?: Record<string, unknown>;
}

export interface MarketResearchSignal {
  label: string;
  direction: "bullish" | "bearish" | "neutral" | "mixed" | string;
  strength: number;
  rationale: string;
  evidence: string[];
  provenance: string[];
}

export interface MarketResearchAgentOutput {
  agent_name: string;
  display_name: string;
  version: string;
  prompt_version: string;
  summary: string;
  signals: MarketResearchSignal[];
  confidence: number;
  warnings: string[];
  details: Record<string, unknown>;
}

export interface MarketResearchAuditEvent {
  agent_name: string;
  display_name: string;
  status: "completed" | "failed" | "timeout" | string;
  prompt_version: string;
  started_at_utc: string;
  finished_at_utc: string;
  duration_ms: number;
  warnings: string[];
  error?: string | null;
}

export interface MarketResearchProvenance {
  source: string;
  provider: string;
  detail: string;
  observed_at_utc: string;
  url?: string | null;
}

export interface MarketResearchReport {
  ticker: string;
  analysis_date: string;
  decision: MarketResearchDecision;
  confidence: number;
  time_horizon: MarketResearchHorizon;
  summary: string;
  bull_thesis: string;
  bear_thesis: string;
  technical_signals: MarketResearchSignal[];
  fundamental_signals: MarketResearchSignal[];
  news_sentiment_signals: MarketResearchSignal[];
  risk_assessment: MarketResearchAgentOutput;
  data_quality_notes: string[];
  disclaimer: string;
  raw_agent_outputs: MarketResearchAgentOutput[];
  audit_trail: MarketResearchAuditEvent[];
  provenance: MarketResearchProvenance[];
  warnings: string[];
  metadata: Record<string, unknown>;
  created_at_utc: string;
  artifact?: Record<string, unknown>;
  artifact_id?: string;
  report_path?: string | null;
}

export interface MarketResearchJob {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "interrupted" | string;
  request: MarketResearchRunRequest | Record<string, unknown>;
  created_at_utc: string;
  updated_at_utc: string;
  organization_id?: string | null;
  user_id?: string | null;
  progress: number;
  stage: string;
  message: string;
  warnings: string[];
  started_at_utc?: string | null;
  finished_at_utc?: string | null;
  result?: MarketResearchReport | null;
  error?: string | null;
}

export interface SentimentAccumulationRequest {
  symbols: string[];
  start: string;
  end: string;
  providers: string[];
  rss_feed_urls: string[];
  local_web_search_urls: string[];
  local_web_refresh_minutes: number;
  local_web_max_pages_per_source: number;
  web_research_urls: string[];
  web_research_domains: string[];
  web_research_query_terms: string;
  web_research_max_articles: number;
  web_research_fetch_article_text: boolean;
  news_files: string[];
  newsapi_api_key?: string | null;
  alphavantage_api_key?: string | null;
  benzinga_api_key?: string | null;
  use_finbert: boolean;
  local_finbert_only: boolean;
}

export interface SentimentAccumulationJob {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "interrupted" | string;
  request: Record<string, unknown>;
  created_at_utc: string;
  updated_at_utc: string;
  progress: number;
  stage: string;
  message: string;
  warnings?: string[];
  started_at_utc?: string | null;
  finished_at_utc?: string | null;
  result?: SentimentDatasetPayload | null;
  error?: string | null;
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

export interface FinancialEventRecord {
  id: string;
  date: string;
  ticker: string;
  event_type: string;
  event_type_label: string;
  event_title: string;
  summary: string;
  reported_result?: string | null;
  reported_metrics: {
    revenue?: number | null;
    earnings?: number | null;
    eps?: number | null;
    revenue_yoy?: number | null;
    earnings_yoy?: number | null;
    eps_yoy?: number | null;
  };
  expected_result?: string | null;
  beat_miss: "beat" | "miss" | "inline" | "not_available" | string;
  market_reaction?: string | null;
  market_reaction_pct?: number | null;
  market_reaction_source?: string | null;
  source: string;
  source_url?: string | null;
  confidence: number;
  data_completeness: "high" | "medium" | "low" | string;
  verified_fields: string[];
  inferred_fields: string[];
  missing_fields: string[];
  event_direction: "positive" | "negative" | "neutral" | string;
  form?: string | null;
  report_date?: string | null;
  accession_number?: string | null;
}

export interface FinancialEventsPayload {
  request: {
    symbols: string[];
    start: string;
    end: string;
    limit: number;
  };
  events: FinancialEventRecord[];
  summary: {
    event_count: number;
    symbols: string[];
    sources: string[];
  };
  analysis: {
    summary: string;
    verified: string[];
    inferred: string[];
    risks: string[];
    catalysts: string[];
    missing_data: string[];
    source_notes: string[];
  };
  warnings: string[];
}

export interface SentimentDatasetPayload {
  dataset_id?: string;
  output_dir: string | null;
  raw_headlines_path: string | null;
  scored_headlines_path: string | null;
  daily_sentiment_path: string | null;
  metadata_path: string | null;
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
