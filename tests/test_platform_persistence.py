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
        self.assertGreaterEqual(reopened.counts().users, 1)

    def test_sqlite_metadata_store_persists_saas_workspace_records(self) -> None:
        workspace = fresh_test_dir("artifacts/test_platform_saas_records")
        store = SQLiteMetadataStore(workspace / "metadata.sqlite3")
        context = store.ensure_demo_workspace()
        org_id = context["organization_id"]

        project = store.create_project(organization_id=org_id, name="Production readiness lab")
        dataset = store.upsert_dataset(
            organization_id=org_id,
            payload={
                "project_id": project["id"],
                "name": "Daily sentiment",
                "kind": "sentiment_daily",
                "path": "data/sentiment_cache/shadow/daily_sentiment.parquet",
                "provider": {"source": "rss"},
                "schema": {"columns": ["date", "ticker", "sentiment_score"]},
                "row_count": 42,
            },
        )
        api_key = store.create_api_key_metadata(
            organization_id=org_id,
            name="NewsAPI",
            provider="newsapi",
            secret="dummy-dummy-dummy",
        )
        experiment = store.upsert_experiment(
            organization_id=org_id,
            payload={
                "id": "exp-1",
                "project_id": project["id"],
                "job_id": "job-1",
                "name": "ETF trend v1",
                "pipeline": "etf_trend",
                "status": "completed",
                "summary": {"sharpe": 1.1},
                "validation": {"dsr": 0.7},
                "lineage": {"symbols": ["SPY"]},
                "readiness": {"score": 80},
                "trades": [{"symbol": "SPY"}],
                "sentiment": {"daily_sentiment_file": "daily.parquet"},
            },
        )
        agent = store.upsert_paper_agent(
            organization_id=org_id,
            payload={
                "id": "agent-1",
                "project_id": project["id"],
                "name": "ETF trend paper",
                "pipeline": "etf_trend",
                "status": "running",
                "fake_cash": 100000,
                "config": {"symbols": ["SPY"]},
                "latest_payload": {"equity": 101000},
                "warnings": ["review turnover"],
            },
        )
        report = store.upsert_market_research_report(
            organization_id=org_id,
            user_id=context["user_id"],
            payload={
                "id": "mrr-1",
                "job_id": "market-job-1",
                "ticker": "AAPL",
                "analysis_date": "2026-05-08",
                "horizon": "swing",
                "status": "completed",
                "decision": "HOLD",
                "confidence": 52,
                "summary": "Research summary",
                "disclaimer": "For research and educational purposes only. Not financial advice.",
                "context": {"request": {"ticker": "AAPL"}},
                "report": {"ticker": "AAPL", "decision": "HOLD"},
                "source_references": [{"id": "source-1", "title": "Demo source"}],
                "provider_metadata": {"llm_provider": "mock", "prompt_version": "v1"},
                "warnings": ["demo"],
            },
        )

        reopened = SQLiteMetadataStore(workspace / "metadata.sqlite3")
        self.assertEqual(reopened.list_projects(organization_id=org_id)[-1]["name"], "Production readiness lab")
        self.assertEqual(dataset["row_count"], 42)
        self.assertIn("...", api_key["masked_value"])
        self.assertEqual(reopened.get_experiment(organization_id=org_id, experiment_id="exp-1")["readiness"]["score"], 80)
        self.assertEqual(reopened.get_paper_agent(organization_id=org_id, agent_id="agent-1")["warnings"], ["review turnover"])
        self.assertEqual(
            reopened.get_market_research_report(organization_id=org_id, user_id=context["user_id"], report_id="mrr-1")["decision"],
            "HOLD",
        )
        self.assertEqual(reopened.list_market_research_reports(organization_id=org_id, user_id=context["user_id"])[0]["ticker"], "AAPL")
        self.assertEqual(report["source_references"][0]["id"], "source-1")
        reopened.soft_delete_market_research_report(organization_id=org_id, user_id=context["user_id"], report_id="mrr-1")
        self.assertEqual(reopened.list_market_research_reports(organization_id=org_id, user_id=context["user_id"]), [])
        self.assertEqual(experiment["pipeline"], "etf_trend")
        self.assertEqual(agent["latest_payload"]["equity"], 101000)
        self.assertGreaterEqual(reopened.counts().subscriptions, 1)

    def test_sqlite_metadata_store_persists_telemetry_and_refresh_state(self) -> None:
        workspace = fresh_test_dir("artifacts/test_platform_observability")
        store = SQLiteMetadataStore(workspace / "metadata.sqlite3")
        context = store.ensure_demo_workspace()
        user_id = context["user_id"]
        org_id = context["organization_id"]

        event = store.record_telemetry_event(
            event_id="evt-1",
            name="backtest_started",
            category="product",
            properties={"pipeline": "etf_trend"},
            context={"view": "backtests"},
            consent="granted",
            user_id=user_id,
            organization_id=org_id,
            occurred_at_utc="2026-05-02T00:00:00Z",
        )
        run, created = store.create_refresh_run(
            run_id="refresh-1",
            idempotency_key="daily:user:2026-05-02",
            user_id=user_id,
            organization_id=org_id,
            max_attempts=3,
            locked_until_utc="2026-05-02T00:30:00Z",
        )
        duplicate, duplicate_created = store.create_refresh_run(
            run_id="refresh-duplicate",
            idempotency_key="daily:user:2026-05-02",
            user_id=user_id,
            organization_id=org_id,
            max_attempts=3,
            locked_until_utc="2026-05-02T00:30:00Z",
        )
        store.update_refresh_run(run_id="refresh-1", status="succeeded", attempt=1, summary={"dataset_count": 2})
        store.upsert_refresh_status(
            user_id=user_id,
            organization_id=org_id,
            status="succeeded",
            latest_run_id="refresh-1",
            last_success_at_utc="2026-05-02T00:01:00Z",
            last_attempt_at_utc="2026-05-02T00:01:00Z",
            next_due_at_utc="2026-05-03T00:01:00Z",
        )

        self.assertEqual(event["properties"]["pipeline"], "etf_trend")
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate["id"], run["id"])
        self.assertEqual(store.get_refresh_status(user_id=user_id)["status"], "succeeded")
        self.assertEqual(store.list_refresh_runs(user_id=user_id)[0]["summary"]["dataset_count"], 2)
        self.assertGreaterEqual(store.counts().telemetry_events, 1)
        self.assertGreaterEqual(store.counts().refresh_runs, 1)
        self.assertGreaterEqual(store.counts().refresh_statuses, 1)


if __name__ == "__main__":
    unittest.main()
