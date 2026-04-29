from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from ..core.framework import StrategyOutput


@dataclass(frozen=True)
class SentimentConfig:
    ranking_weight: float = 0.10
    forecast_weight: float = 0.25
    disagreement_penalty: float = 0.85
    smoothing_span: int = 5
    min_articles: int = 1
    min_confidence: float = 0.10
    max_position_multiplier: float = 1.50
    overlay_cost_bps: float = 1.0


class BaseSentimentModel(ABC):
    @abstractmethod
    def score_texts(self, texts: Sequence[str]) -> pd.DataFrame:
        """
        Return one row per text with:
        - label
        - score in [-1, 1]
        - confidence in [0, 1]
        - positive_prob / negative_prob / neutral_prob
        """


class FinBERTSentimentModel(BaseSentimentModel):
    """
    Finance-specific transformer sentiment model.

    Default checkpoint: ProsusAI/finbert
    Reference:
    - https://huggingface.co/ProsusAI/finbert
    - https://arxiv.org/abs/1908.10063
    """

    def __init__(
        self,
        model_name: str = "ProsusAI/finbert",
        max_length: int = 256,
        batch_size: int = 16,
        local_files_only: bool = False,
        cache_dir: str | Path = "data/hf_cache",
    ) -> None:
        self.model_name = model_name
        self.max_length = max_length
        self.batch_size = batch_size
        self.local_files_only = local_files_only
        self.cache_dir = Path(cache_dir)
        self._tokenizer = None
        self._model = None
        self._torch = None

    def _ensure_loaded(self) -> None:
        if self._model is not None and self._tokenizer is not None and self._torch is not None:
            return

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(self.cache_dir.resolve())
        os.environ["HF_HUB_CACHE"] = str((self.cache_dir / "hub").resolve())

        try:
            import torch
            from huggingface_hub import snapshot_download
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:
            raise ImportError(
                "FinBERTSentimentModel requires 'transformers', 'torch', and 'huggingface_hub'. "
                "Install them in the project venv to use transformer-based sentiment."
            ) from exc

        local_model_dir = self.cache_dir / self.model_name.replace("/", "__")

        if self.local_files_only:
            if not local_model_dir.exists():
                raise FileNotFoundError(
                    f"Local model snapshot not found at {local_model_dir}. "
                    "Set local_files_only=False once to populate the cache."
                )
        else:
            snapshot_download(
                repo_id=self.model_name,
                local_dir=str(local_model_dir),
                local_dir_use_symlinks=False,
            )

        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(
            str(local_model_dir),
            local_files_only=True,
        )
        self._model = AutoModelForSequenceClassification.from_pretrained(
            str(local_model_dir),
            local_files_only=True,
        )
        self._model.eval()

    def _predict_probabilities(self, texts: Sequence[str]) -> np.ndarray:
        self._ensure_loaded()
        assert self._torch is not None
        assert self._model is not None
        assert self._tokenizer is not None

        encodings = self._tokenizer(
            list(texts),
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )

        with self._torch.no_grad():
            logits = self._model(**encodings).logits
            probabilities = self._torch.nn.functional.softmax(logits, dim=-1)
        return probabilities.cpu().numpy()

    def _label_index(self) -> dict[str, int]:
        self._ensure_loaded()
        assert self._model is not None
        id_to_label = getattr(self._model.config, "id2label", {})
        label_to_id = {
            str(label).strip().lower(): int(index)
            for index, label in id_to_label.items()
        }
        if {"positive", "negative", "neutral"} <= set(label_to_id):
            return label_to_id
        return {"positive": 0, "negative": 1, "neutral": 2}

    def score_texts(self, texts: Sequence[str]) -> pd.DataFrame:
        if not texts:
            return pd.DataFrame(
                columns=[
                    "label",
                    "score",
                    "confidence",
                    "positive_prob",
                    "negative_prob",
                    "neutral_prob",
                ]
            )

        label_index = self._label_index()
        rows: list[dict[str, float | str]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            probabilities = self._predict_probabilities(batch)
            for vector in probabilities:
                positive_prob = float(vector[label_index["positive"]])
                negative_prob = float(vector[label_index["negative"]])
                neutral_prob = float(vector[label_index["neutral"]])
                score = float(np.clip(positive_prob - negative_prob, -1.0, 1.0))

                labels = {
                    "positive": positive_prob,
                    "negative": negative_prob,
                    "neutral": neutral_prob,
                }
                label = max(labels, key=labels.get)
                confidence = float(max(labels.values()))

                rows.append(
                    {
                        "label": label,
                        "score": score,
                        "confidence": confidence,
                        "positive_prob": positive_prob,
                        "negative_prob": negative_prob,
                        "neutral_prob": neutral_prob,
                    }
                )

        return pd.DataFrame(rows)


class RuleBasedFinancialSentimentModel(BaseSentimentModel):
    """
    Offline-safe fallback sentiment model.

    It is finance-specific rather than generic, and handles:
    - domain phrases such as "beat estimates" or "cuts guidance"
    - negation
    - intensity modifiers
    """

    POSITIVE_PHRASES = {
        "beat estimates": 1.8,
        "beats estimates": 1.8,
        "beat expectations": 1.8,
        "beats expectations": 1.8,
        "raised guidance": 1.7,
        "guidance raised": 1.7,
        "record revenue": 1.5,
        "record profit": 1.5,
        "margin expansion": 1.3,
        "strong demand": 1.2,
        "dividend increase": 1.4,
        "share buyback": 1.2,
    }

    NEGATIVE_PHRASES = {
        "missed estimates": 1.9,
        "misses estimates": 1.9,
        "missed expectations": 1.9,
        "cuts guidance": 1.8,
        "guidance cut": 1.8,
        "profit warning": 1.8,
        "sec investigation": 2.0,
        "regulatory investigation": 2.0,
        "bankruptcy filing": 2.2,
        "chapter 11": 2.2,
        "credit downgrade": 1.7,
        "impairment charge": 1.5,
        "weak demand": 1.2,
    }

    POSITIVE_WORDS = {
        "beat": 1.0,
        "beats": 1.0,
        "growth": 0.8,
        "surge": 1.0,
        "surges": 1.0,
        "strong": 0.7,
        "improved": 0.8,
        "improves": 0.8,
        "outperform": 1.0,
        "outperforms": 1.0,
        "upgrade": 1.1,
        "upgraded": 1.1,
        "buyback": 1.0,
        "accretive": 1.1,
        "resilient": 0.8,
        "profitability": 0.7,
    }

    NEGATIVE_WORDS = {
        "miss": 1.1,
        "missed": 1.1,
        "misses": 1.1,
        "cut": 0.9,
        "cuts": 0.9,
        "downgrade": 1.1,
        "downgraded": 1.1,
        "investigation": 1.4,
        "lawsuit": 1.3,
        "fraud": 2.1,
        "bankruptcy": 2.1,
        "default": 2.0,
        "loss": 1.0,
        "losses": 1.0,
        "decline": 0.8,
        "declines": 0.8,
        "weak": 0.8,
        "plunge": 1.4,
        "plunges": 1.4,
        "slump": 1.2,
        "layoffs": 1.0,
    }

    NEGATIONS = {"not", "no", "never", "without", "hardly"}
    AMPLIFIERS = {"significantly": 1.35, "sharply": 1.40, "materially": 1.30, "strongly": 1.20}
    DAMPENERS = {"slightly": 0.75, "modestly": 0.85, "marginally": 0.80}

    def __init__(self) -> None:
        self._token_pattern = re.compile(r"[a-zA-Z_]+(?:'[a-z]+)?")

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.lower()).strip()

    def _replace_phrases(self, text: str, phrases: dict[str, float], prefix: str) -> tuple[str, float]:
        score = 0.0
        for phrase, value in sorted(phrases.items(), key=lambda item: len(item[0]), reverse=True):
            if phrase in text:
                replacements = text.count(phrase)
                score += replacements * value
                text = text.replace(phrase, f"{prefix}_{phrase.replace(' ', '_')}")
        return text, score

    def _tokenize(self, text: str) -> list[str]:
        tokens = self._token_pattern.findall(text)
        normalized: list[str] = []
        for token in tokens:
            normalized.append(token)
        return normalized

    def _window_has_negation(self, tokens: Sequence[str], index: int) -> bool:
        start = max(0, index - 3)
        return any(token in self.NEGATIONS for token in tokens[start:index])

    def _intensity_multiplier(self, tokens: Sequence[str], index: int) -> float:
        multiplier = 1.0
        for token in tokens[max(0, index - 2) : index]:
            if token in self.AMPLIFIERS:
                multiplier *= self.AMPLIFIERS[token]
            if token in self.DAMPENERS:
                multiplier *= self.DAMPENERS[token]
        return multiplier

    def _score_text(self, text: str) -> dict[str, float | str]:
        normalized = self._normalize_text(text)
        normalized, positive_phrase_score = self._replace_phrases(normalized, self.POSITIVE_PHRASES, "posphrase")
        normalized, negative_phrase_score = self._replace_phrases(normalized, self.NEGATIVE_PHRASES, "negphrase")

        total = positive_phrase_score - negative_phrase_score
        tokens = self._tokenize(normalized)

        for index, token in enumerate(tokens):
            if token.startswith("posphrase_") or token.startswith("negphrase_"):
                continue

            intensity = self._intensity_multiplier(tokens, index)
            negated = self._window_has_negation(tokens, index)

            if token in self.POSITIVE_WORDS:
                contribution = self.POSITIVE_WORDS[token] * intensity
                total += -0.8 * contribution if negated else contribution
            elif token in self.NEGATIVE_WORDS:
                contribution = self.NEGATIVE_WORDS[token] * intensity
                total += 0.8 * contribution if negated else -contribution

        score = float(math.tanh(total / 3.5))
        confidence = float(min(0.99, 0.35 + abs(score) * 0.65))

        if score > 0.15:
            label = "positive"
        elif score < -0.15:
            label = "negative"
        else:
            label = "neutral"

        positive_prob = float(max(0.0, score))
        negative_prob = float(max(0.0, -score))
        residual = max(0.0, 1.0 - positive_prob - negative_prob)
        neutral_prob = float(residual)

        norm = positive_prob + negative_prob + neutral_prob
        if norm == 0:
            positive_prob = negative_prob = 0.0
            neutral_prob = 1.0
        else:
            positive_prob /= norm
            negative_prob /= norm
            neutral_prob /= norm

        return {
            "label": label,
            "score": score,
            "confidence": confidence,
            "positive_prob": positive_prob,
            "negative_prob": negative_prob,
            "neutral_prob": neutral_prob,
        }

    def score_texts(self, texts: Sequence[str]) -> pd.DataFrame:
        return pd.DataFrame([self._score_text(text or "") for text in texts])


