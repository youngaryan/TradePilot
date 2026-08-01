from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from pairs_trading.data.transcripts import (
    EMPTY_COLUMNS,
    AlphaVantageTranscriptProvider,
    LocalTranscriptFileProvider,
)


def _mock_transcript_response(
    ticker: str = "AAPL",
    year: int = 2024,
    quarter: int = 1,
    text: str = "We had a strong quarter. Revenue increased. Costs were managed carefully.",
    fiscal_date_ending: str = "",
) -> str:
    return json.dumps({
        "symbol": ticker,
        "fiscal_year": str(year),
        "fiscal_quarter": str(quarter),
        "fiscal_date_ending": fiscal_date_ending,
        "transcript": text,
    })


class TestAlphaVantageTranscriptProvider:
    def test_init_requires_api_key(self):
        with pytest.raises(ValueError, match="Alpha Vantage API key is required"):
            AlphaVantageTranscriptProvider(api_key="")

    def test_init_accepts_valid_key(self):
        provider = AlphaVantageTranscriptProvider(api_key="test_key")
        assert provider.api_key == "test_key"

    @patch.object(AlphaVantageTranscriptProvider, "_fetch_json")
    def test_happy_path_single_ticker(self, mock_fetch):
        mock_fetch.return_value = json.loads(_mock_transcript_response(
            ticker="AAPL", year=2024, quarter=1,
            text="We had an excellent quarter with strong performance and growth.",
        ))

        provider = AlphaVantageTranscriptProvider(api_key="test_key")
        df = provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

        assert not df.empty
        assert df.iloc[0]["ticker"] == "AAPL"
        assert df.iloc[0]["event_type"] == "earnings_call_transcript"
        assert df.iloc[0]["source"] == "alphavantage"
        assert df.iloc[0]["transcript_year"] == 2024
        assert df.iloc[0]["transcript_quarter"] == 1
        assert isinstance(df.iloc[0]["event_score"], float)
        assert -1.0 <= df.iloc[0]["event_score"] <= 1.0
        assert "transcript_word_count" in df.columns

    @patch.object(AlphaVantageTranscriptProvider, "_fetch_json")
    def test_empty_response(self, mock_fetch):
        mock_fetch.return_value = {}

        provider = AlphaVantageTranscriptProvider(api_key="test_key")
        df = provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

        assert df.empty
        assert list(df.columns) == EMPTY_COLUMNS

    @patch.object(AlphaVantageTranscriptProvider, "_fetch_json")
    def test_missing_transcript_text(self, mock_fetch):
        mock_fetch.return_value = {"symbol": "AAPL", "transcript": ""}

        provider = AlphaVantageTranscriptProvider(api_key="test_key")
        df = provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

        assert df.empty

    @patch.object(AlphaVantageTranscriptProvider, "_fetch_json")
    def test_premium_required_error(self, mock_fetch):
        mock_fetch.return_value = {"Information": "Thank you for using Alpha Vantage. This endpoint requires a premium subscription."}

        provider = AlphaVantageTranscriptProvider(api_key="free_key")
        with pytest.raises(RuntimeError, match="premium endpoint"):
            provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

    @patch.object(AlphaVantageTranscriptProvider, "_fetch_json")
    def test_api_error_message(self, mock_fetch):
        mock_fetch.return_value = {"Error Message": "Invalid API call."}

        provider = AlphaVantageTranscriptProvider(api_key="test_key")
        with pytest.raises(RuntimeError, match="Alpha Vantage error"):
            provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

    @patch.object(AlphaVantageTranscriptProvider, "_fetch_json")
    def test_filters_by_date_range(self, mock_fetch):
        mock_fetch.return_value = json.loads(_mock_transcript_response(
            ticker="AAPL", year=2022, quarter=1,
        ))

        provider = AlphaVantageTranscriptProvider(api_key="test_key")
        df = provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

        assert df.empty

    @patch.object(AlphaVantageTranscriptProvider, "_fetch_json")
    def test_handles_multiple_tickers(self, mock_fetch):
        calls: list[str] = []

        def side_effect(params):
            ticker = params.get("symbol", "")
            calls.append(ticker)
            if ticker == "AAPL":
                return json.loads(_mock_transcript_response(ticker="AAPL", year=2024, quarter=1))
            return json.loads(_mock_transcript_response(ticker="MSFT", year=2024, quarter=2))

        mock_fetch.side_effect = side_effect

        provider = AlphaVantageTranscriptProvider(api_key="test_key")
        df = provider.get_events(tickers=["AAPL", "MSFT"], start="2024-01-01", end="2024-12-31")

        assert len(df) == 2
        assert set(df["ticker"]) == {"AAPL", "MSFT"}
        assert set(df["transcript_quarter"]) == {1, 2}

    @patch.object(AlphaVantageTranscriptProvider, "_fetch_json")
    def test_event_score_from_positive_text(self, mock_fetch):
        mock_fetch.return_value = json.loads(_mock_transcript_response(
            text="excellent outstanding great growth improve profit success strong",
        ))

        provider = AlphaVantageTranscriptProvider(api_key="test_key")
        df = provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

        assert df.iloc[0]["event_score"] > 0

    @patch.object(AlphaVantageTranscriptProvider, "_fetch_json")
    def test_event_score_from_negative_text(self, mock_fetch):
        mock_fetch.return_value = json.loads(_mock_transcript_response(
            text="loss decline impairment write-down litigation risky downturn",
        ))

        provider = AlphaVantageTranscriptProvider(api_key="test_key")
        df = provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

        assert df.iloc[0]["event_score"] < 0

    @patch.object(AlphaVantageTranscriptProvider, "_fetch_json")
    def test_confidence_is_bounded(self, mock_fetch):
        mock_fetch.return_value = json.loads(_mock_transcript_response(
            text="We had a quarter. Revenue was up. Costs were down. Profit grew.",
        ))

        provider = AlphaVantageTranscriptProvider(api_key="test_key")
        df = provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

        assert 0.0 <= df.iloc[0]["confidence"] <= 1.0

    @patch.object(AlphaVantageTranscriptProvider, "_fetch_json")
    def test_unexpected_payload_type(self, mock_fetch):
        mock_fetch.return_value = "not a dict"

        provider = AlphaVantageTranscriptProvider(api_key="test_key")
        df = provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

        assert df.empty

    @patch.object(AlphaVantageTranscriptProvider, "_fetch_json")
    def test_zero_year_falls_back_to_date_ending(self, mock_fetch):
        mock_fetch.return_value = json.loads(_mock_transcript_response(
            ticker="AAPL", year=0, quarter=0,
            fiscal_date_ending="2024-06-30",
            text="Some transcript text here for testing purposes.",
        ))

        provider = AlphaVantageTranscriptProvider(api_key="test_key")
        df = provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

        assert not df.empty
        assert df.iloc[0]["transcript_year"] == 0

    @patch.object(AlphaVantageTranscriptProvider, "_fetch_json")
    def test_handles_http_error_gracefully(self, mock_fetch):
        from urllib.error import HTTPError

        mock_fetch.side_effect = HTTPError("url", 500, "Server Error", {}, None)

        provider = AlphaVantageTranscriptProvider(api_key="test_key")
        df = provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

        assert df.empty


