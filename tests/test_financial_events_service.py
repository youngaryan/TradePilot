from __future__ import annotations

import unittest

import pandas as pd

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.financial_events import FinancialEventsService
from pairs_trading.data.events import EventProvider
from pairs_trading.data.market import MarketDataProvider
from tests.common import fresh_test_dir


class StaticEventProvider(EventProvider):
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def get_events(self, tickers, start, end) -> pd.DataFrame:  # type: ignore[override]
        if self.frame.empty:
            return self.frame
        request_tickers = {str(ticker).upper() for ticker in tickers}
        filtered = self.frame.copy()
        filtered["timestamp"] = pd.to_datetime(filtered["timestamp"]).dt.tz_localize(None)
        filtered["ticker"] = filtered["ticker"].astype(str).str.upper()
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        return filtered[
            filtered["ticker"].isin(request_tickers)
            & (filtered["timestamp"] >= start_ts)
            & (filtered["timestamp"] <= end_ts)
        ].reset_index(drop=True)


class StaticMarketDataProvider(MarketDataProvider):
    def __init__(self, prices: pd.DataFrame) -> None:
        self.prices = prices

    def get_close_prices(self, symbols, start, end, interval: str = "1d") -> pd.DataFrame:  # type: ignore[override]
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        columns = [symbol for symbol in symbols if symbol in self.prices.columns]
        return self.prices.loc[(self.prices.index >= start_ts) & (self.prices.index < end_ts), columns]


class FinancialEventsServiceTests(unittest.TestCase):
    def _settings(self) -> BackendSettings:
        root = fresh_test_dir("artifacts/test_financial_events")
        return BackendSettings(
            event_cache_dir=root / "events",
            price_cache_dir=root / "prices",
            email_from="test@example.com",
        )

    def test_builds_verified_matrix_rows_from_sec_filings_and_companyfacts(self) -> None:
        filings = pd.DataFrame(
            [
                {
                    "timestamp": "2024-05-02",
                    "ticker": "AAA",
                    "event_score": 0.15,
                    "confidence": 0.45,
                    "event_type": "quarterly_earnings_report",
                    "source": "sec_submissions",
                    "form": "10-Q",
                    "report_date": "2024-03-31",
                    "accession_number": "0000000001-24-000001",
                    "primary_document": "aaa-10q.htm",
                    "items": "",
                    "description": "Quarterly report",
                    "url": "https://www.sec.gov/Archives/edgar/data/1/000000000124000001/aaa-10q.htm",
                },
                {
                    "timestamp": "2024-06-03",
                    "ticker": "BBB",
                    "event_score": 0.10,
                    "confidence": 0.45,
                    "event_type": "dividend_announcement",
                    "source": "sec_submissions",
                    "form": "8-K",
                    "report_date": "",
                    "accession_number": "0000000002-24-000001",
                    "primary_document": "bbb-8k.htm",
                    "items": "8.01",
                    "description": "Dividend announcement",
                    "url": "https://www.sec.gov/Archives/edgar/data/2/000000000224000001/bbb-8k.htm",
                },
            ]
        )
        facts = pd.DataFrame(
            [
                {
                    "timestamp": "2024-05-02",
                    "ticker": "AAA",
                    "cik": "0000000001",
                    "event_score": 0.30,
                    "confidence": 0.90,
                    "event_type": "edgar_companyfacts",
                    "source": "sec_companyfacts",
                    "form": "10-Q",
                    "fy": 2024,
                    "fp": "Q1",
                    "revenue": 125_000_000.0,
                    "earnings": 22_000_000.0,
                    "eps": 1.35,
                    "revenue_yoy": 0.25,
                    "earnings_yoy": 0.10,
                    "eps_yoy": 0.08,
                }
            ]
        )
        prices = pd.DataFrame(
            {
                "AAA": [100.0, 103.0, 104.0, 105.0],
                "BBB": [50.0, 51.0, 52.0, 51.5],
            },
            index=pd.to_datetime(["2024-05-01", "2024-05-03", "2024-06-02", "2024-06-04"]),
        )

        service = FinancialEventsService(
            self._settings(),
            filings_provider=StaticEventProvider(filings),
            companyfacts_provider=StaticEventProvider(facts),
            market_data_provider=StaticMarketDataProvider(prices),
        )

        payload = service.events(["AAA", "BBB"], "2024-05-01", "2024-06-10")

        self.assertEqual(payload["summary"]["event_count"], 2)
        aaa = next(row for row in payload["events"] if row["ticker"] == "AAA")
        self.assertIn("Revenue $125.00M", aaa["reported_result"])
        self.assertIn("EPS $1.35", aaa["reported_result"])
        self.assertIsNone(aaa["expected_result"])
        self.assertEqual(aaa["beat_miss"], "not_available")
        self.assertIsNotNone(aaa["market_reaction_pct"])
        self.assertEqual(aaa["source"], "SEC EDGAR filing")
        self.assertIn("reported_result", aaa["verified_fields"])
        self.assertIn("expected_result", aaa["missing_fields"])
        self.assertTrue(payload["analysis"]["verified"])
        self.assertTrue(any("Consensus estimates" in note for note in payload["analysis"]["missing_data"]))

    def test_handles_missing_financial_event_data_without_fabricating_results(self) -> None:
        service = FinancialEventsService(
            self._settings(),
            filings_provider=StaticEventProvider(pd.DataFrame()),
            companyfacts_provider=StaticEventProvider(pd.DataFrame()),
            market_data_provider=StaticMarketDataProvider(pd.DataFrame()),
        )

        payload = service.events(["ZZZ"], "2024-01-01", "2024-01-31")

        self.assertEqual(payload["events"], [])
        self.assertEqual(payload["summary"]["event_count"], 0)
        self.assertIn("No verified financial events", payload["analysis"]["summary"])
        self.assertTrue(any("Consensus estimates" in note for note in payload["analysis"]["missing_data"]))


if __name__ == "__main__":
    unittest.main()
