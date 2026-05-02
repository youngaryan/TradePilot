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
        self.assertIn("/api/workspaces/experiments", client_source)
        self.assertIn("/api/billing/checkout", client_source)
        self.assertIn("export interface WorkspacePayload", type_source)
        self.assertIn("export interface ExperimentRecord", type_source)
        self.assertIn("export interface PaperAgentRecord", type_source)

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
        self.assertIn("/api/telemetry/events", client_source)
        self.assertIn("/api/refresh/status", client_source)
        self.assertIn("export interface RefreshStatusPayload", type_source)
        self.assertIn("export interface TelemetryEventRequest", type_source)
        self.assertIn(':root[data-theme="dark"]', style_source)
        self.assertIn(".compact-control", style_source)


if __name__ == "__main__":
    unittest.main()
