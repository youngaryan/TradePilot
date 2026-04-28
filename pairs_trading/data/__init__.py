"""Market, news, and event data provider interfaces."""

from .events import CachedEventProvider, CompositeEventProvider, LocalEventFileProvider, SecCompanyFactsEventProvider, SecCompanyFilingsEventProvider
from .market import CachedParquetProvider, MarketDataProvider, YahooFinanceProvider
from .news import (
    AlphaVantageNewsProvider,
    BenzingaNewsProvider,
    CachedNewsSentimentProvider,
    CompositeHeadlineProvider,
    DailySentimentFileProvider,
    HeadlineDedupConfig,
    HeadlineProvider,
    LocalNewsFileProvider,
    deduplicate_headlines,
)

__all__ = [
    "AlphaVantageNewsProvider",
    "BenzingaNewsProvider",
    "CachedEventProvider",
    "CachedNewsSentimentProvider",
    "CachedParquetProvider",
    "CompositeEventProvider",
    "CompositeHeadlineProvider",
    "DailySentimentFileProvider",
    "HeadlineDedupConfig",
    "HeadlineProvider",
    "LocalEventFileProvider",
    "LocalNewsFileProvider",
    "MarketDataProvider",
    "SecCompanyFactsEventProvider",
    "SecCompanyFilingsEventProvider",
    "YahooFinanceProvider",
    "deduplicate_headlines",
]
