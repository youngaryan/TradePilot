from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from pairs_trading.data.stocktwits import StockTwitsHeadlineProvider


def _mock_response(messages: list[dict], cursor: str | None = None, more: bool = False) -> str:
    payload: dict = {"messages": messages}
    if cursor is not None:
        payload["cursor"] = {"next": cursor, "more": more}
    return json.dumps(payload)


def _message(
    body: str,
    created: str = "2024-01-15T14:30:00Z",
    mid: int = 1,
    sentiment: str | None = "Bullish",
    username: str = "trader1",
) -> dict:
    msg: dict = {
        "id": mid,
        "body": body,
        "created_at": created,
        "user": {"username": username},
    }
    if sentiment:
        msg["entities"] = {"sentiment": {"basic": sentiment}}
    return msg


class TestStockTwitsHeadlineProvider:
    def test_init_requires_token(self):
        with pytest.raises(ValueError, match="StockTwits access token is required"):
            StockTwitsHeadlineProvider(access_token="")

    def test_init_accepts_valid_token(self):
        provider = StockTwitsHeadlineProvider(access_token="valid_token")
        assert provider.access_token == "valid_token"

    def test_init_clamps_max_pages(self):
        provider = StockTwitsHeadlineProvider(access_token="t", max_pages=0)
        assert provider.max_pages == 1

    @patch.object(StockTwitsHeadlineProvider, "_fetch_json")
    def test_happy_path_single_page(self, mock_fetch):
        mock_fetch.return_value = json.loads(_mock_response(
            [
                _message("Bullish on $AAPL!", mid=1, sentiment="Bullish"),
                _message("Bearish on $AAPL", mid=2, sentiment="Bearish"),
                _message("Neutral take", mid=3, sentiment=None),
            ],
            cursor=None,
            more=False,
        ))

        provider = StockTwitsHeadlineProvider(access_token="t")
        df = provider.get_headlines(tickers=["AAPL"], start="2024-01-01", end="2024-01-31")

        assert len(df) == 3
        assert list(df.columns) == ["timestamp", "ticker", "headline", "source", "relevance", "url", "user_sentiment", "user_username", "message_id"]
        assert all(df["ticker"] == "AAPL")
        assert all(df["source"] == "StockTwits")
        assert df.iloc[0]["user_sentiment"] == "Bullish"
        assert df.iloc[1]["user_sentiment"] == "Bearish"
        assert pd.isna(df.iloc[2]["user_sentiment"])

    @patch.object(StockTwitsHeadlineProvider, "_fetch_json")
    def test_empty_response_returns_empty_dataframe(self, mock_fetch):
        mock_fetch.return_value = json.loads(_mock_response([], cursor=None, more=False))

        provider = StockTwitsHeadlineProvider(access_token="t")
        df = provider.get_headlines(tickers=["AAPL"], start="2024-01-01", end="2024-01-31")

        assert df.empty
        assert list(df.columns) == ["timestamp", "ticker", "headline", "relevance", "source", "url", "user_sentiment", "user_username", "message_id"]

    @patch.object(StockTwitsHeadlineProvider, "_fetch_json")
    def test_pagination_stops_when_no_more(self, mock_fetch):
        mock_fetch.side_effect = [
            json.loads(_mock_response(
                [_message(f"msg{i}", mid=i) for i in range(30)],
                cursor="cursor2", more=True,
            )),
            json.loads(_mock_response(
                [_message(f"msg{i}", mid=i) for i in range(30, 35)],
                cursor=None, more=False,
            )),
        ]

        provider = StockTwitsHeadlineProvider(access_token="t", max_pages=10)
        df = provider.get_headlines(tickers=["AAPL"], start="2024-01-01", end="2024-01-31")

        assert len(df) == 35
        assert mock_fetch.call_count == 2

    @patch.object(StockTwitsHeadlineProvider, "_fetch_json")
    def test_pagination_respects_max_pages(self, mock_fetch):
        mock_fetch.return_value = json.loads(_mock_response(
            [_message(f"msg{i}", mid=i) for i in range(30)],
            cursor="next_page", more=True,
        ))

        provider = StockTwitsHeadlineProvider(access_token="t", max_pages=3)
        df = provider.get_headlines(tickers=["AAPL"], start="2024-01-01", end="2024-01-31")

        assert len(df) == 90
        assert mock_fetch.call_count == 3
        assert provider.last_errors
        assert "coverage for AAPL is incomplete" in provider.last_errors[0]

    @patch.object(StockTwitsHeadlineProvider, "_fetch_json")
    def test_pagination_stops_after_reaching_requested_start(self, mock_fetch):
        mock_fetch.side_effect = [
            json.loads(_mock_response([_message("recent", created="2024-01-31T12:00:00Z")], cursor="page2", more=True)),
            json.loads(_mock_response([_message("boundary", created="2024-01-01T00:00:00Z")], cursor="page3", more=True)),
        ]

        provider = StockTwitsHeadlineProvider(access_token="t", max_pages=10)
        df = provider.get_headlines(tickers=["AAPL"], start="2024-01-01", end="2024-01-31")

        assert len(df) == 2
        assert mock_fetch.call_count == 2
        assert provider.last_errors == []

    @patch.object(StockTwitsHeadlineProvider, "_fetch_json")
    def test_skips_messages_without_body(self, mock_fetch):
        mock_fetch.return_value = json.loads(_mock_response([
            {"id": 1, "body": "", "created_at": "2024-01-15T14:30:00Z", "user": {"username": "u"}},
            {"id": 2, "created_at": "2024-01-15T14:31:00Z", "user": {"username": "u"}},
        ]))

        provider = StockTwitsHeadlineProvider(access_token="t")
        df = provider.get_headlines(tickers=["AAPL"], start="2024-01-01", end="2024-01-31")

        assert df.empty

    @patch.object(StockTwitsHeadlineProvider, "_fetch_json")
    def test_filters_messages_by_date_range(self, mock_fetch):
        mock_fetch.return_value = json.loads(_mock_response([
            _message("Outside range", created="2023-12-01T00:00:00Z", mid=1),
            _message("Inside range", created="2024-01-15T00:00:00Z", mid=2),
        ]))

        provider = StockTwitsHeadlineProvider(access_token="t")
        df = provider.get_headlines(tickers=["AAPL"], start="2024-01-01", end="2024-01-31")

        assert len(df) == 1
        assert df.iloc[0]["headline"] == "Inside range"

    @patch.object(StockTwitsHeadlineProvider, "_fetch_json")
    def test_sends_bearer_token(self, mock_fetch):
        mock_fetch.return_value = json.loads(_mock_response(
            [_message("test", mid=1)],
        ))

        provider = StockTwitsHeadlineProvider(access_token="my_secret_token")
        provider.get_headlines(tickers=["AAPL"], start="2024-01-01", end="2024-01-31")

        call_kwargs = mock_fetch.call_args[1]
        headers = call_kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer my_secret_token"

    @patch.object(StockTwitsHeadlineProvider, "_fetch_json")
    def test_handles_multiple_tickers(self, mock_fetch):
        def side_effect(url, params, headers=None):
            if "AAPL" in url:
                return json.loads(_mock_response([_message("AAPL msg", mid=1)]))
            return json.loads(_mock_response([_message("MSFT msg", mid=2)]))

        mock_fetch.side_effect = side_effect

        provider = StockTwitsHeadlineProvider(access_token="t")
        df = provider.get_headlines(tickers=["AAPL", "MSFT"], start="2024-01-01", end="2024-01-31")

        assert len(df) == 2
        assert set(df["ticker"]) == {"AAPL", "MSFT"}

    @patch.object(StockTwitsHeadlineProvider, "_fetch_json")
    def test_error_on_missing_token_is_not_silent(self, mock_fetch):
        mock_fetch.side_effect = ConnectionError("API unreachable")

        provider = StockTwitsHeadlineProvider(access_token="t")
        with pytest.raises(ConnectionError):
            provider.get_headlines(tickers=["AAPL"], start="2024-01-01", end="2024-01-31")

    @patch.object(StockTwitsHeadlineProvider, "_fetch_json")
    def test_normalizes_tickers_to_upper(self, mock_fetch):
        mock_fetch.return_value = json.loads(_mock_response(
            [_message("test", mid=1)],
        ))

        provider = StockTwitsHeadlineProvider(access_token="t")
        df = provider.get_headlines(tickers=["aapl"], start="2024-01-01", end="2024-01-31")

        assert df.iloc[0]["ticker"] == "AAPL"

    @patch.object(StockTwitsHeadlineProvider, "_fetch_json")
    def test_returns_dataframe_sorted(self, mock_fetch):
        mock_fetch.return_value = json.loads(_mock_response([
            _message("second", created="2024-01-15T14:30:00Z", mid=2),
            _message("first", created="2024-01-15T14:00:00Z", mid=1),
        ]))

        provider = StockTwitsHeadlineProvider(access_token="t")
        df = provider.get_headlines(tickers=["AAPL"], start="2024-01-01", end="2024-01-31")

        assert df.iloc[0]["headline"] == "first"
        assert df.iloc[1]["headline"] == "second"

    @patch.object(StockTwitsHeadlineProvider, "_fetch_json")
    def test_handles_unexpected_payload_type(self, mock_fetch):
        mock_fetch.return_value = "not a dict"

        provider = StockTwitsHeadlineProvider(access_token="t")
        df = provider.get_headlines(tickers=["AAPL"], start="2024-01-01", end="2024-01-31")

        assert df.empty
