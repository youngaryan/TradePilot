from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

import pandas as pd

from tests.common import fresh_test_dir

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - optional backend dependency
    TestClient = None


@unittest.skipIf(TestClient is None, "FastAPI backend dependencies are not installed.")
class BackendBacktestTests(unittest.TestCase):
    def auth_headers(self, client, *, email: str = "demo@quantops.local", password: str = "quantops-demo") -> dict[str, str]:
        login = client.post("/api/auth/login", json={"email": email, "password": password})
        self.assertEqual(login.status_code, 200)
        return {
            "Authorization": f"Bearer {login.json()['access_token']}",
            "X-Organization-Id": login.json()["active_organization_id"],
        }

    def test_backtest_job_routes_submit_and_complete(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_backtests")
        artifact_dir = workspace / "experiments" / "fake"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        fake_result = SimpleNamespace(
            artifact_dir=artifact_dir,
            fold_metrics=pd.DataFrame({"fold": [1], "sharpe": [1.2]}),
            equity_curve=pd.DataFrame({"net_return": [0.01, 0.02]}, index=pd.date_range("2024-01-01", periods=2)),
        )

        app = create_app(
            BackendSettings(
                paper_state_dir=workspace / "state",
                paper_artifact_root=workspace / "paper_runs",
                backtest_artifact_root=workspace / "backtest_experiments",
                backtest_job_state_dir=workspace / "backtest_jobs",
                metadata_db_path=workspace / "metadata.sqlite3",
                price_cache_dir=workspace / "cache",
                default_paper_config=workspace / "missing.json",
            )
        )
        client = TestClient(app)
        headers = self.auth_headers(client)

        templates = client.get("/api/backtests/templates")
        self.assertEqual(templates.status_code, 200)
        self.assertGreaterEqual(len(templates.json()), 3)

        with patch(
            "pairs_trading.backend.services.run_directional_pipeline",
            return_value={
                "summary": {"annualized_return": 0.12, "sharpe": 1.2, "max_drawdown": -0.05, "avg_turnover": 0.2, "folds": 4},
                "validation": {"dsr": 0.74, "pbo": 0.2},
                "visuals": {"report": str(artifact_dir / "report.html")},
                "result": fake_result,
            },
        ) as run_directional:
            submitted = client.post(
                "/api/backtests/run",
                headers=headers,
                json={
                    "pipeline": "ema_cross",
                    "symbols": ["SPY", "QQQ"],
                    "start": "2021-01-01",
                    "end": "2022-01-01",
                    "experiment_name": "unit_ui_backtest",
                    "parameters": {"ema_fast_window": 10, "ema_slow_window": 35},
                },
            )
            self.assertEqual(submitted.status_code, 202)
            job_id = submitted.json()["id"]
            user_headers = self.auth_headers(client, email="user@quantops.local", password="quantops-user")
            self.assertEqual(TestClient(app).get(f"/api/backtests/jobs/{job_id}").status_code, 401)
            self.assertEqual(client.get(f"/api/backtests/jobs/{job_id}", headers=user_headers).status_code, 404)
            self.assertEqual(client.get("/api/backtests/jobs", headers=user_headers).json(), [])

            job_payload = submitted.json()
            for _ in range(40):
                job = client.get(f"/api/backtests/jobs/{job_id}", headers=headers)
                self.assertEqual(job.status_code, 200)
                job_payload = job.json()
                if job_payload["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.05)

            self.assertEqual(job_payload["status"], "completed")
            self.assertEqual(job_payload["progress"], 1.0)
            self.assertEqual(job_payload["stage"], "completed")
            self.assertEqual(job_payload["result"]["summary"]["sharpe"], 1.2)
            self.assertEqual(job_payload["result"]["validation"]["pbo"], 0.2)
            self.assertEqual(job_payload["result"]["artifact_dir"], str(artifact_dir))
            self.assertEqual(job_payload["result"]["decision"]["verdict"], "paper_candidate")
            self.assertGreater(len(job_payload["result"]["equity_curve_points"]), 0)
            self.assertTrue((workspace / "backtest_jobs" / f"{job_id}.json").exists())
            run_directional.assert_called_once()

        jobs = client.get("/api/backtests/jobs", headers=headers)
        self.assertEqual(jobs.status_code, 200)
        self.assertEqual(jobs.json()[0]["id"], job_id)
        from pairs_trading.platform import SQLiteMetadataStore

        metadata = SQLiteMetadataStore(workspace / "metadata.sqlite3")
        self.assertEqual(metadata.get_job(kind="backtest", job_id=job_id)["status"], "completed")
        self.assertEqual(metadata.list_experiment_runs(kind="backtest")[0]["summary"]["sharpe"], 1.2)

    def test_paper_run_job_routes_submit_and_complete(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_paper_jobs")
        config_path = workspace / "paper_config.json"
        config_path.write_text('{"execution": {}, "strategies": [{"name": "demo", "pipeline": "etf_trend", "symbols": ["SPY"]}]}', encoding="utf-8")

        app = create_app(
            BackendSettings(
                paper_state_dir=workspace / "state",
                paper_artifact_root=workspace / "paper_runs",
                paper_job_state_dir=workspace / "paper_jobs",
                metadata_db_path=workspace / "metadata.sqlite3",
                default_paper_config=config_path,
                price_cache_dir=workspace / "cache",
            )
        )
        client = TestClient(app)
        headers = self.auth_headers(client)

        with patch(
            "pairs_trading.backend.services.run_paper_batch",
            return_value={
                "artifact_dir": str(workspace / "paper_runs" / "fake_batch"),
                "asof_date": "2026-04-24",
                "run_timestamp_utc": "20260424T000000Z",
                "strategies": {},
                "leaderboard": [],
            },
        ) as run_paper:
            submitted = client.post("/api/paper/run-job", headers=headers, json={"asof_date": "2026-04-24"})
            self.assertEqual(submitted.status_code, 202)
            job_id = submitted.json()["id"]
            user_headers = self.auth_headers(client, email="user@quantops.local", password="quantops-user")
            self.assertEqual(TestClient(app).get(f"/api/paper/jobs/{job_id}").status_code, 401)
            self.assertEqual(client.get(f"/api/paper/jobs/{job_id}", headers=user_headers).status_code, 404)
            self.assertEqual(client.get("/api/paper/jobs", headers=user_headers).json(), [])

            job_payload = submitted.json()
            for _ in range(40):
                job = client.get(f"/api/paper/jobs/{job_id}", headers=headers)
                self.assertEqual(job.status_code, 200)
                job_payload = job.json()
                if job_payload["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.05)

            self.assertEqual(job_payload["status"], "completed")
            self.assertEqual(job_payload["progress"], 1.0)
            self.assertEqual(job_payload["stage"], "completed")
            self.assertTrue((workspace / "paper_jobs" / f"{job_id}.json").exists())
            run_paper.assert_called_once()
            from pairs_trading.platform import SQLiteMetadataStore

            metadata = SQLiteMetadataStore(workspace / "metadata.sqlite3")
            self.assertEqual(metadata.get_job(kind="paper", job_id=job_id)["status"], "completed")

        jobs = client.get("/api/paper/jobs", headers=headers)
        self.assertEqual(jobs.status_code, 200)
        self.assertEqual(jobs.json()[0]["id"], job_id)

    def test_paper_run_job_accepts_inline_multi_agent_replay(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_paper_inline_jobs")
        app = create_app(
            BackendSettings(
                paper_state_dir=workspace / "state",
                paper_artifact_root=workspace / "paper_runs",
                paper_job_state_dir=workspace / "paper_jobs",
                metadata_db_path=workspace / "metadata.sqlite3",
                default_paper_config=workspace / "not_used.json",
                price_cache_dir=workspace / "cache",
            )
        )
        client = TestClient(app)
        headers = self.auth_headers(client)

        inline_config = {
            "execution": {
                "initial_cash": 250000,
                "commission_bps": 0.5,
                "slippage_bps": 1.0,
                "min_trade_notional": 100,
                "weight_tolerance": 0.0025,
            },
            "strategies": [
                {
                    "name": "trend_agent",
                    "pipeline": "etf_trend",
                    "symbols": ["SPY", "QQQ", "TLT"],
                    "interval": "1d",
                    "lookback_bars": 360,
                    "params": {"top_n": 2},
                },
                {
                    "name": "stat_arb_agent",
                    "pipeline": "stat_arb",
                    "symbols": [],
                    "sector_map_path": "examples/sector_map.sample.json",
                    "interval": "1d",
                    "lookback_bars": 620,
                    "news_provider_names": ["local"],
                    "use_finbert": True,
                    "params": {"include_residual_book": True, "top_n_pairs": 3},
                },
            ],
        }

        with patch(
            "pairs_trading.backend.services.run_paper_batch",
            return_value={
                "artifact_dir": str(workspace / "paper_runs" / "fake_batch"),
                "asof_date": "2026-04-24",
                "run_timestamp_utc": "20260424T000000Z",
                "strategies": {},
                "leaderboard": [],
            },
        ) as run_paper:
            submitted = client.post(
                "/api/paper/run-job",
                headers=headers,
                json={
                    "deployment_config": inline_config,
                    "asof_start": "2026-04-23",
                    "asof_end": "2026-04-24",
                },
            )
            self.assertEqual(submitted.status_code, 202)
            job_id = submitted.json()["id"]

            job_payload = submitted.json()
            for _ in range(60):
                job = client.get(f"/api/paper/jobs/{job_id}", headers=headers)
                self.assertEqual(job.status_code, 200)
                job_payload = job.json()
                if job_payload["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.05)

            self.assertEqual(job_payload["status"], "completed")
            self.assertEqual(job_payload["result"]["run_sequence"]["count"], 2)
            self.assertEqual([call.kwargs["asof_date"] for call in run_paper.call_args_list], ["2026-04-23", "2026-04-24"])
            deployment_path = Path(job_payload["result"]["run_sequence"]["deployment_config_path"])
            self.assertTrue(deployment_path.exists())
            saved_config = json.loads(deployment_path.read_text(encoding="utf-8"))
            self.assertEqual(len(saved_config["strategies"]), 2)
            self.assertEqual(saved_config["strategies"][1]["news_provider_names"], ["local"])
            self.assertEqual(run_paper.call_count, 2)
            from pairs_trading.platform import SQLiteMetadataStore

            metadata = SQLiteMetadataStore(workspace / "metadata.sqlite3")
            self.assertEqual(metadata.get_deployment_config(config_id=job_id)["config"]["strategies"][1]["name"], "stat_arb_agent")


if __name__ == "__main__":
    unittest.main()
