from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from pairs_trading.data.sec_mda import (
    MDA_SECTION_PATTERNS,
    NEXT_SECTION_PATTERNS,
    _extract_mda_html,
    _extract_mda_text,
    _filing_text_and_doc,
    _filing_url,
    _load_submissions,
    _load_ticker_map,
    _recent_filings,
)


class TestMDAExtraction:
    def test_extract_mda_text_finds_item7(self):
        text = """
        some preamble
        Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations
        We had a great year. Revenue increased 20%.
        Item 8. Financial Statements
        """
        mda = _extract_mda_text(text)
        assert "great year" in mda
        assert "Revenue increased" in mda
        assert "Item 8" not in mda

    def test_extract_mda_text_finds_item2(self):
        text = """
        ITEM 2. MANAGEMENT'S DISCUSSION AND ANALYSIS
        Results were mixed. Costs increased.
        Item 3. Quantitative and Qualitative Disclosures
        """
        mda = _extract_mda_text(text)
        assert "Results were mixed" in mda
        assert "Costs increased" in mda

    def test_extract_mda_text_no_section(self):
        text = "this is just a regular document with no relevant section at all"
        assert _extract_mda_text(text) == ""

    def test_extract_mda_text_short_mda(self):
        text = "Some stuff. MD&A. Not much here. Signatures"
        mda = _extract_mda_text(text)
        assert "MD&A" in mda
        assert "Signatures" not in mda

    def test_extract_mda_html_returns_empty_on_import_error(self):
        with patch.dict("sys.modules", {"bs4": None}):
            import importlib
            import sys
            backup = sys.modules.pop("pairs_trading.data.sec_mda", None)
            try:
                from pairs_trading.data.sec_mda import _extract_mda_html
                result = _extract_mda_html("<html><body>test</body></html>")
                assert result == ""
            finally:
                if backup:
                    sys.modules["pairs_trading.data.sec_mda"] = backup


class TestFilingUrl:
    def test_filing_url_format(self):
        url = _filing_url("0000320193", "0000320193-24-000123", "aapl-20240928.htm")
        assert "0000320193" in url
        assert "0000320193-24-000123" not in url
        assert "000032019324000123" in url
        assert "aapl-20240928.htm" in url


class TestFilingTextAndDoc:
    def test_normal_filing(self):
        filing = {"primaryDocument": "doc.htm", "accessionNumber": "123-45-000001"}
        cik = "0000123456"
        url, doc = _filing_text_and_doc(cik, filing)
        assert doc == "doc.htm"
        assert "doc.htm" in url

    def test_missing_fields(self):
        assert _filing_text_and_doc("0001", {}) == ("", "")


class TestRecentFilings:
    def test_extracts_matching_forms(self):
        payload = {
            "filings": {
                "recent": {
                    "form": ["10-K", "8-K", "10-Q", "10-K"],
                    "filingDate": ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"],
                    "accessionNumber": ["acc1", "acc2", "acc3", "acc4"],
                    "primaryDocument": ["doc1", "doc2", "doc3", "doc4"],
                    "reportDate": ["2023-12-31", "2024-01-15", "2024-02-28", "2024-03-31"],
                }
            }
        }
        filings = _recent_filings(payload, {"10-K", "10-Q"}, 10)
        assert len(filings) == 3
        forms = {f["form"] for f in filings}
        assert forms == {"10-K", "10-Q"}

    def test_empty_recent(self):
        payload = {"filings": {"recent": {}}}
        assert _recent_filings(payload, {"10-K"}, 10) == []


class TestLoadTickerMap:
    def test_loads_from_cache(self, tmp_path):
        cache = tmp_path / "sec"
        cache.mkdir()
        data = {"0": {"ticker": "AAPL", "cik_str": 320193}}
        (cache / "company_tickers.json").write_text(json.dumps(data), encoding="utf-8")
        result = _load_ticker_map(cache, "test [a@b.com]", 30)
        assert result["AAPL"] == "0000320193"

    def test_network_skip(self):
        pytest.skip("Needs network")


class TestPatterns:
    def test_mda_patterns_all_compile(self):
        assert MDA_SECTION_PATTERNS[0].search("Item 7. Management's Discussion and Analysis")
        assert MDA_SECTION_PATTERNS[1].search("Item 2. Management's Discussion and Analysis")
        assert MDA_SECTION_PATTERNS[2].search("management discussion and analysis")
        assert MDA_SECTION_PATTERNS[3].search("MD&A")

    def test_next_section_patterns_all_compile(self):
        assert NEXT_SECTION_PATTERNS[0].search("Item 8. Financial Statements")
        assert NEXT_SECTION_PATTERNS[0].search("Item 7A. Quantitative and Qualitative Disclosures")
        assert NEXT_SECTION_PATTERNS[0].search("Item 7. Quantitative and Qualitative Disclosures")


@pytest.mark.skip(reason="Needs network")
class TestLoadSubmissions:
    def test_network_fetch(self):
        pass
