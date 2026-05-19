from __future__ import annotations

import tempfile
import unittest
from urllib.error import HTTPError

import pandas as pd

from pairs_trading.data.news import (
    AlphaVantageNewsProvider,
    BenzingaNewsProvider,
    CompositeHeadlineProvider,
    HeadlineProvider,
    NewsAPIHeadlineProvider,
    LocalWebSearchHeadlineProvider,
    RSSHeadlineProvider,
    WebResearchHeadlineProvider,
)


class StubAlphaVantageProvider(AlphaVantageNewsProvider):
    def __init__(self) -> None:
        super().__init__(api_key="demo", topics=["earnings"], sort="LATEST", limit=50)
        self.captured_params: dict[str, str] | None = None

    def _fetch_json(self, url: str, params: dict[str, str], headers=None):
        self.captured_params = params
        return {
            "feed": [
                {
                    "title": "Apple beats estimates",
                    "summary": "Guidance was raised.",
                    "time_published": "20240102T143000",
                    "source": "Example",
                    "url": "https://example.com/aapl",
                    "overall_sentiment_score": 0.41,
                    "overall_sentiment_label": "Bullish",
                    "ticker_sentiment": [
                        {
                            "ticker": "AAPL",
                            "relevance_score": "0.91",
                        },
                        {
                            "ticker": "MSFT",
                            "relevance_score": "0.20",
                        },
                    ],
                }
            ]
        }


class StubBenzingaProvider(BenzingaNewsProvider):
    def __init__(self) -> None:
        super().__init__(api_key="demo", display_output="abstract", page_size=2, max_pages=3)
        self.pages_requested: list[int] = []

    def _fetch_json(self, url: str, params: dict[str, str], headers=None):
        self.pages_requested.append(int(params["page"]))
        if params["page"] == "0":
            return [
                {
                    "title": "Nvidia news",
                    "teaser": "Chip demand strong",
                    "body": "",
                    "created": "Mon, 01 Jan 2024 13:35:14 -0400",
                    "url": "https://example.com/nvda",
                    "author": "Benzinga Insights",
                    "channels": [{"name": "Technology"}],
                    "stocks": [{"name": "NVDA"}, {"name": "AMD"}],
                },
                {
                    "title": "Ignored ticker",
                    "teaser": "No match",
                    "body": "",
                    "created": "Mon, 01 Jan 2024 14:00:00 -0400",
                    "url": "https://example.com/other",
                    "author": "Benzinga Insights",
                    "channels": [{"name": "Markets"}],
                    "stocks": [{"name": "SPY"}],
                },
            ]
        return []


class StubRSSProvider(RSSHeadlineProvider):
    def __init__(self) -> None:
        super().__init__(feed_urls=["https://feeds.example.com/{ticker}.xml"], skip_errors=False)
        self.urls: list[str] = []

    def _fetch_text(self, url: str, headers=None) -> str:
        self.urls.append(url)
        return """<?xml version="1.0"?>
        <rss><channel>
          <item>
            <title>NVDA beats estimates and raises guidance</title>
            <description>Chip demand remains strong.</description>
            <link>https://example.com/nvda</link>
            <pubDate>Tue, 02 Jan 2024 14:30:00 GMT</pubDate>
          </item>
        </channel></rss>"""


class StubRedditRSSProvider(RSSHeadlineProvider):
    def __init__(self) -> None:
        super().__init__(feed_urls=["https://www.reddit.com/r/Gold/"], skip_errors=False)
        self.urls: list[str] = []

    def _fetch_text(self, url: str, headers=None) -> str:
        self.urls.append(url)
        return """<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>Gold miners rally as spot gold rises</title>
            <summary>Discussion about bullion and mining shares.</summary>
            <link href="https://www.reddit.com/r/Gold/comments/example"/>
            <updated>2026-04-24T14:30:00+00:00</updated>
          </entry>
        </feed>"""


class StubForexRSSProvider(RSSHeadlineProvider):
    def __init__(self) -> None:
        super().__init__(feed_urls=["https://feeds.example.com/{ticker}.xml"], skip_errors=False)
        self.urls: list[str] = []

    def _fetch_text(self, url: str, headers=None) -> str:
        self.urls.append(url)
        return """<?xml version="1.0"?>
        <rss><channel>
          <item>
            <title>Dollar falls as euro gains before central bank decision</title>
            <description>EUR/USD moved higher during the London session.</description>
            <link>https://example.com/eurusd</link>
            <pubDate>Fri, 17 Apr 2026 08:14:48 GMT</pubDate>
          </item>
        </channel></rss>"""


