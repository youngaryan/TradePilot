from __future__ import annotations

import unittest

import pandas as pd

from pairs_trading.core.portfolio import PortfolioManager
from pairs_trading.strategies import StrategyOutput


class PortfolioManagerTests(unittest.TestCase):
    def test_allocate_capital_caps_leverage_and_emits_portfolio_columns(self) -> None:
        index = pd.date_range("2024-01-01", periods=5, freq="D")

        pair_one = StrategyOutput(
            name="AAA_BBB",
            frame=pd.DataFrame(
                {
                    "signal": [1.0, 1.0, 1.0, 0.0, 0.0],
                    "forecast": [0.8, 0.7, 0.6, 0.2, 0.1],
                    "position": [1.0, 1.0, 1.0, 0.0, 0.0],
                    "cost_estimate": [0.001] * 5,
                    "unit_return": [0.0, 0.01, -0.005, 0.0, 0.0],
                    "spread_return": [0.0, 0.01, -0.005, 0.0, 0.0],
                    "gross_return": [0.0, 0.01, -0.005, 0.0, 0.0],
                    "short_exposure_per_unit": [0.5] * 5,
                    "gross_exposure_per_unit": [2.0] * 5,
                },
                index=index,
            ),
            diagnostics={},
        )

        pair_two = StrategyOutput(
            name="CCC_DDD",
            frame=pd.DataFrame(
                {
                    "signal": [-1.0, -1.0, 0.0, 0.0, 0.0],
                    "forecast": [0.9, 0.8, 0.3, 0.2, 0.1],
                    "position": [-1.0, -1.0, 0.0, 0.0, 0.0],
                    "cost_estimate": [0.0015] * 5,
                    "unit_return": [0.0, 0.015, 0.0, 0.0, 0.0],
                    "spread_return": [0.0, 0.015, 0.0, 0.0, 0.0],
                    "gross_return": [0.0, -0.015, 0.0, 0.0, 0.0],
                    "short_exposure_per_unit": [0.55] * 5,
                    "gross_exposure_per_unit": [2.0] * 5,
                },
                index=index,
            ),
            diagnostics={},
        )

        manager = PortfolioManager(
            max_leverage=1.0,
            risk_per_trade=0.05,
            volatility_window=2,
            max_strategy_weight=0.75,
        )
        portfolio = manager.allocate_capital(
            {"AAA_BBB": pair_one, "CCC_DDD": pair_two},
            portfolio_name="test_portfolio",
        )

        self.assertIn("gross_return", portfolio.frame.columns)
        self.assertIn("weight_AAA_BBB", portfolio.frame.columns)
        self.assertIn("weight_CCC_DDD", portfolio.frame.columns)

        leverage = portfolio.frame[["weight_AAA_BBB", "weight_CCC_DDD"]].abs().sum(axis=1)
        self.assertLessEqual(float(leverage.max()), 1.000001)
        self.assertGreaterEqual(float(portfolio.frame["cost_estimate"].max()), 0.0)


if __name__ == "__main__":
    unittest.main()
