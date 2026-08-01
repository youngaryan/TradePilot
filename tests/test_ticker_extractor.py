from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from pairs_trading.features.ticker_extractor import (
    TickerExtractor,
    _normalize_company_name,
    _strip_corporate_suffix,
)

_MOCK_KB: dict[str, list[str]] = {
    "apple inc": ["AAPL"],
    "apple": ["AAPL"],
    "microsoft corporation": ["MSFT"],
    "microsoft": ["MSFT"],
    "alphabet inc": ["GOOGL", "GOOG"],
    "alphabet": ["GOOGL", "GOOG"],
    "jpmorgan chase co": ["JPM"],
    "jpmorgan": ["JPM"],
    "berkshire hathaway inc": ["BRK.A", "BRK.B"],
    "berkshire hathaway": ["BRK.A", "BRK.B"],
    "amazon com inc": ["AMZN"],
    "amazon": ["AMZN"],
    "nvidia corporation": ["NVDA"],
    "nvidia": ["NVDA"],
    "tesla inc": ["TSLA"],
    "tesla": ["TSLA"],
    "exxon mobil corporation": ["XOM"],
    "exxon": ["XOM"],
    "mobil": ["XOM"],
}


def _mock_load_kb() -> dict[str, list[str]]:
    return _MOCK_KB


class TestCompanyNameNormalization(unittest.TestCase):
    def test_normalize_lowercases(self) -> None:
        self.assertEqual(_normalize_company_name("Apple Inc."), "apple inc")

    def test_normalize_strips_punctuation(self) -> None:
        self.assertEqual(_normalize_company_name("Apple, Inc."), "apple inc")

    def test_normalize_collapses_spaces(self) -> None:
        self.assertEqual(_normalize_company_name("Apple   Inc"), "apple inc")

    def test_normalize_empty(self) -> None:
        self.assertEqual(_normalize_company_name(""), "")

    def test_strip_corporate_suffix_inc(self) -> None:
        self.assertEqual(_strip_corporate_suffix("apple inc"), "apple")

    def test_strip_corporate_suffix_corporation(self) -> None:
        self.assertEqual(_strip_corporate_suffix("microsoft corporation"), "microsoft")

    def test_strip_corporate_suffix_ltd(self) -> None:
        self.assertEqual(_strip_corporate_suffix("bp ltd"), "bp")

    def test_strip_corporate_suffix_no_match(self) -> None:
        self.assertEqual(_strip_corporate_suffix("apple"), "apple")