class StubNewsAPIProvider(NewsAPIHeadlineProvider):
    def __init__(self) -> None:
        super().__init__(api_key="demo", page_size=50, max_pages=1)
        self.captured_params: dict[str, str] | None = None

    def _fetch_json(self, url: str, params: dict[str, str], headers=None):
        self.captured_params = params
        return {
            "status": "ok",
            "articles": [
                {
                    "title": "Tesla cuts prices",
                    "description": "Margins may face pressure.",
                    "publishedAt": "2024-01-02T15:00:00Z",
                    "url": "https://example.com/tsla",
                    "source": {"name": "Example News"},
                    "author": "Reporter",
                }
            ],
        }


class StubWebResearchProvider(WebResearchHeadlineProvider):
    def __init__(self) -> None:
        super().__init__(
            domains=["example.com"],
            query_terms="earnings OR guidance",
            max_articles_per_ticker=2,
            fetch_article_text=True,
        )
        self.captured_params: dict[str, str] | None = None
        self.fetched_urls: list[str] = []

    def _fetch_json(self, url: str, params: dict[str, str], headers=None):
        self.captured_params = params
        return {
            "articles": [
                {
                    "title": "Nvidia suppliers rally",
                    "url": "https://example.com/nvda",
                    "domain": "example.com",
                    "seendate": "20260417T081448Z",
                }
            ]
        }

    def _fetch_text(self, url: str, headers=None) -> str:
        self.fetched_urls.append(url)
        return """
        <html>
          <head>
            <title>Nvidia suppliers rally</title>
            <meta name="description" content="Chip demand remains strong after the company raised guidance." />
          </head>
          <body>
            <article>
              <p>NVDA suppliers rallied as chip demand remained strong and data center orders expanded.</p>
              <p>Analysts said margins could improve after guidance was raised.</p>
            </article>
          </body>
        </html>
        """


class StubLocalWebSearchProvider(LocalWebSearchHeadlineProvider):
    def __init__(self, cache_dir: str) -> None:
        super().__init__(
            feed_urls=["https://feeds.example.com/markets.xml"],
            cache_dir=cache_dir,
            refresh_minutes=60,
            fetch_article_text=False,
            max_results_per_ticker=5,
        )
        self.fetch_count = 0

    def _fetch_text(self, url: str, headers=None) -> str:
        self.fetch_count += 1
        return """<?xml version="1.0"?>
        <rss><channel>
          <item>
            <title>Spot gold rises as bullion ETFs draw inflows</title>
            <description>Gold investors reacted to lower real yields and softer dollar data.</description>
            <link>https://example.com/gold</link>
            <pubDate>Fri, 24 Apr 2026 14:30:00 GMT</pubDate>
          </item>
          <item>
            <title>Nasdaq megacap tech rally lifts growth shares</title>
            <description>Technology stocks rose after AI demand improved.</description>
            <link>https://example.com/nasdaq</link>
            <pubDate>Fri, 24 Apr 2026 15:30:00 GMT</pubDate>
          </item>
        </channel></rss>"""


class StubDomainCrawlLocalWebSearchProvider(LocalWebSearchHeadlineProvider):
    def __init__(self, cache_dir: str) -> None:
        super().__init__(
            feed_urls=[],
            source_domains=["example.com"],
            cache_dir=cache_dir,
            refresh_minutes=0,
            fetch_article_text=True,
            max_crawl_pages_per_source=3,
            max_results_per_ticker=5,
        )
        self.fetched_urls: list[str] = []

    def _fetch_text(self, url: str, headers=None) -> str:
        self.fetched_urls.append(url)
        if url == "https://example.com/sitemap.xml":
            return """<?xml version="1.0"?>
            <urlset>
              <url><loc>https://example.com/markets/gold-rally-2026</loc></url>
              <url><loc>https://example.com/about</loc></url>
            </urlset>"""
        if url == "https://example.com/markets/gold-rally-2026":
            return """
            <html>
              <head>
                <title>Gold rallies as bullion demand improves</title>
                <meta name="description" content="Investors bought GLD and bullion funds after real yields fell." />
              </head>
              <body>
                <p>Gold investors added exposure as ETF inflows improved and the dollar softened.</p>
              </body>
            </html>
            """
        return "<html><body><a href='/markets/gold-rally-2026'>Gold market story</a></body></html>"


