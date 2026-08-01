from __future__ import annotations

import unittest

import pandas as pd

from pairs_trading.engines.backtesting import CostModel, WalkForwardBacktester, WalkForwardConfig
from pairs_trading.pipelines import DirectionalPipelineConfig, DirectionalStrategyPipeline, MultiTimeframeSignalConfig
from pairs_trading.core.portfolio import PortfolioManager
from pairs_trading.strategies import (
    AdaptiveRegimeStrategy,
    BollingerBandMeanReversionStrategy,
    BuyAndHoldStrategy,
    DonchianBreakoutStrategy,
    EMACrossStrategy,
    KeltnerChannelBreakoutStrategy,
    MACDTrendStrategy,
    MovingAverageCrossStrategy,
    PriceSMADeviationStrategy,
    RSIMeanReversionStrategy,
    StochasticOscillatorStrategy,
    TimeSeriesMomentumStrategy,
    VolatilityTargetTrendStrategy,
)
from tests.common import fresh_test_dir, synthetic_directional_prices


class DirectionalStrategyTests(unittest.TestCase):
    def assertStandardDirectionalOutput(self, output) -> None:
        for column in ("signal", "forecast", "position", "cost_estimate", "unit_return", "gross_return"):
            self.assertIn(column, output.frame.columns)
            self.assertFalse(output.frame[column].isna().any(), column)
        self.assertIn("strategy_type", output.diagnostics)

    def test_moving_average_cross_emits_standardized_output(self) -> None:
        prices = synthetic_directional_prices()
        strategy = MovingAverageCrossStrategy(symbol="TREND", fast_window=15, slow_window=60)
        output = strategy.run_fold(train_data=prices.iloc[:300][["TREND"]], test_data=prices.iloc[300:380][["TREND"]])

        self.assertStandardDirectionalOutput(output)
        self.assertGreater(float(output.frame["forecast"].abs().sum()), 0.0)
        self.assertGreater(float(output.frame["position"].abs().sum()), 0.0)

    def test_rsi_mean_reversion_and_donchian_breakout_take_positions(self) -> None:
        prices = synthetic_directional_prices()
        rsi_strategy = RSIMeanReversionStrategy(symbol="MEAN", rsi_window=10, lower_entry=35.0, upper_entry=65.0)
        breakout_strategy = DonchianBreakoutStrategy(symbol="BREAK", breakout_window=40, exit_window=15)

        rsi_output = rsi_strategy.run_fold(train_data=prices.iloc[:300][["MEAN"]], test_data=prices.iloc[300:380][["MEAN"]])
        breakout_output = breakout_strategy.run_fold(train_data=prices.iloc[:320][["BREAK"]], test_data=prices.iloc[320:420][["BREAK"]])

        self.assertGreater(float(rsi_output.frame["position"].abs().sum()), 0.0)
        self.assertGreater(float(breakout_output.frame["position"].abs().sum()), 0.0)

    def test_new_directional_strategies_emit_standardized_outputs(self) -> None:
        prices = synthetic_directional_prices()
        strategies = [
            BuyAndHoldStrategy(symbol="TREND"),
            EMACrossStrategy(symbol="TREND", fast_window=10, slow_window=35),
            PriceSMADeviationStrategy(symbol="MEAN", window=25, entry_z=0.9),
            StochasticOscillatorStrategy(symbol="MEAN", window=12, lower_entry=30.0, upper_entry=70.0),
            BollingerBandMeanReversionStrategy(symbol="MEAN", window=20, num_std=1.4),
            MACDTrendStrategy(symbol="TREND", fast_window=8, slow_window=24, signal_window=6),
            KeltnerChannelBreakoutStrategy(symbol="BREAK", window=25, atr_multiplier=0.8),
            VolatilityTargetTrendStrategy(symbol="TREND", trend_window=70, volatility_window=15),
            TimeSeriesMomentumStrategy(symbol="TREND", lookbacks=(21, 63, 126)),
            AdaptiveRegimeStrategy(symbol="MEAN", fast_window=20, slow_window=70, mean_reversion_window=25, volatility_window=20),
        ]

        active_position_count = 0
        for strategy in strategies:
            symbol = strategy.symbol
            output = strategy.run_fold(train_data=prices.iloc[:360][[symbol]], test_data=prices.iloc[360:520][[symbol]])
            self.assertStandardDirectionalOutput(output)
            self.assertGreater(float(output.frame["forecast"].abs().sum()), 0.0, output.name)
            active_position_count += int(float(output.frame["position"].abs().sum()) > 0.0)

        self.assertGreaterEqual(active_position_count, 8)


class DirectionalPipelineTests(unittest.TestCase):
    def test_directional_pipeline_runs_walk_forward_backtest(self) -> None:
        prices = synthetic_directional_prices()
        pipeline = DirectionalStrategyPipeline(
            strategy_factory=lambda symbol: MovingAverageCrossStrategy(symbol=symbol, fast_window=15, slow_window=60),
            portfolio_manager=PortfolioManager(
                max_leverage=1.0,
                risk_per_trade=0.03,
                volatility_window=15,
                max_strategy_weight=0.4,
            ),
            config=DirectionalPipelineConfig.from_symbols(["TREND", "MEAN", "BREAK"], min_history=120),
            name="directional_smoke",
        )

        snapshots = []
        result = WalkForwardBacktester(
            strategy=pipeline,
            prices=prices,
            config=WalkForwardConfig(train_bars=260, test_bars=80, step_bars=80, bars_per_year=252),
            cost_model=CostModel(commission_bps=0.2, spread_bps=0.5, slippage_bps=0.3, borrow_bps_annual=15.0),
            experiment_root=fresh_test_dir("artifacts/test_runs/directional"),
        ).run("directional_smoke", progress_callback=snapshots.append)

        self.assertGreater(len(result.fold_metrics), 0)
        self.assertTrue(any(column.startswith("weight_") for column in result.equity_curve.columns))
        self.assertTrue(result.artifact_dir.exists())
        self.assertEqual(len(snapshots), len(result.fold_metrics))
        self.assertEqual(snapshots[-1]["completed_folds"], len(result.fold_metrics))
        self.assertIn("equity_curve", snapshots[-1])

    def test_short_term_directional_pipeline_applies_four_hour_confirmation(self) -> None:
        prices = synthetic_directional_prices()
        prices.index = pd.date_range("2024-01-02 09:00", periods=len(prices), freq="h")
        pipeline = DirectionalStrategyPipeline(
            strategy_factory=lambda symbol: EMACrossStrategy(symbol=symbol, fast_window=8, slow_window=24),
            portfolio_manager=PortfolioManager(max_leverage=1.0, risk_per_trade=0.03, volatility_window=12, max_strategy_weight=0.4),
            config=DirectionalPipelineConfig.from_symbols(["TREND"], min_history=80),
            name="short_term_directional",
            multi_timeframe=MultiTimeframeSignalConfig(execution_interval="1h", confirmation_interval="4h", fast_window=4, slow_window=12),
            timeframe_metadata={"trading_mode": "short_term", "execution_interval": "1h", "signal_intervals": ["1h", "4h"]},
        )

        output = pipeline.run_fold(prices.iloc[:180][["TREND"]], prices.iloc[180:260][["TREND"]])

        self.assertEqual(output.diagnostics["trading_mode"], "short_term")
        self.assertEqual(output.diagnostics["signal_intervals"], ["1h", "4h"])
        self.assertTrue(any(column.startswith("weight_") for column in output.frame.columns))


if __name__ == "__main__":
    unittest.main()
