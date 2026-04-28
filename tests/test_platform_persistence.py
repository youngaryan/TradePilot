from __future__ import annotations

import unittest

from pairs_trading.platform import SQLiteMetadataStore
from tests.common import fresh_test_dir


class PlatformPersistenceTests(unittest.TestCase):
    def test_sqlite_metadata_store_persists_jobs_deployments_and_experiments(self) -> None:
        workspace = fresh_test_dir("artifacts/test_platform_persistence")
        store = SQLiteMetadataStore(workspace / "metadata.sqlite3")

        job_payload = {
            "id": "job-1",
            "status": "queued",
            "stage": "queued",
            "progress": 0.1,
            "request": {"pipeline": "etf_trend"},
            "created_at_utc": "2026-04-28T00:00:00Z",
            "updated_at_utc": "2026-04-28T00:00:00Z",
            "message": "Queued",
            "result": None,
            "error": None,
        }
        store.upsert_job(kind="backtest", payload=job_payload)
        store.save_deployment_config(
            config_id="deployment-1",
            source="paper_inline",
            path=workspace / "deployment.json",
            config={"strategies": [{"name": "demo", "pipeline": "etf_trend"}]},
            created_at_utc="2026-04-28T00:00:00Z",
        )
        store.save_experiment_run(
            experiment_id="experiment-1",
            kind="backtest",
            artifact_dir=workspace / "experiment",
            summary={"sharpe": 1.2},
            created_at_utc="2026-04-28T00:01:00Z",
        )

        reopened = SQLiteMetadataStore(workspace / "metadata.sqlite3")
        self.assertEqual(reopened.get_job(kind="backtest", job_id="job-1")["request"]["pipeline"], "etf_trend")
        self.assertEqual(reopened.list_jobs(kind="backtest")[0]["id"], "job-1")
        self.assertEqual(reopened.get_deployment_config(config_id="deployment-1")["config"]["strategies"][0]["name"], "demo")
        self.assertEqual(reopened.list_experiment_runs(kind="backtest")[0]["summary"]["sharpe"], 1.2)
        self.assertEqual(reopened.counts().jobs, 1)


if __name__ == "__main__":
    unittest.main()
