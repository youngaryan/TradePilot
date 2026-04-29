from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from pairs_trading.features.sentiment import (
    BaseSentimentModel,
    FinBERTSentimentModel,
    NewsSentimentAggregator,
    RuleBasedFinancialSentimentModel,
    SentimentConfig,
    adjust_pair_rankings_with_sentiment,
    apply_sentiment_overlay,
    build_pair_sentiment_overlay,
)
from pairs_trading.strategies import StrategyOutput


class StubFinBERT(FinBERTSentimentModel):
    def __init__(self, probabilities: np.ndarray) -> None:
        super().__init__(local_files_only=True)
        self._probabilities = probabilities

    def _label_index(self) -> dict[str, int]:
        return {"positive": 0, "negative": 1, "neutral": 2}

    def _predict_probabilities(self, texts: list[str]) -> np.ndarray:
        return self._probabilities[: len(texts)]


class FixedSentimentModel(BaseSentimentModel):
    def __init__(self, rows: list[dict[str, float | str]]) -> None:
        self.rows = rows

    def score_texts(self, texts: list[str]) -> pd.DataFrame:
        return pd.DataFrame(self.rows[: len(texts)])


class SentimentModelTests(unittest.TestCase):
    def test_rule_based_model_scores_financial_language(self) -> None:
        model = RuleBasedFinancialSentimentModel()
        scores = model.score_texts(
            [
                "Company beats estimates and raises guidance on strong demand",
                "Company misses estimates and cuts guidance after weak demand",
                "Results are not weak and guidance is not cut",
            ]
        )

        self.assertEqual(scores.loc[0, "label"], "positive")
        self.assertGreater(scores.loc[0, "score"], 0.40)
        self.assertEqual(scores.loc[1, "label"], "negative")
        self.assertLess(scores.loc[1, "score"], -0.40)
        self.assertGreater(scores.loc[2, "score"], -0.20)

    def test_finbert_wrapper_maps_probabilities_to_scores(self) -> None:
        model = StubFinBERT(
            probabilities=np.array(
                [
                    [0.80, 0.10, 0.10],
                    [0.05, 0.85, 0.10],
                ]
            )
        )
        scores = model.score_texts(["good", "bad"])

        self.assertEqual(scores.loc[0, "label"], "positive")
        self.assertAlmostEqual(scores.loc[0, "score"], 0.70, places=6)
        self.assertEqual(scores.loc[1, "label"], "negative")
        self.assertAlmostEqual(scores.loc[1, "score"], -0.80, places=6)


