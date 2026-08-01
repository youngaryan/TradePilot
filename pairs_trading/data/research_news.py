from __future__ import annotations

from datetime import date
import hashlib
from typing import Protocol

import pandas as pd
from pydantic import BaseModel, Field

from .news import HeadlineProvider


class NormalizedHeadline(BaseModel):
    ticker: str
    timestamp: str
    headline: str
    source: str
    provider: str
    url: str | None = None
    sentiment_score: float | None = None
    relevance: float = Field(default=1.0, ge=0, le=1)
    confidence: float = Field(default=0.7, ge=0, le=1)
    deduplication_key: str | None = None


class MarketResearchNewsProvider(Protocol):
    def get_news(self, ticker: str, start: date, end: date) -> list[NormalizedHeadline]:
        ...


class HeadlineMarketResearchNewsProvider:
    def __init__(self, provider: HeadlineProvider, *, maximum_articles: int = 100) -> None:
        self.provider = provider
        self.maximum_articles = max(1, min(int(maximum_articles), 500))
        self.last_warnings: list[str] = []

    def get_news(self, ticker: str, start: date, end: date) -> list[NormalizedHeadline]:
        frame = self.provider.get_headlines([ticker], start.isoformat(), end.isoformat()).copy()
        self.last_warnings = [
            str(item)[:300]
            for item in getattr(self.provider, "last_errors", [])
            if str(item).strip()
        ]
        if frame.empty:
            return []
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
        floor = pd.Timestamp(start).tz_localize("UTC")
        cutoff = pd.Timestamp(end).tz_localize("UTC") + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        frame = frame.dropna(subset=["timestamp", "headline"])
        frame = frame[(frame["timestamp"] >= floor) & (frame["timestamp"] <= cutoff)]
        frame = frame.sort_values("timestamp", ascending=False).head(self.maximum_articles)
        rows: list[NormalizedHeadline] = []
        seen: set[str] = set()
        for row in frame.to_dict("records"):
            headline = str(row.get("headline") or "").strip()
            if not headline:
                continue
            provider = str(row.get("provider_name") or self.provider.__class__.__name__)
            dedup_key = row.get("dedup_key") or row.get("canonical_hash")
            normalized_key = str(dedup_key or hashlib.sha256(" ".join(headline.lower().split()).encode("utf-8")).hexdigest())[:128]
            if normalized_key in seen:
                continue
            seen.add(normalized_key)
            sentiment = row.get("sentiment_score", row.get("score"))
            try:
                sentiment_value = float(sentiment) if sentiment is not None and not pd.isna(sentiment) else None
            except (TypeError, ValueError):
                sentiment_value = None
            rows.append(
                NormalizedHeadline(
                    ticker=str(row.get("ticker") or ticker).upper(),
                    timestamp=pd.Timestamp(row["timestamp"]).isoformat().replace("+00:00", "Z"),
                    headline=headline[:1_000],
                    source=str(row.get("source") or provider)[:160],
                    provider=provider[:160],
                    url=str(row.get("url"))[:2_000] if row.get("url") else None,
                    sentiment_score=sentiment_value,
                    relevance=max(0.0, min(float(row.get("relevance", 1.0) or 0.0), 1.0)),
                    confidence=max(0.0, min(float(row.get("confidence", row.get("relevance", 0.7)) or 0.0), 1.0)),
                    deduplication_key=normalized_key,
                )
            )
        return rows


__all__ = [
    "HeadlineMarketResearchNewsProvider",
    "MarketResearchNewsProvider",
    "NormalizedHeadline",
]
