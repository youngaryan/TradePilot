from __future__ import annotations

import json
from pathlib import Path
import unittest

from pairs_trading.reporting.paper import PaperDashboardVisualizer
from tests.common import fresh_test_dir


class PaperVisualizationTests(unittest.TestCase):
    def test_visualizer_builds_multi_page_dashboard(self) -> None:
        workspace = fresh_test_dir("artifacts/test_paper_visuals")
        state_dir = workspace / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        output_dir = workspace / "visuals"

        state_dir.joinpath("trend_core.json").write_text(
            json.dumps(
                {
                    "strategy_name": "trend_core",
                    "mode": "asset",
                    "initial_cash": 100000.0,
                    "cash": 24000.0,
                    "positions": {"SPY": 210.0, "QQQ": 120.0},
                    "instrument_prices": {"SPY": 510.0, "QQQ": 420.0},
                    "history": [
                        {
                            "timestamp": "2026-04-22T00:00:00",
                            "mode": "asset",
                            "equity_before": 100000.0,
                            "equity_after": 100050.0,
                            "daily_pnl": 80.0,
                            "rebalance_cost_pnl": -30.0,
                            "net_return_since_inception": 0.0005,
                            "cash_after": 22000.0,
                            "gross_exposure_notional": 78050.0,
                            "gross_exposure_ratio": 0.7801,
                            "position_count": 2,
                            "trade_count": 2,
                            "turnover_notional": 18000.0,
                        },
                        {
                            "timestamp": "2026-04-23T00:00:00",
                            "mode": "asset",
                            "equity_before": 100120.0,
                            "equity_after": 100090.0,
                            "daily_pnl": 60.0,
                            "rebalance_cost_pnl": -30.0,
                            "net_return_since_inception": 0.0009,
                            "cash_after": 24000.0,
                            "gross_exposure_notional": 76090.0,
                            "gross_exposure_ratio": 0.7602,
                            "position_count": 2,
                            "trade_count": 1,
                            "turnover_notional": 9000.0,
                        },
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        state_dir.joinpath("trend_core_latest_orders.json").write_text(
            json.dumps(
                [
                    {
                        "instrument": "QQQ",
                        "side": "buy",
                        "quantity": 5.0,
                        "mark_price": 420.0,
                        "execution_price": 420.5,
                        "target_weight": 0.32,
                        "commission": 1.05,
                        "notional": 2102.5,
                    }
                ],
                indent=2,
            ),
            encoding="utf-8",
        )

        state_dir.joinpath("residual_shadow.json").write_text(
            json.dumps(
                {
                    "strategy_name": "residual_shadow",
                    "mode": "synthetic",
                    "initial_cash": 100000.0,
                    "cash": 63000.0,
                    "positions": {"residual_A": -150.0, "residual_B": 150.0},
                    "instrument_prices": {"residual_A": 101.0, "residual_B": 103.0},
                    "history": [
                        {
                            "timestamp": "2026-04-22T00:00:00",
                            "mode": "synthetic",
                            "equity_before": 100000.0,
                            "equity_after": 99970.0,
                            "daily_pnl": -10.0,
                            "rebalance_cost_pnl": -20.0,
                            "net_return_since_inception": -0.0003,
                            "cash_after": 64000.0,
                            "gross_exposure_notional": 74000.0,
                            "gross_exposure_ratio": 0.7402,
                            "position_count": 2,
                            "trade_count": 2,
                            "turnover_notional": 12000.0,
                        },
                        {
                            "timestamp": "2026-04-23T00:00:00",
                            "mode": "synthetic",
                            "equity_before": 99980.0,
                            "equity_after": 99960.0,
                            "daily_pnl": -5.0,
                            "rebalance_cost_pnl": -15.0,
                            "net_return_since_inception": -0.0004,
                            "cash_after": 63000.0,
                            "gross_exposure_notional": 76000.0,
                            "gross_exposure_ratio": 0.7603,
                            "position_count": 2,
                            "trade_count": 2,
                            "turnover_notional": 15000.0,
                        },
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        state_dir.joinpath("residual_shadow_latest_orders.json").write_text("[]", encoding="utf-8")

        batch_summary = {
            "run_timestamp_utc": "20260424T210000Z",
            "asof_date": "2026-04-23",
            "artifact_dir": str(workspace / "run"),
            "state_dir": str(state_dir),
            "leaderboard": [
                {
                    "strategy": "trend_core",
                    "pipeline": "etf_trend",
                    "mode": "asset",
                    "equity_after": 100090.0,
                    "net_return_since_inception": 0.0009,
                    "daily_pnl": 60.0,
                    "trade_count": 1,
                    "gross_exposure_ratio": 0.7602,
                },
                {
                    "strategy": "residual_shadow",
                    "pipeline": "stat_arb",
                    "mode": "synthetic",
                    "equity_after": 99960.0,
                    "net_return_since_inception": -0.0004,
                    "daily_pnl": -5.0,
                    "trade_count": 2,
                    "gross_exposure_ratio": 0.7603,
                },
            ],
            "strategies": {
                "trend_core": {
                    "timestamp": "2026-04-23T00:00:00",
                    "mode": "asset",
                    "equity_before": 100120.0,
                    "equity_after": 100090.0,
                    "daily_pnl": 60.0,
                    "rebalance_cost_pnl": -30.0,
                    "net_return_since_inception": 0.0009,
                    "cash_after": 24000.0,
                    "gross_exposure_notional": 76090.0,
                    "gross_exposure_ratio": 0.7602,
                    "position_count": 2,
                    "trade_count": 1,
                    "turnover_notional": 9000.0,
                    "positions": {"SPY": 210.0, "QQQ": 120.0},
                    "target_weights": {"SPY": 0.44, "QQQ": 0.32},
                    "metadata": {"pipeline": "etf_trend"},
                    "diagnostics": {"selected_symbols": ["SPY", "QQQ"], "strategy_type": "etf_trend_momentum"},
                },
                "residual_shadow": {
                    "timestamp": "2026-04-23T00:00:00",
                    "mode": "synthetic",
                    "equity_before": 99980.0,
                    "equity_after": 99960.0,
                    "daily_pnl": -5.0,
                    "rebalance_cost_pnl": -15.0,
                    "net_return_since_inception": -0.0004,
                    "cash_after": 63000.0,
                    "gross_exposure_notional": 76000.0,
                    "gross_exposure_ratio": 0.7603,
                    "position_count": 2,
                    "trade_count": 2,
                    "turnover_notional": 15000.0,
                    "positions": {"residual_A": -150.0, "residual_B": 150.0},
                    "target_weights": {"residual_A": -0.20, "residual_B": 0.20},
                    "metadata": {"pipeline": "stat_arb"},
                    "diagnostics": {
                        "strategy_count": 2,
                        "strategy_summaries": [
                            {"strategy": "residual_A", "mean_abs_forecast": 1.2},
                            {"strategy": "residual_B", "mean_abs_forecast": 1.1},
                        ],
                    },
                },
            },
        }

        dashboard = PaperDashboardVisualizer(output_dir).create_dashboard(
            batch_summary=batch_summary,
            state_dir=state_dir,
            allow_legacy=True,
        )

        self.assertTrue(Path(dashboard["pages"]["overview"]).exists())
        self.assertTrue(Path(dashboard["pages"]["capital_flow"]).exists())
        self.assertTrue(Path(dashboard["pages"]["glossary"]).exists())
        self.assertTrue(Path(dashboard["pages"]["strategy_pages"]["trend_core"]).exists())
        self.assertTrue(Path(dashboard["pages"]["strategy_pages"]["residual_shadow"]).exists())
        self.assertTrue(Path(dashboard["charts"]["combined_equity"]).exists())
        self.assertTrue(Path(dashboard["charts"]["trend_core_equity"]).exists())

        overview_html = Path(dashboard["pages"]["overview"]).read_text(encoding="utf-8")
        self.assertIn("Shadow Paper Dashboard", overview_html)
        self.assertIn("How To Read The Money", overview_html)
        self.assertIn("Capital Flow", overview_html)


if __name__ == "__main__":
    unittest.main()
