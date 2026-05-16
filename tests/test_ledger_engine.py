from __future__ import annotations

import unittest

import pandas as pd

from pairs_trading.engines.backtesting import CostModel, WalkForwardBacktester, WalkForwardConfig
from pairs_trading.engines.ledger import LedgerBacktestSimulator, LedgerConfig
from pairs_trading.engines.risk import RiskConfig, RiskManager
from pairs_trading.strategies.directional import BuyAndHoldStrategy


class LedgerEngineTests(unittest.TestCase):
    def test_buy_and_hold_synthetic_path_has_known_equity(self) -> None:
        index = pd.date_range("2024-01-01", periods=3, freq="D")
        prices = pd.DataFrame({"AAA": [100.0, 110.0, 121.0]}, index=index)
        targets = pd.DataFrame({"target_weight_AAA": [1.0, 1.0, 1.0]}, index=index)

        result = LedgerBacktestSimulator(
            LedgerConfig(initial_cash=1_000.0, execution_mode="close_to_close")
        ).run(strategy_frame=targets, prices=prices)

        self.assertAlmostEqual(float(result.snapshots["portfolio_value"].iloc[0]), 1_000.0, places=6)
        self.assertAlmostEqual(float(result.snapshots["portfolio_value"].iloc[-1]), 1_210.0, places=6)
        self.assertAlmostEqual(float(result.benchmark_snapshots["benchmark_value"].iloc[-1]), 1_210.0, places=6)
        self.assertEqual(len(result.fills), 1)

    def test_directional_first_test_bar_carries_prior_position(self) -> None:
        index = pd.date_range("2024-01-01", periods=5, freq="D")
        prices = pd.DataFrame({"AAA": [100.0, 100.0, 110.0, 121.0, 133.1]}, index=index)

        output = BuyAndHoldStrategy("AAA", transaction_cost_bps=0.0).run_fold(
            train_data=prices.iloc[:2],
            test_data=prices.iloc[2:],
        )

        self.assertAlmostEqual(float(output.frame["gross_return"].iloc[0]), 0.10, places=6)
        self.assertAlmostEqual(float(output.frame["turnover"].iloc[0]), 0.0, places=6)

    def test_walk_forward_rejects_overlapping_test_windows(self) -> None:
        index = pd.date_range("2024-01-01", periods=12, freq="D")
        prices = pd.DataFrame({"AAA": range(100, 112)}, index=index, dtype=float)

        backtester = WalkForwardBacktester(
            strategy=BuyAndHoldStrategy("AAA"),
            prices=prices,
            config=WalkForwardConfig(train_bars=4, test_bars=4, step_bars=2),
            cost_model=CostModel(initial_cash=1_000.0),
            experiment_root="artifacts/tmp_tests",
        )

        with self.assertRaisesRegex(ValueError, "Overlapping walk-forward"):
            backtester.run("overlap_rejected")

    def test_cash_accounting_identity_holds_after_fills(self) -> None:
        index = pd.date_range("2024-01-01", periods=3, freq="D")
        prices = pd.DataFrame({"AAA": [100.0, 105.0, 95.0]}, index=index)
        targets = pd.DataFrame({"target_weight_AAA": [0.5, 0.0, 0.0]}, index=index)
        result = LedgerBacktestSimulator(LedgerConfig(initial_cash=1_000.0, execution_mode="close_to_close")).run(
            strategy_frame=targets,
            prices=prices,
        )

        position_rows = result.position_snapshots
        for timestamp, snapshot in result.snapshots.iterrows():
            positions_value = float(position_rows.loc[position_rows["timestamp"] == timestamp, "market_value"].sum())
            self.assertAlmostEqual(float(snapshot["portfolio_value"]), float(snapshot["cash"]) + positions_value, places=6)

    def test_fees_slippage_and_spread_are_reflected_in_fill_and_cash(self) -> None:
        index = pd.date_range("2024-01-01", periods=2, freq="D")
        prices = pd.DataFrame({"AAA": [100.0, 100.0]}, index=index)
        targets = pd.DataFrame({"target_weight_AAA": [1.0, 1.0]}, index=index)
        config = LedgerConfig(
            initial_cash=1_000.0,
            execution_mode="close_to_close",
            commission_bps=10.0,
            spread_bps=20.0,
            slippage_bps=5.0,
        )

        result = LedgerBacktestSimulator(config).run(strategy_frame=targets, prices=prices)
        fill = result.fills[0]
        expected_price = 100.0 * (1.0 + 0.0015)
        expected_quantity = 1_000.0 / (expected_price * 1.001)

        self.assertAlmostEqual(fill.price, expected_price, places=6)
        self.assertAlmostEqual(fill.quantity, expected_quantity, places=6)
        self.assertAlmostEqual(fill.commission, fill.quantity * fill.price * 0.001, places=6)
        self.assertAlmostEqual(float(result.snapshots["cash"].iloc[0]), 0.0, places=6)

    def test_rejects_unaffordable_levered_order_without_partial_fill(self) -> None:
        index = pd.date_range("2024-01-01", periods=2, freq="D")
        prices = pd.DataFrame({"AAA": [100.0, 100.0]}, index=index)
        targets = pd.DataFrame({"target_weight_AAA": [2.0, 2.0]}, index=index)

        result = LedgerBacktestSimulator(LedgerConfig(initial_cash=1_000.0, execution_mode="close_to_close")).run(
            strategy_frame=targets,
            prices=prices,
        )

        self.assertEqual(len(result.fills), 0)
        self.assertEqual(result.rejected_orders[0].reason, "insufficient_cash")

    def test_closed_trade_pnl_matches_fill_ledger(self) -> None:
        index = pd.date_range("2024-01-01", periods=3, freq="D")
        prices = pd.DataFrame({"AAA": [100.0, 110.0, 110.0]}, index=index)
        targets = pd.DataFrame({"target_weight_AAA": [1.0, 0.0, 0.0]}, index=index)

        result = LedgerBacktestSimulator(LedgerConfig(initial_cash=1_000.0, execution_mode="close_to_close")).run(
            strategy_frame=targets,
            prices=prices,
        )

        self.assertEqual(len(result.trades), 1)
        self.assertAlmostEqual(result.trades[0].pnl, 100.0, places=6)
        self.assertAlmostEqual(float(result.snapshots["portfolio_value"].iloc[-1]), 1_100.0, places=6)

    def test_ledger_rejects_next_open_until_ohlc_prices_are_supported(self) -> None:
        index = pd.date_range("2024-01-01", periods=2, freq="D")
        prices = pd.DataFrame({"AAA": [100.0, 101.0]}, index=index)
        targets = pd.DataFrame({"target_weight_AAA": [1.0, 1.0]}, index=index)

        with self.assertRaisesRegex(ValueError, "next_open"):
            LedgerBacktestSimulator(LedgerConfig(initial_cash=1_000.0, execution_mode="next_open")).run(
                strategy_frame=targets,
                prices=prices,
            )

    def test_walk_forward_ledger_config_preserves_extended_cost_model(self) -> None:
        index = pd.date_range("2024-01-01", periods=8, freq="D")
        prices = pd.DataFrame({"AAA": [100.0 + value for value in range(8)]}, index=index)
        cost_model = CostModel(
            initial_cash=1_000.0,
            borrow_bps_annual=2_520.0,
            funding_bps_annual=1_260.0,
            delay_bars=2,
        )
        backtester = WalkForwardBacktester(
            strategy=BuyAndHoldStrategy("AAA"),
            prices=prices,
            config=WalkForwardConfig(train_bars=4, test_bars=2, step_bars=2),
            cost_model=cost_model,
            experiment_root="artifacts/tmp_tests",
        )

        ledger_config = backtester._ledger_config()

        self.assertEqual(ledger_config.borrow_bps_annual, cost_model.borrow_bps_annual)
        self.assertEqual(ledger_config.funding_bps_annual, cost_model.funding_bps_annual)
        self.assertEqual(ledger_config.delay_bars, cost_model.delay_bars)

    def test_ledger_applies_borrow_funding_delay_and_strategy_costs(self) -> None:
        index = pd.date_range("2024-01-01", periods=3, freq="D")
        prices = pd.DataFrame({"AAA": [100.0, 100.0, 100.0]}, index=index)
        targets = pd.DataFrame(
            {
                "target_weight_AAA": [-0.5, -0.5, -0.5],
                "cost_estimate": [0.002, 0.0, 0.0],
            },
            index=index,
        )
        no_extra_cost = LedgerBacktestSimulator(
            LedgerConfig(initial_cash=1_000.0, execution_mode="close_to_close")
        ).run(strategy_frame=targets, prices=prices)
        with_extra_cost = LedgerBacktestSimulator(
            LedgerConfig(
                initial_cash=1_000.0,
                execution_mode="close_to_close",
                borrow_bps_annual=2_520.0,
                funding_bps_annual=1_260.0,
                delay_bars=1,
            )
        ).run(strategy_frame=targets, prices=prices)

        snapshots = with_extra_cost.snapshots

        self.assertLess(
            float(snapshots["portfolio_value"].iloc[-1]),
            float(no_extra_cost.snapshots["portfolio_value"].iloc[-1]),
        )
        self.assertGreater(float(snapshots["borrow_cost"].sum()), 0.0)
        self.assertGreater(float(snapshots["funding_cost"].sum()), 0.0)
        self.assertGreater(float(snapshots["latency_cost"].sum()), 0.0)
        self.assertGreater(float(snapshots["strategy_cost"].sum()), 0.0)

    def test_risk_manager_scales_explicit_target_weights(self) -> None:
        index = pd.date_range("2024-01-01", periods=2, freq="D")
        frame = pd.DataFrame(
            {
                "signal": [1.0, 1.0],
                "forecast": [1.0, 1.0],
                "position": [2.0, 2.0],
                "gross_return": [0.0, 0.0],
                "turnover": [2.0, 0.0],
                "cost_estimate": [0.0, 0.0],
                "target_weight_AAA": [1.0, 1.0],
                "target_weight_BBB": [1.0, 1.0],
            },
            index=index,
        )

        adjusted = RiskManager(RiskConfig(max_gross_leverage=1.0, max_net_leverage=1.0)).apply(frame)

        self.assertAlmostEqual(float(adjusted["target_weight_AAA"].iloc[0]), 0.5, places=6)
        self.assertAlmostEqual(float(adjusted["target_weight_BBB"].iloc[0]), 0.5, places=6)
        self.assertAlmostEqual(float(adjusted["risk_gross_exposure"].iloc[0]), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
