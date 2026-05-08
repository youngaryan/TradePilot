from __future__ import annotations

import json
from pathlib import Path
import unittest

from pairs_trading.api import build_paper_dashboard_payload
from pairs_trading.apps.cli import main
from pairs_trading.backend.backtest_services import BacktestService
from pairs_trading.backend.market_research_services import MarketResearchService
from pairs_trading.backend.paper_services import PaperService
from pairs_trading.backend.sentiment_services import SentimentService
from pairs_trading.core import PortfolioManager, StrategyOutput
from pairs_trading.data import CachedParquetProvider, LocalNewsFileProvider
from pairs_trading.engines import CostModel, WalkForwardBacktester
from pairs_trading.operations import run_paper_batch
from pairs_trading.reporting import ExperimentVisualizer, PaperDashboardVisualizer
from tests.common import fresh_test_dir


class ArchitectureLayoutTests(unittest.TestCase):
    def test_new_domain_imports_resolve(self) -> None:
        self.assertIsNotNone(main)
        self.assertIsNotNone(CostModel)
        self.assertIsNotNone(CachedParquetProvider)
        self.assertIsNotNone(run_paper_batch)
        self.assertIsNotNone(ExperimentVisualizer)
        self.assertIsNotNone(WalkForwardBacktester)
        self.assertIsNotNone(StrategyOutput)
        self.assertIsNotNone(PortfolioManager)
        self.assertIsNotNone(LocalNewsFileProvider)
        self.assertIsNotNone(PaperDashboardVisualizer)
        self.assertIsNotNone(BacktestService)
        self.assertIsNotNone(MarketResearchService)
        self.assertIsNotNone(PaperService)
        self.assertIsNotNone(SentimentService)

    def test_frontend_paper_payload_uses_stable_shape(self) -> None:
        workspace = fresh_test_dir("artifacts/test_architecture_payload")
        state_dir = workspace / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        state_dir.joinpath("trend.json").write_text(
            json.dumps(
                {
                    "strategy_name": "trend",
                    "mode": "asset",
                    "initial_cash": 100000.0,
                    "cash": 25000.0,
                    "positions": {"SPY": 100.0},
                    "history": [
                        {
                            "timestamp": "2026-04-24T00:00:00",
                            "mode": "asset",
                            "equity_after": 101000.0,
                            "daily_pnl": 1000.0,
                            "rebalance_cost_pnl": -5.0,
                            "net_return_since_inception": 0.01,
                            "cash_after": 25000.0,
                            "gross_exposure_notional": 76000.0,
                            "gross_exposure_ratio": 0.752475,
                            "position_count": 1,
                            "trade_count": 2,
                            "turnover_notional": 12000.0,
                            "positions": {"SPY": 100.0},
                            "target_weights": {"SPY": 0.75},
                            "metadata": {"pipeline": "etf_trend"},
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        state_dir.joinpath("trend_latest_orders.json").write_text(
            json.dumps([{"instrument": "SPY", "side": "buy", "notional": 12000.0}], indent=2),
            encoding="utf-8",
        )
        summary_path = workspace / "paper_batch_summary.json"
        summary_path.write_text(
            json.dumps({"asof_date": "2026-04-24", "run_timestamp_utc": "20260424T220000Z", "visuals": {"overview": "index.html"}}),
            encoding="utf-8",
        )

        payload = build_paper_dashboard_payload(state_dir=state_dir, batch_summary_path=summary_path)

        self.assertEqual(payload["asof_date"], "2026-04-24")
        self.assertEqual(payload["totals"]["equity"], 101000.0)
        self.assertEqual(payload["leaderboard"][0]["strategy"], "trend")
        self.assertEqual(payload["strategies"][0]["latest_orders"][0]["instrument"], "SPY")
        self.assertIn("visuals", payload)


if __name__ == "__main__":
    unittest.main()
