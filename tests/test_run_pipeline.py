from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import pandas as pd

from pairs_trading.apps.cli import load_daily_sentiment, load_events, load_sector_map, run_directional_pipeline, run_stat_arb_pipeline
from pairs_trading.features.sentiment import RuleBasedFinancialSentimentModel
from tests.common import fresh_test_dir, synthetic_directional_prices, synthetic_prices_and_sector_map


class RunPipelineHelperTests(unittest.TestCase):
    def test_load_sector_map_from_json_and_csv(self) -> None:
        data_dir = fresh_test_dir("artifacts/test_runner")
        json_path = data_dir / "sector_map.json"
        csv_path = data_dir / "sector_map.csv"

        json_path.write_text(json.dumps({"aaa": "SectorA", "bbb": "SectorB"}), encoding="utf-8")
        pd.DataFrame({"ticker": ["ccc", "ddd"], "sector": ["SectorC", "SectorD"]}).to_csv(csv_path, index=False)

        from_json = load_sector_map(json_path)
        from_csv = load_sector_map(csv_path)

        self.assertEqual(from_json, {"AAA": "SectorA", "BBB": "SectorB"})
        self.assertEqual(from_csv, {"CCC": "SectorC", "DDD": "SectorD"})

    def test_load_daily_sentiment_from_precomputed_file(self) -> None:
        data_dir = fresh_test_dir("artifacts/test_runner/sentiment")
        daily_path = data_dir / "daily_sentiment.parquet"
        pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-01"]),
                "ticker": ["AAA", "AAA", "BBB"],
                "sentiment_score": [0.5, 0.4, -0.2],
                "sentiment_abs": [0.5, 0.4, 0.2],
                "confidence": [0.8, 0.7, 0.6],
                "article_count": [2, 2, 1],
                "positive_prob": [0.7, 0.6, 0.1],
                "negative_prob": [0.1, 0.1, 0.5],
                "neutral_prob": [0.2, 0.3, 0.4],
            }
        ).to_parquet(daily_path)

        loaded = load_daily_sentiment(
            tickers=["AAA"],
            start="2024-01-01",
            end="2024-01-02",
            news_provider_names=None,
            news_files=None,
            daily_sentiment_file=str(daily_path),
            use_finbert=False,
            local_finbert_only=False,
            sentiment_cache_dir=str(data_dir / "cache"),
            news_api_key=None,
            alphavantage_api_key=None,
            benzinga_api_key=None,
        )

        self.assertEqual(loaded["ticker"].tolist(), ["AAA", "AAA"])

    def test_load_events_combines_local_and_official_sec_filings(self) -> None:
        data_dir = fresh_test_dir("artifacts/test_runner/events")
        event_path = data_dir / "events.csv"
        pd.DataFrame(
            {
                "timestamp": ["2024-01-02"],
                "ticker": ["AAA"],
                "event_score": [0.4],
                "confidence": [0.8],
                "event_type": ["manual_event"],
            }
        ).to_csv(event_path, index=False)

        sec_events = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2024-02-01"]),
                "ticker": ["AAA"],
                "event_score": [0.2],
                "confidence": [0.35],
                "event_type": ["earnings_release_8k"],
                "source": ["sec_submissions"],
                "form": ["8-K"],
            }
        )

        with patch("pairs_trading.apps.cli.CachedEventProvider.get_events", return_value=sec_events):
            events = load_events(
                tickers=["AAA"],
                start="2024-01-01",
                end="2024-12-31",
                event_file=str(event_path),
                event_cache_dir=str(data_dir / "cache"),
                edgar_user_agent="PairsTradingTest [test@example.com]",
                use_sec_companyfacts=False,
                include_sec_filings=True,
                sec_filing_forms=["8-K", "10-Q", "10-K"],
            )

        self.assertIsNotNone(events)
        assert events is not None
        self.assertEqual(set(events["event_type"]), {"manual_event", "earnings_release_8k"})

    def test_run_stat_arb_pipeline_with_local_news_provider(self) -> None:
        prices, sector_map = synthetic_prices_and_sector_map()
        data_dir = fresh_test_dir("artifacts/test_runner/integration")
        sector_map_path = data_dir / "sector_map.json"
        news_path_one = data_dir / "headlines_one.csv"
        news_path_two = data_dir / "headlines_two.csv"

        sector_map_path.write_text(json.dumps(sector_map), encoding="utf-8")
        pd.DataFrame(
            {
                "timestamp": [
                    "2020-06-01 09:00:00",
                    "2020-06-01 10:00:00",
                ],
                "ticker": ["A1", "A2"],
                "headline": [
                    "A1 beats estimates and raises guidance",
                    "A2 misses estimates and cuts guidance",
                ],
                "relevance": [1.0, 1.0],
            }
        ).to_csv(news_path_one, index=False)
        pd.DataFrame(
            {
                "timestamp": [
                    "2020-06-02 09:00:00",
                    "2020-06-02 10:00:00",
                    "2020-06-01 09:05:00",
                ],
                "ticker": ["B1", "B2", "A1"],
                "headline": [
                    "B1 faces regulatory investigation",
                    "B2 sees strong demand and margin expansion",
                    "A1 beats estimates and raises guidance",
                ],
                "relevance": [1.0, 1.0, 0.9],
            }
        ).to_csv(news_path_two, index=False)

        with patch("pairs_trading.apps.cli.CachedParquetProvider.get_close_prices", return_value=prices), patch(
            "pairs_trading.apps.cli.build_best_available_sentiment_model",
            return_value=RuleBasedFinancialSentimentModel(),
        ):
            output = run_stat_arb_pipeline(
                sector_map_path=str(sector_map_path),
                start="2020-01-01",
                end="2023-12-31",
                experiment_name="integration_test",
                artifact_root=str(data_dir / "experiments"),
                news_provider_names=["local"],
                news_files=[str(news_path_one), str(news_path_two)],
                use_finbert=False,
            )

        self.assertIn("summary", output)
        self.assertTrue(output["result"].artifact_dir.exists())
        self.assertIn("report", output["visuals"])

    def test_run_directional_pipeline_with_ma_cross_strategy(self) -> None:
        prices = synthetic_directional_prices()
        data_dir = fresh_test_dir("artifacts/test_runner/directional")

        with patch("pairs_trading.apps.cli.CachedParquetProvider.get_close_prices", return_value=prices):
            output = run_directional_pipeline(
                strategy_name="ma_cross",
                symbols=["TREND", "MEAN", "BREAK"],
                start="2020-01-01",
                end="2023-12-31",
                experiment_name="directional_integration",
                artifact_root=str(data_dir / "experiments"),
                fast_window=15,
                slow_window=60,
            )

        self.assertIn("summary", output)
        self.assertTrue(output["result"].artifact_dir.exists())
        self.assertIn("report", output["visuals"])


if __name__ == "__main__":
    unittest.main()
