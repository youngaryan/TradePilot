from __future__ import annotations

import unittest

import pandas as pd

from pairs_trading.strategies import (
    OxfordBollingerMomentumStrategy,
    OxfordBollingerPercentBReversalStrategy,
    OxfordCombinedDonchianStrategy,
    OxfordDualMomentumROCStrategy,
    OxfordKeltnerThreePhaseStrategy,
    OxfordNormalizedRegressionSlopeStrategy,
    OxfordPriceMomentumStrategy,
    OxfordRSI2PullbackStrategy,
    OxfordVolatilityClusteringStrategy,
    OxfordWyckoffRangeReversionStrategy,
)
from tests.common import synthetic_directional_prices


class OxfordStrategyTests(unittest.TestCase):
    def _assert_standard_output(self, output) -> None:
        for column in ("signal", "forecast", "position", "cost_estimate", "unit_return", "gross_return"):
            self.assertIn(column, output.frame.columns)
            self.assertFalse(output.frame[column].isna().any(), column)
        self.assertIn("source_url", output.diagnostics)
        self.assertIn("implementation_assumption", output.diagnostics)
        expected_gross = output.frame["position"].shift(1).fillna(0.0) * output.frame["unit_return"].fillna(0.0)
        pd.testing.assert_series_equal(output.frame["gross_return"].iloc[1:], expected_gross.iloc[1:], check_names=False)

    def _strategies(self):
        return [
            OxfordCombinedDonchianStrategy(symbol="BREAK", entry_window=35, exit_window=12),
            OxfordPriceMomentumStrategy(symbol="TREND", slow_lookback=80, fast_lookback_index=0.75),
            OxfordDualMomentumROCStrategy(symbol="TREND", slow_lookback=80, fast_lookback=35, time_exit_bars=30),
            OxfordBollingerMomentumStrategy(symbol="BREAK", window=35, num_std=1.0),
            OxfordKeltnerThreePhaseStrategy(symbol="BREAK", window=30, atr_multiplier=0.7),
            OxfordNormalizedRegressionSlopeStrategy(symbol="TREND", window=60, growth_threshold=0.005),
            OxfordBollingerPercentBReversalStrategy(symbol="MEAN", band_window=18, trend_window=45, num_std=1.0, confirmation_bars=2),
            OxfordRSI2PullbackStrategy(symbol="TREND", setup_window=45, rsi_entry=55.0, exit_window=8),
            OxfordWyckoffRangeReversionStrategy(symbol="MEAN", range_window=25, exit_window=8),
            OxfordVolatilityClusteringStrategy(symbol="BREAK", range_window=18, move_window=2, time_exit_bars=3),
        ]

    def test_oxford_strategies_emit_standardized_outputs(self) -> None:
        prices = synthetic_directional_prices()
        active_position_count = 0

        for strategy in self._strategies():
            symbol = strategy.symbol
            output = strategy.run_fold(train_data=prices.iloc[:420][[symbol]], test_data=prices.iloc[420:620][[symbol]])

            self._assert_standard_output(output)
            self.assertEqual(output.diagnostics["symbol"], symbol)
            self.assertTrue(output.diagnostics["strategy_type"].startswith("oxford_"))
            self.assertGreater(float(output.frame["forecast"].abs().sum()), 0.0, output.name)
            active_position_count += int(float(output.frame["position"].abs().sum()) > 0.0)

        self.assertGreaterEqual(active_position_count, 8)

    def test_oxford_strategies_handle_missing_and_insufficient_data(self) -> None:
        prices = synthetic_directional_prices()
        prices.iloc[430:436, prices.columns.get_loc("BREAK")] = float("nan")
        prices.iloc[440:445, prices.columns.get_loc("MEAN")] = float("nan")
        prices.iloc[450:453, prices.columns.get_loc("TREND")] = float("nan")

        for strategy in self._strategies():
            symbol = strategy.symbol
            output = strategy.run_fold(train_data=prices.iloc[:420][[symbol]], test_data=prices.iloc[420:500][[symbol]])
            self._assert_standard_output(output)

            short_output = strategy.run_fold(train_data=prices.iloc[:12][[symbol]], test_data=prices.iloc[12:30][[symbol]])
            self._assert_standard_output(short_output)
            self.assertEqual(len(short_output.frame), 18)


if __name__ == "__main__":
    unittest.main()