class NewsSentimentAggregator:
    def __init__(
        self,
        model: BaseSentimentModel,
        timestamp_col: str = "timestamp",
        ticker_col: str = "ticker",
        text_col: str = "headline",
    ) -> None:
        self.model = model
        self.timestamp_col = timestamp_col
        self.ticker_col = ticker_col
        self.text_col = text_col

    def score_headlines(self, headlines: pd.DataFrame) -> pd.DataFrame:
        required = {self.timestamp_col, self.ticker_col, self.text_col}
        missing = required.difference(headlines.columns)
        if missing:
            raise ValueError(f"Missing required headline columns: {sorted(missing)}")

        frame = headlines.copy().reset_index(drop=True)
        score_defaults = {
            "label": "neutral",
            "score": 0.0,
            "confidence": 0.0,
            "positive_prob": 0.0,
            "negative_prob": 0.0,
            "neutral_prob": 1.0,
        }
        if frame.empty:
            for column, default in score_defaults.items():
                frame[column] = pd.Series(dtype="object" if isinstance(default, str) else "float64")
            frame["date"] = pd.Series(dtype="datetime64[ns]")
            frame["relevance"] = pd.Series(dtype="float64")
            frame["weight"] = pd.Series(dtype="float64")
            return frame

        frame[self.timestamp_col] = pd.to_datetime(frame[self.timestamp_col], utc=False)
        frame["date"] = frame[self.timestamp_col].dt.tz_localize(None).dt.normalize()
        frame["relevance"] = frame.get("relevance", 1.0)
        frame["relevance"] = frame["relevance"].fillna(1.0).clip(lower=0.0, upper=1.0)

        scores = self.model.score_texts(frame[self.text_col].fillna("").tolist())
        frame = pd.concat([frame, scores], axis=1)
        for column, default in score_defaults.items():
            if column not in frame.columns:
                frame[column] = default
        frame["weight"] = frame["confidence"].clip(lower=0.05) * frame["relevance"]
        return frame

    def aggregate_daily_sentiment(self, scored_headlines: pd.DataFrame) -> pd.DataFrame:
        if scored_headlines.empty:
            return pd.DataFrame(
                columns=[
                    "date",
                    "ticker",
                    "sentiment_score",
                    "sentiment_abs",
                    "confidence",
                    "article_count",
                    "positive_prob",
                    "negative_prob",
                    "neutral_prob",
                ]
            )

        def weighted_average(values: pd.Series, weights: pd.Series) -> float:
            valid = ~(values.isna() | weights.isna())
            if not valid.any():
                return 0.0
            clipped_weights = weights.loc[valid].clip(lower=1e-6)
            return float(np.average(values.loc[valid], weights=clipped_weights))

        rows: list[dict[str, float | str | pd.Timestamp]] = []
        grouped = scored_headlines.groupby(["date", self.ticker_col], sort=True)
        for (date, ticker), group in grouped:
            weights = group["weight"]
            rows.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "sentiment_score": weighted_average(group["score"], weights),
                    "sentiment_abs": weighted_average(group["score"].abs(), weights),
                    "confidence": weighted_average(group["confidence"], weights),
                    "article_count": int(len(group)),
                    "positive_prob": weighted_average(group["positive_prob"], weights),
                    "negative_prob": weighted_average(group["negative_prob"], weights),
                    "neutral_prob": weighted_average(group["neutral_prob"], weights),
                }
            )

        return pd.DataFrame(rows).sort_values(["date", "ticker"]).reset_index(drop=True)

    def build_daily_sentiment(self, headlines: pd.DataFrame) -> pd.DataFrame:
        return self.aggregate_daily_sentiment(self.score_headlines(headlines))


