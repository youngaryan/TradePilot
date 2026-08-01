"""Market, news, and event data provider interfaces."""

from .events import CachedEventProvider, CompositeEventProvider, LocalEventFileProvider, SecCompanyFactsEventProvider, SecCompanyFilingsEventProvider
from .fred import FredEventProvider
from .market import CachedParquetProvider, MarketDataProvider, YahooFinanceProvider
from .stocktwits import StockTwitsHeadlineProvider
from .transcripts import AlphaVantageTranscriptProvider, LocalTranscriptFileProvider
from .news import (
    AlphaVantageNewsProvider,
    BenzingaNewsProvider,
    CachedNewsSentimentProvider,
    CompositeHeadlineProvider,
    DailySentimentFileProvider,
    HeadlineDedupConfig,
    HeadlineProvider,
    LocalNewsFileProvider,
    NewsAPIHeadlineProvider,
    RSSHeadlineProvider,
    deduplicate_headlines,
)
from .sentiment_accumulator import ShadowSentimentAccumulator, SentimentAccumulationResult

__all__ = [
    "AlphaVantageNewsProvider",
    "AlphaVantageTranscriptProvider",
    "BenzingaNewsProvider",
    "CachedEventProvider",
    "CachedNewsSentimentProvider",
    "CachedParquetProvider",
    "CompositeEventProvider",
    "CompositeHeadlineProvider",
    "DailySentimentFileProvider",
    "FredEventProvider",
    "HeadlineDedupConfig",
    "HeadlineProvider",
    "LocalEventFileProvider",
    "LocalNewsFileProvider",
    "LocalTranscriptFileProvider",
    "MarketDataProvider",
    "NewsAPIHeadlineProvider",
    "RSSHeadlineProvider",
    "SecCompanyFactsEventProvider",
    "SecCompanyFilingsEventProvider",
    "SentimentAccumulationResult",
    "ShadowSentimentAccumulator",
    "StockTwitsHeadlineProvider",
    "YahooFinanceProvider",
    "deduplicate_headlines",
]
