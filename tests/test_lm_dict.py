from __future__ import annotations

import pandas as pd
import pytest

from pairs_trading.features.lm_dict import (
    CONSTRAINING_WORDS,
    LITIGIOUS_WORDS,
    NEGATIVE_WORDS,
    POSITIVE_WORDS,
    UNCERTAINTY_WORDS,
    LoughranMcDonaldScorer,
)
from pairs_trading.features.sentiment import BaseSentimentModel


class TestWordListsSize:
    def test_positive_has_words(self):
        assert len(POSITIVE_WORDS) > 100

    def test_negative_has_words(self):
        assert len(NEGATIVE_WORDS) > 1000

    def test_uncertainty_has_words(self):
        assert len(UNCERTAINTY_WORDS) > 50

    def test_litigious_has_words(self):
        assert len(LITIGIOUS_WORDS) > 200

    def test_constraining_has_words(self):
        assert len(CONSTRAINING_WORDS) > 30


class TestLoughranMcDonaldScorer:
    def test_implements_base(self):
        assert isinstance(LoughranMcDonaldScorer(), BaseSentimentModel)

    def test_score_texts_returns_dataframe(self):
        scorer = LoughranMcDonaldScorer()
        result = scorer.score_texts(["good earnings growth"])
        assert isinstance(result, pd.DataFrame)
        assert list(result.columns) == ["label", "score", "confidence", "positive_prob", "negative_prob", "neutral_prob"]

    def test_positive_text(self):
        scorer = LoughranMcDonaldScorer()
        result = scorer.score_texts([
            "strong growth excellent performance outstanding results "
            "profit increased significantly improve"
        ])
        row = result.iloc[0]
        assert row["label"] == "positive"
        assert row["score"] > 0
        assert row["positive_prob"] > row["negative_prob"]

    def test_negative_text(self):
        scorer = LoughranMcDonaldScorer()
        result = scorer.score_texts([
            "loss decline bankruptcy failure adverse deficit "
            "impairment write-down litigation"
        ])
        row = result.iloc[0]
        assert row["label"] == "negative"
        assert row["score"] < 0
        assert row["positive_prob"] < row["negative_prob"]

    def test_neutral_text(self):
        scorer = LoughranMcDonaldScorer()
        result = scorer.score_texts(["the company filed its quarterly report with the commission"])
        row = result.iloc[0]
        assert row["label"] == "neutral"
        assert abs(row["score"]) < 0.2

    def test_empty_text(self):
        scorer = LoughranMcDonaldScorer()
        result = scorer.score_texts(["", "   ", "!@#$%"])
        assert len(result) == 3
        for _, row in result.iterrows():
            assert row["label"] == "neutral"
            assert row["score"] == 0.0

    def test_score_range(self):
        scorer = LoughranMcDonaldScorer()
        texts = [
            "excellent amazing outstanding superb wonderful great",
            "terrible awful horrible disastrous catastrophic loss",
            "the cat sat on the mat",
        ]
        result = scorer.score_texts(texts)
        for _, row in result.iterrows():
            assert -1.0 <= row["score"] <= 1.0
            assert 0.0 <= row["confidence"] <= 1.0

    def test_confidence_empty(self):
        scorer = LoughranMcDonaldScorer()
        result = scorer.score_texts(["xyzzy zork"])
        assert result.iloc[0]["confidence"] == 0.0

    def test_mixed_sentiment(self):
        scorer = LoughranMcDonaldScorer()
        result = scorer.score_texts(["good growth but high debt and loss"])
        row = result.iloc[0]
        assert row["positive_prob"] > 0
        assert row["negative_prob"] > 0

    def test_custom_word_lists(self):
        scorer = LoughranMcDonaldScorer(
            positive_words=frozenset({"good", "great"}),
            negative_words=frozenset({"bad", "terrible"}),
        )
        result = scorer.score_texts(["good and bad"])
        row = result.iloc[0]
        assert row["positive_prob"] == 0.5
        assert row["negative_prob"] == 0.5
