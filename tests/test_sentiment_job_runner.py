from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.common import fresh_test_dir


class SentimentJobRunnerTests(unittest.TestCase):

    def _make_valid_request(self) -> dict:
        from pairs_trading.backend.schemas import SentimentAccumulationRequest
        return SentimentAccumulationRequest(
            symbols=["AAA"],
            start="2024-01-01",
            end="2024-01-02",
            providers=["local"],
            news_files=["/tmp/test_headlines.csv"],
        )

    def _make_settings(self, workspace: Path) -> "BackendSettings":
        from pairs_trading.backend.config import BackendSettings
        return BackendSettings(
            paper_state_dir=workspace / "state",
            paper_artifact_root=workspace / "runs",
            paper_job_state_dir=workspace / "paper_jobs",
            backtest_job_state_dir=workspace / "backtest_jobs",
            sentiment_job_state_dir=workspace / "sentiment_jobs",
            metadata_db_path=workspace / "metadata.sqlite3",
            default_paper_config=workspace / "missing.json",
            sentiment_cache_dir=workspace / "sentiment_cache",
            enable_in_process_jobs=False,
        )

    # ---------- submit ----------

    def test_submit_creates_job_in_memory_and_persistence(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_submit")
        settings = self._make_settings(workspace)

        with (
            patch("pairs_trading.backend.services.SentimentService.validate_request"),
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
        ):
            runner = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()
            job = runner.submit(request, organization_id=demo["organization_id"], user_id=demo["user_id"])

        self.assertIn("id", job)
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["organization_id"], demo["organization_id"])
        self.assertEqual(job["user_id"], demo["user_id"])

        self.assertIn(job["id"], runner.jobs)
        sqlite = runner.metadata_store.get_job(
            kind="sentiment", job_id=job["id"], organization_id=demo["organization_id"]
        )
        self.assertIsNotNone(sqlite)
        self.assertEqual(sqlite["id"], job["id"])

        json_path = runner.jobs_dir / f"{job['id']}.json"
        self.assertTrue(json_path.exists())
        on_disk = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["id"], job["id"])

    def test_submit_rejects_empty_symbols(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.schemas import SentimentAccumulationRequest
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_submit_empty")
        settings = self._make_settings(workspace)
        runner = SentimentJobRunner(settings, mark_interrupted_on_load=False)
        demo = runner.metadata_store.ensure_demo_workspace()

        request = SentimentAccumulationRequest(
            symbols=[],
            start="2024-01-01",
            end="2024-01-02",
        )
        with self.assertRaises(ValueError):
            runner.submit(request, organization_id=demo["organization_id"])

    def test_submit_rejects_empty_providers(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.schemas import SentimentAccumulationRequest
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_submit_no_providers")
        settings = self._make_settings(workspace)
        runner = SentimentJobRunner(settings, mark_interrupted_on_load=False)
        demo = runner.metadata_store.ensure_demo_workspace()

        request = SentimentAccumulationRequest(
            symbols=["AAA"],
            start="2024-01-01",
            end="2024-01-02",
            providers=[],
        )
        with self.assertRaises(ValueError):
            runner.submit(request, organization_id=demo["organization_id"])

    # ---------- get_job (in-memory fast path) ----------

    def test_get_job_returns_in_memory_job(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_get_memory")
        settings = self._make_settings(workspace)

        with (
            patch("pairs_trading.backend.services.SentimentService.validate_request"),
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
        ):
            runner = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()
            job = runner.submit(request, organization_id=demo["organization_id"])

            result = runner.get_job(job["id"], organization_id=demo["organization_id"])

        self.assertIsNotNone(result)
        self.assertEqual(result["id"], job["id"])
        self.assertEqual(result["status"], "queued")

    def test_get_job_returns_none_for_wrong_org(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_wrong_org")
        settings = self._make_settings(workspace)

        with (
            patch("pairs_trading.backend.services.SentimentService.validate_request"),
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
        ):
            runner = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()
            job = runner.submit(request, organization_id=demo["organization_id"])

            result = runner.get_job(job["id"], organization_id="other-org-id")

        self.assertIsNone(result)

    def test_get_job_returns_none_for_nonexistent_id(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_nonexistent")
        settings = self._make_settings(workspace)
        runner = SentimentJobRunner(settings, mark_interrupted_on_load=False)
        demo = runner.metadata_store.ensure_demo_workspace()

        result = runner.get_job("nonexistent-job-id", organization_id=demo["organization_id"])

        self.assertIsNone(result)

    # ---------- get_job persistence fallback (simulating multi-worker) ----------

    def test_get_job_falls_back_to_sqlite_when_not_in_memory(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_sqlite_fallback")
        settings = self._make_settings(workspace)

        with (
            patch("pairs_trading.backend.services.SentimentService.validate_request"),
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
        ):
            runner_a = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner_a.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()
            job = runner_a.submit(request, organization_id=demo["organization_id"])

            runner_b = SentimentJobRunner(settings, mark_interrupted_on_load=False)

            result = runner_b.get_job(job["id"], organization_id=demo["organization_id"])

        self.assertIsNotNone(result)
        self.assertEqual(result["id"], job["id"])
        self.assertEqual(result["organization_id"], demo["organization_id"])

    def test_get_job_falls_back_to_filesystem_when_sqlite_missing(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_fs_fallback")
        settings = self._make_settings(workspace)

        with (
            patch("pairs_trading.backend.services.SentimentService.validate_request"),
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
        ):
            runner_a = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner_a.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()
            job = runner_a.submit(request, organization_id=demo["organization_id"])

            runner_a.metadata_store.delete_job(kind="sentiment", job_id=job["id"])

            runner_b = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            result = runner_b.get_job(job["id"], organization_id=demo["organization_id"])

        self.assertIsNotNone(result)
        self.assertEqual(result["id"], job["id"])

    def test_get_job_returns_none_when_all_sources_empty(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_all_empty")
        settings = self._make_settings(workspace)

        with (
            patch("pairs_trading.backend.services.SentimentService.validate_request"),
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
        ):
            runner_a = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner_a.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()
            job = runner_a.submit(request, organization_id=demo["organization_id"])

            runner_a.metadata_store.delete_job(kind="sentiment", job_id=job["id"])
            json_path = runner_a.jobs_dir / f"{job['id']}.json"
            if json_path.exists():
                json_path.unlink()

            runner_b = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            result = runner_b.get_job(job["id"], organization_id=demo["organization_id"])

        self.assertIsNone(result)

    def test_get_job_wrong_org_fallback_from_second_runner(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_wrong_org_fallback")
        settings = self._make_settings(workspace)

        with (
            patch("pairs_trading.backend.services.SentimentService.validate_request"),
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
        ):
            runner_a = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner_a.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()
            job = runner_a.submit(request, organization_id=demo["organization_id"])

            runner_b = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            result = runner_b.get_job(job["id"], organization_id="some-other-org")

        self.assertIsNone(result)

    def test_get_job_with_corrupted_filesystem_falls_back_gracefully(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_corrupted_fs")
        settings = self._make_settings(workspace)

        with (
            patch("pairs_trading.backend.services.SentimentService.validate_request"),
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
        ):
            runner_a = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner_a.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()
            job = runner_a.submit(request, organization_id=demo["organization_id"])

            runner_a.metadata_store.delete_job(kind="sentiment", job_id=job["id"])
            json_path = runner_a.jobs_dir / f"{job['id']}.json"
            json_path.write_text("this is not valid json", encoding="utf-8")

            runner_b = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            result = runner_b.get_job(job["id"], organization_id=demo["organization_id"])

        self.assertIsNone(result)

    # ---------- get_job with status updates ----------

    def test_get_job_returns_updated_status_from_second_runner(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_updated_status")
        settings = self._make_settings(workspace)

        with (
            patch("pairs_trading.backend.services.SentimentService.validate_request"),
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
        ):
            runner_a = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner_a.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()
            job = runner_a.submit(request, organization_id=demo["organization_id"])

            runner_a._set_status(job["id"], "completed", progress=1.0, stage="completed")

            runner_b = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            result = runner_b.get_job(job["id"], organization_id=demo["organization_id"])

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["progress"], 1.0)

    # ---------- list_jobs ----------

    def test_list_jobs_returns_only_own_org_jobs(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_list_orgs")
        settings = self._make_settings(workspace)

        with (
            patch("pairs_trading.backend.services.SentimentService.validate_request"),
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
        ):
            runner = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()

            job_a = runner.submit(request, organization_id=demo["organization_id"])
            runner.submit(request, organization_id="org-b")

            jobs_for_demo = runner.list_jobs(organization_id=demo["organization_id"])
            jobs_for_b = runner.list_jobs(organization_id="org-b")

        self.assertEqual(len(jobs_for_demo), 1)
        self.assertEqual(jobs_for_demo[0]["id"], job_a["id"])

        self.assertEqual(len(jobs_for_b), 1)

    def test_list_jobs_returns_empty_when_no_jobs(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_list_empty")
        settings = self._make_settings(workspace)
        runner = SentimentJobRunner(settings, mark_interrupted_on_load=False)
        demo = runner.metadata_store.ensure_demo_workspace()

        jobs = runner.list_jobs(organization_id=demo["organization_id"])

        self.assertEqual(jobs, [])

    def test_list_jobs_orders_by_created_at_desc(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_list_order")
        settings = self._make_settings(workspace)

        with (
            patch("pairs_trading.backend.services.SentimentService.validate_request"),
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
        ):
            runner = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()

            job1 = runner.submit(request, organization_id=demo["organization_id"])
            job2 = runner.submit(request, organization_id=demo["organization_id"])
            job3 = runner.submit(request, organization_id=demo["organization_id"])

            jobs = runner.list_jobs(organization_id=demo["organization_id"])

        self.assertEqual(len(jobs), 3)
        self.assertEqual(jobs[0]["id"], job3["id"])
        self.assertEqual(jobs[1]["id"], job2["id"])
        self.assertEqual(jobs[2]["id"], job1["id"])

    # ---------- trim (max_history) ----------

    def test_trim_removes_oldest_jobs_beyond_max_history(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_trim")
        settings = self._make_settings(workspace)

        with (
            patch("pairs_trading.backend.services.SentimentService.validate_request"),
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
        ):
            runner = SentimentJobRunner(settings, max_history=3, mark_interrupted_on_load=False)
            demo = runner.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()

            runner.submit(request, organization_id=demo["organization_id"])
            runner.submit(request, organization_id=demo["organization_id"])
            runner.submit(request, organization_id=demo["organization_id"])
            job4 = runner.submit(request, organization_id=demo["organization_id"])

            self.assertEqual(len(runner.jobs), 3)
            jobs = runner.list_jobs(organization_id=demo["organization_id"])
            self.assertEqual(len(jobs), 3)
            self.assertEqual(jobs[0]["id"], job4["id"])

    # ---------- load from persistence on init ----------

    def test_runner_loads_previous_jobs_on_initialization(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_load_init")
        settings = self._make_settings(workspace)

        with (
            patch("pairs_trading.backend.services.SentimentService.validate_request"),
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
        ):
            runner_a = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner_a.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()
            job = runner_a.submit(request, organization_id=demo["organization_id"])

            runner_b = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            loaded = runner_b.get_job(job["id"], organization_id=demo["organization_id"])

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["id"], job["id"])

    def test_runner_marks_queued_jobs_as_interrupted_on_load(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_interrupted")
        settings = self._make_settings(workspace)

        with (
            patch("pairs_trading.backend.services.SentimentService.validate_request"),
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
        ):
            runner_a = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner_a.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()
            job = runner_a.submit(request, organization_id=demo["organization_id"])

            runner_b = SentimentJobRunner(settings, mark_interrupted_on_load=True)
            loaded = runner_b.get_job(job["id"], organization_id=demo["organization_id"])

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["status"], "interrupted")
        self.assertEqual(loaded["progress"], 1.0)
        self.assertIn("backend restarted", loaded["message"])

    # ---------- end-to-end with in-process execution ----------

    def test_in_process_job_completes_successfully(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_e2e")
        settings = BackendSettings(
            paper_state_dir=workspace / "state",
            paper_artifact_root=workspace / "runs",
            paper_job_state_dir=workspace / "paper_jobs",
            backtest_job_state_dir=workspace / "backtest_jobs",
            sentiment_job_state_dir=workspace / "sentiment_jobs",
            metadata_db_path=workspace / "metadata.sqlite3",
            default_paper_config=workspace / "missing.json",
            sentiment_cache_dir=workspace / "sentiment_cache",
            enable_in_process_jobs=True,
        )

        fake_result = {
            "summary": {"headline_count": 2, "daily_rows": 1},
            "daily_points": [{"ticker": "AAA", "date": "2024-01-01", "score": 0.7}],
            "metadata": {"fetched_headlines": 2},
            "warnings": [],
        }

        with patch("pairs_trading.backend.services.SentimentService.accumulate", return_value=fake_result):
            runner = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()
            job = runner.submit(request, organization_id=demo["organization_id"])

            completed = None
            for _ in range(80):
                current = runner.get_job(job["id"], organization_id=demo["organization_id"])
                if current and current["status"] not in {"queued", "running"}:
                    completed = current
                    break
                time.sleep(0.05)

        self.assertIsNotNone(completed)
        self.assertEqual(completed["status"], "completed", completed.get("error"))
        self.assertEqual(completed["progress"], 1.0)
        self.assertEqual(completed["result"]["summary"]["headline_count"], 2)

    def test_in_process_job_persistence_cross_runner(self) -> None:
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SentimentJobRunner

        workspace = fresh_test_dir("artifacts/test_sjr_e2e_cross")
        settings = BackendSettings(
            paper_state_dir=workspace / "state",
            paper_artifact_root=workspace / "runs",
            paper_job_state_dir=workspace / "paper_jobs",
            backtest_job_state_dir=workspace / "backtest_jobs",
            sentiment_job_state_dir=workspace / "sentiment_jobs",
            metadata_db_path=workspace / "metadata.sqlite3",
            default_paper_config=workspace / "missing.json",
            sentiment_cache_dir=workspace / "sentiment_cache",
            enable_in_process_jobs=True,
        )

        fake_result = {
            "summary": {"headline_count": 1, "daily_rows": 1},
            "daily_points": [{"ticker": "AAA", "date": "2024-01-01", "score": 0.7}],
            "metadata": {"fetched_headlines": 1},
            "warnings": [],
        }

        job_id: str | None = None
        org_id: str | None = None

        with patch("pairs_trading.backend.services.SentimentService.accumulate", return_value=fake_result):
            runner_a = SentimentJobRunner(settings, mark_interrupted_on_load=False)
            demo = runner_a.metadata_store.ensure_demo_workspace()
            request = self._make_valid_request()
            job = runner_a.submit(request, organization_id=demo["organization_id"])
            job_id = job["id"]
            org_id = demo["organization_id"]

            completed = None
            for _ in range(80):
                current = runner_a.get_job(job_id, organization_id=org_id)
                if current and current["status"] not in {"queued", "running"}:
                    completed = current
                    break
                time.sleep(0.05)

        self.assertIsNotNone(completed)
        self.assertEqual(completed["status"], "completed")

        runner_b = SentimentJobRunner(settings, mark_interrupted_on_load=False)
        reloaded = runner_b.get_job(job_id, organization_id=org_id)

        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["id"], job_id)
        self.assertEqual(reloaded["status"], "completed")


if __name__ == "__main__":
    unittest.main()