class TestLocalTranscriptFileProvider:
    def test_init_requires_valid_columns(self, tmp_path):
        csv_path = tmp_path / "bad.csv"
        pd.DataFrame({"timestamp": ["2024-01-01"], "ticker": ["AAPL"]}).to_csv(csv_path, index=False)

        with pytest.raises(ValueError, match="missing required columns"):
            LocalTranscriptFileProvider(str(csv_path))

    def test_reads_csv_and_scores(self, tmp_path):
        csv_path = tmp_path / "transcripts.csv"
        pd.DataFrame({
            "timestamp": ["2024-01-15", "2024-04-10"],
            "ticker": ["AAPL", "MSFT"],
            "transcript_text": [
                "We had an excellent quarter with strong performance.",
                "We faced challenges and declining revenue this quarter.",
            ],
        }).to_csv(csv_path, index=False)

        provider = LocalTranscriptFileProvider(str(csv_path))
        df = provider.get_events(tickers=["AAPL", "MSFT"], start="2024-01-01", end="2024-12-31")

        assert len(df) == 2
        assert set(df["ticker"]) == {"AAPL", "MSFT"}
        assert df.iloc[0]["event_score"] > 0  # AAPL positive
        assert df.iloc[1]["event_score"] < 0  # MSFT negative
        assert df.iloc[0]["source"] == "local_file"
        assert df.iloc[0]["event_type"] == "earnings_call_transcript"

    def test_reads_parquet(self, tmp_path):
        parquet_path = tmp_path / "transcripts.parquet"
        pd.DataFrame({
            "timestamp": ["2024-01-15"],
            "ticker": ["AAPL"],
            "transcript_text": ["Good quarter with growth."],
        }).to_parquet(parquet_path, index=False)

        provider = LocalTranscriptFileProvider(str(parquet_path))
        df = provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

        assert len(df) == 1

    def test_empty_file_returns_empty_dataframe(self, tmp_path):
        csv_path = tmp_path / "empty.csv"
        pd.DataFrame(columns=["timestamp", "ticker", "transcript_text"]).to_csv(csv_path, index=False)

        provider = LocalTranscriptFileProvider(str(csv_path))
        df = provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

        assert df.empty
        assert list(df.columns) == EMPTY_COLUMNS

    def test_filters_by_ticker_and_date(self, tmp_path):
        csv_path = tmp_path / "transcripts.csv"
        pd.DataFrame({
            "timestamp": ["2023-12-01", "2024-06-15"],
            "ticker": ["AAPL", "MSFT"],
            "transcript_text": ["Old transcript.", "Current transcript."],
        }).to_csv(csv_path, index=False)

        provider = LocalTranscriptFileProvider(str(csv_path))
        df = provider.get_events(tickers=["MSFT"], start="2024-01-01", end="2024-12-31")

        assert len(df) == 1
        assert df.iloc[0]["ticker"] == "MSFT"

    def test_accepts_custom_scorer(self, tmp_path):
        csv_path = tmp_path / "transcripts.csv"
        pd.DataFrame({
            "timestamp": ["2024-01-15"],
            "ticker": ["AAPL"],
            "transcript_text": ["Some text."],
        }).to_csv(csv_path, index=False)

        from pairs_trading.features.lm_dict import LoughranMcDonaldScorer

        custom_scorer = LoughranMcDonaldScorer(positive_words=frozenset({"good", "great"}), negative_words=frozenset({"bad", "poor"}))
        provider = LocalTranscriptFileProvider(str(csv_path), scorer=custom_scorer)
        df = provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

        assert not df.empty

    def test_honors_year_and_quarter_columns(self, tmp_path):
        csv_path = tmp_path / "transcripts.csv"
        pd.DataFrame({
            "timestamp": ["2024-01-15"],
            "ticker": ["AAPL"],
            "transcript_text": ["Good quarter."],
            "year": [2024],
            "quarter": [1],
        }).to_csv(csv_path, index=False)

        provider = LocalTranscriptFileProvider(str(csv_path))
        df = provider.get_events(tickers=["AAPL"], start="2024-01-01", end="2024-12-31")

        assert df.iloc[0]["transcript_year"] == 2024
        assert df.iloc[0]["transcript_quarter"] == 1
