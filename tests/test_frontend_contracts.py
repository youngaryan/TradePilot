from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
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
        self.assertIn("!selectedTickers.includes(String(point.ticker).toUpperCase())", source)
        self.assertIn("rowMatchesFilters(row, tableFilters)", source)
        self.assertIn("selectedTickers.join(\" + \")", source)
        self.assertIn("SentimentHeatmapChart points={filteredDailyPoints}", source)

    def test_symbol_inputs_preserve_raw_text_while_typing_spaces(self) -> None:
        sentiment_source = PROJECT_ROOT.joinpath("frontend/src/features/SentimentLab.tsx").read_text(encoding="utf-8")
        live_source = PROJECT_ROOT.joinpath("frontend/src/features/LiveOps.tsx").read_text(encoding="utf-8")

        self.assertIn("const [symbolsText, setSymbolsText]", sentiment_source)
        self.assertIn("function updateSymbolsText(value: string)", sentiment_source)
        self.assertIn("const finalSymbols = splitSymbols(symbolsText)", sentiment_source)
        self.assertIn("value={symbolsText}", sentiment_source)
        self.assertIn("onBlur={commitSymbolsText}", sentiment_source)
        self.assertNotIn('value={request.symbols.join(" ")} onChange={(event) => setRequest({ ...request, symbols: splitSymbols(event.target.value) })}', sentiment_source)

        self.assertIn("const [symbolsText, setSymbolsText] = useState(agent.symbols.join(\" \"))", live_source)
        self.assertIn("function updateSymbolsText(value: string)", live_source)
        self.assertIn("function commitSymbolsText()", live_source)
        self.assertIn("setSymbolsText(nextSymbols.join(\" \"))", live_source)
        self.assertIn("value={symbolsText}", live_source)
        self.assertNotIn('value={agent.symbols.join(" ")} onChange={(event) => onChange({ ...agent, symbols: splitSymbols(event.target.value) })}', live_source)

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

        self.assertIn('const DEFAULT_SENTIMENT_PROVIDERS = ["rss", "local_web", "local"]', live_source)
        self.assertIn('["rss", "local_web", "web", "local", "newsapi", "alphavantage", "benzinga"]', live_source)
        self.assertIn("local_web_search_urls", live_source)
        self.assertIn("local_web_refresh_minutes", live_source)
        self.assertIn("local_web_max_pages_per_source", live_source)
        self.assertIn("web_research_fetch_article_text", live_source)
        self.assertIn("Use FinBERT when available (heavier)", live_source)

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
        self.assertIn("getWorkspaceExperiment", workspace_source)
        self.assertIn("getWorkspacePaperAgent", workspace_source)
        self.assertIn("startBillingCheckout", workspace_source)
        self.assertIn("/api/auth/login", client_source)
        self.assertIn("/api/auth/signup", client_source)
        self.assertIn("/api/workspaces/experiments", client_source)
        self.assertIn("/api/billing/checkout", client_source)
        self.assertIn("export interface WorkspacePayload", type_source)
        self.assertIn("export interface ExperimentRecord", type_source)
        self.assertIn("export interface PaperAgentRecord", type_source)

    def test_frontend_exposes_admin_pricing_and_payment_wall_contracts(self) -> None:
        app_source = PROJECT_ROOT.joinpath("frontend/src/App.tsx").read_text(encoding="utf-8")
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
        self.assertIn("Use admin demo", app_source)
        self.assertIn("Use free user demo", app_source)
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
        admin_source = PROJECT_ROOT.joinpath("frontend/src/features/AdminDashboard.tsx").read_text(encoding="utf-8")
        type_source = PROJECT_ROOT.joinpath("frontend/src/api/types.ts").read_text(encoding="utf-8")
        style_source = PROJECT_ROOT.joinpath("frontend/src/styles.css").read_text(encoding="utf-8")

        self.assertIn("return <LoginScreen onLogin={handleLogin} />", app_source)
        for section in ('"features"', '"examples"', '"pricing"', '"faq"', '"login"', '"signup"'):
            self.assertIn(section, app_source)
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
            self.assertIn(event_name, app_source)
        self.assertIn("IntersectionObserver", app_source)
        self.assertIn("landingVisitorId", app_source)
        self.assertIn("Create a free workspace", app_source)
        self.assertIn("Northstar Quant Lab", app_source)
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

        self.assertIn("export function TelemetryTimelineChart", source)
        self.assertIn('aria-label="Telemetry events over time"', source)
        self.assertIn("Stacked by product, refresh, engineering, other, and error events", source)
        self.assertIn("export function TelemetryLatencyChart", source)
        self.assertIn("latency_ms, duration_ms, elapsed_ms, response_ms, or runtime_ms", source)
        self.assertIn("export function TelemetryCategoryBars", source)
        self.assertIn("export function TelemetryConsentBars", source)
        self.assertIn("export function TelemetryTopEventsBars", source)


if __name__ == "__main__":
    unittest.main()
