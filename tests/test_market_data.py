from __future__ import annotations

from pathlib import Path
import unittest

import pandas as pd

from pairs_trading.data.market import CachedParquetProvider, MarketDataProvider
from tests.common import fresh_test_dir


class DummyProvider(MarketDataProvider):
    def __init__(self, data: pd.DataFrame) -> None:
        self.data = data
        self.calls = 0

    def get_close_prices(self, symbols, start, end, interval="1d") -> pd.DataFrame:
        self.calls += 1
        return self.data.loc[:, list(symbols)].copy()


class MarketDataCacheTests(unittest.TestCase):
    def test_cached_parquet_provider_uses_cache_after_first_fetch(self) -> None:
        index = pd.date_range("2024-01-01", periods=3, freq="D")
        data = pd.DataFrame({"AAA": [1.0, 1.1, 1.2], "BBB": [2.0, 2.1, 2.2]}, index=index)
        upstream = DummyProvider(data=data)
        cache_dir = fresh_test_dir("artifacts/test_cache/market_data")

        provider = CachedParquetProvider(upstream=upstream, cache_dir=cache_dir)
        first = provider.get_close_prices(["AAA", "BBB"], "2024-01-01", "2024-01-04")
        self.assertEqual(upstream.calls, 1)
        self.assertTrue(any(Path(cache_dir).rglob("*.parquet")))

        upstream.data = pd.DataFrame({"AAA": [9.0, 9.0, 9.0], "BBB": [9.0, 9.0, 9.0]}, index=index)
        second = provider.get_close_prices(["AAA", "BBB"], "2024-01-01", "2024-01-04")

        self.assertEqual(upstream.calls, 1)
        pd.testing.assert_frame_equal(first, second, check_freq=False)

    def test_cached_parquet_provider_reuses_wider_cached_range(self) -> None:
        index = pd.date_range("2024-01-01", periods=5, freq="D")
        data = pd.DataFrame({"AAA": [1.0, 1.1, 1.2, 1.3, 1.4], "BBB": [2.0, 2.1, 2.2, 2.3, 2.4]}, index=index)
        upstream = DummyProvider(data=data)
        cache_dir = fresh_test_dir("artifacts/test_cache/market_data_subset")

        provider = CachedParquetProvider(upstream=upstream, cache_dir=cache_dir)
        provider.get_close_prices(["AAA", "BBB"], "2024-01-01", "2024-01-06")
        self.assertEqual(upstream.calls, 1)

        upstream.data = pd.DataFrame({"AAA": [9.0] * 5, "BBB": [9.0] * 5}, index=index)
        subset = provider.get_close_prices(["AAA"], "2024-01-02", "2024-01-05")

        self.assertEqual(upstream.calls, 1)
        expected = data.loc[(data.index >= pd.Timestamp("2024-01-02")) & (data.index < pd.Timestamp("2024-01-05")), ["AAA"]]
        pd.testing.assert_frame_equal(subset, expected, check_freq=False)

    def test_cached_parquet_provider_derives_four_hour_bars_from_hourly_cache(self) -> None:
        index = pd.date_range("2024-01-01 09:00", periods=8, freq="h")
        data = pd.DataFrame({"AAA": [float(value) for value in range(8)]}, index=index)
        upstream = DummyProvider(data=data)
        cache_dir = fresh_test_dir("artifacts/test_cache/market_data_4h")

        provider = CachedParquetProvider(upstream=upstream, cache_dir=cache_dir)
        derived = provider.get_close_prices(["AAA"], "2024-01-01", "2024-01-02", interval="4h")

        self.assertEqual(upstream.calls, 1)
        self.assertTrue(cache_dir.joinpath("4h").exists())
        self.assertLess(len(derived), len(data))
        self.assertEqual(float(derived["AAA"].iloc[-1]), 7.0)


if __name__ == "__main__":
    unittest.main()
