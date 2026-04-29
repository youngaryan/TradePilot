from __future__ import annotations

import json
from pathlib import Path
import unittest

import pandas as pd

from pairs_trading.data.news import (
    CachedNewsSentimentProvider,
    CompositeHeadlineProvider,
    DailySentimentFileProvider,
    HeadlineProvider,
    LocalNewsFileProvider,
)
from pairs_trading.data.sentiment_accumulator import ShadowSentimentAccumulator
from pairs_trading.features.sentiment import BaseSentimentModel
from tests.common import fresh_test_dir


class FixedSentimentModel(BaseSentimentModel):
    def score_texts(self, texts: list[str]) -> pd.DataFrame:
        rows = []
        for index, _ in enumerate(texts):
            score = 0.6 if index % 2 == 0 else -0.2
            rows.append(
                {
                    "label": "positive" if score > 0 else "negative",
                    "score": score,
                    "confidence": 0.8,
                    "positive_prob": max(score, 0.0),
                    "negative_prob": max(-score, 0.0),
                    "neutral_prob": 1.0 - abs(score),
                }
            )
        return pd.DataFrame(rows)


class StubHeadlineProvider(HeadlineProvider):
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def get_headlines(self, tickers, start, end) -> pd.DataFrame:
        return self.frame.copy()


