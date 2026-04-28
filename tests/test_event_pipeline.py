from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from pairs_trading.data.events import LocalEventFileProvider, SecCompanyFactsEventProvider, SecCompanyFilingsEventProvider
from pairs_trading.engines.backtesting import CostModel, WalkForwardBacktester, WalkForwardConfig
from pairs_trading.pipelines import EventDrivenConfig, EventDrivenPipeline
from pairs_trading.core.portfolio import PortfolioManager
from tests.common import fresh_test_dir, synthetic_directional_prices, synthetic_event_panel


class EventProviderTests(unittest.TestCase):
    def test_local_event_file_provider_filters_rows(self) -> None:
        data_dir = fresh_test_dir("artifacts/test_events/provider")
        event_path = data_dir / "events.csv"
        pd.DataFrame(
            {
                "timestamp": ["2024-01-01", "2024-01-03", "2024-01-02"],
                "ticker": ["AAA", "BBB", "AAA"],
                "event_score": [0.4, -0.2, 0.3],
                "confidence": [0.8, 0.9, 0.7],
            }
        ).to_csv(event_path, index=False)

        provider = LocalEventFileProvider(event_path)
        events = provider.get_events(["AAA"], "2024-01-01", "2024-01-02")

        self.assertEqual(events["ticker"].tolist(), ["AAA", "AAA"])
        self.assertEqual(len(events), 2)

    def test_sec_companyfacts_provider_builds_events_from_payload(self) -> None:
        provider = SecCompanyFactsEventProvider(
            user_agent="PairsTradingTest [test@example.com]",
            cache_dir=fresh_test_dir("artifacts/test_events/sec"),
        )
        payload = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"form": "10-Q", "filed": "2023-05-01", "fy": 2023, "fp": "Q1", "val": 100.0},
                                {"form": "10-Q", "filed": "2024-05-01", "fy": 2024, "fp": "Q1", "val": 120.0},
                            ]
                        }
                    },
                    "NetIncomeLoss": {
                        "units": {
                            "USD": [
                                {"form": "10-Q", "filed": "2023-05-01", "fy": 2023, "fp": "Q1", "val": 20.0},
                                {"form": "10-Q", "filed": "2024-05-01", "fy": 2024, "fp": "Q1", "val": 28.0},
                            ]
                        }
                    },
                }
            }
        }

        with patch.object(provider, "_load_ticker_map", return_value={"AAA": "0000000001"}), patch.object(
            provider,
            "_load_companyfacts",
            return_value=payload,
        ):
            events = provider.get_events(["AAA"], "2024-01-01", "2024-12-31")

        self.assertEqual(len(events), 1)
        self.assertEqual(events.iloc[0]["ticker"], "AAA")
        self.assertGreater(float(events.iloc[0]["event_score"]), 0.0)

    def test_sec_company_filings_provider_builds_official_earnings_events(self) -> None:
        provider = SecCompanyFilingsEventProvider(
            user_agent="PairsTradingTest [test@example.com]",
            cache_dir=fresh_test_dir("artifacts/test_events/sec_filings"),
        )
        payload = {
            "filings": {
                "recent": {
                    "accessionNumber": ["0000000001-24-000001", "0000000001-24-000002", "0000000001-24-000003"],
                    "filingDate": ["2024-02-01", "2024-05-02", "2024-05-03"],
                    "reportDate": ["2023-12-31", "2024-03-31", "2024-03-31"],
                    "form": ["10-K", "10-Q", "8-K"],
                    "primaryDocument": ["aaa-10k.htm", "aaa-10q.htm", "aaa-8k.htm"],
                    "primaryDocDescription": ["Annual report", "Quarterly report", "Results of Operations and Financial Condition"],
                    "items": ["", "", "2.02"],
                }
            }
        }

        with patch.object(provider, "_load_ticker_map", return_value={"AAA": "0000000001"}), patch.object(
            provider,
            "_load_submission_payload",
            return_value=payload,
        ):
            events = provider.get_events(["AAA"], "2024-01-01", "2024-12-31")

        self.assertEqual(len(events), 3)
        self.assertIn("earnings_release_8k", set(events["event_type"]))
        self.assertIn("quarterly_earnings_report", set(events["event_type"]))
        self.assertGreater(float(events["confidence"].max()), 0.0)


class EventPipelineTests(unittest.TestCase):
    def test_event_driven_pipeline_runs(self) -> None:
        prices = synthetic_directional_prices()[["TREND", "MEAN", "BREAK"]].rename(
            columns={"TREND": "AAA", "MEAN": "BBB", "BREAK": "CCC"}
        )
        events = synthetic_event_panel(prices.index, ["AAA", "BBB", "CCC"])
        pipeline = EventDrivenPipeline(
            events=events,
            portfolio_manager=PortfolioManager(
                max_leverage=1.1,
                risk_per_trade=0.04,
                volatility_window=15,
                max_strategy_weight=0.30,
            ),
            config=EventDrivenConfig.from_symbols(["AAA", "BBB", "CCC"], holding_period_bars=5, entry_threshold=0.10),
            name="event_pipeline_test",
        )

        result = WalkForwardBacktester(
            strategy=pipeline,
            prices=prices,
            config=WalkForwardConfig(
                train_bars=260,
                test_bars=80,
                step_bars=40,
                bars_per_year=252,
                purge_bars=5,
            ),
            cost_model=CostModel(
                commission_bps=0.5,
                spread_bps=1.0,
                slippage_bps=1.0,
                market_impact_bps=0.75,
                borrow_bps_annual=25.0,
                delay_bars=1,
            ),
            experiment_root=fresh_test_dir("artifacts/test_runs/event_pipeline"),
        ).run("event_pipeline_test")

        self.assertGreater(len(result.fold_metrics), 0)
        self.assertIn("dsr", result.summary)
        self.assertTrue(any(column.startswith("weight_") for column in result.equity_curve.columns))


if __name__ == "__main__":
    unittest.main()