class TestTickerExtractor(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch(
            "pairs_trading.features.ticker_extractor._load_kb",
            _mock_load_kb,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.extractor = TickerExtractor()
        self.extractor._kb_lower = {k.lower(): v for k, v in _MOCK_KB.items()}

    def test_extract_explicit_ticker(self) -> None:
        result = self.extractor.extract_tickers("$AAPL up 5% today")
        self.assertIn("AAPL", result)

    def test_extract_explicit_ticker_no_dollar(self) -> None:
        result = self.extractor.extract_tickers("AAPL reported strong earnings")
        self.assertIn("AAPL", result)

    def test_extract_company_name_apple(self) -> None:
        result = self.extractor.extract_tickers("Apple announced a new product")
        self.assertIn("AAPL", result)

    def test_extract_company_name_unknown(self) -> None:
        result = self.extractor.extract_tickers("the weather is nice and sunny today")
        self.assertEqual(len(result), 0)

    def test_extract_requested_filter_included(self) -> None:
        result = self.extractor.extract_tickers(
            "Apple announced earnings",
            requested={"AAPL", "MSFT"},
        )
        self.assertIn("AAPL", result)
        self.assertNotIn("MSFT", result)

    def test_extract_requested_filter_excluded(self) -> None:
        result = self.extractor.extract_tickers(
            "Apple announced earnings",
            requested={"MSFT"},
        )
        self.assertNotIn("AAPL", result)

    def test_extract_multiple_companies(self) -> None:
        result = self.extractor.extract_tickers("Apple partners with Microsoft")
        self.assertIn("AAPL", result)
        self.assertIn("MSFT", result)

    def test_extract_empty_text(self) -> None:
        result = self.extractor.extract_tickers("")
        self.assertEqual(result, [])

    def test_extract_none_text(self) -> None:
        result = self.extractor.extract_tickers("   ")
        self.assertEqual(result, [])

    def test_extract_berkshire(self) -> None:
        result = self.extractor.extract_tickers("Berkshire Hathaway bought a company")
        self.assertTrue("BRK.A" in result or "BRK.B" in result)

    def test_extract_alphabet(self) -> None:
        result = self.extractor.extract_tickers("Alphabet Inc reported earnings")
        self.assertIn("GOOGL", result)
        self.assertIn("GOOG", result)

    def test_extract_nvidia(self) -> None:
        result = self.extractor.extract_tickers("Nvidia announced new AI chip")
        self.assertIn("NVDA", result)

    def test_extract_tesla(self) -> None:
        result = self.extractor.extract_tickers("Tesla delivered record numbers")
        self.assertIn("TSLA", result)


class TestTickerExtractorScoreRow(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch(
            "pairs_trading.features.ticker_extractor._load_kb",
            _mock_load_kb,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.extractor = TickerExtractor()
        self.extractor._kb_lower = {k.lower(): v for k, v in _MOCK_KB.items()}

    def test_score_exact_ticker_match(self) -> None:
        row = pd.Series({"ticker": "AAPL", "relevance": 0.7})
        score = self.extractor.score_row(row, "AAPL")
        self.assertGreaterEqual(score, 0.85)

    def test_score_ticker_mismatch(self) -> None:
        row = pd.Series({"ticker": "MSFT", "relevance": 0.9})
        score = self.extractor.score_row(row, "AAPL")
        self.assertEqual(score, 0.0)

    def test_score_company_name_in_text(self) -> None:
        row = pd.Series({"ticker": "", "headline": "Apple reports strong quarter", "title": "", "summary": "", "source": ""})
        score = self.extractor.score_row(row, "AAPL")
        self.assertGreater(score, 0.0)

    def test_score_no_match(self) -> None:
        row = pd.Series({"ticker": "", "headline": "Weather is nice today", "title": "", "summary": "", "source": ""})
        score = self.extractor.score_row(row, "AAPL")
        self.assertEqual(score, 0.0)

    def test_score_empty_text(self) -> None:
        row = pd.Series({"ticker": "", "headline": "", "title": "", "summary": "", "source": ""})
        score = self.extractor.score_row(row, "AAPL")
        self.assertEqual(score, 0.0)


class TestRegexTickers(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch(
            "pairs_trading.features.ticker_extractor._load_kb",
            _mock_load_kb,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.extractor = TickerExtractor()
        self.extractor._kb_lower = {k.lower(): v for k, v in _MOCK_KB.items()}

    def test_regex_ticker_with_dollar(self) -> None:
        result = self.extractor._regex_tickers("Check out $AAPL")
        self.assertIn("AAPL", result)

    def test_regex_ticker_no_dollar(self) -> None:
        result = self.extractor._regex_tickers("Check out AAPL")
        self.assertIn("AAPL", result)

    def test_regex_ticker_with_requested(self) -> None:
        result = self.extractor._regex_tickers("AAPL and MSFT", requested={"AAPL"})
        self.assertIn("AAPL", result)
        self.assertNotIn("MSFT", result)

    def test_regex_ticker_not_ticker(self) -> None:
        result = self.extractor._regex_tickers("this is a test of lowercase text")
        self.assertEqual(len(result), 0)

    def test_regex_ticker_empty(self) -> None:
        result = self.extractor._regex_tickers("")
        self.assertEqual(len(result), 0)

    def test_regex_dot_ticker(self) -> None:
        result = self.extractor._regex_tickers("BRK.A is a stock")
        self.assertIn("BRK.A", result)

    def test_regex_dollar_dot_ticker(self) -> None:
        result = self.extractor._regex_tickers("$BRK.A is a stock")
        self.assertIn("BRK.A", result)


class TestTickerAliases(unittest.TestCase):
    def test_ticker_self_alias(self) -> None:
        aliases = TickerExtractor._ticker_aliases("AAPL")
        self.assertIn("aapl", aliases)

    def test_ticker_dollar_alias(self) -> None:
        aliases = TickerExtractor._ticker_aliases("AAPL")
        self.assertIn("$aapl", aliases)

    def test_ticker_dot_stripped(self) -> None:
        aliases = TickerExtractor._ticker_aliases("BRK.A")
        any_dot_stripped = any("brka" == a.replace(".", "") for a in aliases)
        self.assertTrue(any_dot_stripped)

    def test_token_match_exact(self) -> None:
        self.assertTrue(TickerExtractor._token_match("apple reports earnings", "apple"))

    def test_token_match_not_present(self) -> None:
        self.assertFalse(TickerExtractor._token_match("microsoft reports", "apple"))

    def test_token_match_word_boundary(self) -> None:
        self.assertFalse(TickerExtractor._token_match("pineapple is good", "apple"))

    def test_token_match_multi_word(self) -> None:
        self.assertTrue(TickerExtractor._token_match("berkshire hathaway", "berkshire hathaway"))


class TestInferTickersIntegration(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch(
            "pairs_trading.features.ticker_extractor._load_kb",
            _mock_load_kb,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_infer_tickers_apple_in_text(self) -> None:
        from pairs_trading.data.news import RSSHeadlineProvider

        result = RSSHeadlineProvider._infer_tickers(
            "Apple reported earnings beat",
            requested={"AAPL", "MSFT"},
        )
        self.assertIn("AAPL", result)
        self.assertNotIn("MSFT", result)

    def test_infer_tickers_explicit_dollar(self) -> None:
        from pairs_trading.data.news import RSSHeadlineProvider

        result = RSSHeadlineProvider._infer_tickers(
            "$AAPL up 5%",
            requested={"AAPL"},
        )
        self.assertIn("AAPL", result)

    def test_infer_tickers_no_match(self) -> None:
        from pairs_trading.data.news import RSSHeadlineProvider

        result = RSSHeadlineProvider._infer_tickers(
            "Weather is nice today",
            requested={"AAPL"},
        )
        self.assertEqual(result, [])

    def test_infer_tickers_empty_text(self) -> None:
        from pairs_trading.data.news import RSSHeadlineProvider

        result = RSSHeadlineProvider._infer_tickers("", requested={"AAPL"})
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