def build_best_available_sentiment_model() -> BaseSentimentModel:
    try:
        model = FinBERTSentimentModel(local_files_only=True)
        model.score_texts(["earnings beat expectations"])
        return model
    except Exception:
        return RuleBasedFinancialSentimentModel()


def _wide_sentiment_view(daily_sentiment: pd.DataFrame, value_col: str) -> pd.DataFrame:
    if daily_sentiment.empty:
        return pd.DataFrame()
    frame = daily_sentiment.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None).dt.normalize()
    return frame.pivot_table(index="date", columns="ticker", values=value_col, aggfunc="last").sort_index()


def build_pair_sentiment_overlay(
    daily_sentiment: pd.DataFrame,
    ticker1: str,
    ticker2: str,
    index: pd.Index,
    config: SentimentConfig,
) -> pd.DataFrame:
    overlay = pd.DataFrame(index=pd.DatetimeIndex(index))
    if daily_sentiment is None or daily_sentiment.empty:
        overlay["relative_sentiment"] = 0.0
        overlay["relative_confidence"] = 0.0
        overlay["article_coverage"] = 0.0
        overlay["sentiment_strength"] = 0.0
        return overlay

    score_wide = _wide_sentiment_view(daily_sentiment, "sentiment_score")
    confidence_wide = _wide_sentiment_view(daily_sentiment, "confidence")
    article_wide = _wide_sentiment_view(daily_sentiment, "article_count")

    combined_index = score_wide.index.union(overlay.index).sort_values()
    score_wide = score_wide.reindex(combined_index).ffill()
    confidence_wide = confidence_wide.reindex(combined_index).ffill().fillna(0.0)
    article_wide = article_wide.reindex(combined_index).ffill().fillna(0.0)

    for ticker in (ticker1, ticker2):
        if ticker not in score_wide.columns:
            score_wide[ticker] = 0.0
        if ticker not in confidence_wide.columns:
            confidence_wide[ticker] = 0.0
        if ticker not in article_wide.columns:
            article_wide[ticker] = 0.0

    smoothed_scores = score_wide[[ticker1, ticker2]].ewm(span=config.smoothing_span, adjust=False).mean()
    relative_sentiment = (smoothed_scores[ticker1] - smoothed_scores[ticker2]).clip(-1.0, 1.0)

    confidence = ((confidence_wide[ticker1] + confidence_wide[ticker2]) / 2.0).clip(0.0, 1.0)
    articles = (article_wide[ticker1].fillna(0.0) + article_wide[ticker2].fillna(0.0)).clip(lower=0.0)
    article_coverage = (articles / max(config.min_articles, 1)).clip(lower=0.0, upper=1.0)

    valid_mask = (articles >= config.min_articles) & (confidence >= config.min_confidence)
    sentiment_strength = (relative_sentiment * confidence * article_coverage).where(valid_mask, 0.0)

    overlay["relative_sentiment"] = relative_sentiment.reindex(overlay.index).fillna(0.0)
    overlay["relative_confidence"] = confidence.reindex(overlay.index).fillna(0.0)
    overlay["article_coverage"] = article_coverage.reindex(overlay.index).fillna(0.0)
    overlay["sentiment_strength"] = sentiment_strength.reindex(overlay.index).fillna(0.0)
    return overlay


