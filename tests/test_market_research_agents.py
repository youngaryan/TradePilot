from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