class PartiallyBlockedDomainCrawlLocalWebSearchProvider(LocalWebSearchHeadlineProvider):
    def __init__(self, cache_dir: str) -> None:
        super().__init__(
            feed_urls=[],
            source_domains=["example.com"],
            cache_dir=cache_dir,
            refresh_minutes=0,
            fetch_article_text=True,
            max_crawl_pages_per_source=3,
            max_results_per_ticker=5,
        )
        self.fetched_urls: list[str] = []

    def _fetch_text(self, url: str, headers=None) -> str:
        self.fetched_urls.append(url)
        if url == "https://example.com/sitemap.xml":
            raise HTTPError(url, 404, "Not Found", hdrs={}, fp=None)
        if url == "https://www.example.com/sitemap.xml":
            return """<?xml version="1.0"?>
            <urlset>
              <url><loc>https://www.example.com/markets/gold-rally-2026</loc></url>
            </urlset>"""
        if url == "https://www.example.com/markets/gold-rally-2026":
            return """
            <html>
              <head><title>Gold rallies as bullion demand improves</title></head>
              <body><p>GLD and bullion funds rose as real yields fell.</p></body>
            </html>
            """
        return "<html><body></body></html>"


class StubDirectWebResearchProvider(WebResearchHeadlineProvider):
    def __init__(self) -> None:
        super().__init__(
            research_urls=["https://example.com/gold-outlook"],
            fetch_article_text=True,
            use_gdelt=False,
        )

    def _fetch_text(self, url: str, headers=None) -> str:
        return """
        <html>
          <head><title>Gold ETF outlook improves</title></head>
          <body>
            <p>GLD rose as gold investors reacted to lower real yields and a softer dollar.</p>
            <p>The move improved demand for bullion ETFs.</p>
          </body>
        </html>
        """


class RateLimitedThenSuccessfulWebResearchProvider(WebResearchHeadlineProvider):
    def __init__(self) -> None:
        super().__init__(
            max_articles_per_ticker=1,
            fetch_article_text=False,
            max_retry_attempts=2,
            retry_backoff_seconds=0,
            request_pause_seconds=0,
        )
        self.attempts = 0

    def _fetch_json(self, url: str, params: dict[str, str], headers=None):
        self.attempts += 1
        if self.attempts == 1:
            raise HTTPError(url, 429, "Too Many Requests", hdrs={}, fp=None)
        return {
            "articles": [
                {
                    "title": "Nvidia rebounds after AI chip update",
                    "url": "https://example.com/nvda-rebound",
                    "domain": "example.com",
                    "seendate": "20260417T081448Z",
                }
            ]
        }


class AlwaysRateLimitedWebResearchProvider(WebResearchHeadlineProvider):
    def __init__(self) -> None:
        super().__init__(
            max_articles_per_ticker=1,
            fetch_article_text=False,
            max_retry_attempts=1,
            retry_backoff_seconds=0,
            request_pause_seconds=0,
        )

    def _fetch_json(self, url: str, params: dict[str, str], headers=None):
        raise HTTPError(url, 429, "Too Many Requests", hdrs={}, fp=None)


class StubAlphaVantageFXProvider(AlphaVantageNewsProvider):
    def __init__(self) -> None:
        super().__init__(api_key="demo", topics=["forex"], sort="LATEST", limit=50)
        self.captured_params: dict[str, str] | None = None

    def _fetch_json(self, url: str, params: dict[str, str], headers=None):
        self.captured_params = params
        return {
            "feed": [
                {
                    "title": "Euro rises against dollar",
                    "summary": "The currency pair strengthened after weaker US data.",
                    "time_published": "20260417T081448",
                    "source": "Example FX",
                    "url": "https://example.com/fx",
                    "overall_sentiment_score": 0.18,
                    "overall_sentiment_label": "Somewhat-Bullish",
                    "ticker_sentiment": [
                        {
                            "ticker": "FOREX:EUR",
                            "relevance_score": "0.77",
                        }
                    ],
                }
            ]
        }


