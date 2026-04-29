from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Sequence

import pandas as pd

from ..features.sentiment import BaseSentimentModel, NewsSentimentAggregator
from .news import HeadlineProvider, deduplicate_headlines


@dataclass(frozen=True)
class SentimentAccumulationResult:
    output_dir: str
    raw_headlines_path: str
    scored_headlines_path: str
    daily_sentiment_path: str
    metadata_path: str
    fetched_headlines: int
    stored_headlines: int
    daily_rows: int


class ShadowSentimentAccumulator:
    """
    Accumulate free news into a local proprietary sentiment dataset.

    The accumulator is intentionally file-based and append-friendly:
    - raw_headlines.parquet stores deduplicated source text,
    - scored_headlines.parquet stores model-level headline scores,
    - daily_sentiment.parquet is the file consumed by PEAD/stat-arb overlays.
    """

    def __init__(
        self,
        headline_provider: HeadlineProvider,
        sentiment_model: BaseSentimentModel,
        output_dir: str | Path = "data/sentiment_cache/shadow",
    ) -> None:
        self.headline_provider = headline_provider
        self.sentiment_model = sentiment_model
        self.aggregator = NewsSentimentAggregator(model=sentiment_model)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_headlines_path = self.output_dir / "raw_headlines.parquet"
        self.scored_headlines_path = self.output_dir / "scored_headlines.parquet"
        self.daily_sentiment_path = self.output_dir / "daily_sentiment.parquet"
        self.metadata_path = self.output_dir / "metadata.json"

    @staticmethod
    def _json_ready(value):
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): ShadowSentimentAccumulator._json_ready(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [ShadowSentimentAccumulator._json_ready(item) for item in value]
        return value

    @staticmethod
    def _read_existing(path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame()
        frame = pd.read_parquet(path)
        if "timestamp" in frame.columns:
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=False).dt.tz_localize(None)
        if "date" in frame.columns:
            frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None).dt.normalize()
        return frame

    def run(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> SentimentAccumulationResult:
        fetched = self.headline_provider.get_headlines(tickers=tickers, start=start, end=end)
        existing = self._read_existing(self.raw_headlines_path)
        combined = pd.concat([existing, fetched], axis=0, ignore_index=True, sort=False) if not existing.empty else fetched
        raw_headlines = deduplicate_headlines(combined)

        if raw_headlines.empty:
            scored_headlines = self.aggregator.score_headlines(
                pd.DataFrame(columns=["timestamp", "ticker", "headline", "relevance"])
            )
            daily_sentiment = self.aggregator.aggregate_daily_sentiment(scored_headlines)
        else:
            scored_headlines = self.aggregator.score_headlines(raw_headlines)
            daily_sentiment = self.aggregator.aggregate_daily_sentiment(scored_headlines)

        raw_headlines.to_parquet(self.raw_headlines_path)
        scored_headlines.to_parquet(self.scored_headlines_path)
        daily_sentiment.to_parquet(self.daily_sentiment_path)

        result = SentimentAccumulationResult(
            output_dir=str(self.output_dir),
            raw_headlines_path=str(self.raw_headlines_path),
            scored_headlines_path=str(self.scored_headlines_path),
            daily_sentiment_path=str(self.daily_sentiment_path),
            metadata_path=str(self.metadata_path),
            fetched_headlines=int(len(fetched)),
            stored_headlines=int(len(raw_headlines)),
            daily_rows=int(len(daily_sentiment)),
        )
        metadata = {
            **asdict(result),
            "tickers": [str(ticker).upper() for ticker in tickers],
            "start": str(pd.Timestamp(start).strftime("%Y-%m-%d")),
            "end": str(pd.Timestamp(end).strftime("%Y-%m-%d")),
            "sentiment_model": self.sentiment_model.__class__.__name__,
            "headline_provider": self.headline_provider.__class__.__name__,
        }
        self.metadata_path.write_text(json.dumps(self._json_ready(metadata), indent=2), encoding="utf-8")
        return result
