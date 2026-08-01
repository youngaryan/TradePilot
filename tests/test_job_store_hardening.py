from __future__ import annotations

import unittest

from pairs_trading.platform import SQLiteMetadataStore
from tests.common import fresh_test_dir


def _job(
    job_id: str,
    *,
    organization_id: str,
    created_at_utc: str,
) -> dict[str, object]:
    return {
        "id": job_id,
        "organization_id": organization_id,
        "status": "queued",
        "stage": "queued",
        "progress": 0.0,
        "request": {},
        "created_at_utc": created_at_utc,
        "updated_at_utc": created_at_utc,
        "result": None,
        "error": None,
    }


class JobStoreHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        workspace = fresh_test_dir("artifacts/test_job_store_hardening")
        self.database_path = workspace / "metadata.sqlite3"
        self.store = SQLiteMetadataStore(self.database_path, enable_demo_accounts=False)

    def test_list_jobs_applies_explicit_limit_and_reverse_created_order(self) -> None:
        for index in range(55):
            self.store.upsert_job(
                kind="backtest",
                payload=_job(
                    f"job-{index:02d}",
                    organization_id="org-a",
                    created_at_utc=f"2026-01-01T00:{index:02d}:00Z",
                ),
            )

        jobs = self.store.list_jobs(kind="backtest", organization_id="org-a", limit=50)

        self.assertEqual(len(jobs), 50)
        self.assertEqual(jobs[0]["id"], "job-54")
        self.assertEqual(jobs[-1]["id"], "job-05")

    def test_list_jobs_is_unbounded_by_default(self) -> None:
        for index in range(55):
            self.store.upsert_job(
                kind="backtest",
                payload=_job(
                    f"job-{index:02d}",
                    organization_id="org-a",
                    created_at_utc=f"2026-01-01T00:{index:02d}:00Z",
                ),
            )

        jobs = self.store.list_jobs(kind="backtest", organization_id="org-a")

        self.assertEqual(len(jobs), 55)
        self.assertEqual(jobs[0]["id"], "job-54")
        self.assertEqual(jobs[-1]["id"], "job-00")

    def test_list_jobs_supports_limit_and_offset(self) -> None:
        for index in range(5):
            self.store.upsert_job(
                kind="paper",
                payload=_job(
                    f"job-{index}",
                    organization_id="org-a",
                    created_at_utc=f"2026-01-01T00:0{index}:00Z",
                ),
            )

        page = self.store.list_jobs(kind="paper", organization_id="org-a", limit=2, offset=2)

        self.assertEqual([job["id"] for job in page], ["job-2", "job-1"])

    def test_list_jobs_rejects_invalid_pagination(self) -> None:
        invalid_arguments = (
            {"limit": 0},
            {"limit": 201},
            {"limit": True},
            {"limit": "10"},
            {"offset": -1},
            {"offset": False},
            {"offset": "0"},
            {"offset": 1},
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    self.store.list_jobs(kind="backtest", **arguments)  # type: ignore[arg-type]

    def test_list_jobs_is_scoped_by_tenant_and_kind(self) -> None:
        self.store.upsert_job(
            kind="backtest",
            payload=_job("org-a-backtest", organization_id="org-a", created_at_utc="2026-01-01T00:00:00Z"),
        )
        self.store.upsert_job(
            kind="paper",
            payload=_job("org-a-paper", organization_id="org-a", created_at_utc="2026-01-01T00:01:00Z"),
        )
        self.store.upsert_job(
            kind="backtest",
            payload=_job("org-b-backtest", organization_id="org-b", created_at_utc="2026-01-01T00:02:00Z"),
        )

        jobs = self.store.list_jobs(kind="backtest", organization_id="org-a")

        self.assertEqual([job["id"] for job in jobs], ["org-a-backtest"])

    def test_job_written_by_one_store_is_visible_to_another(self) -> None:
        reader = SQLiteMetadataStore(self.database_path, enable_demo_accounts=False)
        self.store.upsert_job(
            kind="sentiment",
            payload=_job("durable-job", organization_id="org-a", created_at_utc="2026-01-01T00:00:00Z"),
        )

        listed = reader.list_jobs(kind="sentiment", organization_id="org-a")
        loaded = reader.get_job(kind="sentiment", job_id="durable-job", organization_id="org-a")

        self.assertEqual([job["id"] for job in listed], ["durable-job"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["id"], "durable-job")


if __name__ == "__main__":
    unittest.main()
