from __future__ import annotations

import json
from pathlib import Path
import unittest

import pandas as pd

from pairs_trading.data.market import MarketDataProvider
from pairs_trading.operations.paper_trading import run_paper_batch
from pairs_trading.operations.paper_state import ledger_key
from tests.common import (
    fresh_test_dir,
    synthetic_directional_prices,
    synthetic_etf_prices,
    synthetic_event_panel,
    synthetic_prices_and_sector_map,
)


class StaticPriceProvider(MarketDataProvider):
    def __init__(self, prices: pd.DataFrame) -> None:
        self.prices = prices.sort_index()

    def get_close_prices(
        self,
        symbols,
        start,
        end,
        interval: str = "1d",
    ) -> pd.DataFrame:
        del interval
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        requested = list(dict.fromkeys(symbols))
        frame = self.prices.loc[(self.prices.index >= start_ts) & (self.prices.index < end_ts), requested]
        return frame.dropna(how="all").sort_index()


class PaperTradingTests(unittest.TestCase):
    def test_run_paper_batch_persists_multi_strategy_state(self) -> None:
        workspace = fresh_test_dir("artifacts/test_paper")
        state_dir = workspace / "state"
        artifact_root = workspace / "runs"

        etf_prices = synthetic_etf_prices().copy()
        stat_prices, sector_map = synthetic_prices_and_sector_map()
        directional_prices = synthetic_directional_prices()[["TREND", "MEAN"]].copy()

        etf_prices.index = pd.date_range("2020-01-01", periods=len(etf_prices), freq="B")
        stat_prices = stat_prices.copy()
        directional_prices.index = pd.date_range("2020-01-01", periods=len(directional_prices), freq="B")

        combined = pd.concat([etf_prices, stat_prices, directional_prices], axis=1).sort_index()
        provider = StaticPriceProvider(combined)

        event_frame = synthetic_event_panel(directional_prices.index, ["TREND", "MEAN"])
        events_path = workspace / "events.csv"
        event_frame.to_csv(events_path, index=False)

        sector_map_path = workspace / "sector_map.json"
        sector_map_path.write_text(json.dumps(sector_map), encoding="utf-8")

        config_path = workspace / "paper_config.json"
        config_path.write_text(
            json.dumps(
                {
                    "execution": {
                        "initial_cash": 100000.0,
                        "commission_bps": 0.5,
                        "slippage_bps": 1.0,
                        "min_trade_notional": 25.0,
                        "weight_tolerance": 0.001,
                    },
                    "strategies": [
                        {
                            "name": "etf_core",
                            "pipeline": "etf_trend",
                            "symbols": ["SPY", "QQQ", "TLT", "GLD", "XLE", "XLF"],
                            "lookback_bars": 500,
                        },
                        {
                            "name": "stat_arb_shadow",
                            "pipeline": "stat_arb",
                            "sector_map_path": str(sector_map_path),
                            "lookback_bars": 620,
                        },
                        {
                            "name": "event_shadow",
                            "pipeline": "edgar_event",
                            "symbols": ["TREND", "MEAN"],
                            "event_file": str(events_path),
                            "lookback_bars": 400,
                        },
                        {
                            "name": "ema_shadow",
                            "pipeline": "ema_cross",
                            "symbols": ["TREND", "MEAN"],
                            "lookback_bars": 220,
                            "params": {
                                "ema_fast_window": 10,
                                "ema_slow_window": 35,
                            },
                        },
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        first_asof = directional_prices.index[620]
        second_asof = directional_prices.index[621]

        first_run = run_paper_batch(
            deployment_config_path=config_path,
            asof_date=first_asof,
            state_dir=state_dir,
            artifact_root=artifact_root,
            price_provider=provider,
        )
        second_run = run_paper_batch(
            deployment_config_path=config_path,
            asof_date=second_asof,
            state_dir=state_dir,
            artifact_root=artifact_root,
            price_provider=provider,
        )

        self.assertEqual(set(first_run["strategies"]), {"etf_core", "stat_arb_shadow", "event_shadow", "ema_shadow"})
        self.assertEqual(set(second_run["strategies"]), {"etf_core", "stat_arb_shadow", "event_shadow", "ema_shadow"})

        for strategy_name in ("etf_core", "stat_arb_shadow", "event_shadow", "ema_shadow"):
            state_path = state_dir / f"{ledger_key(strategy_name)}.json"
            self.assertTrue(state_path.exists())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(state["history"]), 2)
            self.assertIn("positions", state)
            self.assertIn("cash", state)
            self.assertEqual(state["schema_version"], 2)
            self.assertEqual(state["revision"], 2)
            self.assertIn("latest_orders", state)

        self.assertEqual(second_run["strategies"]["stat_arb_shadow"]["mode"], "synthetic")
        self.assertEqual(second_run["strategies"]["etf_core"]["mode"], "asset")
        self.assertEqual(second_run["strategies"]["event_shadow"]["mode"], "asset")
        self.assertEqual(second_run["strategies"]["ema_shadow"]["mode"], "asset")

        self.assertTrue(Path(first_run["artifact_dir"]).exists())
        self.assertTrue(Path(second_run["artifact_dir"]).exists())
        self.assertNotEqual(first_run["run_id"], second_run["run_id"])
        self.assertNotEqual(first_run["artifact_dir"], second_run["artifact_dir"])
        self.assertTrue((Path(second_run["artifact_dir"]) / "paper_leaderboard.parquet").exists())
        self.assertTrue((Path(second_run["artifact_dir"]) / "paper_batch_summary.json").exists())
        self.assertIn("visuals", second_run)
        self.assertTrue(Path(second_run["visuals"]["run_dashboard"]["pages"]["overview"]).exists())
        self.assertTrue(Path(second_run["visuals"]["run_dashboard"]["pages"]["capital_flow"]).exists())
        self.assertTrue(Path(second_run["visuals"]["run_dashboard"]["pages"]["glossary"]).exists())
        self.assertTrue(Path(second_run["visuals"]["live_dashboard"]["pages"]["overview"]).exists())


if __name__ == "__main__":
    unittest.main()
