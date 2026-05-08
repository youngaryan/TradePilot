from __future__ import annotations

import hashlib
import hmac
import json
import time
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
    def auth_headers(self, client, *, email: str = "demo@quantops.local", password: str = "quantops-demo") -> dict[str, str]:
        login = client.post("/api/auth/login", json={"email": email, "password": password})
        self.assertEqual(login.status_code, 200)
        return {
            "X-Organization-Id": login.json()["active_organization_id"],
            "X-CSRF-Token": client.cookies.get("quantops_csrf") or "",
        }

    def register_sentiment_dataset(self, *, settings, organization_id: str, output_dir, dataset_id: str = "dst-test-sentiment") -> str:
        from pairs_trading.platform import SQLiteMetadataStore

        store = SQLiteMetadataStore(settings.metadata_db_path)
        artifact_id = f"art-{dataset_id}"
        store.upsert_artifact(
            organization_id=organization_id,
            payload={
                "id": artifact_id,
                "artifact_type": "sentiment",
                "source_id": dataset_id,
                "provider": "local",
                "storage_key": str(output_dir),
                "uri": str(output_dir),
                "metadata": {"test_fixture": True},
            },
        )
        store.upsert_dataset(
            organization_id=organization_id,
            payload={
                "id": dataset_id,
                "name": "Unit test sentiment dataset",
                "kind": "sentiment_daily",
                "path": str(output_dir / "daily_sentiment.parquet"),
                "provider": {"source": "unit_test", "artifact_id": artifact_id},
                "schema": {},
                "row_count": 0,
            },
        )
        return dataset_id

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
                sentiment_job_state_dir=workspace / "sentiment_jobs",
                metadata_db_path=workspace / "metadata.sqlite3",
                default_paper_config=workspace / "missing.json",
            )
        )
        client = TestClient(app)
        headers = self.auth_headers(client)

        health = client.get("/api/health")
        unauth_summary = TestClient(app).get("/api/paper/summary")
        summary = client.get("/api/paper/summary", headers=headers)
        strategy = client.get("/api/paper/strategies/trend", headers=headers)
        missing = client.get("/api/paper/strategies/missing", headers=headers)
        catalog = client.get("/api/strategies/catalog")
        catalog_item = client.get("/api/strategies/catalog/ema_cross")
        paper_jobs = client.get("/api/paper/jobs", headers=headers)
        unauth_metadata = TestClient(app).get("/api/system/metadata")
        unauth_counts = TestClient(app).get("/api/system/admin-counts")
        metadata = client.get("/api/system/metadata", headers=headers)
        admin_counts = client.get("/api/system/admin-counts", headers=headers)

        self.assertEqual(health.status_code, 200)
        self.assertEqual(unauth_summary.status_code, 401)
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
        self.assertEqual(unauth_metadata.status_code, 200)
        self.assertNotIn("counts", unauth_metadata.json())
        self.assertEqual(unauth_counts.status_code, 401)
        self.assertEqual(metadata.status_code, 200)
        self.assertNotIn("counts", metadata.json())
        self.assertEqual(admin_counts.status_code, 200)
        self.assertEqual(admin_counts.json()["counts"]["jobs"], 0)
        self.assertGreaterEqual(admin_counts.json()["counts"]["organizations"], 1)

    def test_saas_routes_auth_workspace_billing_and_details(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.platform import SQLiteMetadataStore

        workspace = fresh_test_dir("artifacts/test_backend_saas")
        settings = BackendSettings(
            paper_state_dir=workspace / "state",
            paper_artifact_root=workspace / "runs",
            paper_job_state_dir=workspace / "paper_jobs",
            backtest_job_state_dir=workspace / "backtest_jobs",
            metadata_db_path=workspace / "metadata.sqlite3",
            default_paper_config=workspace / "missing.json",
            app_base_url="http://127.0.0.1:5173",
        )
        app = create_app(settings)
        client = TestClient(app)

        login = client.post("/api/auth/login", json={"email": "demo@quantops.local", "password": "quantops-demo"})
        self.assertEqual(login.status_code, 200)
        self.assertNotIn("access_token", login.json())
        org_id = login.json()["active_organization_id"]
        headers = {"X-Organization-Id": org_id, "X-CSRF-Token": client.cookies.get("quantops_csrf") or ""}
        session_token = client.cookies.get("quantops_session")
        bearer_session = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {session_token}", "X-Organization-Id": org_id},
        )
        self.assertEqual(bearer_session.status_code, 401)

        store = SQLiteMetadataStore(settings.metadata_db_path)
        store.upsert_experiment(
            organization_id=org_id,
            payload={
                "id": "exp-api-1",
                "name": "API readiness experiment",
                "pipeline": "etf_trend",
                "status": "completed",
                "summary": {"sharpe": 1.2},
                "validation": {"dsr": 0.75, "pbo": 0.2},
                "lineage": {"symbols": ["SPY", "QQQ"]},
                "readiness": {"score": 83, "checks": [{"name": "DSR", "passed": True, "target": ">= 0.60"}]},
                "trades": [],
                "sentiment": {"daily_sentiment_file": "daily.parquet"},
            },
        )
        store.upsert_paper_agent(
            organization_id=org_id,
            payload={
                "id": "agent-api-1",
                "name": "ETF paper",
                "pipeline": "etf_trend",
                "status": "running",
                "fake_cash": 100000,
                "config": {"symbols": ["SPY"]},
                "latest_payload": {"equity": 100500, "target_weights": {"SPY": 1.0}},
                "warnings": [],
            },
        )

        me = client.get("/api/auth/me", headers=headers)
        workspace_response = client.get("/api/workspaces", headers=headers)
        project = client.post("/api/workspaces/projects", headers=headers, json={"name": "New SaaS Project"})
        api_key = client.post(
            "/api/workspaces/api-keys",
            headers=headers,
            json={"name": "NewsAPI", "provider": "newsapi", "secret_ref": "NEWSAPI_API_KEY"},
        )
        machine_key = client.post(
            "/api/workspaces/api-keys",
            headers=headers,
            json={"name": "Worker key", "provider": "machine", "scopes": ["read"]},
        )
        checkout = client.post("/api/billing/checkout", headers=headers, json={"plan": "pro"})
        experiments = client.get("/api/workspaces/experiments", headers=headers)
        experiment_detail = client.get("/api/workspaces/experiments/exp-api-1", headers=headers)
        agents = client.get("/api/workspaces/paper-agents", headers=headers)
        agent_detail = client.get("/api/workspaces/paper-agents/agent-api-1", headers=headers)

        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"]["email"], "demo@quantops.local")
        self.assertEqual(workspace_response.status_code, 200)
        self.assertGreaterEqual(len(workspace_response.json()["projects"]), 1)
        self.assertEqual(project.status_code, 201)
        self.assertEqual(api_key.status_code, 201)
        self.assertEqual(api_key.json()["secret_ref"], "NEWSAPI_API_KEY")
        self.assertEqual(machine_key.status_code, 201)
        self.assertTrue(machine_key.json()["token"].startswith("qops_"))
        machine_headers = {"Authorization": f"Bearer {machine_key.json()['token']}", "X-Organization-Id": org_id}
        self.assertEqual(client.get("/api/auth/me", headers=machine_headers).status_code, 200)
        self.assertEqual(client.get("/api/account/export", headers=machine_headers).status_code, 403)
        self.assertEqual(checkout.status_code, 200)
        self.assertEqual(checkout.json()["mode"], "demo")
        self.assertEqual(experiments.status_code, 200)
        self.assertEqual(experiment_detail.status_code, 200)
        self.assertEqual(experiment_detail.json()["readiness"]["score"], 83)
        self.assertEqual(agents.status_code, 200)
        self.assertEqual(agent_detail.status_code, 200)
        self.assertEqual(agent_detail.json()["latest_payload"]["equity"], 100500)

    def test_free_workspace_onboarding_does_not_mark_billing_complete(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_free_onboarding")
        settings = BackendSettings(
            paper_state_dir=workspace / "state",
            paper_artifact_root=workspace / "runs",
            paper_job_state_dir=workspace / "paper_jobs",
            backtest_job_state_dir=workspace / "backtest_jobs",
            metadata_db_path=workspace / "metadata.sqlite3",
            default_paper_config=workspace / "missing.json",
            app_base_url="http://127.0.0.1:5173",
        )
        client = TestClient(create_app(settings))
        login = client.post("/api/auth/login", json={"email": "user@quantops.local", "password": "quantops-user"})
        self.assertEqual(login.status_code, 200)
        org_id = login.json()["active_organization_id"]

        workspace_response = client.get("/api/workspaces", headers={"X-Organization-Id": org_id})

        self.assertEqual(workspace_response.status_code, 200)
        billing_step = next(step for step in workspace_response.json()["onboarding"]["steps"] if step["id"] == "billing")
        self.assertFalse(billing_step["complete"])

    def test_observability_routes_capture_telemetry_and_refresh_status(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_observability")
        settings = BackendSettings(
            paper_state_dir=workspace / "state",
            paper_artifact_root=workspace / "runs",
            paper_job_state_dir=workspace / "paper_jobs",
            backtest_job_state_dir=workspace / "backtest_jobs",
            metadata_db_path=workspace / "metadata.sqlite3",
            default_paper_config=workspace / "missing.json",
            refresh_interval_hours=24,
            refresh_max_attempts=2,
        )
        app = create_app(settings)
        client = TestClient(app)

        login = client.post("/api/auth/login", json={"email": "demo@quantops.local", "password": "quantops-demo"})
        org_id = login.json()["active_organization_id"]
        headers = {"X-Organization-Id": org_id, "X-CSRF-Token": client.cookies.get("quantops_csrf") or ""}

        denied = client.post(
            "/api/telemetry/events",
            headers=headers,
            json={"name": "view_opened", "category": "product", "properties": {"email": "user@example.com"}, "consent": "denied"},
        )
        captured = client.post(
            "/api/telemetry/events",
            headers=headers,
            json={"name": "backtest_started", "category": "product", "properties": {"pipeline": "etf_trend", "api_key": "secret"}, "consent": "granted"},
        )
        events = client.get("/api/telemetry/events", headers=headers)
        refresh = client.post("/api/refresh/run", headers=headers, json={"force": True})
        refresh_duplicate = client.post("/api/refresh/run", headers=headers, json={"force": True})
        status = client.get("/api/refresh/status", headers=headers)

        self.assertEqual(denied.status_code, 200)
        self.assertFalse(denied.json()["stored"])
        self.assertEqual(captured.status_code, 200)
        self.assertTrue(captured.json()["stored"])
        self.assertEqual(captured.json()["event"]["properties"]["api_key"], "[redacted]")
        self.assertEqual(events.status_code, 200)
        self.assertGreaterEqual(len(events.json()), 1)
        self.assertEqual(refresh.status_code, 202)
        self.assertIn(refresh.json()["status"], {"succeeded", "queued", "running"})
        self.assertEqual(refresh_duplicate.status_code, 202)
        self.assertTrue(refresh_duplicate.json().get("deduplicated"))
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["interval_hours"], 24)
        self.assertGreaterEqual(len(status.json()["recent_runs"]), 1)

    def test_admin_rbac_and_payment_wall_are_enforced_server_side(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_admin_rbac")
        settings = BackendSettings(
            paper_state_dir=workspace / "state",
            paper_artifact_root=workspace / "runs",
            paper_job_state_dir=workspace / "paper_jobs",
            backtest_job_state_dir=workspace / "backtest_jobs",
            sentiment_job_state_dir=workspace / "sentiment_jobs",
            metadata_db_path=workspace / "metadata.sqlite3",
            default_paper_config=workspace / "missing.json",
        )
        app = create_app(settings)
        client = TestClient(app)

        admin_login = client.post("/api/auth/login", json={"email": "demo@quantops.local", "password": "quantops-demo"})
        user_login = client.post("/api/auth/login", json={"email": "user@quantops.local", "password": "quantops-user"})
        self.assertEqual(admin_login.status_code, 200)
        self.assertEqual(user_login.status_code, 200)
        self.assertEqual(admin_login.json()["user"]["role"], "admin")
        self.assertEqual(user_login.json()["user"]["role"], "user")

        admin_cookie = "; ".join(f"{key}={value}" for key, value in admin_login.cookies.items())
        user_cookie = "; ".join(f"{key}={value}" for key, value in user_login.cookies.items())
        admin_headers = {
            "Cookie": admin_cookie,
            "X-Organization-Id": admin_login.json()["active_organization_id"],
            "X-CSRF-Token": admin_login.cookies.get("quantops_csrf") or "",
        }
        user_headers = {
            "Cookie": user_cookie,
            "X-Organization-Id": user_login.json()["active_organization_id"],
            "X-CSRF-Token": user_login.cookies.get("quantops_csrf") or "",
        }

        self.assertEqual(client.get("/api/admin/overview", headers=user_headers).status_code, 403)
        admin_overview = client.get("/api/admin/overview", headers=admin_headers)
        self.assertEqual(admin_overview.status_code, 200)
        self.assertGreaterEqual(admin_overview.json()["metrics"]["admins_active"], 1)

        users = client.get("/api/admin/users", headers=admin_headers)
        self.assertEqual(users.status_code, 200)
        normal_user = next(row for row in users.json() if row["email"] == "user@quantops.local")

        premium_run = client.post("/api/backtests/run", headers=user_headers, json={})
        self.assertEqual(premium_run.status_code, 402)
        self.assertEqual(premium_run.json()["detail"]["code"], "payment_required")
        self.assertEqual(client.post("/api/refresh/tick", headers=user_headers, json={"limit": 10}).status_code, 403)

        deactivate = client.patch(
            f"/api/admin/users/{normal_user['id']}",
            headers=admin_headers,
            json={"status": "inactive"},
        )
        self.assertEqual(deactivate.status_code, 200)
        inactive_login = client.post("/api/auth/login", json={"email": "user@quantops.local", "password": "quantops-user"})
        self.assertEqual(inactive_login.status_code, 403)

        reactivate = client.patch(
            f"/api/admin/users/{normal_user['id']}",
            headers=admin_headers,
            json={"status": "active", "role": "admin"},
        )
        self.assertEqual(reactivate.status_code, 200)
        self.assertEqual(reactivate.json()["role"], "admin")

    def test_admin_role_bypasses_paid_subscription_for_premium_routes(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.platform import SQLiteMetadataStore

        workspace = fresh_test_dir("artifacts/test_backend_admin_paid_bypass")
        settings = BackendSettings(
            paper_state_dir=workspace / "state",
            paper_artifact_root=workspace / "runs",
            paper_job_state_dir=workspace / "paper_jobs",
            backtest_job_state_dir=workspace / "backtest_jobs",
            sentiment_job_state_dir=workspace / "sentiment_jobs",
            metadata_db_path=workspace / "metadata.sqlite3",
            default_paper_config=workspace / "missing.json",
        )
        app = create_app(settings)
        client = TestClient(app)

        user_login = client.post("/api/auth/login", json={"email": "user@quantops.local", "password": "quantops-user"})
        self.assertEqual(user_login.status_code, 200)
        headers = {
            "X-Organization-Id": user_login.json()["active_organization_id"],
            "X-CSRF-Token": client.cookies.get("quantops_csrf") or "",
        }

        billing_before = client.get("/api/billing/status", headers=headers)
        self.assertEqual(billing_before.status_code, 200)
        self.assertFalse(billing_before.json()["premium"])
        blocked = client.post("/api/backtests/run", headers=headers, json={})
        self.assertEqual(blocked.status_code, 402)
        self.assertEqual(blocked.json()["detail"]["code"], "payment_required")

        store = SQLiteMetadataStore(settings.metadata_db_path)
        store.update_user_role(user_id=user_login.json()["user"]["id"], role="admin")

        me_after = client.get("/api/auth/me", headers=headers)
        self.assertEqual(me_after.status_code, 200)
        self.assertEqual(me_after.json()["user"]["role"], "admin")
        billing_after = client.get("/api/billing/status", headers=headers)
        self.assertEqual(billing_after.status_code, 200)
        self.assertTrue(billing_after.json()["premium"])
        self.assertEqual(billing_after.json()["access"], "admin")
        allowed = client.post("/api/backtests/run", headers=headers, json={})
        self.assertEqual(allowed.status_code, 202)

    def test_signup_and_landing_analytics_are_recorded_for_admins(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_landing_analytics")
        settings = BackendSettings(
            paper_state_dir=workspace / "state",
            paper_artifact_root=workspace / "runs",
            paper_job_state_dir=workspace / "paper_jobs",
            backtest_job_state_dir=workspace / "backtest_jobs",
            sentiment_job_state_dir=workspace / "sentiment_jobs",
            metadata_db_path=workspace / "metadata.sqlite3",
            default_paper_config=workspace / "missing.json",
        )
        app = create_app(settings)
        client = TestClient(app)

        landing = client.post(
            "/api/telemetry/events",
            headers={"cf-ipcountry": "GB"},
            json={
                "name": "landing_page_view",
                "category": "landing",
                "properties": {"section": "top", "cta": "hero"},
                "anonymous_id": "visitor-test",
                "consent": "granted",
            },
        )
        pricing = client.post(
            "/api/telemetry/events",
            headers={"cf-ipcountry": "GB"},
            json={
                "name": "pricing_viewed",
                "category": "landing",
                "properties": {"section": "pricing"},
                "anonymous_id": "visitor-test",
                "consent": "granted",
            },
        )
        signup = client.post(
            "/api/auth/signup",
            json={
                "email": "founder@example.com",
                "password": "safe-password-123",
                "display_name": "Founder User",
                "organization_name": "Founder Quant Lab",
            },
        )
        self.assertEqual(landing.status_code, 200)
        self.assertEqual(pricing.status_code, 200)
        self.assertEqual(signup.status_code, 201)
        self.assertEqual(signup.json()["user"]["role"], "user")

        user_cookie = "; ".join(f"{key}={value}" for key, value in signup.cookies.items())
        user_headers = {"Cookie": user_cookie, "X-Organization-Id": signup.json()["active_organization_id"]}
        self.assertEqual(client.get("/api/admin/overview", headers=user_headers).status_code, 403)
        self.assertEqual(client.post("/api/backtests/run", headers=user_headers, json={}).status_code, 402)

        admin_headers = self.auth_headers(client)
        overview = client.get("/api/admin/overview", headers=admin_headers)
        self.assertEqual(overview.status_code, 200)
        analytics = overview.json()["landing_analytics"]
        self.assertGreaterEqual(analytics["totals"]["landing_page_visits"], 1)
        self.assertGreaterEqual(analytics["totals"]["pricing_views"], 1)
        self.assertGreaterEqual(analytics["visitors_by_country"]["GB"], 1)

    def test_stripe_webhook_updates_subscription_lifecycle(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.platform import SQLiteMetadataStore

        workspace = fresh_test_dir("artifacts/test_backend_stripe_webhook")
        secret = "whsec_test_secret"
        settings = BackendSettings(
            paper_state_dir=workspace / "state",
            paper_artifact_root=workspace / "runs",
            paper_job_state_dir=workspace / "paper_jobs",
            backtest_job_state_dir=workspace / "backtest_jobs",
            sentiment_job_state_dir=workspace / "sentiment_jobs",
            metadata_db_path=workspace / "metadata.sqlite3",
            default_paper_config=workspace / "missing.json",
            stripe_webhook_secret=secret,
        )
        app = create_app(settings)
        client = TestClient(app)
        org_id = self.auth_headers(client)["X-Organization-Id"]

        def stripe_signature(payload: bytes) -> str:
            timestamp = str(int(time.time()))
            signed = f"{timestamp}.{payload.decode('utf-8')}".encode("utf-8")
            digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
            return f"t={timestamp},v1={digest}"

        checkout_payload = json.dumps(
            {
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "metadata": {"organization_id": org_id},
                        "customer": "cus_test_123",
                        "subscription": "sub_test_123",
                    }
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        checkout = client.post(
            "/api/billing/webhook",
            content=checkout_payload,
            headers={"stripe-signature": stripe_signature(checkout_payload)},
        )
        self.assertEqual(checkout.status_code, 200)
        self.assertTrue(checkout.json()["updated"])

        store = SQLiteMetadataStore(workspace / "metadata.sqlite3")
        subscription = store.get_subscription(organization_id=org_id)
        self.assertEqual(subscription["plan"], "pro")
        self.assertEqual(subscription["status"], "active")
        self.assertEqual(subscription["stripe_customer_id"], "cus_test_123")
        self.assertEqual(subscription["stripe_subscription_id"], "sub_test_123")

        deleted_payload = json.dumps(
            {
                "type": "customer.subscription.deleted",
                "data": {"object": {"id": "sub_test_123", "customer": "cus_test_123"}},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        deleted = client.post(
            "/api/billing/webhook",
            content=deleted_payload,
            headers={"stripe-signature": stripe_signature(deleted_payload)},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(store.get_subscription(organization_id=org_id)["status"], "canceled")

        rejected = client.post("/api/billing/webhook", content=checkout_payload, headers={"stripe-signature": "t=1,v1=bad"})
        self.assertEqual(rejected.status_code, 400)

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
        headers = self.auth_headers(client)

        with patch(
            "pairs_trading.backend.services.build_best_available_sentiment_model",
            return_value=FixedBackendSentimentModel(),
        ):
            response = client.post(
                "/api/sentiment/accumulate",
                headers=headers,
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

        dataset = client.get("/api/sentiment/dataset", headers=headers, params={"dataset_id": payload["dataset_id"]})
        self.assertEqual(dataset.status_code, 200)
        self.assertEqual(dataset.json()["summary"]["scored_headline_count"], 2)

    def test_sentiment_job_routes_report_progress_completion_and_failures(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_sentiment_jobs")
        news_path = workspace / "headlines.csv"
        output_dir = workspace / "sentiment_shadow"
        pd.DataFrame(
            {
                "timestamp": ["2024-01-01T09:00:00Z"],
                "ticker": ["AAA"],
                "headline": ["AAA raises guidance after strong earnings"],
                "source": ["unit_news"],
                "url": ["https://example.com/a"],
                "relevance": [1.0],
            }
        ).to_csv(news_path, index=False)

        app = create_app(
            BackendSettings(
                paper_state_dir=workspace / "state",
                paper_artifact_root=workspace / "runs",
                paper_job_state_dir=workspace / "paper_jobs",
                backtest_job_state_dir=workspace / "backtest_jobs",
                sentiment_job_state_dir=workspace / "sentiment_jobs",
                metadata_db_path=workspace / "metadata.sqlite3",
                default_paper_config=workspace / "missing.json",
                sentiment_cache_dir=workspace / "sentiment_cache",
            )
        )
        client = TestClient(app)
        headers = self.auth_headers(client)

        with patch("pairs_trading.backend.services.build_best_available_sentiment_model", return_value=FixedBackendSentimentModel()):
            submitted = client.post(
                "/api/sentiment/accumulate-job",
                headers=headers,
                json={
                    "symbols": ["AAA"],
                    "start": "2024-01-01",
                    "end": "2024-01-02",
                    "providers": ["local"],
                    "news_files": [str(news_path)],
                    "output_dir": str(output_dir),
                    "use_finbert": False,
                    "local_finbert_only": True,
                },
            )
            self.assertEqual(submitted.status_code, 202)
            job_id = submitted.json()["id"]
            user_client = TestClient(app)
            user_headers = self.auth_headers(user_client, email="user@quantops.local", password="quantops-user")
            self.assertEqual(TestClient(app).get(f"/api/sentiment/jobs/{job_id}").status_code, 401)
            self.assertEqual(user_client.get(f"/api/sentiment/jobs/{job_id}", headers=user_headers).status_code, 404)
            self.assertEqual(user_client.get("/api/sentiment/jobs", headers=user_headers).json(), [])

            completed_payload = None
            for _ in range(50):
                job = client.get(f"/api/sentiment/jobs/{job_id}", headers=headers)
                self.assertEqual(job.status_code, 200)
                if job.json()["status"] == "completed":
                    completed_payload = job.json()
                    break
                time.sleep(0.05)

        self.assertIsNotNone(completed_payload)
        assert completed_payload is not None
        self.assertEqual(completed_payload["progress"], 1.0)
        self.assertEqual(completed_payload["stage"], "completed")
        self.assertIn("Sentiment accumulation completed", completed_payload["message"])
        self.assertEqual(completed_payload["result"]["summary"]["headline_count"], 1)
        self.assertEqual(completed_payload["result"]["daily_points"][0]["ticker"], "AAA")
        jobs = client.get("/api/sentiment/jobs", headers=headers)
        self.assertEqual(jobs.status_code, 200)
        self.assertIn(job_id, {job["id"] for job in jobs.json()})

        failed = client.post(
            "/api/sentiment/accumulate-job",
            headers=headers,
            json={
                "symbols": ["AAA"],
                "start": "2024-01-01",
                "end": "2024-01-02",
                "providers": ["newsapi"],
                "news_files": [],
                "output_dir": str(output_dir),
                "use_finbert": False,
                "local_finbert_only": True,
            },
        )
        self.assertEqual(failed.status_code, 202)
        failed_id = failed.json()["id"]
        failed_payload = None
        for _ in range(50):
            job = client.get(f"/api/sentiment/jobs/{failed_id}", headers=headers)
            self.assertEqual(job.status_code, 200)
            if job.json()["status"] == "failed":
                failed_payload = job.json()
                break
            time.sleep(0.05)

        self.assertIsNotNone(failed_payload)
        assert failed_payload is not None
        self.assertEqual(failed_payload["progress"], 1.0)
        self.assertEqual(failed_payload["stage"], "failed")
        self.assertIn("NewsAPI accumulation requires", failed_payload["error"])

    def test_sentiment_dataset_preview_includes_latest_run_ticker_when_truncated(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings
        from pairs_trading.backend.services import SENTIMENT_TABLE_ROW_LIMIT

        workspace = fresh_test_dir("artifacts/test_backend_sentiment_preview_window")
        output_dir = workspace / "sentiment_shadow"
        output_dir.mkdir(parents=True, exist_ok=True)

        newer_rows = pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-04-29T12:00:00", periods=SENTIMENT_TABLE_ROW_LIMIT + 20, freq="-1min"),
                "ticker": ["AAA"] * (SENTIMENT_TABLE_ROW_LIMIT + 20),
                "headline": [f"AAA market update {index}" for index in range(SENTIMENT_TABLE_ROW_LIMIT + 20)],
                "source": ["unit_feed"] * (SENTIMENT_TABLE_ROW_LIMIT + 20),
                "url": [f"https://example.com/aaa/{index}" for index in range(SENTIMENT_TABLE_ROW_LIMIT + 20)],
                "relevance": [1.0] * (SENTIMENT_TABLE_ROW_LIMIT + 20),
            }
        )
        older_rows = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2026-04-22T20:10:00", "2026-04-16T14:30:00", "2026-04-15T10:00:00"]),
                "ticker": ["COKE", "COKE", "COKE"],
                "headline": ["COKE announces earnings release date", "COKE volume trend improves", "COKE dividend coverage improves"],
                "source": ["feeds.finance.yahoo.com", "alphavantage", "benzinga"],
                "url": ["https://example.com/coke/1", "https://example.com/coke/2", "https://example.com/coke/3"],
                "relevance": [1.0, 0.8, 0.7],
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
                    "fetched_headlines": 3,
                    "stored_headlines": SENTIMENT_TABLE_ROW_LIMIT + 23,
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
        headers = self.auth_headers(client)

        dataset_id = self.register_sentiment_dataset(
            settings=app.state.settings if hasattr(app.state, "settings") else BackendSettings(
                paper_state_dir=workspace / "state",
                paper_artifact_root=workspace / "runs",
                paper_job_state_dir=workspace / "paper_jobs",
                backtest_job_state_dir=workspace / "backtest_jobs",
                metadata_db_path=workspace / "metadata.sqlite3",
                default_paper_config=workspace / "missing.json",
                sentiment_cache_dir=workspace / "sentiment_cache",
            ),
            organization_id=headers["X-Organization-Id"],
            output_dir=output_dir,
            dataset_id="dst-preview-window",
        )
        response = client.get("/api/sentiment/dataset", headers=headers, params={"dataset_id": dataset_id})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["headline_count"], SENTIMENT_TABLE_ROW_LIMIT + 23)
        self.assertEqual(payload["summary"]["returned_headline_count"], SENTIMENT_TABLE_ROW_LIMIT + 3)
        self.assertEqual(payload["summary"]["returned_scored_headline_count"], SENTIMENT_TABLE_ROW_LIMIT + 3)
        self.assertTrue(payload["summary"]["headline_rows_truncated"])
        self.assertTrue(payload["summary"]["scored_headline_rows_truncated"])
        self.assertIn("COKE", {row["ticker"] for row in payload["headlines"]})
        self.assertIn("COKE", {row["ticker"] for row in payload["scored_headlines"]})
        self.assertEqual(sum(row["ticker"] == "COKE" for row in payload["headlines"]), 3)

    def test_sentiment_dataset_empty_directory_returns_stable_empty_payload(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_sentiment_empty_dataset")
        output_dir = workspace / "sentiment_shadow"
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
        headers = self.auth_headers(client)

        dataset_id = self.register_sentiment_dataset(
            settings=app.state.settings if hasattr(app.state, "settings") else BackendSettings(
                paper_state_dir=workspace / "state",
                paper_artifact_root=workspace / "runs",
                paper_job_state_dir=workspace / "paper_jobs",
                backtest_job_state_dir=workspace / "backtest_jobs",
                metadata_db_path=workspace / "metadata.sqlite3",
                default_paper_config=workspace / "missing.json",
                sentiment_cache_dir=workspace / "sentiment_cache",
            ),
            organization_id=headers["X-Organization-Id"],
            output_dir=output_dir,
            dataset_id="dst-empty-sentiment",
        )
        response = client.get("/api/sentiment/dataset", headers=headers, params={"dataset_id": dataset_id})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["headline_count"], 0)
        self.assertEqual(payload["summary"]["returned_headline_count"], 0)
        self.assertEqual(payload["summary"]["daily_rows"], 0)
        self.assertEqual(payload["headlines"], [])
        self.assertEqual(payload["scored_headlines"], [])
        self.assertTrue(any("No headlines are stored" in warning for warning in payload["warnings"]))

    def test_sentiment_accumulate_rejects_empty_symbol_list(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_sentiment_no_symbols")
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
        headers = self.auth_headers(client)

        response = client.post(
            "/api/sentiment/accumulate",
            headers=headers,
            json={
                "symbols": [],
                "start": "2026-04-15",
                "end": "2026-04-29",
                "providers": ["rss"],
                "rss_feed_urls": [],
                "news_files": [],
                "output_dir": str(workspace / "sentiment_shadow"),
                "use_finbert": False,
                "local_finbert_only": True,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Choose at least one symbol", response.json()["detail"])

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
        headers = self.auth_headers(client)

        with (
            patch("pairs_trading.backend.services.RSSHeadlineProvider", EmptyBackendHeadlineProvider),
            patch("pairs_trading.backend.services.build_best_available_sentiment_model", return_value=FixedBackendSentimentModel()),
        ):
            response = client.post(
                "/api/sentiment/accumulate",
                headers=headers,
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

    def test_sentiment_routes_warn_when_latest_run_fetches_no_rows_from_nonempty_cache(self) -> None:
        from pairs_trading.backend.app import create_app
        from pairs_trading.backend.config import BackendSettings

        workspace = fresh_test_dir("artifacts/test_backend_sentiment_empty_latest_run")
        output_dir = workspace / "sentiment_shadow"
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {
                "timestamp": ["2026-04-24T09:00:00"],
                "ticker": ["AAPL"],
                "headline": ["Apple raises guidance"],
                "source": ["unit_news"],
                "url": ["https://example.com/aapl"],
                "relevance": [1.0],
            }
        ).to_parquet(output_dir / "raw_headlines.parquet", index=False)

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
        headers = self.auth_headers(client)

        with (
            patch("pairs_trading.backend.services.RSSHeadlineProvider", EmptyBackendHeadlineProvider),
            patch("pairs_trading.backend.services.build_best_available_sentiment_model", return_value=FixedBackendSentimentModel()),
        ):
            response = client.post(
                "/api/sentiment/accumulate",
                headers=headers,
                json={
                    "symbols": ["EURUSD"],
                    "start": "2026-04-15",
                    "end": "2026-04-29",
                    "providers": ["rss"],
                    "rss_feed_urls": [],
                    "news_files": [],
                    "output_dir": str(output_dir),
                    "use_finbert": False,
                    "local_finbert_only": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["metadata"]["fetched_headlines"], 0)
        self.assertEqual(payload["summary"]["headline_count"], 1)
        self.assertTrue(any("No new headlines were fetched" in warning for warning in payload["warnings"]))

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
        headers = self.auth_headers(client)

        with (
            patch("pairs_trading.backend.services.BenzingaNewsProvider", FailingBackendHeadlineProvider),
            patch("pairs_trading.backend.services.build_best_available_sentiment_model", return_value=FixedBackendSentimentModel()),
        ):
            response = client.post(
                "/api/sentiment/accumulate",
                headers=headers,
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