class SentimentAggregationTests(unittest.TestCase):
    def test_news_sentiment_aggregator_requires_core_headline_columns(self) -> None:
        aggregator = NewsSentimentAggregator(model=FixedSentimentModel(rows=[]))

        with self.assertRaisesRegex(ValueError, "Missing required headline columns"):
            aggregator.score_headlines(pd.DataFrame({"timestamp": ["2024-01-01"], "headline": ["missing ticker"]}))

    def test_news_sentiment_aggregator_handles_empty_headlines(self) -> None:
        aggregator = NewsSentimentAggregator(model=FixedSentimentModel(rows=[]))
        headlines = pd.DataFrame(columns=["timestamp", "ticker", "headline", "relevance"])

        scored = aggregator.score_headlines(headlines)
        daily = aggregator.aggregate_daily_sentiment(scored)

        self.assertTrue(scored.empty)
        self.assertIn("confidence", scored.columns)
        self.assertTrue(daily.empty)
        self.assertIn("sentiment_score", daily.columns)

    def test_news_sentiment_aggregator_clips_relevance_and_builds_weights(self) -> None:
        model = FixedSentimentModel(
            rows=[
                {
                    "label": "positive",
                    "score": 0.5,
                    "confidence": 0.8,
                    "positive_prob": 0.7,
                    "negative_prob": 0.1,
                    "neutral_prob": 0.2,
                },
                {
                    "label": "negative",
                    "score": -0.5,
                    "confidence": 0.8,
                    "positive_prob": 0.1,
                    "negative_prob": 0.7,
                    "neutral_prob": 0.2,
                },
            ]
        )
        headlines = pd.DataFrame(
            {
                "timestamp": ["2024-01-02 09:00:00", "2024-01-02 12:00:00"],
                "ticker": ["AAA", "AAA"],
                "headline": ["high relevance", "negative relevance"],
                "relevance": [2.0, -1.0],
            }
        )

        scored = NewsSentimentAggregator(model=model).score_headlines(headlines)

        self.assertEqual(scored["relevance"].tolist(), [1.0, 0.0])
        self.assertAlmostEqual(float(scored.loc[0, "weight"]), 0.8, places=6)
        self.assertAlmostEqual(float(scored.loc[1, "weight"]), 0.0, places=6)
        self.assertEqual(scored["date"].dt.strftime("%Y-%m-%d").tolist(), ["2024-01-02", "2024-01-02"])

    def test_news_sentiment_aggregator_builds_weighted_daily_scores(self) -> None:
        model = FixedSentimentModel(
            rows=[
                {
                    "label": "positive",
                    "score": 0.70,
                    "confidence": 0.90,
                    "positive_prob": 0.85,
                    "negative_prob": 0.05,
                    "neutral_prob": 0.10,
                },
                {
                    "label": "negative",
                    "score": -0.40,
                    "confidence": 0.50,
                    "positive_prob": 0.10,
                    "negative_prob": 0.70,
                    "neutral_prob": 0.20,
                },
            ]
        )
        headlines = pd.DataFrame(
            {
                "timestamp": ["2024-01-02 09:00:00", "2024-01-02 12:00:00"],
                "ticker": ["AAA", "AAA"],
                "headline": ["positive headline", "negative headline"],
                "relevance": [1.0, 0.5],
            }
        )

        aggregator = NewsSentimentAggregator(model=model)
        daily = aggregator.build_daily_sentiment(headlines)

        self.assertEqual(len(daily), 1)
        weighted_score = (0.70 * 0.90 * 1.0 + -0.40 * 0.50 * 0.5) / (0.90 * 1.0 + 0.50 * 0.5)
        self.assertAlmostEqual(float(daily.loc[0, "sentiment_score"]), weighted_score, places=6)
        self.assertEqual(int(daily.loc[0, "article_count"]), 2)

    def test_pair_overlay_and_position_scaling(self) -> None:
        daily_sentiment = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"]),
                "ticker": ["AAA", "BBB", "AAA", "BBB"],
                "sentiment_score": [0.7, -0.5, 0.6, -0.6],
                "sentiment_abs": [0.7, 0.5, 0.6, 0.6],
                "confidence": [0.9, 0.9, 0.8, 0.8],
                "article_count": [2, 2, 2, 2],
                "positive_prob": [0.8, 0.1, 0.7, 0.1],
                "negative_prob": [0.1, 0.7, 0.1, 0.8],
                "neutral_prob": [0.1, 0.2, 0.2, 0.1],
            }
        )
        config = SentimentConfig(
            ranking_weight=0.1,
            forecast_weight=0.3,
            disagreement_penalty=1.0,
            smoothing_span=2,
            min_articles=1,
            min_confidence=0.1,
            max_position_multiplier=1.5,
            overlay_cost_bps=1.0,
        )
        index = pd.date_range("2024-01-01", periods=2, freq="D")
        overlay = build_pair_sentiment_overlay(daily_sentiment, "AAA", "BBB", index, config)

        self.assertGreater(float(overlay["sentiment_strength"].iloc[-1]), 0.0)

        base_frame = pd.DataFrame(
            {
                "signal": [1.0, 1.0],
                "forecast": [0.5, 0.5],
                "position": [1.0, 1.0],
                "cost_estimate": [0.001, 0.001],
                "unit_return": [0.0, 0.02],
                "spread_return": [0.0, 0.02],
                "gross_return": [0.0, 0.02],
            },
            index=index,
        )
        output = StrategyOutput(name="pair", frame=base_frame, diagnostics={})
        adjusted = apply_sentiment_overlay(output, overlay, config)

        self.assertIn("sentiment_score", adjusted.frame.columns)
        self.assertGreater(float(adjusted.frame["position"].iloc[-1]), 1.0)
        self.assertGreaterEqual(float(adjusted.frame["cost_estimate"].iloc[-1]), 0.001)

    def test_sentiment_overlay_penalizes_disagreement_and_tracks_costs(self) -> None:
        index = pd.date_range("2024-01-01", periods=2, freq="D")
        base_frame = pd.DataFrame(
            {
                "signal": [1.0, 1.0],
                "forecast": [0.4, 0.4],
                "position": [1.0, 1.0],
                "cost_estimate": [0.001, 0.001],
                "unit_return": [0.0, 0.01],
                "gross_return": [0.0, 0.01],
            },
            index=index,
        )
        output = StrategyOutput(name="pair", frame=base_frame, diagnostics={})
        overlay = pd.DataFrame(
            {
                "relative_sentiment": [-0.8, -0.8],
                "relative_confidence": [0.9, 0.9],
                "article_coverage": [1.0, 1.0],
                "sentiment_strength": [-0.8, -0.8],
            },
            index=index,
        )
        adjusted = apply_sentiment_overlay(
            output,
            overlay,
            SentimentConfig(forecast_weight=0.25, disagreement_penalty=1.0, max_position_multiplier=1.5, overlay_cost_bps=2.0),
        )

        self.assertLess(float(adjusted.frame["position"].iloc[-1]), 1.0)
        self.assertLess(float(adjusted.frame["forecast"].iloc[-1]), 0.4)
        self.assertGreater(float(adjusted.frame["cost_estimate"].iloc[-1]), 0.001)
        self.assertGreater(float(adjusted.frame["sentiment_overlay_turnover"].iloc[-1]), 0.0)
        self.assertGreater(float(adjusted.diagnostics["sentiment_overlay"]["mean_strength"]), 0.0)

    def test_rank_adjustment_uses_sentiment_strength(self) -> None:
        ranked_pairs = pd.DataFrame(
            {
                "Sector": ["SectorA"],
                "Ticker_1": ["AAA"],
                "Ticker_2": ["BBB"],
                "Correlation": [0.8],
                "Coint_PValue": [0.01],
                "Half_Life": [12.0],
                "Hedge_Ratio": [1.0],
                "Spread_Std": [0.5],
                "Latest_ZScore": [0.4],
                "Rank_Score": [0.7],
            }
        )
        daily_sentiment = pd.DataFrame(
            {
                "date": pd.to_datetime(["2024-01-02", "2024-01-02"]),
                "ticker": ["AAA", "BBB"],
                "sentiment_score": [0.8, -0.6],
                "sentiment_abs": [0.8, 0.6],
                "confidence": [0.9, 0.9],
                "article_count": [3, 3],
                "positive_prob": [0.85, 0.10],
                "negative_prob": [0.05, 0.75],
                "neutral_prob": [0.10, 0.15],
            }
        )
        adjusted = adjust_pair_rankings_with_sentiment(
            ranked_pairs=ranked_pairs,
            daily_sentiment=daily_sentiment,
            asof_date=pd.Timestamp("2024-01-02"),
            config=SentimentConfig(),
        )

        self.assertIn("Adjusted_Rank_Score", adjusted.columns)
        self.assertGreater(float(adjusted.loc[0, "Adjusted_Rank_Score"]), 0.7)


if __name__ == "__main__":
    unittest.main()
