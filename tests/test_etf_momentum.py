from __future__ import annotations

import unittest

from pairs_trading.engines.backtesting import CostModel, WalkForwardBacktester, WalkForwardConfig
from pairs_trading.pipelines import ETFMomentumConfig, ETFTrendMomentumPipeline
from tests.common import fresh_test_dir, synthetic_etf_prices


class ETFMomentumTests(unittest.TestCase):
    def test_etf_trend_momentum_pipeline_runs(self) -> None:
        prices = synthetic_etf_prices()
        symbols = list(prices.columns)
        pipeline = ETFTrendMomentumPipeline(
            ETFMomentumConfig.from_symbols(symbols, top_n=3, trend_window=180),
            name="etf_trend_test",
        )

        result = WalkForwardBacktester(
            strategy=pipeline,
            prices=prices,
            config=WalkForwardConfig(
                train_bars=500,
                test_bars=84,
                step_bars=84,
                bars_per_year=252,
                purge_bars=5,
            ),
            cost_model=CostModel(
                commission_bps=0.5,
                spread_bps=0.75,
                slippage_bps=0.75,
                market_impact_bps=0.5,
                borrow_bps_annual=0.0,
                delay_bars=1,
            ),
            experiment_root=fresh_test_dir("artifacts/test_runs/etf_momentum"),
        ).run("etf_trend_test")

        self.assertGreater(len(result.fold_metrics), 0)
        self.assertIn("dsr", result.summary)
        self.assertTrue(any(column.startswith("weight_") for column in result.equity_curve.columns))


if __name__ == "__main__":
    unittest.main()
