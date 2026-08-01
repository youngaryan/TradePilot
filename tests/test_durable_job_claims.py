from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import threading
import unittest

from pairs_trading.platform import SQLiteMetadataStore
from tests.common import fresh_test_dir


def _queued_job(
    job_id: str,
    *,
    max_attempts: int = 3,
    rq_job_id: str | None = None,
) -> dict[str, object]:
    return {
        "id": job_id,
        "organization_id": "org-a",
        "status": "queued",
        "stage": "queued",
        "progress": 0.0,
        "request": {"pipeline": "buy_and_hold"},
        "created_at_utc": "2026-01-01T00:00:00Z",
        "updated_at_utc": "2026-01-01T00:00:00Z",
        "result": None,
        "error": None,
        "max_attempts": max_attempts,
        "rq_job_id": rq_job_id,
    }


class DurableJobClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        workspace = fresh_test_dir("artifacts/test_durable_job_claims")
        self.database_path = workspace / "metadata.sqlite3"
        self.first = SQLiteMetadataStore(self.database_path, enable_demo_accounts=False)
        self.second = SQLiteMetadataStore(self.database_path, enable_demo_accounts=False)

    def _insert(self, job_id: str = "job-1", **overrides: object) -> None:
        payload = _queued_job(job_id)
        payload.update(overrides)
        self.first.upsert_job(kind="backtest", payload=payload)

    def _raw_job(self, job_id: str = "job-1") -> tuple[sqlite3.Row, dict[str, object]]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        finally:
            connection.close()
        assert row is not None
        return row, json.loads(row["payload_json"])

    def assert_claim_columns_match_payload(self, job_id: str = "job-1") -> None:
        row, payload = self._raw_job(job_id)
        for field in (
            "status",
            "version",
            "attempt",
            "max_attempts",
            "worker_id",
            "heartbeat_at_utc",
            "lease_expires_at_utc",
            "rq_job_id",
        ):
            self.assertEqual(row[field], payload[field], field)

    def test_exactly_one_concurrent_claim_wins(self) -> None:
        self._insert()
        barrier = threading.Barrier(2)

        def claim(store: SQLiteMetadataStore, worker_id: str) -> dict[str, object] | None:
            barrier.wait(timeout=5)
            return store.claim_job(
                kind="backtest",
                job_id="job-1",
                worker_id=worker_id,
                lease_expires_at_utc="2099-01-01T00:00:00Z",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda args: claim(*args), ((self.first, "worker-a"), (self.second, "worker-b"))))

        winners = [result for result in results if result is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0]["status"], "running")
        self.assertEqual(winners[0]["attempt"], 1)
        self.assertEqual(winners[0]["version"], 1)
        self.assert_claim_columns_match_payload()

    def test_wrong_owner_cannot_heartbeat_release_or_update(self) -> None:
        self._insert()
        claimed = self.first.claim_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            lease_expires_at_utc="2099-01-01T00:00:00Z",
        )
        self.assertIsNotNone(claimed)

        self.assertIsNone(
            self.second.heartbeat_job(
                kind="backtest",
                job_id="job-1",
                worker_id="worker-b",
                heartbeat_at_utc="2026-01-01T00:01:00Z",
                lease_expires_at_utc="2099-01-01T00:01:00Z",
            )
        )
        self.assertIsNone(
            self.second.update_claimed_job(
                kind="backtest",
                job_id="job-1",
                worker_id="worker-b",
                updates={"message": "stolen"},
            )
        )
        self.assertIsNone(
            self.second.release_job_claim(
                kind="backtest",
                job_id="job-1",
                worker_id="worker-b",
                status="completed",
            )
        )
        heartbeat = self.first.heartbeat_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            heartbeat_at_utc="2026-01-01T00:01:00Z",
            lease_expires_at_utc="2099-01-01T00:02:00Z",
        )
        self.assertIsNotNone(heartbeat)
        self.assertEqual(heartbeat["heartbeat_at_utc"], "2026-01-01T00:01:00Z")
        self.assertEqual(heartbeat["lease_expires_at_utc"], "2099-01-01T00:02:00Z")
        self.assertEqual(heartbeat["version"], 2)
        persisted = self.first.get_job(kind="backtest", job_id="job-1")
        self.assertEqual(persisted["worker_id"], "worker-a")
        self.assertEqual(persisted["status"], "running")

    def test_progress_cannot_regress_and_immutable_fields_are_rejected(self) -> None:
        self._insert()
        self.first.claim_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            lease_expires_at_utc="2099-01-01T00:00:00Z",
        )
        advanced = self.first.update_claimed_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            updates={"progress": 0.75, "stage": "calculating"},
        )
        regressed = self.first.update_claimed_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            updates={"progress": 0.25},
        )

        self.assertEqual(advanced["progress"], 0.75)
        self.assertEqual(regressed["progress"], 0.75)
        with self.assertRaisesRegex(ValueError, "status"):
            self.first.update_claimed_job(
                kind="backtest",
                job_id="job-1",
                worker_id="worker-a",
                updates={"status": "completed"},
            )
        self.assert_claim_columns_match_payload()

    def test_same_owner_heartbeat_and_progress_retry_benign_version_conflict(self) -> None:
        self._insert()
        self.first.claim_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            lease_expires_at_utc="2099-01-01T00:00:00Z",
        )
        barrier = threading.Barrier(2)

        def synchronize_first_read(store: SQLiteMetadataStore) -> None:
            original = store._job_claim_row
            first_read = True

            def synchronized_read(**kwargs: object) -> object:
                nonlocal first_read
                row = original(**kwargs)  # type: ignore[arg-type]
                if first_read:
                    first_read = False
                    barrier.wait(timeout=5)
                return row

            store._job_claim_row = synchronized_read  # type: ignore[method-assign]

        synchronize_first_read(self.first)
        synchronize_first_read(self.second)
        with ThreadPoolExecutor(max_workers=2) as executor:
            heartbeat_future = executor.submit(
                self.first.heartbeat_job,
                kind="backtest",
                job_id="job-1",
                worker_id="worker-a",
                heartbeat_at_utc="2026-01-01T00:01:00Z",
                lease_expires_at_utc="2099-01-01T00:01:00Z",
            )
            progress_future = executor.submit(
                self.second.update_claimed_job,
                kind="backtest",
                job_id="job-1",
                worker_id="worker-a",
                updates={"progress": 0.5, "stage": "working"},
            )
            heartbeat = heartbeat_future.result(timeout=5)
            progress = progress_future.result(timeout=5)

        self.assertIsNotNone(heartbeat)
        self.assertIsNotNone(progress)
        durable = SQLiteMetadataStore(self.database_path, enable_demo_accounts=False).get_job(kind="backtest", job_id="job-1")
        self.assertEqual(durable["worker_id"], "worker-a")
        self.assertEqual(durable["progress"], 0.5)
        self.assertEqual(durable["version"], 3)

    def test_same_owner_heartbeat_cannot_make_terminal_release_fail_on_version_conflict(self) -> None:
        self._insert()
        self.first.claim_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            lease_expires_at_utc="2099-01-01T00:00:00Z",
        )
        barrier = threading.Barrier(2)

        def synchronize_first_read(store: SQLiteMetadataStore) -> None:
            original = store._job_claim_row
            first_read = True

            def synchronized_read(**kwargs: object) -> object:
                nonlocal first_read
                row = original(**kwargs)  # type: ignore[arg-type]
                if first_read:
                    first_read = False
                    barrier.wait(timeout=5)
                return row

            store._job_claim_row = synchronized_read  # type: ignore[method-assign]

        synchronize_first_read(self.first)
        synchronize_first_read(self.second)
        with ThreadPoolExecutor(max_workers=2) as executor:
            heartbeat_future = executor.submit(
                self.first.heartbeat_job,
                kind="backtest",
                job_id="job-1",
                worker_id="worker-a",
                lease_expires_at_utc="2099-01-01T00:01:00Z",
            )
            release_future = executor.submit(
                self.second.release_job_claim,
                kind="backtest",
                job_id="job-1",
                worker_id="worker-a",
                status="completed",
                updates={"progress": 1.0, "stage": "completed", "result": {"ok": True}},
            )
            heartbeat_future.result(timeout=5)
            released = release_future.result(timeout=5)

        self.assertIsNotNone(released)
        durable = SQLiteMetadataStore(self.database_path, enable_demo_accounts=False).get_job(kind="backtest", job_id="job-1")
        self.assertEqual(durable["status"], "completed")
        self.assertEqual(durable["result"], {"ok": True})

    def test_expired_owner_cannot_heartbeat_update_or_finalize_before_recovery(self) -> None:
        self._insert()
        self.first.claim_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            lease_expires_at_utc="2020-01-01T00:00:00Z",
        )

        self.assertIsNone(
            self.first.heartbeat_job(
                kind="backtest",
                job_id="job-1",
                worker_id="worker-a",
                lease_expires_at_utc="2099-01-01T00:00:00Z",
            )
        )
        self.assertIsNone(
            self.first.update_claimed_job(
                kind="backtest",
                job_id="job-1",
                worker_id="worker-a",
                updates={"progress": 0.9},
            )
        )
        self.assertIsNone(
            self.first.release_job_claim(
                kind="backtest",
                job_id="job-1",
                worker_id="worker-a",
                status="completed",
            )
        )
        durable = self.first.get_job(kind="backtest", job_id="job-1")
        self.assertEqual(durable["status"], "running")
        self.assertEqual(durable["progress"], 0.0)

    def test_release_rejects_immutable_payload_injection(self) -> None:
        self._insert()
        self.first.claim_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            lease_expires_at_utc="2099-01-01T00:00:00Z",
        )
        for field, value in (
            ("id", "other-job"),
            ("kind", "paper"),
            ("organization_id", "org-b"),
            ("request", {"poisoned": True}),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, field):
                    self.first.release_job_claim(
                        kind="backtest",
                        job_id="job-1",
                        worker_id="worker-a",
                        status="completed",
                        updates={field: value},
                    )
        durable = self.first.get_job(kind="backtest", job_id="job-1", organization_id="org-a")
        self.assertEqual(durable["id"], "job-1")
        self.assertEqual(durable["organization_id"], "org-a")
        self.assertEqual(durable["request"], {"pipeline": "buy_and_hold"})

    def test_exhausted_claim_cannot_be_released_back_to_an_unclaimable_queue(self) -> None:
        self._insert(max_attempts=1)
        self.first.claim_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            lease_expires_at_utc="2099-01-01T00:00:00Z",
        )

        released = self.first.release_job_claim(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            status="queued",
        )

        self.assertEqual(released["status"], "failed")
        self.assertEqual(released["progress"], 1.0)
        self.assertEqual(released["error"], "Maximum job attempts exhausted.")
        self.assertEqual(self.first.list_jobs(kind="backtest", status="queued"), [])
        self.assertEqual([job["id"] for job in self.first.list_jobs(kind="backtest", status="failed")], ["job-1"])
        with self.assertRaisesRegex(ValueError, "recognized job status"):
            self.first.list_jobs(kind="backtest", status="not-a-status")

    def test_recovery_retries_then_interrupts_at_max_attempts(self) -> None:
        self._insert(max_attempts=2)
        first_claim = self.first.claim_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            lease_expires_at_utc="2026-01-01T00:01:00Z",
        )
        first_recovery = self.second.recover_expired_jobs(now_utc="2026-01-01T00:02:00Z")

        self.assertEqual(first_claim["attempt"], 1)
        self.assertEqual(first_recovery[0]["status"], "queued")
        self.assertEqual(first_recovery[0]["kind"], "backtest")
        self.assertIsNone(first_recovery[0]["worker_id"])
        self.assertIsNone(first_recovery[0]["lease_expires_at_utc"])
        self.assertEqual(self.first.get_job(kind="backtest", job_id="job-1")["kind"], "backtest")

        second_claim = self.second.claim_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-b",
            lease_expires_at_utc="2026-01-01T00:03:00Z",
        )
        second_recovery = self.first.recover_expired_jobs(now_utc="2026-01-01T00:04:00Z")

        self.assertEqual(second_claim["attempt"], 2)
        self.assertEqual(second_recovery[0]["status"], "interrupted")
        self.assertEqual(second_recovery[0]["progress"], 1.0)
        self.assertIsNone(
            self.first.claim_job(
                kind="backtest",
                job_id="job-1",
                worker_id="worker-c",
                lease_expires_at_utc="2099-01-01T00:00:00Z",
            )
        )
        self.assertEqual(self.first.recover_expired_jobs(now_utc="2100-01-01T00:00:00Z"), [])
        self.assert_claim_columns_match_payload()

    def test_former_owner_cannot_update_after_recovery_and_reclaim(self) -> None:
        self._insert()
        self.first.claim_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            lease_expires_at_utc="2026-01-01T00:01:00Z",
        )
        self.second.recover_expired_jobs(now_utc="2026-01-01T00:02:00Z")
        self.second.claim_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-b",
            lease_expires_at_utc="2099-01-01T00:00:00Z",
        )

        self.assertIsNone(
            self.first.update_claimed_job(
                kind="backtest",
                job_id="job-1",
                worker_id="worker-a",
                updates={"progress": 0.9},
            )
        )
        current = self.second.get_job(kind="backtest", job_id="job-1")
        self.assertEqual(current["worker_id"], "worker-b")
        self.assertEqual(current["attempt"], 2)

    def test_terminal_job_cannot_be_claimed_recovered_or_updated(self) -> None:
        self._insert()
        self.first.claim_job(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            lease_expires_at_utc="2099-01-01T00:00:00Z",
        )
        completed = self.first.release_job_claim(
            kind="backtest",
            job_id="job-1",
            worker_id="worker-a",
            status="completed",
            updates={"progress": 1.0, "stage": "completed", "result": {"ok": True}},
        )

        self.assertEqual(completed["status"], "completed")
        self.assertIsNone(
            self.second.claim_job(
                kind="backtest",
                job_id="job-1",
                worker_id="worker-b",
                lease_expires_at_utc="2100-01-01T00:00:00Z",
            )
        )
        self.assertIsNone(
            self.second.update_claimed_job(
                kind="backtest",
                job_id="job-1",
                worker_id="worker-a",
                updates={"message": "late mutation"},
            )
        )
        self.assertEqual(self.second.recover_expired_jobs(now_utc="2200-01-01T00:00:00Z"), [])
        self.assert_claim_columns_match_payload()

    def test_partial_unique_rq_job_id_index_allows_null_but_rejects_duplicates(self) -> None:
        self.first.upsert_job(kind="backtest", payload=_queued_job("job-a"))
        self.first.upsert_job(kind="backtest", payload=_queued_job("job-b"))
        self.first.upsert_job(kind="backtest", payload=_queued_job("job-c", rq_job_id="rq-1"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.first.upsert_job(kind="backtest", payload=_queued_job("job-d", rq_job_id="rq-1"))

    def test_existing_sqlite_jobs_table_is_extended_with_claim_columns_and_indexes(self) -> None:
        workspace = fresh_test_dir("artifacts/test_durable_job_claims_legacy")
        path = workspace / "legacy.sqlite3"
        connection = sqlite3.connect(path)
        connection.execute(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, stage TEXT,
                progress REAL NOT NULL DEFAULT 0, request_json TEXT NOT NULL,
                payload_json TEXT NOT NULL, error TEXT, created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL, started_at_utc TEXT, finished_at_utc TEXT
            )
            """
        )
        connection.commit()
        connection.close()

        SQLiteMetadataStore(path, enable_demo_accounts=False)
        connection = sqlite3.connect(path)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            indexes = {row[1] for row in connection.execute("PRAGMA index_list(jobs)")}
        finally:
            connection.close()

        self.assertTrue(
            {"version", "attempt", "max_attempts", "worker_id", "heartbeat_at_utc", "lease_expires_at_utc", "rq_job_id"}.issubset(columns)
        )
        self.assertIn("idx_jobs_status_lease", indexes)
        self.assertIn("idx_jobs_rq_job_id_unique", indexes)


if __name__ == "__main__":
    unittest.main()