class StubAlphaVantageFXFallbackProvider(AlphaVantageNewsProvider):
    def __init__(self) -> None:
        super().__init__(api_key="demo", topics=["forex"], sort="LATEST", limit=50)

    def _fetch_json(self, url: str, params: dict[str, str], headers=None):
        return {
            "feed": [
                {
                    "title": "Dollar slips against major currencies",
                    "summary": "Foreign exchange markets reacted to weaker US inflation data.",
                    "time_published": "20260417T091500",
                    "source": "Example FX",
                    "url": "https://example.com/fx-fallback",
                    "overall_sentiment_score": 0.12,
                    "overall_sentiment_label": "Neutral",
                    "ticker_sentiment": [],
                }
            ]
        }


class FailingHeadlineProvider(HeadlineProvider):
    def get_headlines(self, tickers, start, end) -> pd.DataFrame:
        raise RuntimeError("bad credential")


class RemoteNewsAdapterTests(unittest.TestCase):
    def test_alpha_vantage_adapter_builds_expected_request_and_rows(self) -> None:
        provider = StubAlphaVantageProvider()
        headlines = provider.get_headlines(["AAPL"], "2024-01-01", "2024-01-03")

        self.assertIsNotNone(provider.captured_params)
        assert provider.captured_params is not None
        self.assertEqual(provider.captured_params["function"], "NEWS_SENTIMENT")
        self.assertEqual(provider.captured_params["tickers"], "AAPL")
        self.assertEqual(provider.captured_params["topics"], "earnings")
        self.assertEqual(provider.captured_params["time_from"], "20240101T0000")
        self.assertEqual(provider.captured_params["time_to"], "20240103T2359")

        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "AAPL")
        self.assertAlmostEqual(float(headlines.loc[0, "relevance"]), 0.91, places=6)
        self.assertIn("Apple beats estimates", headlines.loc[0, "headline"])

    def test_benzinga_adapter_paginates_and_filters_requested_tickers(self) -> None:
        provider = StubBenzingaProvider()
        headlines = provider.get_headlines(["NVDA"], "2024-01-01", "2024-01-02")

        self.assertEqual(provider.pages_requested, [0, 1])
        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "NVDA")
        self.assertEqual(headlines.loc[0, "source"], "Benzinga")
        self.assertIn("Chip demand strong", headlines.loc[0, "headline"])
        self.assertEqual(headlines.loc[0, "channels"], "Technology")
        self.assertTrue(pd.notna(headlines.loc[0, "timestamp"]))

    def test_rss_adapter_parses_ticker_template_feeds(self) -> None:
        provider = StubRSSProvider()
        headlines = provider.get_headlines(["NVDA"], "2024-01-01", "2024-01-03")

        self.assertEqual(provider.urls, ["https://feeds.example.com/NVDA.xml"])
        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "NVDA")
        self.assertIn("raises guidance", headlines.loc[0, "headline"])
        self.assertEqual(headlines.loc[0, "source"], "feeds.example.com")

    def test_rss_adapter_normalizes_subreddit_urls_and_assigns_single_requested_symbol(self) -> None:
        provider = StubRedditRSSProvider()
        headlines = provider.get_headlines(["GLD"], "2026-04-20", "2026-04-29")

        self.assertEqual(provider.urls, ["https://www.reddit.com/r/Gold/.rss"])
        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "GLD")
        self.assertEqual(headlines.loc[0, "source"], "reddit:r/Gold")
        self.assertIn("Gold miners rally", headlines.loc[0, "headline"])

    def test_rss_adapter_does_not_blindly_assign_topic_feed_to_multiple_symbols(self) -> None:
        provider = StubRedditRSSProvider()
        headlines = provider.get_headlines(["GLD", "AAPL"], "2026-04-20", "2026-04-29")

        self.assertEqual(provider.urls, ["https://www.reddit.com/r/Gold/.rss"])
        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "GLD")

    def test_rss_adapter_maps_fx_pair_to_yahoo_alias_but_stores_requested_symbol(self) -> None:
        provider = StubForexRSSProvider()
        headlines = provider.get_headlines(["EURUSD"], "2026-04-15", "2026-04-29")

        self.assertEqual(provider.urls, ["https://feeds.example.com/EURUSD=X.xml"])
        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "EURUSD")
        self.assertIn("EUR/USD", headlines.loc[0, "headline"])

    def test_newsapi_adapter_builds_everything_request(self) -> None:
        provider = StubNewsAPIProvider()
        headlines = provider.get_headlines(["TSLA"], "2024-01-01", "2024-01-03")

        self.assertIsNotNone(provider.captured_params)
        assert provider.captured_params is not None
        self.assertEqual(provider.captured_params["q"], '"TSLA" OR $TSLA')
        self.assertEqual(provider.captured_params["from"], "2024-01-01")
        self.assertEqual(provider.captured_params["to"], "2024-01-03")
        self.assertEqual(provider.captured_params["apiKey"], "demo")
        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "TSLA")
        self.assertEqual(headlines.loc[0, "source"], "Example News")

    def test_newsapi_adapter_expands_fx_pair_query_terms(self) -> None:
        provider = StubNewsAPIProvider()
        headlines = provider.get_headlines(["EURUSD"], "2024-01-01", "2024-01-03")

        self.assertIsNotNone(provider.captured_params)
        assert provider.captured_params is not None
        query = provider.captured_params["q"]
        self.assertIn('"EURUSD"', query)
        self.assertIn('"EUR/USD"', query)
        self.assertIn('"EURUSD=X"', query)
        self.assertIn('"euro dollar"', query)
        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "EURUSD")

    def test_web_research_provider_discovers_articles_and_creates_lightweight_summary(self) -> None:
        provider = StubWebResearchProvider()
        headlines = provider.get_headlines(["NVDA"], "2026-04-15", "2026-04-29")

        self.assertIsNotNone(provider.captured_params)
        assert provider.captured_params is not None
        self.assertIn('"NVDA" OR $NVDA', provider.captured_params["query"])
        self.assertIn("domainis:example.com", provider.captured_params["query"])
        self.assertIn("earnings OR guidance", provider.captured_params["query"])
        self.assertEqual(provider.fetched_urls, ["https://example.com/nvda"])

        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "NVDA")
        self.assertEqual(headlines.loc[0, "source"], "web:example.com")
        self.assertEqual(headlines.loc[0, "web_research_model"], "lightweight_extractive_v1")
        self.assertEqual(headlines.loc[0, "extraction_status"], "extracted")
        self.assertGreater(int(headlines.loc[0, "article_text_chars"]), 40)
        self.assertIn("chip demand", headlines.loc[0, "headline"].lower())

    def test_local_web_search_builds_cached_index_and_matches_topic_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            provider = StubLocalWebSearchProvider(cache_dir=cache_dir)
            headlines = provider.get_headlines(["GLD"], "2026-04-20", "2026-04-29")
            cached_headlines = provider.get_headlines(["GLD"], "2026-04-20", "2026-04-29")

        self.assertEqual(provider.fetch_count, 1)
        self.assertEqual(len(headlines), 1)
        self.assertEqual(len(cached_headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "GLD")
        self.assertEqual(headlines.loc[0, "source"], "local-web:feeds.example.com")
        self.assertEqual(headlines.loc[0, "local_web_search_tool"], "local_web_index_v2")
        self.assertIn("Spot gold rises", headlines.loc[0, "headline"])

    def test_local_web_search_crawls_configured_domains_and_scores_pages(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            provider = StubDomainCrawlLocalWebSearchProvider(cache_dir=cache_dir)
            headlines = provider.get_headlines(["GLD"], "2026-04-20", "2026-05-03")

        self.assertIn("https://example.com/sitemap.xml", provider.fetched_urls)
        self.assertIn("https://example.com/markets/gold-rally-2026", provider.fetched_urls)
        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "GLD")
        self.assertEqual(headlines.loc[0, "source"], "local-web:example.com")
        self.assertEqual(headlines.loc[0, "extraction_status"], "extracted")
        self.assertGreater(int(headlines.loc[0, "article_text_chars"]), 40)
        self.assertIn("bullion demand", headlines.loc[0, "headline"].lower())

    def test_local_web_search_uses_source_presets_for_common_financial_domains(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            provider = LocalWebSearchHeadlineProvider(
                feed_urls=[],
                source_domains=["cnbc.com", "marketwatch.com", "finance.yahoo.com"],
                cache_dir=cache_dir,
            )

        seed_urls = provider._domain_seed_urls()
        self.assertIn("https://www.cnbc.com/?format=rss", seed_urls["cnbc.com"])
        self.assertIn("https://www.marketwatch.com/rss/topstories", seed_urls["marketwatch.com"])
        self.assertIn("https://finance.yahoo.com/news/", seed_urls["finance.yahoo.com"])

    def test_local_web_search_suppresses_seed_errors_when_domain_eventually_yields_rows(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            provider = PartiallyBlockedDomainCrawlLocalWebSearchProvider(cache_dir=cache_dir)
            headlines = provider.get_headlines(["GLD"], "2026-04-20", "2026-05-03")

        self.assertIn("https://example.com/sitemap.xml", provider.fetched_urls)
        self.assertIn("https://www.example.com/sitemap.xml", provider.fetched_urls)
        self.assertEqual(len(headlines), 1)
        self.assertEqual(provider.last_errors, [])

    def test_web_research_direct_url_assigns_single_requested_symbol_without_gdelt(self) -> None:
        provider = StubDirectWebResearchProvider()
        headlines = provider.get_headlines(["GLD"], "2020-01-01", "2020-01-02")

        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "GLD")
        self.assertEqual(headlines.loc[0, "source"], "web:example.com")
        self.assertTrue(bool(headlines.loc[0, "is_direct_url"]))
        self.assertEqual(headlines.loc[0, "web_research_model"], "lightweight_extractive_v1")
        self.assertIn("gold investors", headlines.loc[0, "headline"].lower())

    def test_web_research_retries_transient_rate_limits_before_success(self) -> None:
        provider = RateLimitedThenSuccessfulWebResearchProvider()
        headlines = provider.get_headlines(["NVDA"], "2026-04-15", "2026-04-29")

        self.assertEqual(provider.attempts, 2)
        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "NVDA")
        self.assertEqual(headlines.loc[0, "source"], "web:example.com")
        self.assertIn("AI chip", headlines.loc[0, "headline"])

    def test_alpha_vantage_adapter_maps_forex_aliases_back_to_requested_pair(self) -> None:
        provider = StubAlphaVantageFXProvider()
        headlines = provider.get_headlines(["EURUSD"], "2026-04-15", "2026-04-29")

        self.assertIsNotNone(provider.captured_params)
        assert provider.captured_params is not None
        self.assertEqual(provider.captured_params["tickers"], "EURUSD,FOREX:EUR,FOREX:USD")
        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "EURUSD")
        self.assertAlmostEqual(float(headlines.loc[0, "relevance"]), 0.77, places=6)

    def test_alpha_vantage_adapter_single_fx_pair_falls_back_when_feed_has_no_ticker_sentiment(self) -> None:
        provider = StubAlphaVantageFXFallbackProvider()
        headlines = provider.get_headlines(["EURUSD"], "2026-04-15", "2026-04-29")

        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "EURUSD")
        self.assertIn("Dollar slips", headlines.loc[0, "headline"])

    def test_composite_provider_keeps_successful_sources_when_one_fails(self) -> None:
        provider = CompositeHeadlineProvider([FailingHeadlineProvider(), StubRSSProvider()])

        headlines = provider.get_headlines(["NVDA"], "2024-01-01", "2024-01-03")

        self.assertEqual(len(headlines), 1)
        self.assertEqual(headlines.loc[0, "ticker"], "NVDA")
        self.assertTrue(provider.last_errors)
        self.assertIn("Failing failed", provider.last_errors[0])

    def test_composite_provider_reports_web_rate_limit_as_partial_success_warning(self) -> None:
        provider = CompositeHeadlineProvider([AlwaysRateLimitedWebResearchProvider(), StubRSSProvider()])

        headlines = provider.get_headlines(["NVDA"], "2024-01-01", "2024-01-03")

        self.assertEqual(len(headlines), 1)
        self.assertTrue(provider.last_errors)
        self.assertIn("Web research was rate-limited (HTTP 429)", provider.last_errors[0])
        self.assertIn("Partial results from other selected sources were saved", provider.last_errors[0])

    def test_composite_provider_reports_all_failures_and_returns_empty_frame(self) -> None:
        provider = CompositeHeadlineProvider([FailingHeadlineProvider(), FailingHeadlineProvider()])

        headlines = provider.get_headlines(["NVDA"], "2024-01-01", "2024-01-03")

        self.assertTrue(headlines.empty)
        self.assertEqual(len(provider.last_errors), 2)
        self.assertEqual(
            list(headlines.columns),
            ["timestamp", "ticker", "headline", "relevance", "source", "url"],
        )


if __name__ == "__main__":
    unittest.main()
