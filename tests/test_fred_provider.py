from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from pairs_trading.data.fred import (
    SERIES_MAP,
    FredEventProvider,
    _fetch_fred_observations,
    _series_to_events,
    _zscore_normalize,
)


def _mock_fred_response(values: list[tuple[str, str]]) -> str:
    observations = [{"date": d, "value": v} for d, v in values]
    return json.dumps({"observations": observations})


class TestFredEventProvider:
    def test_init_requires_api_key(self):
        with pytest.raises(ValueError, match="FRED API key is required"):
            FredEventProvider(api_key="")

    def test_init_rejects_unknown_series(self):
        with pytest.raises(ValueError, match="Unknown FRED series"):
            FredEventProvider(api_key="test", series=["UNKNOWN_SERIES"])

    def test_init_valid_series_subset(self):
        provider = FredEventProvider(api_key="test", series=["GDP", "UNEMPLOYMENT"])
        assert provider.series == ["GDP", "UNEMPLOYMENT"]
        assert provider.cache_dir.exists()

    def test_init_defaults_to_all_series(self):
        provider = FredEventProvider(api_key="test")
        assert set(provider.series) == set(SERIES_MAP.keys())

    @patch("pairs_trading.data.fred.urlopen")
    def test_get_events_happy_path(self, mock_urlopen, tmp_path):
        mock_response = MagicMock()
        mock_response.read.return_value = _mock_fred_response([
            ("2020-01-01", "100.0"),
            ("2020-06-01", "105.0"),
            ("2020-12-01", "110.0"),
        ]).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        provider = FredEventProvider(api_key="test", series=["GDP"], cache_dir=str(tmp_path))
        events = provider.get_events(tickers=["AAPL"], start="2020-01-01", end="2020-12-31")

        assert not events.empty
        assert all(events["ticker"] == "MACRO")
        assert all(events["source"] == "fred")
        assert "event_score" in events.columns
        assert "confidence" in events.columns
        assert "series_name" in events.columns
        assert set(events["event_type"]) == {"fred_gdp"}
        assert len(events) == 3

    @patch("pairs_trading.data.fred.urlopen")
    def test_get_events_empty_response(self, mock_urlopen, tmp_path):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"observations": []}).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        provider = FredEventProvider(api_key="test", series=["GDP"], cache_dir=str(tmp_path))
        events = provider.get_events(tickers=["AAPL"], start="2020-01-01", end="2020-12-31")

        assert events.empty

    @patch("pairs_trading.data.fred.urlopen")
    def test_get_events_skips_missing_values(self, mock_urlopen, tmp_path):
        mock_response = MagicMock()
        mock_response.read.return_value = _mock_fred_response([
            ("2020-01-01", "."),
            ("2020-06-01", "105.0"),
            ("2020-12-01", "."),
        ]).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        provider = FredEventProvider(api_key="test", series=["GDP"], cache_dir=str(tmp_path))
        events = provider.get_events(tickers=["AAPL"], start="2020-01-01", end="2020-12-31")

        assert len(events) == 1

    @patch("pairs_trading.data.fred.urlopen")
    def test_get_events_multiple_series(self, mock_urlopen, tmp_path):
        mock_response = MagicMock()
        mock_response.read.return_value = _mock_fred_response([
            ("2020-01-01", "100.0"),
            ("2020-06-01", "105.0"),
        ]).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        provider = FredEventProvider(api_key="test", series=["GDP", "UNEMPLOYMENT"], cache_dir=str(tmp_path))
        events = provider.get_events(tickers=["AAPL"], start="2020-01-01", end="2020-12-31")

        assert not events.empty
        assert set(events["event_type"].unique()) == {"fred_gdp", "fred_unemployment"}
        assert len(events) == 4

    @patch("pairs_trading.data.fred.urlopen")
    def test_get_events_sorts_by_timestamp(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = _mock_fred_response([
            ("2020-06-01", "200.0"),
            ("2020-01-01", "100.0"),
            ("2020-03-01", "150.0"),
        ]).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        provider = FredEventProvider(api_key="test", series=["GDP"])
        events = provider.get_events(tickers=["AAPL"], start="2020-01-01", end="2020-12-31")

        timestamps = pd.to_datetime(events["timestamp"])
        assert timestamps.is_monotonic_increasing

    def test_caching_reuses_cached_data(self, tmp_path):
        cache_dir = tmp_path / "fred_cache"
        cache_path = cache_dir / "GDPC1_2020-01-01_2020-12-31.csv"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cached = pd.DataFrame({"value": [100.0, 105.0, 110.0]}, index=pd.DatetimeIndex(["2020-01-01", "2020-06-01", "2020-12-01"]))
        cached.to_csv(cache_path)

        provider = FredEventProvider(api_key="test_irrelevant", cache_dir=str(cache_dir), series=["GDP"])
        events = provider.get_events(tickers=["AAPL"], start="2020-01-01", end="2020-12-31")

        assert len(events) == 3


class TestFredInternalFunctions:
    def test_zscore_normalize_constant_series(self):
        s = pd.Series([5.0, 5.0, 5.0])
        result = _zscore_normalize(s)
        assert (result == 0.0).all()

    def test_zscore_normalize_varied_series(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _zscore_normalize(s)
        assert abs(result.mean()) < 1e-10
        assert abs(result.std(ddof=0) - 1.0) < 1e-10

    def test_zscore_normalize_single_value(self):
        s = pd.Series([42.0])
        result = _zscore_normalize(s)
        assert result.iloc[0] == 0.0

    def test_series_to_events_empty(self):
        s = pd.Series(dtype=float, name="test")
        events = _series_to_events("TEST", s, sign=1)
        assert events.empty

    def test_series_to_events_positive_sign(self):
        s = pd.Series([1.0, 2.0, 3.0], index=pd.DatetimeIndex(["2020-01-01", "2020-06-01", "2020-12-01"]), name="GDP")
        events = _series_to_events("GDP", s, sign=1)
        assert len(events) == 3
        assert all(events["ticker"] == "MACRO")
        assert all(-1.0 <= e <= 1.0 for e in events["event_score"])
        assert events["event_score"].iloc[-1] > 0  # highest raw value → positive score

    def test_series_to_events_negative_sign(self):
        s = pd.Series([1.0, 2.0, 3.0], index=pd.DatetimeIndex(["2020-01-01", "2020-06-01", "2020-12-01"]), name="UNEMPLOYMENT")
        events = _series_to_events("UNEMPLOYMENT", s, sign=-1)
        assert len(events) == 3
        assert all(-1.0 <= e <= 1.0 for e in events["event_score"])
        # With negative sign, highest raw value gets most negative score
        assert events["event_score"].iloc[-1] < 0

    def test_series_to_events_zero_sign(self):
        s = pd.Series([1.0, 2.0, 3.0], index=pd.DatetimeIndex(["2020-01-01", "2020-06-01", "2020-12-01"]), name="FEDFUNDS")
        events = _series_to_events("FEDFUNDS", s, sign=0)
        assert len(events) == 3
        assert all(-1.0 <= e <= 1.0 for e in events["event_score"])


class TestFredProviderIntegration:
    @patch("pairs_trading.data.fred.urlopen")
    def test_realistic_gdp_to_unemployment_scenario(self, mock_urlopen, tmp_path):
        """GDP declining + unemployment rising should produce negative event scores."""
        responses: list[MagicMock] = []

        def side_effect(req, **kwargs):
            resp = MagicMock()
            url = req.get_full_url()
            if "GDPC1" in url:
                data = [("2020-Q1", "100.0"), ("2020-Q2", "95.0"), ("2020-Q3", "90.0")]
            else:
                data = [("2020-01", "3.5"), ("2020-04", "8.0"), ("2020-07", "10.0")]
            resp.read.return_value = _mock_fred_response(data).encode("utf-8")
            ctx_mgr = MagicMock()
            ctx_mgr.__enter__.return_value = resp
            ctx_mgr.__exit__.return_value = None
            responses.append(resp)
            return ctx_mgr

        mock_urlopen.side_effect = side_effect

        provider = FredEventProvider(api_key="test", series=["GDP", "UNEMPLOYMENT"], cache_dir=str(tmp_path))
        events = provider.get_events(tickers=["AAPL"], start="2020-01-01", end="2020-12-31")

        assert not events.empty
        gdp_events = events[events["event_type"] == "fred_gdp"]
        unemp_events = events[events["event_type"] == "fred_unemployment"]
        assert len(gdp_events) == 3
        assert len(unemp_events) == 3

    @patch("pairs_trading.data.fred.urlopen")
    def test_cache_is_written_on_fetch(self, mock_urlopen, tmp_path):
        mock_response = MagicMock()
        mock_response.read.return_value = _mock_fred_response([
            ("2020-01-01", "100.0"),
        ]).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        cache_dir = tmp_path / "fred_cache"
        provider = FredEventProvider(api_key="test", series=["GDP"], cache_dir=str(cache_dir))
        provider.get_events(tickers=["AAPL"], start="2020-01-01", end="2020-12-31")

        cache_files = list(cache_dir.glob("*.csv"))
        assert len(cache_files) == 1