class NewsProviderTests(unittest.TestCase):
    def test_local_news_file_provider_filters_by_ticker_and_date(self) -> None:
        data_dir = fresh_test_dir("artifacts/test_news/provider")
        news_path = data_dir / "headlines.csv"
        pd.DataFrame(
            {
                "timestamp": ["2024-01-01 08:00:00", "2024-01-03 10:00:00", "2024-01-02 09:00:00"],
                "ticker": ["AAA", "BBB", "AAA"],
                "headline": ["A", "B", "C"],
                "relevance": [1.0, 1.0, 0.5],
            }
        ).to_csv(news_path, index=False)

        provider = LocalNewsFileProvider(news_path)
        headlines = provider.get_headlines(["AAA"], "2024-01-01", "2024-01-02")

        self.assertEqual(list(headlines["ticker"].unique()), ["AAA"])
        self.assertEqual(len(headlines), 2)
        self.assertEqual(headlines["headline"].tolist(), ["A", "C"])

    def test_cached_news_sentiment_provider_builds_and_reuses_daily_sentiment(self) -> None:
        data_dir = fresh_test_dir("artifacts/test_news/cache")
        news_path = data_dir / "headlines.csv"
        pd.DataFrame(
            {
                "timestamp": ["2024-01-01 08:00:00", "2024-01-01 09:00:00", "2024-01-02 09:00:00"],
                "ticker": ["AAA", "AAA", "AAA"],
                "headline": ["good", "bad", "good again"],
                "relevance": [1.0, 0.5, 1.0],
            }
        ).to_csv(news_path, index=False)

        provider = CachedNewsSentimentProvider(
            headline_provider=LocalNewsFileProvider(news_path),
            sentiment_model=FixedSentimentModel(),
            cache_dir=data_dir / "cache",
        )

        first = provider.get_daily_sentiment(["AAA"], "2024-01-01", "2024-01-02")
        self.assertEqual(len(first), 2)
        self.assertTrue(any(Path(data_dir / "cache").rglob("*.parquet")))

        news_path.unlink()
        second = provider.get_daily_sentiment(["AAA"], "2024-01-01", "2024-01-02")
        pd.testing.assert_frame_equal(first, second, check_dtype=False)

    def test_daily_sentiment_file_provider_filters_rows(self) -> None:
        data_dir = fresh_test_dir("artifacts/test_news/daily_file")
        daily_path = data_dir / "daily_sentiment.parquet"
        pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-01"]),
                "ticker": ["AAA", "AAA", "BBB"],
                "sentiment_score": [0.4, 0.5, -0.2],
                "sentiment_abs": [0.4, 0.5, 0.2],
                "confidence": [0.8, 0.8, 0.7],
                "article_count": [2, 2, 1],
                "positive_prob": [0.6, 0.7, 0.1],
                "negative_prob": [0.1, 0.1, 0.5],
                "neutral_prob": [0.3, 0.2, 0.4],
            }
        ).to_parquet(daily_path)

        provider = DailySentimentFileProvider(daily_path)
        filtered = provider.get_daily_sentiment(["AAA"], "2024-01-01", "2024-01-02")

        self.assertEqual(filtered["ticker"].tolist(), ["AAA", "AAA"])
        self.assertEqual(len(filtered), 2)

    def test_composite_provider_deduplicates_same_story_across_sources(self) -> None:
        frame_one = pd.DataFrame(
            {
                "timestamp": ["2024-01-01 09:00:00"],
                "ticker": ["AAA"],
                "headline": ["Apple beats estimates and raises guidance"],
                "title": ["Apple beats estimates"],
                "source": ["source_one"],
                "url": ["https://example.com/story"],
                "relevance": [0.9],
            }
        )
        frame_two = pd.DataFrame(
            {
                "timestamp": ["2024-01-01 09:05:00"],
                "ticker": ["AAA"],
                "headline": ["Apple beats estimates and raises guidance for the quarter"],
                "title": ["Apple beats estimates"],
                "source": ["source_two"],
                "url": ["https://example.com/story"],
                "relevance": [0.8],
            }
        )

        provider = CompositeHeadlineProvider(
            [
                StubHeadlineProvider(frame_one),
                StubHeadlineProvider(frame_two),
            ]
        )
        headlines = provider.get_headlines(["AAA"], "2024-01-01", "2024-01-02")

        self.assertEqual(len(headlines), 1)
        self.assertEqual(int(headlines.loc[0, "source_count"]), 2)
        self.assertEqual(int(headlines.loc[0, "duplicate_count"]), 2)
        self.assertIn("source_one", headlines.loc[0, "source_list"])
        self.assertIn("source_two", headlines.loc[0, "source_list"])

    def test_shadow_accumulator_persists_raw_scored_and_daily_sentiment(self) -> None:
        data_dir = fresh_test_dir("artifacts/test_news/accumulator")
        provider = StubHeadlineProvider(
            pd.DataFrame(
                {
                    "timestamp": ["2024-01-01 09:00:00", "2024-01-01 09:05:00"],
                    "ticker": ["AAA", "AAA"],
                    "headline": ["AAA beats estimates", "AAA beats estimates"],
                    "source": ["source_one", "source_two"],
                    "url": ["https://example.com/a", "https://example.com/a"],
                    "relevance": [1.0, 0.8],
                }
            )
        )
        accumulator = ShadowSentimentAccumulator(
            headline_provider=provider,
            sentiment_model=FixedSentimentModel(),
            output_dir=data_dir,
        )

        result = accumulator.run(["AAA"], "2024-01-01", "2024-01-02")

        self.assertEqual(result.fetched_headlines, 2)
        self.assertEqual(result.stored_headlines, 1)
        self.assertTrue(Path(result.raw_headlines_path).exists())
        self.assertTrue(Path(result.scored_headlines_path).exists())
        self.assertTrue(Path(result.daily_sentiment_path).exists())
        daily = pd.read_parquet(result.daily_sentiment_path)
        self.assertEqual(daily["ticker"].tolist(), ["AAA"])
        self.assertEqual(int(daily.loc[0, "article_count"]), 1)

    def test_shadow_accumulator_appends_new_runs_without_erasing_existing_symbols(self) -> None:
        data_dir = fresh_test_dir("artifacts/test_news/accumulator_append")
        provider = StubHeadlineProvider(
            pd.DataFrame(
                {
                    "timestamp": ["2026-04-20 09:00:00"],
                    "ticker": ["AAA"],
                    "headline": ["AAA beats estimates"],
                    "source": ["source_one"],
                    "url": ["https://example.com/aaa"],
                    "relevance": [1.0],
                }
            )
        )
        accumulator = ShadowSentimentAccumulator(
            headline_provider=provider,
            sentiment_model=FixedSentimentModel(),
            output_dir=data_dir,
        )

        first = accumulator.run(["AAA"], "2026-04-20", "2026-04-20")
        provider.frame = pd.DataFrame(
            {
                "timestamp": ["2026-04-21 10:00:00"],
                "ticker": ["EURUSD"],
                "headline": ["EUR/USD rises as the dollar weakens"],
                "source": ["fx_feed"],
                "url": ["https://example.com/eurusd"],
                "relevance": [0.9],
            }
        )
        second = accumulator.run(["EURUSD"], "2026-04-21", "2026-04-21")

        self.assertEqual(first.fetched_headlines, 1)
        self.assertEqual(second.fetched_headlines, 1)
        self.assertEqual(second.stored_headlines, 2)
        raw = pd.read_parquet(second.raw_headlines_path)
        daily = pd.read_parquet(second.daily_sentiment_path)
        metadata = json.loads(Path(second.metadata_path).read_text(encoding="utf-8"))
        self.assertEqual(sorted(raw["ticker"].astype(str).str.upper().unique().tolist()), ["AAA", "EURUSD"])
        self.assertEqual(sorted(daily["ticker"].astype(str).str.upper().unique().tolist()), ["AAA", "EURUSD"])
        self.assertEqual(metadata["tickers"], ["EURUSD"])

    def test_shadow_accumulator_preserves_existing_cache_when_latest_fetch_is_empty(self) -> None:
        data_dir = fresh_test_dir("artifacts/test_news/accumulator_empty_append")
        provider = StubHeadlineProvider(
            pd.DataFrame(
                {
                    "timestamp": ["2026-04-20 09:00:00"],
                    "ticker": ["AAA"],
                    "headline": ["AAA raises guidance"],
                    "source": ["source_one"],
                    "url": ["https://example.com/aaa"],
                    "relevance": [1.0],
                }
            )
        )
        accumulator = ShadowSentimentAccumulator(
            headline_provider=provider,
            sentiment_model=FixedSentimentModel(),
            output_dir=data_dir,
        )
        accumulator.run(["AAA"], "2026-04-20", "2026-04-20")

        provider.frame = pd.DataFrame(columns=["timestamp", "ticker", "headline", "source", "url", "relevance"])
        result = accumulator.run(["EURUSD"], "2026-04-21", "2026-04-21")

        self.assertEqual(result.fetched_headlines, 0)
        self.assertEqual(result.stored_headlines, 1)
        raw = pd.read_parquet(result.raw_headlines_path)
        daily = pd.read_parquet(result.daily_sentiment_path)
        self.assertEqual(raw["ticker"].tolist(), ["AAA"])
        self.assertEqual(daily["ticker"].tolist(), ["AAA"])


if __name__ == "__main__":
    unittest.main()
