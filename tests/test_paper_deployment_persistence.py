from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ast
import json
from pathlib import Path
import sqlite3
import threading
import unittest

from pairs_trading.platform.persistence import IdempotencyConflictError, SQLiteMetadataStore
from tests.common import fresh_test_dir


class PaperDeploymentPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        workspace = fresh_test_dir("artifacts/test_paper_deployment_persistence")
        self.database_path = workspace / "metadata.sqlite3"
        self.store = SQLiteMetadataStore(self.database_path, enable_demo_accounts=False)
        self.org_a = self.store.ensure_demo_workspace(
            email="paper-a@example.test",
            organization_name="Paper A",
            organization_slug="paper-a",
        )
        self.org_b = self.store.ensure_demo_workspace(
            email="paper-b@example.test",
            organization_name="Paper B",
            organization_slug="paper-b",
        )

    @staticmethod
    def _deployment_payload(*, name: str = "Momentum deployment") -> dict[str, object]:
        return {
            "name": name,
            "source": "test",
            "config": {
                "execution": {"initial_cash": 100_000.0, "commission_bps": 1.5},
                "strategies": [{"name": "momentum", "pipeline": "etf_trend", "symbols": ["SPY", "QQQ"]}],
            },
        }

    def _create_deployment(self, *, key: str = "deployment-1", organization_id: str | None = None) -> dict[str, object]:
        return self.store.create_paper_deployment(
            organization_id=organization_id or self.org_a["organization_id"],
            payload=self._deployment_payload(),
            idempotency_key=key,
        )

    def test_tenant_scoped_composite_foreign_key_rejects_cross_tenant_run(self) -> None:
        deployment = self._create_deployment()
        with self.assertRaisesRegex(ValueError, "not found for organization"):
            self.store.create_paper_run(
                organization_id=self.org_b["organization_id"],
                deployment_id=deployment["id"],
                idempotency_key="cross-tenant",
                request={"asof_date": "2026-08-01"},
            )

        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys=ON")
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO paper_runs (
                    id, organization_id, deployment_id, idempotency_key, request_hash,
                    deployment_version, status, run_index, request_json,
                    deployment_config_json, batch_summary_json, aggregate_payload_json,
                    version, created_at_utc, updated_at_utc
                ) VALUES ('cross', ?, ?, 'cross', 'hash', 1, 'queued', 1, '{}', '{}', '{}', '{}', 1, 'now', 'now')
                """,
                (self.org_b["organization_id"], deployment["id"]),
            )
        connection.close()

    def test_concurrent_run_creation_with_same_idempotency_key_returns_one_row(self) -> None:
        deployment = self._create_deployment()
        first = SQLiteMetadataStore(self.database_path, enable_demo_accounts=False)
        second = SQLiteMetadataStore(self.database_path, enable_demo_accounts=False)
        barrier = threading.Barrier(2)

        def create(store: SQLiteMetadataStore) -> dict[str, object]:
            barrier.wait(timeout=5)
            return store.create_paper_run(
                organization_id=self.org_a["organization_id"],
                deployment_id=deployment["id"],
                idempotency_key="job-1:2026-08-01:1",
                request={"asof_date": "2026-08-01", "symbols": ["SPY"]},
                job_id="job-1",
                asof_date="2026-08-01",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create, (first, second)))

        self.assertEqual(results[0]["id"], results[1]["id"])
        self.assertEqual(
            len(self.store.list_paper_runs(organization_id=self.org_a["organization_id"])),
            1,
        )

    def test_idempotency_key_reuse_with_mismatched_payload_is_rejected(self) -> None:
        self._create_deployment(key="same-key")
        with self.assertRaises(IdempotencyConflictError):
            self.store.create_paper_deployment(
                organization_id=self.org_a["organization_id"],
                payload=self._deployment_payload(name="Different deployment"),
                idempotency_key="same-key",
            )

        deployment = self._create_deployment(key="run-deployment")
        self.store.create_paper_run(
            organization_id=self.org_a["organization_id"],
            deployment_id=deployment["id"],
            idempotency_key="same-run",
            request={"asof_date": "2026-08-01"},
        )
        with self.assertRaises(IdempotencyConflictError):
            self.store.create_paper_run(
                organization_id=self.org_a["organization_id"],
                deployment_id=deployment["id"],
                idempotency_key="same-run",
                request={"asof_date": "2026-08-02"},
            )

    def test_deployment_and_run_updates_use_compare_and_swap_versions(self) -> None:
        deployment = self._create_deployment()
        updated = self.store.update_paper_deployment(
            organization_id=self.org_a["organization_id"],
            deployment_id=deployment["id"],
            expected_version=1,
            updates={"status": "paused", "name": "Paused momentum"},
        )
        stale = self.store.update_paper_deployment(
            organization_id=self.org_a["organization_id"],
            deployment_id=deployment["id"],
            expected_version=1,
            updates={"status": "archived"},
        )
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["status"], "paused")
        self.assertIsNone(stale)

        run = self.store.create_paper_run(
            organization_id=self.org_a["organization_id"],
            deployment_id=deployment["id"],
            idempotency_key="cas-run",
            request={"asof_date": "2026-08-01"},
        )
        running = self.store.update_paper_run_status(
            organization_id=self.org_a["organization_id"],
            run_id=run["id"],
            expected_version=1,
            status="running",
        )
        self.assertEqual(running["version"], 2)
        self.assertIsNone(
            self.store.fail_paper_run(
                organization_id=self.org_a["organization_id"],
                run_id=run["id"],
                expected_version=1,
                error="stale failure",
            )
        )

    def test_exact_nested_aggregate_round_trip_and_latest_completed_selection(self) -> None:
        deployment = self._create_deployment()
        completed_run = self.store.create_paper_run(
            organization_id=self.org_a["organization_id"],
            deployment_id=deployment["id"],
            idempotency_key="completed",
            request={"asof_date": "2026-08-01"},
        )
        batch_summary = {
            "strategies": {"momentum": {"positions": {"SPY": 12.5}, "orders": [], "note": None}},
            "leaderboard": [{"strategy": "momentum", "return": 0.0125}],
            "flags": [True, False],
        }
        aggregate = {
            "strategies": [{"name": "momentum", "equity": 101_250.75, "warnings": []}],
            "summary": {"cash": 50_000.25, "nullable": None},
        }
        completed = self.store.complete_paper_run(
            organization_id=self.org_a["organization_id"],
            run_id=completed_run["id"],
            expected_version=1,
            batch_summary=batch_summary,
            aggregate_payload=aggregate,
        )
        failed_run = self.store.create_paper_run(
            organization_id=self.org_a["organization_id"],
            deployment_id=deployment["id"],
            idempotency_key="failed",
            request={"asof_date": "2026-08-02"},
        )
        self.store.fail_paper_run(
            organization_id=self.org_a["organization_id"],
            run_id=failed_run["id"],
            expected_version=1,
            error="expected test failure",
        )
        self.store.create_paper_run(
            organization_id=self.org_a["organization_id"],
            deployment_id=deployment["id"],
            idempotency_key="running",
            request={"asof_date": "2026-08-03"},
            status="running",
        )

        loaded = self.store.get_paper_run(organization_id=self.org_a["organization_id"], run_id=completed_run["id"])
        latest = self.store.get_latest_completed_paper_run(
            organization_id=self.org_a["organization_id"],
            deployment_id=deployment["id"],
        )
        self.assertEqual(completed["batch_summary"], batch_summary)
        self.assertEqual(loaded["aggregate_payload"], aggregate)
        self.assertEqual(latest["id"], completed_run["id"])

    def test_retry_paper_run_is_cas_guarded_and_cannot_reopen_completed_run(self) -> None:
        deployment = self._create_deployment()
        run = self.store.create_paper_run(
            organization_id=self.org_a["organization_id"],
            deployment_id=deployment["id"],
            idempotency_key="retry-run",
            request={"asof_date": "2026-08-01"},
            status="running",
        )
        resumed = self.store.retry_paper_run(
            organization_id=self.org_a["organization_id"],
            run_id=run["id"],
            expected_version=run["version"],
        )
        self.assertEqual(resumed["status"], "running")
        self.assertEqual(resumed["version"], 2)
        self.assertIsNone(
            self.store.retry_paper_run(
                organization_id=self.org_a["organization_id"],
                run_id=run["id"],
                expected_version=run["version"],
            )
        )
        completed = self.store.complete_paper_run(
            organization_id=self.org_a["organization_id"],
            run_id=run["id"],
            expected_version=resumed["version"],
            batch_summary={"ok": True},
            aggregate_payload={"ok": True},
        )
        self.assertEqual(completed["status"], "completed")
        self.assertIsNone(
            self.store.retry_paper_run(
                organization_id=self.org_a["organization_id"],
                run_id=run["id"],
                expected_version=completed["version"],
            )
        )

    def test_terminal_artifact_must_belong_to_the_run_tenant(self) -> None:
        deployment = self._create_deployment()
        run = self.store.create_paper_run(
            organization_id=self.org_a["organization_id"],
            deployment_id=deployment["id"],
            idempotency_key="tenant-artifact",
            request={},
        )
        foreign_artifact = self.store.upsert_artifact(
            organization_id=self.org_b["organization_id"],
            payload={
                "artifact_type": "paper_run",
                "storage_key": "paper/foreign",
                "uri": "file:///foreign",
            },
        )
        with self.assertRaisesRegex(ValueError, "Artifact not found for organization"):
            self.store.complete_paper_run(
                organization_id=self.org_a["organization_id"],
                run_id=run["id"],
                expected_version=1,
                batch_summary={},
                aggregate_payload={},
                artifact_id=foreign_artifact["id"],
            )
        self.assertEqual(
            self.store.get_paper_run(organization_id=self.org_a["organization_id"], run_id=run["id"])["status"],
            "queued",
        )

    def test_status_filters_and_pagination_are_tenant_scoped_and_deterministic(self) -> None:
        active = self._create_deployment(key="list-active")
        archived = self.store.create_paper_deployment(
            organization_id=self.org_a["organization_id"],
            payload={**self._deployment_payload(name="Archived"), "status": "archived"},
            idempotency_key="list-archived",
        )
        self.assertEqual(
            [item["id"] for item in self.store.list_paper_deployments(
                organization_id=self.org_a["organization_id"], status="archived", limit=1, offset=0
            )],
            [archived["id"]],
        )
        self.store.create_paper_run(
            organization_id=self.org_a["organization_id"],
            deployment_id=active["id"],
            idempotency_key="page-1",
            request={"page": 1},
        )
        self.store.create_paper_run(
            organization_id=self.org_a["organization_id"],
            deployment_id=active["id"],
            idempotency_key="page-2",
            request={"page": 2},
        )
        page_one = self.store.list_paper_runs(
            organization_id=self.org_a["organization_id"], status="queued", limit=1, offset=0
        )
        page_two = self.store.list_paper_runs(
            organization_id=self.org_a["organization_id"], status="queued", limit=1, offset=1
        )
        self.assertEqual(len(page_one), 1)
        self.assertEqual(len(page_two), 1)
        self.assertNotEqual(page_one[0]["id"], page_two[0]["id"])
        with self.assertRaises(ValueError):
            self.store.list_paper_runs(organization_id=self.org_a["organization_id"], status="unknown")

    def test_same_strategy_name_is_distinct_across_deployments_and_legacy_null_survives(self) -> None:
        first_deployment = self._create_deployment(key="first-deployment")
        second_deployment = self._create_deployment(key="second-deployment")
        first_agent = self.store.upsert_paper_agent(
            organization_id=self.org_a["organization_id"],
            payload={"deployment_id": first_deployment["id"], "name": "momentum", "pipeline": "etf_trend"},
        )
        second_agent = self.store.upsert_paper_agent(
            organization_id=self.org_a["organization_id"],
            payload={"deployment_id": second_deployment["id"], "name": "momentum", "pipeline": "etf_trend"},
        )
        legacy = self.store.upsert_paper_agent(
            organization_id=self.org_a["organization_id"],
            payload={"name": "legacy", "pipeline": "buy_and_hold"},
        )

        self.assertNotEqual(first_agent["id"], second_agent["id"])
        self.assertIsNone(legacy["deployment_id"])
        self.assertEqual(
            [agent["id"] for agent in self.store.list_paper_agents(
                organization_id=self.org_a["organization_id"], deployment_id=first_deployment["id"]
            )],
            [first_agent["id"]],
        )
        self.assertIn(legacy["id"], {agent["id"] for agent in self.store.list_paper_agents(organization_id=self.org_a["organization_id"])})

    def test_secret_fields_and_non_finite_numbers_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "sensitive credential"):
            self.store.create_paper_deployment(
                organization_id=self.org_a["organization_id"],
                payload={"name": "unsafe", "config": {"newsapi_api_key": "do-not-store"}},
                idempotency_key="unsafe",
            )
        with self.assertRaisesRegex(ValueError, "finite JSON"):
            self.store.create_paper_deployment(
                organization_id=self.org_a["organization_id"],
                payload={"name": "nan", "config": {"weight": float("nan")}},
                idempotency_key="nan",
            )

        deployment = self._create_deployment()
        with self.assertRaisesRegex(ValueError, "sensitive credential"):
            self.store.create_paper_run(
                organization_id=self.org_a["organization_id"],
                deployment_id=deployment["id"],
                idempotency_key="unsafe-run",
                request={"access_token": "do-not-store"},
            )
        run = self.store.create_paper_run(
            organization_id=self.org_a["organization_id"],
            deployment_id=deployment["id"],
            idempotency_key="nan-result",
            request={},
        )
        with self.assertRaisesRegex(ValueError, "finite JSON"):
            self.store.complete_paper_run(
                organization_id=self.org_a["organization_id"],
                run_id=run["id"],
                expected_version=1,
                batch_summary={"value": float("inf")},
                aggregate_payload={},
            )

    def test_sqlite_schema_contains_new_tables_columns_and_indexes(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            agent_columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_agents)")}
            deployment_indexes = {row[1] for row in connection.execute("PRAGMA index_list(paper_deployments)")}
            run_indexes = {row[1] for row in connection.execute("PRAGMA index_list(paper_runs)")}
            run_foreign_keys = connection.execute("PRAGMA foreign_key_list(paper_runs)").fetchall()
        finally:
            connection.close()
        self.assertTrue({"paper_deployments", "paper_runs"}.issubset(tables))
        self.assertIn("deployment_id", agent_columns)
        self.assertIn("idx_paper_deployments_org_idempotency", deployment_indexes)
        self.assertIn("idx_paper_runs_org_deployment_created", run_indexes)
        self.assertEqual(sum(1 for row in run_foreign_keys if row[2] == "paper_deployments"), 2)

    def test_alembic_migration_contract_matches_runtime_schema(self) -> None:
        migration_path = Path("migrations/versions/0006_paper_deployments_runs.py")
        source = migration_path.read_text(encoding="utf-8")
        module = ast.parse(source)
        assignments = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in module.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"revision", "down_revision"}
        }
        self.assertEqual(assignments["revision"], "0006_paper_deployments_runs")
        self.assertEqual(assignments["down_revision"], "0005_durable_job_claims")
        for required_contract in (
            '"paper_deployments"',
            '"paper_runs"',
            '"deployment_id"',
            '"idx_paper_agents_org_deployment_name"',
            '"idx_paper_runs_org_deployment_created"',
            '"fk_paper_runs_org_deployment"',
        ):
            self.assertIn(required_contract, source)


if __name__ == "__main__":
    unittest.main()
