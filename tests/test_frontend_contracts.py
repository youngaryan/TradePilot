from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    def test_market_research_lab_exposes_committee_api_and_disclaimer(self) -> None:
        app_source = PROJECT_ROOT.joinpath("frontend/src/App.tsx").read_text(encoding="utf-8")
        lab_source = PROJECT_ROOT.joinpath("frontend/src/features/MarketResearchLab.tsx").read_text(encoding="utf-8")
        reports_source = PROJECT_ROOT.joinpath("frontend/src/features/workspace/MarketResearchReports.tsx").read_text(encoding="utf-8")
        client_source = PROJECT_ROOT.joinpath("frontend/src/api/client.ts").read_text(encoding="utf-8")
        type_source = PROJECT_ROOT.joinpath("frontend/src/api/types.ts").read_text(encoding="utf-8")
        style_source = PROJECT_ROOT.joinpath("frontend/src/styles.css").read_text(encoding="utf-8")

        self.assertIn('id: "research"', app_source)
        self.assertIn("MarketResearchLab", app_source)
        self.assertIn('"research"', app_source)
        self.assertIn("For research and educational purposes only. Not financial advice.", lab_source)
        self.assertIn("startMarketResearchJob", lab_source)
        self.assertIn("getMarketResearchJob", lab_source)
        self.assertIn("getMarketResearchRuntime", lab_source)
        self.assertIn("listMarketResearchJobs", lab_source)
        self.assertIn("/api/market-research/run-job", client_source)
        self.assertIn("/api/market-research/jobs", client_source)
        self.assertIn("/api/market-research/runtime", client_source)
        self.assertIn("export interface MarketResearchRuntimeConfig", type_source)
        self.assertIn("free_endpoint_timeout_cap_seconds?: number", type_source)
        self.assertIn("llm_fail_fast_after_failures?: number", type_source)
        self.assertIn("api_key_configured?: boolean", type_source)
        self.assertIn("runtime-diagnostics", lab_source)
        self.assertNotIn('provider: "mock"', lab_source)
        self.assertNotIn('model: "mock-research-v1"', lab_source)
        self.assertIn("listWorkspaceReports", client_source)
        self.assertIn("getWorkspaceReport", client_source)
        self.assertIn("deleteWorkspaceReport", client_source)
        self.assertIn("regenerateWorkspaceReport", client_source)
        self.assertIn("exportWorkspaceReport", client_source)
        self.assertIn("/api/workspaces/reports", client_source)
        self.assertIn("export interface MarketResearchReport", type_source)
        self.assertIn("export interface MarketResearchReportSummary", type_source)
        self.assertIn("export interface MarketResearchReportDetail", type_source)
        self.assertIn("export interface MarketResearchJob", type_source)
        self.assertIn("export interface MarketResearchProgressEvent", type_source)
        self.assertIn("progress_events?: MarketResearchProgressEvent[]", type_source)
        self.assertIn("Show progress trace", lab_source)
        self.assertIn("llm_refinement_skipped", lab_source)
        self.assertIn("Hosted guardrail", lab_source)
        self.assertIn("NVIDIA_API_KEY is not visible to the backend", lab_source)
        self.assertIn("progress-trace-list", lab_source)
        self.assertIn(".progress-trace-row", style_source)
        self.assertIn("technical_signals", type_source)
        self.assertIn("risk_assessment", type_source)
        self.assertIn("sentiment_matrix", type_source)
        self.assertIn("financial_events_matrix", type_source)
        self.assertIn("MarketResearchReports", reports_source)
        self.assertIn("For research and educational purposes only. Not financial advice.", reports_source)
        self.assertIn(".market-research-lab", style_source)
        self.assertIn(".research-disclaimer", style_source)
        self.assertIn(".reports-workspace", style_source)

    def test_sentiment_explorer_uses_additive_multi_symbol_filters(self) -> None:
        source = PROJECT_ROOT.joinpath("frontend/src/features/SentimentLab.tsx").read_text(encoding="utf-8")

        self.assertIn("const [selectedTickers, setSelectedTickers] = useState<string[]>([])", source)
        self.assertIn("function addTickerFilter(ticker: string)", source)
        self.assertIn("function removeTickerFilter(ticker: string)", source)
        self.assertIn("current.includes(normalized) ? current : [...current, normalized]", source)
        self.assertIn("onDoubleClick={() => removeTickerFilter(ticker)}", source)
        self.assertIn('title="Double-click to remove this symbol"', source)
        self.assertIn("setSelectedTickers([])", source)
        self.assertNotIn("const [selectedTicker, setSelectedTicker]", source)

    def test_sentiment_explorer_filters_all_tables_from_same_selected_ticker_set(self) -> None:
        source = PROJECT_ROOT.joinpath("frontend/src/features/SentimentLab.tsx").read_text(encoding="utf-8")

        self.assertIn("tickers: selectedTickers", source)
        self.assertIn("sourceGroup: selectedSourceGroup", source)
        self.assertIn("!selectedTickers.includes(String(point.ticker).toUpperCase())", source)
        self.assertIn("rowMatchesFilters(row, tableFilters)", source)
        self.assertIn("selectedTickers.join(\" + \")", source)
        self.assertIn("SentimentHeatmapChart points={filteredDailyPoints}", source)

    def test_sentiment_explorer_exposes_source_group_filtering(self) -> None:
        lab_source = PROJECT_ROOT.joinpath("frontend/src/features/SentimentLab.tsx").read_text(encoding="utf-8")
        type_source = PROJECT_ROOT.joinpath("frontend/src/api/types.ts").read_text(encoding="utf-8")

        self.assertIn("const SOURCE_GROUP_OPTIONS", lab_source)
        self.assertIn("proper_news", lab_source)
        self.assertIn("generic_web", lab_source)
        self.assertIn("const [selectedSourceGroup, setSelectedSourceGroup] = useState(\"ALL\")", lab_source)
        self.assertIn("sourceGroupOfRow(row)", lab_source)
        self.assertIn("Source group", lab_source)
        self.assertIn("Exact source", lab_source)
        self.assertIn("filteredSourceGroupSummary", lab_source)
        self.assertIn("Groups with zero rows are still selectable", lab_source)
        self.assertNotIn("Only group:", lab_source)
        self.assertIn("source_group?: string", type_source)
        self.assertIn("source_group_summary?: SentimentSourceSummary[]", type_source)

    def test_sentiment_lab_shows_financial_events_matrix_and_analysis(self) -> None:
        lab_source = PROJECT_ROOT.joinpath("frontend/src/features/SentimentLab.tsx").read_text(encoding="utf-8")
        client_source = PROJECT_ROOT.joinpath("frontend/src/api/client.ts").read_text(encoding="utf-8")
        type_source = PROJECT_ROOT.joinpath("frontend/src/api/types.ts").read_text(encoding="utf-8")
        style_source = PROJECT_ROOT.joinpath("frontend/src/styles.css").read_text(encoding="utf-8")

        self.assertIn("getFinancialEvents", lab_source)
        self.assertIn("FinancialEventsMatrix", lab_source)
        self.assertIn("Financial Events Analysis", lab_source)
        self.assertIn("financialSentimentComparison", lab_source)
        self.assertIn("/api/sentiment/financial-events", client_source)
        self.assertIn("export interface FinancialEventRecord", type_source)
        self.assertIn("export interface FinancialEventsPayload", type_source)
        self.assertIn(".matrix-comparison-grid", style_source)
        self.assertIn(".financial-analysis-grid", style_source)

    def test_symbol_inputs_preserve_raw_text_while_typing_spaces(self) -> None:
        sentiment_source = PROJECT_ROOT.joinpath("frontend/src/features/SentimentLab.tsx").read_text(encoding="utf-8")
        agent_source = PROJECT_ROOT.joinpath("frontend/src/components/AgentEditor.tsx").read_text(encoding="utf-8")

        self.assertIn("const [symbolsText, setSymbolsText]", sentiment_source)
        self.assertIn("function updateSymbolsText(value: string)", sentiment_source)
        self.assertIn("const finalSymbols = splitSymbols(symbolsText)", sentiment_source)
        self.assertIn("value={symbolsText}", sentiment_source)
        self.assertIn("onBlur={commitSymbolsText}", sentiment_source)
        self.assertNotIn('value={request.symbols.join(" ")} onChange={(event) => setRequest({ ...request, symbols: splitSymbols(event.target.value) })}', sentiment_source)

        self.assertIn("const [symbolsText, setSymbolsText] = useState(agent.symbols.join(\" \"))", agent_source)
        self.assertIn("function updateSymbolsText(value: string)", agent_source)
        self.assertIn("function commitSymbolsText()", agent_source)
        self.assertIn("setSymbolsText(nextSymbols.join(\" \"))", agent_source)
        self.assertIn("value={symbolsText}", agent_source)
        self.assertNotIn('value={agent.symbols.join(" ")} onChange={(event) => onChange({ ...agent, symbols: splitSymbols(event.target.value) })}', agent_source)

    def test_sentiment_stored_warnings_are_contextual_not_fresh_alerts(self) -> None:
        source = PROJECT_ROOT.joinpath("frontend/src/features/SentimentLab.tsx").read_text(encoding="utf-8")

        self.assertIn("const storedWarnings = dataset?.warnings ?? []", source)
        self.assertIn("Last run saved", source)
        self.assertIn("These warnings are persisted with the dataset for audit/debugging.", source)
        self.assertIn("warning-disclosure", source)
        self.assertNotIn("const warnings = dataset?.warnings ?? []", source)
        self.assertNotIn("{warnings.length ? (", source)

    def test_sentiment_heatmap_documents_professional_encoding_contract(self) -> None:
        source = PROJECT_ROOT.joinpath("frontend/src/components/Charts.tsx").read_text(encoding="utf-8")

        self.assertIn("function sentimentColor(value: number)", source)
        self.assertIn('aria-label="Sentiment heatmap by ticker and date"', source)
        self.assertIn("Color = sentiment score | dot size = article volume | stronger borders = confidence", source)
        self.assertIn("Neutral center is zero", source)
        self.assertIn("strokeWidth={point ? 0.8 + confidence * 1.2 : 0.6}", source)
        self.assertIn("Articles: ${formatNumber(point.article_count, 0)}", source)
        self.assertIn("Confidence: ${formatNumber(point.confidence)}", source)

    def test_sentiment_api_types_expose_table_preview_metadata(self) -> None:
        source = PROJECT_ROOT.joinpath("frontend/src/api/types.ts").read_text(encoding="utf-8")

        for field in (
            "returned_headline_count?: number",
            "returned_scored_headline_count?: number",
            "table_row_limit?: number",
            "headline_rows_truncated?: boolean",
            "scored_headline_rows_truncated?: boolean",
        ):
            self.assertIn(field, source)

    def test_lightweight_web_sentiment_controls_are_available_without_finbert_default(self) -> None:
        lab_source = PROJECT_ROOT.joinpath("frontend/src/features/SentimentLab.tsx").read_text(encoding="utf-8")
        live_source = PROJECT_ROOT.joinpath("frontend/src/features/LiveOps.tsx").read_text(encoding="utf-8")
        agent_source = PROJECT_ROOT.joinpath("frontend/src/components/AgentEditor.tsx").read_text(encoding="utf-8")
        backtest_source = PROJECT_ROOT.joinpath("frontend/src/features/BacktestLab.tsx").read_text(encoding="utf-8")
        type_source = PROJECT_ROOT.joinpath("frontend/src/api/types.ts").read_text(encoding="utf-8")

        self.assertIn('id: "local_web"', lab_source)
        self.assertIn('id: "web"', lab_source)
        self.assertIn('providers: ["rss", "local_web", "local"]', lab_source)
        self.assertIn("local_web_search_urls", lab_source)
        self.assertIn("local_web_refresh_minutes: 60", lab_source)
        self.assertIn("local_web_max_pages_per_source: 30", lab_source)
        self.assertIn("web_research_domains", lab_source)
        self.assertIn("web_research_urls", lab_source)
        self.assertIn("web_research_query_terms", lab_source)
        self.assertIn("web_research_max_articles: 4", lab_source)
        self.assertIn("web_research_fetch_article_text: true", lab_source)
        self.assertIn("use_finbert: false", lab_source)
        self.assertIn("For weak hardware, leave FinBERT unchecked", lab_source)
        self.assertIn("startSentimentAccumulationJob", lab_source)
        self.assertIn("getSentimentAccumulationJob", lab_source)
        self.assertIn("role=\"progressbar\"", lab_source)
        self.assertIn("Sentiment run in progress", lab_source)
        self.assertIn("Sentiment run needs attention", lab_source)
        self.assertIn("Sentiment dataset updated with warnings", lab_source)
        self.assertIn("job-progress-card--warning", lab_source)
        self.assertIn("job-step-row", lab_source)

        self.assertIn('const DEFAULT_SENTIMENT_PROVIDERS = ["rss", "local_web", "local"]', agent_source)
        self.assertIn('["rss", "local_web", "web", "local", "newsapi", "alphavantage", "benzinga", "stocktwits"]', agent_source)
        self.assertIn("local_web_search_urls", live_source)
        self.assertIn("local_web_refresh_minutes", live_source)
        self.assertIn("local_web_max_pages_per_source", live_source)
        self.assertIn("web_research_fetch_article_text", live_source)
        self.assertIn("Use FinBERT when available (heavier)", agent_source)

        self.assertIn("const SENTIMENT_PIPELINES = new Set", backtest_source)
        self.assertIn("DEFAULT_SENTIMENT_PARAMETERS", backtest_source)
        self.assertIn("Local web-search feeds", backtest_source)
        self.assertIn("Website domains to crawl", backtest_source)
        self.assertIn("Fetch web pages and create lightweight summaries", backtest_source)

        for field in (
            "local_web_search_urls: string[]",
            "local_web_refresh_minutes: number",
            "local_web_max_pages_per_source: number",
            "web_research_urls: string[]",
            "web_research_domains: string[]",
            "web_research_query_terms: string",
            "web_research_max_articles: number",
            "web_research_fetch_article_text: boolean",
            "export interface SentimentAccumulationJob",
            "local_web_search_urls?: string[]",
            "local_web_max_pages_per_source?: number",
            "web_research_urls?: string[]",
            "web_research_domains?: string[]",
        ):
            self.assertIn(field, type_source)

        client_source = PROJECT_ROOT.joinpath("frontend/src/api/client.ts").read_text(encoding="utf-8")
        self.assertIn("/api/sentiment/accumulate-job", client_source)
        self.assertIn("/api/sentiment/jobs", client_source)

    def test_saas_frontend_exposes_login_workspace_and_detail_pages(self) -> None:
        app_source = PROJECT_ROOT.joinpath("frontend/src/App.tsx").read_text(encoding="utf-8")
        workspace_source = PROJECT_ROOT.joinpath("frontend/src/features/SaaSWorkspace.tsx").read_text(encoding="utf-8")
        client_source = PROJECT_ROOT.joinpath("frontend/src/api/client.ts").read_text(encoding="utf-8")
        type_source = PROJECT_ROOT.joinpath("frontend/src/api/types.ts").read_text(encoding="utf-8")

        self.assertIn('id: "workspace"', app_source)
        self.assertIn("LoginScreen", app_source)
        self.assertIn("setApiAuth", app_source)
        self.assertIn("SaaSWorkspace", app_source)
        self.assertIn("Launch first strategy wizard", workspace_source)
        self.assertIn("MarketResearchReports", workspace_source)
        self.assertIn('"reports"', workspace_source)
        self.assertIn("getWorkspaceExperiment", workspace_source)
        self.assertIn("getWorkspacePaperAgent", workspace_source)
        self.assertIn("startBillingCheckout", workspace_source)
        self.assertIn("/api/auth/login", client_source)
        self.assertIn("/api/auth/signup", client_source)
        self.assertIn("/api/workspaces/experiments", client_source)
        self.assertIn("/api/billing/checkout", client_source)
        self.assertIn("export interface WorkspacePayload", type_source)
        self.assertIn("market_research_reports", type_source)
        self.assertIn("export interface ExperimentRecord", type_source)
        self.assertIn("export interface PaperAgentRecord", type_source)

    def test_frontend_exposes_admin_pricing_and_payment_wall_contracts(self) -> None:
        app_source = PROJECT_ROOT.joinpath("frontend/src/App.tsx").read_text(encoding="utf-8")
        login_source = PROJECT_ROOT.joinpath("frontend/src/features/LoginScreen.tsx").read_text(encoding="utf-8")
        admin_source = PROJECT_ROOT.joinpath("frontend/src/features/AdminDashboard.tsx").read_text(encoding="utf-8")
        pricing_source = PROJECT_ROOT.joinpath("frontend/src/features/PricingPage.tsx").read_text(encoding="utf-8")
        client_source = PROJECT_ROOT.joinpath("frontend/src/api/client.ts").read_text(encoding="utf-8")
        type_source = PROJECT_ROOT.joinpath("frontend/src/api/types.ts").read_text(encoding="utf-8")
        style_source = PROJECT_ROOT.joinpath("frontend/src/styles.css").read_text(encoding="utf-8")

        self.assertIn('id: "pricing"', app_source)
        self.assertIn('id: "admin"', app_source)
        self.assertIn("adminOnly: true", app_source)
        self.assertIn("premiumViews", app_source)
        self.assertIn("paymentWallReason", app_source)
        self.assertIn("viewFromLocationHash", app_source)
        self.assertIn("hashchange", app_source)
        self.assertIn("#/app/", app_source)
        self.assertIn("Use admin demo", login_source)
        self.assertIn("Use free user demo", login_source)
        self.assertIn("logoutRequest", app_source)

        self.assertIn("AdminDashboard", admin_source)
        self.assertIn("window.confirm", admin_source)
        self.assertIn("updateAdminUser", admin_source)
        self.assertIn("listAdminUsers", admin_source)
        self.assertIn("getAdminOverview", admin_source)

        self.assertIn("PricingPage", pricing_source)
        self.assertIn("getBillingStatus", pricing_source)
        self.assertIn("getPricing", pricing_source)
        self.assertIn("startBillingCheckout", pricing_source)
        self.assertIn("server-side", pricing_source)

        self.assertIn("/api/admin/overview", client_source)
        self.assertIn("/api/admin/users", client_source)
        self.assertIn("/api/billing/pricing", client_source)
        self.assertIn("/api/billing/status", client_source)
        self.assertIn("/api/system/admin-counts", client_source)
        self.assertIn("React.lazy", app_source)
        self.assertIn("getSystemAdminCounts", app_source)
        self.assertIn("export interface AdminUserRecord", type_source)
        self.assertIn("export interface AdminOverviewPayload", type_source)
        self.assertIn("export interface PricingPlan", type_source)
        self.assertIn("role:", type_source)
        self.assertIn("status:", type_source)
        self.assertIn(".pricing-grid", style_source)
        self.assertIn(".payment-wall-banner", style_source)
        self.assertIn(".admin-toolbar", style_source)

    def test_landing_page_default_sections_and_analytics_contract(self) -> None:
        app_source = PROJECT_ROOT.joinpath("frontend/src/App.tsx").read_text(encoding="utf-8")
        login_source = PROJECT_ROOT.joinpath("frontend/src/features/LoginScreen.tsx").read_text(encoding="utf-8")
        admin_source = PROJECT_ROOT.joinpath("frontend/src/features/AdminDashboard.tsx").read_text(encoding="utf-8")
        type_source = PROJECT_ROOT.joinpath("frontend/src/api/types.ts").read_text(encoding="utf-8")
        style_source = PROJECT_ROOT.joinpath("frontend/src/styles.css").read_text(encoding="utf-8")

        self.assertIn("return <LoginScreen onLogin={handleLogin} />", app_source)
        for section in ('"features"', '"examples"', '"pricing"', '"faq"', '"login"', '"signup"'):
            self.assertIn(section, login_source)
        for event_name in (
            "landing_page_view",
            "landing_section_view",
            "landing_cta_clicked",
            "pricing_viewed",
            "auth_signup_started",
            "auth_signup_completed",
            "auth_login_started",
            "auth_login_completed",
        ):
            self.assertIn(event_name, login_source)
        self.assertIn("IntersectionObserver", login_source)
        self.assertIn("landingVisitorId", login_source)
        self.assertIn("Create a free workspace", login_source)
        self.assertIn("Northstar Quant Lab", login_source)
        self.assertIn("landing_analytics", type_source)
        self.assertIn("visitors_by_country", type_source)
        self.assertIn("Landing page analytics", admin_source)
        self.assertIn("Visitors by country", admin_source)
        self.assertIn("CTA clicks", admin_source)
        self.assertIn("TelemetryTimelineChart", admin_source)
        self.assertIn(".example-grid", style_source)
        self.assertIn(".faq-grid", style_source)

    def test_frontend_has_theme_refresh_and_telemetry_contracts(self) -> None:
        app_source = PROJECT_ROOT.joinpath("frontend/src/App.tsx").read_text(encoding="utf-8")
        workspace_source = PROJECT_ROOT.joinpath("frontend/src/features/SaaSWorkspace.tsx").read_text(encoding="utf-8")
        client_source = PROJECT_ROOT.joinpath("frontend/src/api/client.ts").read_text(encoding="utf-8")
        type_source = PROJECT_ROOT.joinpath("frontend/src/api/types.ts").read_text(encoding="utf-8")
        style_source = PROJECT_ROOT.joinpath("frontend/src/styles.css").read_text(encoding="utf-8")

        self.assertIn('type ThemeMode = "light" | "dark" | "system"', app_source)
        self.assertIn("document.documentElement.dataset.theme", app_source)
        self.assertIn("telemetryConsent", app_source)
        self.assertIn("trackTelemetryEvent", app_source)
        self.assertIn('"operations"', workspace_source)
        self.assertIn("getRefreshStatus", workspace_source)
        self.assertIn("runDailyRefresh", workspace_source)
        self.assertIn("listTelemetryEvents", workspace_source)
        self.assertIn("TelemetryTimelineChart", workspace_source)
        self.assertIn("TelemetryLatencyChart", workspace_source)
        self.assertIn("TelemetryCategoryBars", workspace_source)
        self.assertIn("TelemetryConsentBars", workspace_source)
        self.assertIn("TelemetryTopEventsBars", workspace_source)
        self.assertIn("listTelemetryEvents(200)", workspace_source)
        self.assertIn("Telemetry dashboard", workspace_source)
        self.assertIn("/api/telemetry/events", client_source)
        self.assertIn("/api/refresh/status", client_source)
        self.assertIn("export interface RefreshStatusPayload", type_source)
        self.assertIn("export interface TelemetryEventRequest", type_source)
        self.assertIn(':root[data-theme="dark"]', style_source)
        self.assertIn(".compact-control", style_source)
        self.assertIn(".telemetry-chart-grid", style_source)
        self.assertIn(".telemetry-segment--error", style_source)

    def test_frontend_telemetry_charts_explain_observability_contract(self) -> None:
        source = PROJECT_ROOT.joinpath("frontend/src/components/Charts.tsx").read_text(encoding="utf-8")

        self.assertIn("export const TelemetryTimelineChart", source)
        self.assertIn('aria-label="Telemetry events over time"', source)
        self.assertIn("Stacked by product, refresh, engineering, other, and error events", source)
        self.assertIn("export const TelemetryLatencyChart", source)
        self.assertIn("latency_ms, duration_ms, elapsed_ms, response_ms, or runtime_ms", source)
        self.assertIn("export const TelemetryCategoryBars", source)
        self.assertIn("export const TelemetryConsentBars", source)
        self.assertIn("export const TelemetryTopEventsBars", source)

    def test_backtest_lab_exposes_realtime_baseline_chart_contract(self) -> None:
        lab_source = PROJECT_ROOT.joinpath("frontend/src/features/BacktestLab.tsx").read_text(encoding="utf-8")
        chart_source = PROJECT_ROOT.joinpath("frontend/src/components/BacktestPerformanceChart.tsx").read_text(encoding="utf-8")
        type_source = PROJECT_ROOT.joinpath("frontend/src/api/types.ts").read_text(encoding="utf-8")
        style_source = PROJECT_ROOT.joinpath("frontend/src/styles.css").read_text(encoding="utf-8")

        self.assertIn("progress_snapshot", lab_source)
        self.assertIn("BacktestPerformanceChart", lab_source)
        self.assertIn("Performance vs Baseline", lab_source)
        self.assertIn("Trade-Level Summary", lab_source)
        self.assertIn("createSeriesMarkers", chart_source)
        self.assertIn("CrosshairMode.Normal", chart_source)
        self.assertIn("Reset zoom", chart_source)
        self.assertIn("Pane 1: strategy vs baseline equity", chart_source)
        self.assertIn("prepared.hoverRows.get(hoverKey)", chart_source)
        self.assertIn("prepared.hoverRows.get(latestKey)", chart_source)
        self.assertIn("export interface BacktestVisualizationPayload", type_source)
        self.assertIn("baseline_equity", type_source)
        self.assertIn("trade_summary", type_source)
        self.assertIn(".backtest-chart-canvas", style_source)

    def test_strategy_builder_clears_stale_response_before_next_request(self) -> None:
        source = PROJECT_ROOT.joinpath("frontend/src/features/ApolloDashboard.tsx").read_text(encoding="utf-8")
        type_source = PROJECT_ROOT.joinpath("frontend/src/api/types.ts").read_text(encoding="utf-8")
        request_start = source.index("const sendBuilderMessage = useCallback")
        request_end = source.index("const approveBuilderDraft = useCallback", request_start)
        request_source = source[request_start:request_end]

        self.assertIn("setBuilderResp(null)", request_source)
        self.assertLess(request_source.index("setBuilderResp(null)"), request_source.index("await chatStrategyBuilder"))
        self.assertIn("interpreted_intent", type_source)
        self.assertIn("requirement_trace", type_source)
        self.assertIn("semantic_repair_count", type_source)
        self.assertIn("How the AI interpreted this request", source)


if __name__ == "__main__":
    unittest.main()
