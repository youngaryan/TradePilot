from __future__ import annotations

import unittest

import pandas as pd

from pairs_trading.data.news import AlphaVantageNewsProvider, BenzingaNewsProvider, CompositeHeadlineProvider, HeadlineProvider, NewsAPIHeadlineProvider, RSSHeadlineProvider


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
        self.assertTrue(headlines.empty)

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
