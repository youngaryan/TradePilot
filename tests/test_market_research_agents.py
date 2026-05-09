from __future__ import annotations

import unittest

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.llm_config import validate_market_research_llm_settings
from pairs_trading.research.llm_providers import LLMCallResult
from pairs_trading.research.market_research_agents import (
    BearResearcher,
    BullResearcher,
    DemoMarketResearchDataProvider,
    FundamentalAnalyst,
    MarketResearchInput,
    MarketResearchOrchestrator,
    MarketResearchReport,
    PortfolioRiskManager,
    ResearchDecision,
    ResearchHorizon,
    RiskAnalyst,
    TechnicalAnalyst,
    TraderSynthesizer,
)
from pairs_trading.research.market_research_prompts import RESEARCH_DISCLAIMER


class FakeStructuredLLMProvider:
    provider_name = "fake"
    model_name = "fake-structured-v1"

    def __init__(self) -> None:
        self.calls = 0

    def generate_structured(self, prompt, schema, options=None):  # noqa: ANN001
        del prompt, options
        self.calls += 1
        return LLMCallResult(
            value=schema.model_validate(
                {
                    "agent_name": "placeholder",
                    "display_name": "LLM Agent",
                    "summary": "LLM-refined research output grounded in the provided context.",
                    "signals": [],
                    "confidence": 61,
                    "warnings": [],
                    "details": {"llm_refined": True},
                }
            ),
            provider=self.provider_name,
            model=self.model_name,
            latency_ms=1,
        )


class FailingNewsAgent:
    agent_name = "news_sentiment_analyst"
    display_name = "News/Sentiment Analyst"
    version = "test-failing"

    def run(self, context, previous_outputs):  # noqa: ANN001
        del context, previous_outputs
        raise RuntimeError("synthetic news failure")


class MarketResearchAgentTests(unittest.TestCase):
    def test_ticker_validation_normalizes_and_rejects_invalid_symbols(self) -> None:
        request = MarketResearchInput(ticker="aapl", analysis_date="2026-05-08", horizon=ResearchHorizon.SWING)
        self.assertEqual(request.ticker, "AAPL")
        with self.assertRaises(ValueError):
            MarketResearchInput(ticker="bad symbol", analysis_date="2026-05-08")

    def test_orchestrator_happy_path_generates_complete_demo_report(self) -> None:
        request = MarketResearchInput(ticker="AAPL", analysis_date="2026-05-08", horizon=ResearchHorizon.SWING)
        context = DemoMarketResearchDataProvider().collect(request)
        report = MarketResearchOrchestrator(per_agent_timeout_seconds=2.0).run(context)
        payload = report.model_dump(mode="json")
        reparsed = MarketResearchReport.model_validate(payload)

        self.assertEqual(reparsed.ticker, "AAPL")
        self.assertIn(reparsed.decision, {ResearchDecision.BUY, ResearchDecision.HOLD, ResearchDecision.SELL, ResearchDecision.AVOID})
        self.assertGreaterEqual(reparsed.confidence, 0)
        self.assertLessEqual(reparsed.confidence, 100)
        self.assertTrue(reparsed.technical_signals)
        self.assertTrue(reparsed.fundamental_signals)
        self.assertTrue(reparsed.news_sentiment_signals)
        self.assertEqual(reparsed.disclaimer, RESEARCH_DISCLAIMER)
        self.assertEqual(len(reparsed.audit_trail), 8)
        self.assertEqual(reparsed.metadata["trade_execution"], "disabled")

    def test_orchestrator_continues_after_partial_agent_failure(self) -> None:
        request = MarketResearchInput(ticker="MSFT", analysis_date="2026-05-08", horizon=ResearchHorizon.LONG_TERM)
        context = DemoMarketResearchDataProvider().collect(request)
        orchestrator = MarketResearchOrchestrator(
            agents=[
                TechnicalAnalyst(),
                FundamentalAnalyst(),
                FailingNewsAgent(),
                RiskAnalyst(),
                BullResearcher(),
                BearResearcher(),
                TraderSynthesizer(),
                PortfolioRiskManager(),
            ],
            per_agent_timeout_seconds=2.0,
        )

        report = orchestrator.run(context)

        self.assertIn("synthetic news failure", " ".join(report.warnings))
        failed = [event for event in report.audit_trail if event.agent_name == "news_sentiment_analyst"]
        self.assertEqual(failed[0].status, "failed")
        self.assertIsNotNone(report.risk_assessment)
        self.assertEqual(report.disclaimer, RESEARCH_DISCLAIMER)

    def test_report_always_contains_disclaimer(self) -> None:
        request = MarketResearchInput(ticker="NVDA", analysis_date="2026-05-08")
        report = MarketResearchOrchestrator().run(DemoMarketResearchDataProvider().collect(request))

        self.assertEqual(report.disclaimer, "For research and educational purposes only. Not financial advice.")
        self.assertIn("Not financial advice", report.summary)

    def test_orchestrator_uses_hosted_structured_llm_provider_when_configured(self) -> None:
        request = MarketResearchInput(ticker="AAPL", analysis_date="2026-05-08", horizon=ResearchHorizon.SWING)
        provider = FakeStructuredLLMProvider()
        report = MarketResearchOrchestrator(llm_provider=provider, per_agent_timeout_seconds=2.0).run(
            DemoMarketResearchDataProvider().collect(request)
        )

        self.assertEqual(provider.calls, 8)
        self.assertEqual(report.metadata["llm_provider"], "fake")
        self.assertTrue(all(output.details.get("llm_refined") for output in report.raw_agent_outputs))
        self.assertEqual(report.disclaimer, RESEARCH_DISCLAIMER)

    def test_market_research_llm_config_fails_closed_in_production(self) -> None:
        with self.assertRaises(RuntimeError):
            validate_market_research_llm_settings(
                BackendSettings(
                    app_env="production",
                    enable_demo_accounts=False,
                    enable_in_process_jobs=False,
                    session_secret="x" * 32,
                    csrf_secret="y" * 32,
                    cors_origins=("https://app.example.com",),
                    database_url="postgresql://quantops:quantops@example.com:5432/quantops",
                    redis_url="redis://example.com:6379/0",
                    stripe_secret_key="sk_live_demo",
                    stripe_webhook_secret="whsec_demo",
                    stripe_price_pro_monthly="price_demo",
                    smtp_host="smtp.example.com",
                    email_from="ops@example.com",
                    s3_bucket="quantops",
                    s3_access_key_id="access",
                    s3_secret_access_key="secret",
                    market_research_llm_provider="mock",
                )
            )


if __name__ == "__main__":
    unittest.main()
