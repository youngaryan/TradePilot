from __future__ import annotations

import unittest

from pairs_trading.api import build_strategy_catalog
from pairs_trading.apps.cli import DIRECTIONAL_PIPELINES, _build_directional_strategy_factory


class StrategyCatalogTests(unittest.TestCase):
    def test_catalog_documents_directional_and_core_sleeves(self) -> None:
        catalog = build_strategy_catalog()
        ids = {item["id"] for item in catalog}

        for strategy_id in DIRECTIONAL_PIPELINES:
            self.assertIn(strategy_id, ids)

        self.assertIn("etf_trend", ids)
        self.assertIn("stat_arb", ids)
        self.assertIn("graph_stat_arb", ids)
        self.assertIn("edgar_event", ids)
        self.assertIn("pead_sentiment", ids)

        for item in catalog:
            self.assertTrue(item["summary"])
            self.assertTrue(item["how_it_works"])
            self.assertTrue(item["best_for"])
            self.assertTrue(item["watch_out"])
            self.assertTrue(item["example_cli"].startswith(".\\.venv\\Scripts\\python.exe"))
            self.assertIsInstance(item["paper_config_example"], dict)

    def test_cli_factory_supports_every_directional_catalog_strategy(self) -> None:
        for strategy_id in DIRECTIONAL_PIPELINES:
            factory, min_history = _build_directional_strategy_factory(strategy_id)
            strategy = factory("SPY")
            self.assertEqual(strategy.symbol, "SPY")
            self.assertGreater(min_history, 0)


if __name__ == "__main__":
    unittest.main()
