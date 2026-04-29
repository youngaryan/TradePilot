from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import pandas as pd

from tests.common import fresh_test_dir

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - optional backend dependency
    TestClient = None


class FixedBackendSentimentModel:
    def score_texts(self, texts: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "label": "positive",
                    "score": 0.7,
                    "confidence": 0.85,
                    "positive_prob": 0.75,
                    "negative_prob": 0.05,
                    "neutral_prob": 0.20,
                }
                for _ in texts
            ]
        )


class EmptyBackendHeadlineProvider:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_headlines(self, tickers, start, end) -> pd.DataFrame:
        return pd.DataFrame(columns=["timestamp", "ticker", "headline", "relevance", "source", "url"])


class FailingBackendHeadlineProvider:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_headlines(self, tickers, start, end) -> pd.DataFrame:
        raise RuntimeError("unauthorized")


@unittest.skipIf(TestClient is None, "FastAPI backend dependencies are not installed.")
class BackendAppTests(unittest.TestCase):
    def test_backend_routes_return_paper_payload(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_app")
        state_dir = workspace / "state"
        run_dir = workspace / "runs" / "20260424T230000Z_paper_batch"
        state_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)

        state_dir.joinpath("trend.json").write_text(
            json.dumps(
                {
                    "strategy_name": "trend",
                    "mode": "asset",
                    "initial_cash": 100000.0,
                    "history": [
                        {
                            "timestamp": "2026-04-24T00:00:00",
                            "mode": "asset",
                            "equity_after": 101000.0,
                            "daily_pnl": 1000.0,
                            "rebalance_cost_pnl": -5.0,
                            "net_return_since_inception": 0.01,
                            "cash_after": 20000.0,
                            "gross_exposure_notional": 81000.0,
                            "gross_exposure_ratio": 0.80198,
                            "position_count": 1,
                            "trade_count": 1,
                            "turnover_notional": 7000.0,
                            "positions": {"SPY": 120.0},
                            "target_weights": {"SPY": 0.80},
                            "metadata": {"pipeline": "etf_trend"},
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        state_dir.joinpath("trend_latest_orders.json").write_text("[]", encoding="utf-8")
        (run_dir / "paper_batch_summary.json").write_text(
            json.dumps({"asof_date": "2026-04-24", "run_timestamp_utc": "20260424T230000Z"}),
            encoding="utf-8",
        )

        app = create_app(
            BackendSettings(
                paper_state_dir=state_dir,
                paper_artifact_root=workspace / "runs",
                paper_job_state_dir=workspace / "paper_jobs",
                backtest_job_state_dir=workspace / "backtest_jobs",
                metadata_db_path=workspace / "metadata.sqlite3",
                default_paper_config=workspace / "missing.json",
            )
        )
        client = TestClient(app)

        health = client.get("/api/health")
        summary = client.get("/api/paper/summary")
        strategy = client.get("/api/paper/strategies/trend")
        missing = client.get("/api/paper/strategies/missing")
        catalog = client.get("/api/strategies/catalog")
        catalog_item = client.get("/api/strategies/catalog/ema_cross")
        paper_jobs = client.get("/api/paper/jobs")
        metadata = client.get("/api/system/metadata")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["totals"]["equity"], 101000.0)
        self.assertEqual(strategy.status_code, 200)
        self.assertEqual(strategy.json()["name"], "trend")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(catalog.status_code, 200)
        self.assertGreaterEqual(len(catalog.json()), 10)
        self.assertEqual(catalog_item.status_code, 200)
        self.assertEqual(catalog_item.json()["id"], "ema_cross")
        self.assertEqual(paper_jobs.status_code, 200)
        self.assertEqual(metadata.status_code, 200)
        self.assertEqual(metadata.json()["counts"]["jobs"], 0)

    def test_sentiment_routes_accumulate_local_news_dataset(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_sentiment")
        news_path = workspace / "headlines.csv"
        output_dir = workspace / "sentiment_shadow"
        pd.DataFrame(
            {
                "timestamp": ["2024-01-01T09:00:00Z", "2024-01-01T10:15:00Z"],
                "ticker": ["AAA", "AAA"],
                "headline": ["AAA beats earnings estimates", "AAA raises full year guidance"],
                "source": ["unit_news", "unit_news"],
                "url": ["https://example.com/a", "https://example.com/b"],
                "relevance": [1.0, 0.9],
            }
        ).to_csv(news_path, index=False)

        app = create_app(
            BackendSettings(
                paper_state_dir=workspace / "state",
                paper_artifact_root=workspace / "runs",
                paper_job_state_dir=workspace / "paper_jobs",
                backtest_job_state_dir=workspace / "backtest_jobs",
                metadata_db_path=workspace / "metadata.sqlite3",
                default_paper_config=workspace / "missing.json",
                sentiment_cache_dir=workspace / "sentiment_cache",
            )
        )
        client = TestClient(app)

        with patch(
            "pairs_trading.backend.services.build_best_available_sentiment_model",
            return_value=FixedBackendSentimentModel(),
        ):
            response = client.post(
                "/api/sentiment/accumulate",
                json={
                    "symbols": ["AAA"],
                    "start": "2024-01-01",
                    "end": "2024-01-02",
                    "providers": ["local"],
                    "news_files": [str(news_path)],
                    "rss_feed_urls": [],
                    "output_dir": str(output_dir),
                    "use_finbert": False,
                    "local_finbert_only": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["headline_count"], 2)
        self.assertEqual(payload["summary"]["daily_rows"], 1)
        self.assertEqual(payload["daily_points"][0]["ticker"], "AAA")
        self.assertEqual(payload["source_summary"][0]["source"], "unit_news")
        self.assertTrue(output_dir.joinpath("daily_sentiment.parquet").exists())

        dataset = client.get("/api/sentiment/dataset", params={"output_dir": str(output_dir)})
        self.assertEqual(dataset.status_code, 200)
        self.assertEqual(dataset.json()["summary"]["scored_headline_count"], 2)

    def test_sentiment_dataset_returns_rows_beyond_old_preview_window(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_sentiment_preview_window")
        output_dir = workspace / "sentiment_shadow"
        output_dir.mkdir(parents=True, exist_ok=True)

        newer_rows = pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-04-29T12:00:00", periods=90, freq="-1min"),
                "ticker": ["AAA"] * 90,
                "headline": [f"AAA market update {index}" for index in range(90)],
                "source": ["unit_feed"] * 90,
                "url": [f"https://example.com/aaa/{index}" for index in range(90)],
                "relevance": [1.0] * 90,
            }
        )
        older_rows = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2026-04-22T20:10:00", "2026-04-16T14:30:00"]),
                "ticker": ["COKE", "COKE"],
                "headline": ["COKE announces earnings release date", "COKE volume trend improves"],
                "source": ["feeds.finance.yahoo.com", "alphavantage"],
                "url": ["https://example.com/coke/1", "https://example.com/coke/2"],
                "relevance": [1.0, 0.8],
            }
        )
        raw = pd.concat([newer_rows, older_rows], ignore_index=True)
        scored = raw.assign(label="positive", score=0.7, confidence=0.85)
        daily = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-04-16", "2026-04-22"]),
                "ticker": ["COKE", "COKE"],
                "sentiment_score": [0.3, 0.7],
                "article_count": [1, 1],
                "confidence": [0.85, 0.85],
            }
        )
        raw.to_parquet(output_dir / "raw_headlines.parquet", index=False)
        scored.to_parquet(output_dir / "scored_headlines.parquet", index=False)
        daily.to_parquet(output_dir / "daily_sentiment.parquet", index=False)
        output_dir.joinpath("metadata.json").write_text(
            json.dumps(
                {
                    "tickers": ["COKE"],
                    "providers": ["rss", "alphavantage"],
                    "fetched_headlines": 2,
                    "stored_headlines": 92,
                    "daily_rows": 2,
                }
            ),
            encoding="utf-8",
        )

        app = create_app(
            BackendSettings(
                paper_state_dir=workspace / "state",
                paper_artifact_root=workspace / "runs",
                paper_job_state_dir=workspace / "paper_jobs",
                backtest_job_state_dir=workspace / "backtest_jobs",
                metadata_db_path=workspace / "metadata.sqlite3",
                default_paper_config=workspace / "missing.json",
                sentiment_cache_dir=workspace / "sentiment_cache",
            )
        )
        client = TestClient(app)

        response = client.get("/api/sentiment/dataset", params={"output_dir": str(output_dir)})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["headline_count"], 92)
        self.assertEqual(payload["summary"]["returned_headline_count"], 92)
        self.assertEqual(payload["summary"]["returned_scored_headline_count"], 92)
        self.assertIn("COKE", {row["ticker"] for row in payload["headlines"]})
        self.assertIn("COKE", {row["ticker"] for row in payload["scored_headlines"]})

    def test_sentiment_routes_explain_empty_rss_date_windows(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_sentiment_empty_rss")
        app = create_app(
            BackendSettings(
                paper_state_dir=workspace / "state",
                paper_artifact_root=workspace / "runs",
                paper_job_state_dir=workspace / "paper_jobs",
                backtest_job_state_dir=workspace / "backtest_jobs",
                metadata_db_path=workspace / "metadata.sqlite3",
                default_paper_config=workspace / "missing.json",
                sentiment_cache_dir=workspace / "sentiment_cache",
            )
        )
        client = TestClient(app)

        with (
            patch("pairs_trading.backend.services.RSSHeadlineProvider", EmptyBackendHeadlineProvider),
            patch("pairs_trading.backend.services.build_best_available_sentiment_model", return_value=FixedBackendSentimentModel()),
        ):
            response = client.post(
                "/api/sentiment/accumulate",
                json={
                    "symbols": ["GLD"],
                    "start": "2024-01-01",
                    "end": "2024-02-10",
                    "providers": ["rss"],
                    "rss_feed_urls": [],
                    "news_files": [],
                    "output_dir": str(workspace / "sentiment_shadow"),
                    "use_finbert": False,
                    "local_finbert_only": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["headline_count"], 0)
        self.assertTrue(any("RSS feeds are live feeds" in warning for warning in payload["warnings"]))

    def test_sentiment_routes_continue_when_optional_api_provider_fails(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_sentiment_partial_failure")
        news_path = workspace / "headlines.csv"
        output_dir = workspace / "sentiment_shadow"
        pd.DataFrame(
            {
                "timestamp": ["2026-04-24T09:00:00Z"],
                "ticker": ["GLD"],
                "headline": ["Gold ETF demand improves"],
                "source": ["unit_news"],
                "url": ["https://example.com/gld"],
                "relevance": [1.0],
            }
        ).to_csv(news_path, index=False)

        app = create_app(
            BackendSettings(
                paper_state_dir=workspace / "state",
                paper_artifact_root=workspace / "runs",
                paper_job_state_dir=workspace / "paper_jobs",
                backtest_job_state_dir=workspace / "backtest_jobs",
                metadata_db_path=workspace / "metadata.sqlite3",
                default_paper_config=workspace / "missing.json",
                sentiment_cache_dir=workspace / "sentiment_cache",
            )
        )
        client = TestClient(app)

        with (
            patch("pairs_trading.backend.services.BenzingaNewsProvider", FailingBackendHeadlineProvider),
            patch("pairs_trading.backend.services.build_best_available_sentiment_model", return_value=FixedBackendSentimentModel()),
        ):
            response = client.post(
                "/api/sentiment/accumulate",
                json={
                    "symbols": ["GLD"],
                    "start": "2026-04-20",
                    "end": "2026-04-29",
                    "providers": ["local", "benzinga"],
                    "rss_feed_urls": [],
                    "news_files": [str(news_path)],
                    "benzinga_api_key": "bad-key",
                    "output_dir": str(output_dir),
                    "use_finbert": False,
                    "local_finbert_only": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["headline_count"], 1)
        self.assertTrue(any("FailingBackend failed" in warning for warning in payload["warnings"]))


if __name__ == "__main__":
    unittest.main()