def adjust_pair_rankings_with_sentiment(
    ranked_pairs: pd.DataFrame,
    daily_sentiment: pd.DataFrame,
    asof_date: pd.Timestamp,
    config: SentimentConfig,
) -> pd.DataFrame:
    if ranked_pairs.empty or daily_sentiment is None or daily_sentiment.empty:
        return ranked_pairs

    adjusted = ranked_pairs.copy()
    scored_rows: list[dict[str, float | str]] = []
    asof = pd.Timestamp(asof_date).normalize()

    sentiment_history = daily_sentiment[pd.to_datetime(daily_sentiment["date"]).dt.normalize() <= asof]
    for record in adjusted.to_dict("records"):
        overlay = build_pair_sentiment_overlay(
            daily_sentiment=sentiment_history,
            ticker1=record["Ticker_1"],
            ticker2=record["Ticker_2"],
            index=pd.DatetimeIndex([asof]),
            config=config,
        )
        latest = overlay.iloc[-1]
        adjustment = config.ranking_weight * abs(float(latest["sentiment_strength"]))
        record["Sentiment_Score"] = float(latest["relative_sentiment"])
        record["Sentiment_Confidence"] = float(latest["relative_confidence"])
        record["Sentiment_Rank_Adjustment"] = adjustment
        record["Adjusted_Rank_Score"] = float(record["Rank_Score"]) + adjustment
        scored_rows.append(record)

    return pd.DataFrame(scored_rows).sort_values("Adjusted_Rank_Score", ascending=False).reset_index(drop=True)


