from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
import shutil
import unittest

from tests.common import fresh_test_dir

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None


def secure_test_settings(workspace: Path, **overrides):
    from pairs_trading.backend.config import BackendSettings

    values = dict(
        paper_state_dir=workspace / "state",
        paper_artifact_root=workspace / "runs",
        paper_job_state_dir=workspace / "paper_jobs",
        backtest_job_state_dir=workspace / "backtest_jobs",
        sentiment_job_state_dir=workspace / "sentiment_jobs",
        metadata_db_path=workspace / "metadata.sqlite3",
        default_paper_config=workspace / "missing.json",
        sentiment_cache_dir=workspace / "sentiment_cache",
    )
    values.update(overrides)
    return BackendSettings(**values)


@unittest.skipIf(TestClient is None, "FastAPI backend dependencies are not installed.")
class SecureV1HardeningTests(unittest.TestCase):
    def test_artifact_and_system_routes_require_auth(self) -> None:
        from pairs_trading.backend.app import create_app

        workspace = fresh_test_dir("artifacts/test_secure_v1_public_routes")
        client = TestClient(create_app(secure_test_settings(workspace)))

        self.assertEqual(client.get("/api/paper/summary").status_code, 401)
        self.assertEqual(client.get("/api/sentiment/dataset").status_code, 401)
        self.assertEqual(client.get("/api/sentiment/financial-events?symbols=AAPL&start=2024-01-01&end=2024-01-31").status_code, 401)
        self.assertEqual(client.get("/api/system/metadata").status_code, 401)

    def test_cookie_session_and_csrf_guard_mutating_routes(self) -> None:
        from pairs_trading.backend.app import create_app

        workspace = fresh_test_dir("artifacts/test_secure_v1_cookie_csrf")
        client = TestClient(create_app(secure_test_settings(workspace)))

        login = client.post("/api/auth/login", json={"email": "demo@quantops.local", "password": "quantops-demo"})
        self.assertEqual(login.status_code, 200)
        self.assertIn("quantops_session", client.cookies)
        csrf = client.cookies.get("quantops_csrf")
        self.assertTrue(csrf)

        missing_csrf = client.post("/api/workspaces/projects", json={"name": "Blocked Project"})
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(missing_csrf.json()["detail"]["code"], "csrf_required")

        created = client.post("/api/workspaces/projects", headers={"X-CSRF-Token": csrf}, json={"name": "Allowed Project"})
        self.assertEqual(created.status_code, 201)

    def test_expired_sessions_are_rejected_and_purged(self) -> None:
        from pairs_trading.backend.saas import AuthService
        from pairs_trading.platform import SQLiteMetadataStore

        workspace = fresh_test_dir("artifacts/test_secure_v1_expired_sessions")
        settings = secure_test_settings(workspace)
        store = SQLiteMetadataStore(settings.metadata_db_path, enable_demo_accounts=settings.enable_demo_accounts)
        user = store.get_user_by_email("demo@quantops.local")
        assert user is not None
        token = "expired-session-token"
        expired_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        store.create_auth_session(user_id=str(user["id"]), token=token, expires_at_utc=expired_at)

        with self.assertRaises(ValueError):
            AuthService(settings).authenticate(token=token)
        self.assertIsNone(store.get_auth_session(token=token))

    def test_production_demo_accounts_and_stripe_bypass_are_disabled(self) -> None:
        from pairs_trading.backend.saas import BillingService
        from pairs_trading.backend.schemas import BillingCheckoutRequest
        from pairs_trading.platform import SQLiteMetadataStore

        workspace = fresh_test_dir("artifacts/test_secure_v1_production_fail_closed")
        settings = secure_test_settings(
            workspace,
            app_env="production",
            app_base_url="https://quantops.example",
            database_url="postgresql+psycopg://user:pass@db:5432/app",
            redis_url="redis://redis:6379/0",
            session_secret="prod-session-secret",
            csrf_secret="prod-csrf-secret",
            enable_demo_accounts=False,
            enable_in_process_jobs=False,
            s3_endpoint_url="https://s3.example",
            s3_bucket="quantops",
            s3_access_key_id="access",
            s3_secret_access_key="secret",
        )
        store = SQLiteMetadataStore(settings.metadata_db_path, enable_demo_accounts=settings.enable_demo_accounts)
        self.assertEqual(store.counts().users, 0)
        with self.assertRaises(ValueError):
            BillingService(settings).checkout(
                organization_id="org_prod_fake",
                request=BillingCheckoutRequest(plan="pro", price_id="client_supplied_price"),
            )
        with self.assertRaises(RuntimeError):
            BillingService(settings).checkout(
                organization_id="org_prod_fake",
                request=BillingCheckoutRequest(plan="pro"),
            )

    def test_production_path_and_url_validation_blocks_unsafe_inputs(self) -> None:
        from pairs_trading.backend.schemas import BacktestRunRequest, SentimentAccumulationRequest
        from pairs_trading.backend.services import BacktestService, SentimentService

        workspace = fresh_test_dir("artifacts/test_secure_v1_validation")
        settings = secure_test_settings(
            workspace,
            app_env="production",
            database_url="postgresql+psycopg://user:pass@db:5432/app",
            enable_demo_accounts=False,
            enable_in_process_jobs=False,
            s3_bucket="quantops",
            s3_endpoint_url="https://s3.example",
            s3_access_key_id="access",
            s3_secret_access_key="secret",
        )

        with self.assertRaises(ValueError):
            BacktestService(settings).validate_request(
                BacktestRunRequest(
                    pipeline="ema_cross",
                    symbols=["SPY"],
                    artifact_root=Path("C:/unsafe/artifacts"),
                )
            )

        with self.assertRaises(ValueError):
            SentimentService(settings).validate_request(
                SentimentAccumulationRequest(
                    symbols=["SPY"],
                    providers=["rss"],
                    rss_feed_urls=["http://localhost:8000/rss"],
                )
            )


    def test_email_password_reset_and_totp_mfa_flows_are_real(self) -> None:
        from pairs_trading.backend.saas import AuthService, totp_code
        from pairs_trading.backend.schemas import SignupRequest

        shutil.rmtree("artifacts/email_outbox", ignore_errors=True)
        workspace = fresh_test_dir("artifacts/test_secure_v1_auth_lifecycle")
        settings = secure_test_settings(workspace)
        auth = AuthService(settings)
        auth.signup(
            SignupRequest(
                email="newuser@example.com",
                password="old-password",
                display_name="New User",
                organization_name="New User Lab",
            )
        )
        verification_email = max(Path("artifacts/email_outbox").glob("*.json"), key=lambda path: path.stat().st_mtime)
        verification_token = re.search(r"token=([A-Za-z0-9_-]+)", verification_email.read_text(encoding="utf-8"))
        self.assertIsNotNone(verification_token)
        verified = auth.verify_email(token=verification_token.group(1))
        self.assertEqual(verified["status"], "verified")

        auth.request_password_reset(email="newuser@example.com")
        reset_email = max(Path("artifacts/email_outbox").glob("*.json"), key=lambda path: path.stat().st_mtime)
        reset_token = re.search(r"token=([A-Za-z0-9_-]+)", reset_email.read_text(encoding="utf-8"))
        self.assertIsNotNone(reset_token)
        self.assertEqual(auth.confirm_password_reset(token=reset_token.group(1), new_password="new-password")["status"], "updated")
        self.assertEqual(auth.login(email="newuser@example.com", password="new-password")["user"]["email"], "newuser@example.com")

        user = auth.store.get_user_by_email("newuser@example.com")
        assert user is not None
        setup = auth.setup_mfa(user_id=str(user["id"]))
        self.assertTrue(setup["secret"])
        self.assertEqual(auth.verify_mfa_code(user_id=str(user["id"]), code=totp_code(setup["secret"]))["status"], "verified")

    def test_quota_service_blocks_over_limit_premium_jobs(self) -> None:
        from pairs_trading.backend.quotas import QuotaExceeded, QuotaService
        from pairs_trading.backend.saas import hash_password
        from pairs_trading.platform import SQLiteMetadataStore

        workspace = fresh_test_dir("artifacts/test_secure_v1_quotas")
        settings = secure_test_settings(workspace)
        store = SQLiteMetadataStore(settings.metadata_db_path, enable_demo_accounts=False)
        payload = store.create_user_workspace(
            email="quota@example.com",
            display_name="Quota User",
            password_hash=hash_password("safe-password"),
            organization_name="Quota Lab",
            plan="pro",
            subscription_status="active",
        )
        org_id = str(payload["organization_id"])
        store.upsert_organization_quotas(organization_id=org_id, quotas={"backtest_job": 1})
        quotas = QuotaService(settings)
        quotas.check_and_record(organization_id=org_id, feature="backtest_job", role="user")
        with self.assertRaises(QuotaExceeded):
            quotas.check_and_record(organization_id=org_id, feature="backtest_job", role="user")
        admin_allowance = quotas.check_and_record(organization_id=org_id, feature="backtest_job", role="admin")
        self.assertTrue(admin_allowance["allowance"]["bypassed"])
        self.assertIsNone(admin_allowance["usage"])

    def test_postgres_metadata_store_is_selected_lazily_for_production(self) -> None:
        from pairs_trading.platform import PostgresMetadataStore, build_metadata_store

        workspace = fresh_test_dir("artifacts/test_secure_v1_postgres_factory")
        settings = secure_test_settings(
            workspace,
            app_env="production",
            database_url="postgresql+psycopg://user:pass@db:5432/app",
            enable_demo_accounts=False,
        )
        store = build_metadata_store(settings)
        self.assertIsInstance(store, PostgresMetadataStore)

    def test_docker_api_runs_migrations_before_gunicorn(self) -> None:
        dockerfile = Path("Dockerfile.api").read_text(encoding="utf-8")
        entrypoint = Path("scripts/docker-entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("docker-entrypoint.sh", dockerfile)
        self.assertIn("gunicorn", entrypoint)
        self.assertIn("alembic upgrade head", entrypoint)
        self.assertIn("uvicorn.workers.UvicornWorker", entrypoint)


if __name__ == "__main__":
    unittest.main()
