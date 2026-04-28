from __future__ import annotations

import json
import unittest

from tests.common import fresh_test_dir

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - optional backend dependency
    TestClient = None


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


if __name__ == "__main__":
    unittest.main()