def apply_sentiment_overlay(
    strategy_output: StrategyOutput,
    sentiment_overlay: pd.DataFrame,
    config: SentimentConfig,
) -> StrategyOutput:
    frame = strategy_output.frame.copy()
    strategy_output.validate(extra_columns=("unit_return", "gross_return"))

    overlay = sentiment_overlay.reindex(frame.index).fillna(0.0)
    base_position = frame["position"].fillna(0.0)
    base_forecast = frame["forecast"].fillna(0.0)
    sentiment_strength = overlay["sentiment_strength"].clip(-1.0, 1.0)

    agreement = np.sign(base_position) == np.sign(sentiment_strength)
    disagreement = (base_position != 0.0) & (sentiment_strength != 0.0) & (~agreement)

    multiplier = pd.Series(1.0, index=frame.index)
    multiplier = multiplier + agreement.astype(float) * sentiment_strength.abs() * config.forecast_weight
    multiplier = multiplier - disagreement.astype(float) * sentiment_strength.abs() * config.disagreement_penalty
    multiplier = multiplier.clip(lower=0.0, upper=config.max_position_multiplier)

    adjusted_position = base_position * multiplier
    overlay_turnover = (adjusted_position - base_position).abs()
    adjusted_forecast = (base_forecast + config.forecast_weight * sentiment_strength).clip(-2.5, 2.5)
    adjusted_signal = np.sign(adjusted_position).replace({-0.0: 0.0})
    adjusted_gross_return = adjusted_position.shift(1).fillna(0.0) * frame["unit_return"].fillna(0.0)
    adjusted_cost = frame["cost_estimate"].fillna(0.0) + overlay_turnover * (config.overlay_cost_bps / 10_000.0)

    frame["signal"] = adjusted_signal
    frame["forecast"] = adjusted_forecast
    frame["position"] = adjusted_position
    frame["gross_return"] = adjusted_gross_return
    frame["cost_estimate"] = adjusted_cost
    frame["sentiment_score"] = overlay["relative_sentiment"]
    frame["sentiment_confidence"] = overlay["relative_confidence"]
    frame["sentiment_strength"] = sentiment_strength
    frame["sentiment_position_multiplier"] = multiplier
    frame["sentiment_overlay_turnover"] = overlay_turnover

    diagnostics = dict(strategy_output.diagnostics)
    diagnostics["sentiment_overlay"] = {
        "mean_strength": float(sentiment_strength.abs().mean()),
        "mean_confidence": float(overlay["relative_confidence"].mean()),
    }
    return StrategyOutput(
        name=strategy_output.name,
        frame=frame,
        diagnostics=diagnostics,
    ).validate(extra_columns=("unit_return", "gross_return"))
