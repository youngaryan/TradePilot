from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest
import yaml

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.secrets import SecretProvider


ROOT = Path(__file__).resolve().parents[1]


def _production_settings(**overrides: object) -> BackendSettings:
    values: dict[str, object] = {
        "app_env": "production",
        "app_base_url": "https://tradepilot.example",
        "database_url": "postgresql://database.example/tradepilot",
        "redis_url": "rediss://redis.example/0",
        "session_secret": "s" * 40,
        "csrf_secret": "c" * 40,
        "mfa_encryption_key": "m" * 40,
        "enable_demo_accounts": False,
        "enable_in_process_jobs": False,
        "cookie_secure": True,
        "cors_origins": ("https://tradepilot.example",),
        "trusted_hosts": ("tradepilot.example",),
        "hsts_enabled": True,
        "hsts_max_age_seconds": 31_536_000,
        "stripe_secret_key": "stripe-secret",
        "stripe_webhook_secret": "stripe-webhook",
        "stripe_price_pro_monthly": "price_pro",
        "s3_endpoint_url": "https://s3.example",
        "s3_bucket": "tradepilot",
        "s3_access_key_id": "access-id",
        "s3_secret_access_key": "access-secret",
        "smtp_host": "smtp.example",
        "smtp_port": 587,
        "email_from": "system@tradepilot.example",
        "market_research_data_provider": "cached_yahoo",
        "market_research_allow_demo_fallback": False,
        "market_research_llm_provider": "openai",
    }
    values.update(overrides)
    return BackendSettings(**values)  # type: ignore[arg-type]


def test_production_compose_has_external_immutable_images_and_no_bundled_stateful_services() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.production.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert set(services) == {"migrate", "api", "worker", "job-control", "web"}
    assert not {"postgres", "redis", "minio"}.intersection(services)
    for name, service in services.items():
        assert "build" not in service
        assert ":latest" not in service["image"]
        assert "@${" in service["image"]
        assert "DIGEST" in service["image"]
        if name != "web":
            assert service.get("ports") is None
    assert services["web"]["ports"] == ["${WEB_BIND_ADDRESS:-0.0.0.0}:${WEB_PORT:?WEB_PORT is required}:8080"]
    assert services["migrate"]["command"] == ["/app/scripts/run-migrations.sh"]
    assert services["migrate"]["restart"] == "no"
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"


def test_production_compose_hardens_roles_and_preserves_controlled_egress() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.production.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert compose["networks"]["backend"]["internal"] is True
    assert compose["networks"]["egress"] is None
    assert set(services["web"]["networks"]) == {"edge", "backend"}
    for name in ("api", "worker", "job-control"):
        service = services[name]
        assert set(service["networks"]) == {"backend", "egress"}
        assert service["environment"]["APP_ENV"] == "production"
        assert service["read_only"] is True
        assert "no-new-privileges:true" in service["security_opt"]
        assert service["cap_drop"] == ["ALL"]
        assert service["init"] is True
        assert service["restart"] == "unless-stopped"
        assert service["pids_limit"] > 0
        assert service["mem_limit"]
        assert service["cpus"]
        assert service["healthcheck"]
    assert services["migrate"]["networks"] == ["egress"]
    assert services["api"]["environment"]["FORWARDED_ALLOW_IPS"] == "*"
    assert services["api"]["environment"]["PROMETHEUS_MULTIPROC_DIR"] == "/tmp/tradepilot-prometheus"
    assert "PROMETHEUS_MULTIPROC_DIR" not in services["worker"]["environment"]
    assert services["worker"]["environment"]["OBSERVABILITY_METRICS_PORT"] == "9101"
    assert services["job-control"]["environment"]["OBSERVABILITY_METRICS_PORT"] == "9102"
    for name in ("api", "worker", "job-control"):
        assert services[name]["environment"]["SENTRY_DSN_FILE"] == "/run/secrets/sentry_dsn"
        assert services[name]["environment"]["SENTRY_REQUIRED"] == "true"
        assert "sentry_dsn" in services[name]["secrets"]
    nginx = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")
    assert "proxy_set_header X-Forwarded-For $trusted_forwarded_for" in nginx
    assert '"" $remote_addr' in nginx
    assert "proxy_set_header X-Forwarded-Proto $trusted_forwarded_proto" in nginx
    assert "$proxy_add_x_forwarded_for" not in nginx


def test_production_compose_provisions_only_bounded_ephemeral_writable_paths() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.production.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    for name in ("api", "worker"):
        tmpfs = "\n".join(services[name]["tmpfs"])
        assert "/tmp:" in tmpfs
        assert "/app/artifacts:" in tmpfs
        assert "/app/data:" in tmpfs
        assert "size=" in tmpfs
        assert services[name].get("volumes") is None
    assert all(service.get("user") not in {None, "0", "0:0", "root"} for service in services.values())
    assert all("ports" not in services[name] for name in ("api", "worker", "job-control", "migrate"))


def test_container_builds_set_explicit_non_root_runtime_users_and_web_security() -> None:
    api = (ROOT / "Dockerfile.api").read_text(encoding="utf-8")
    web = (ROOT / "Dockerfile.web").read_text(encoding="utf-8")
    nginx = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")
    assert "USER ${APP_UID}:${APP_GID}" in api
    assert "USER 101:101" in web
    assert "listen 8080" in nginx
    assert "Strict-Transport-Security" in nginx
    assert "server_tokens off" in nginx
    assert "location = /healthz" in nginx


def test_production_preflight_enforces_digest_shape_without_disclosing_secret_paths(tmp_path: Path) -> None:
    secret = tmp_path / "deployment-secret"
    secret.write_text("not-a-real-secret", encoding="utf-8")
    environment = {
        "API_IMAGE_REPOSITORY": "registry.example/tradepilot-api",
        "API_IMAGE_DIGEST": "sha256:" + "a" * 64,
        "WEB_IMAGE_REPOSITORY": "registry.example/tradepilot-web",
        "WEB_IMAGE_DIGEST": "sha256:" + "b" * 64,
        "APP_BASE_URL": "https://tradepilot.example",
        "CORS_ORIGINS": "https://tradepilot.example",
        "TRUSTED_HOSTS": "tradepilot.example",
        "API_HEALTH_HOST": "tradepilot.example",
        "S3_ENDPOINT_URL": "https://s3.example",
        "S3_BUCKET": "tradepilot",
        "SMTP_HOST": "smtp.example",
        "EMAIL_FROM": "system@tradepilot.example",
        "STRIPE_PRICE_PRO_MONTHLY": "price_pro",
        "STRIPE_SUCCESS_URL": "https://tradepilot.example/billing/success",
        "STRIPE_CANCEL_URL": "https://tradepilot.example/billing/cancel",
        "PAIRS_TRADING_MARKET_RESEARCH_LLM_PROVIDER": "openai",
        "PAIRS_TRADING_MARKET_RESEARCH_LLM_MODEL": "provider-model",
        "WEB_PORT": "8443",
        "APP_RELEASE": "test-release",
        **{
            name: str(secret)
            for name in (
                "DATABASE_URL_SECRET_FILE",
                "REDIS_URL_SECRET_FILE",
                "SESSION_SECRET_FILE",
                "CSRF_SECRET_FILE",
                "MFA_ENCRYPTION_KEY_FILE",
                "S3_ACCESS_KEY_ID_FILE",
                "S3_SECRET_ACCESS_KEY_FILE",
                "SMTP_USERNAME_FILE",
                "SMTP_PASSWORD_FILE",
                "STRIPE_SECRET_KEY_FILE",
                "STRIPE_WEBHOOK_SECRET_FILE",
                "MARKET_RESEARCH_API_KEY_FILE",
                "SENTRY_DSN_SECRET_FILE",
                "OBSERVABILITY_METRICS_TOKEN_SECRET_FILE",
            )
        },
    }
    command = [sys.executable, str(ROOT / "scripts/validate-production-deployment.py")]
    valid = subprocess.run(command, env=environment, text=True, capture_output=True, check=False)
    assert valid.returncode == 0
    invalid_environment = {**environment, "API_IMAGE_DIGEST": "release-candidate"}
    invalid = subprocess.run(command, env=invalid_environment, text=True, capture_output=True, check=False)
    assert invalid.returncode == 1
    assert "API_IMAGE_DIGEST" in invalid.stderr
    assert str(secret) not in invalid.stderr
    assert "not-a-real-secret" not in invalid.stderr


def test_sensitive_settings_support_files_without_exposing_content(tmp_path: Path) -> None:
    values = {
        "DATABASE_URL": "postgresql://secret-database",
        "REDIS_URL": "rediss://secret-redis",
        "SESSION_SECRET": "session-from-file",
        "CSRF_SECRET": "csrf-from-file",
        "MFA_ENCRYPTION_KEY": "mfa-encryption-from-file",
        "S3_ACCESS_KEY_ID": "s3-id-from-file",
        "S3_SECRET_ACCESS_KEY": "s3-secret-from-file",
        "SMTP_USERNAME": "smtp-user-from-file",
        "SMTP_PASSWORD": "smtp-secret-from-file",
        "STRIPE_SECRET_KEY": "stripe-from-file",
        "STRIPE_WEBHOOK_SECRET": "webhook-from-file",
        "SENTRY_DSN": "https://sentry-secret.example/1",
        "OBSERVABILITY_METRICS_TOKEN": "metrics-token-from-file-1234567890",
    }
    environment: dict[str, str] = {}
    for name, value in values.items():
        path = tmp_path / name.lower()
        path.write_text(f"{value}\n", encoding="utf-8")
        environment[f"{name}_FILE"] = str(path)
    with patch.dict(os.environ, environment, clear=True):
        settings = BackendSettings.from_env()
    assert settings.database_url == values["DATABASE_URL"]
    assert settings.redis_url == values["REDIS_URL"]
    assert settings.session_secret == values["SESSION_SECRET"]
    assert settings.csrf_secret == values["CSRF_SECRET"]
    assert settings.mfa_encryption_key == values["MFA_ENCRYPTION_KEY"]
    assert settings.s3_access_key_id == values["S3_ACCESS_KEY_ID"]
    assert settings.s3_secret_access_key == values["S3_SECRET_ACCESS_KEY"]
    assert settings.observability_metrics_token == values["OBSERVABILITY_METRICS_TOKEN"]
    assert settings.smtp_username == values["SMTP_USERNAME"]
    assert settings.smtp_password == values["SMTP_PASSWORD"]
    assert settings.stripe_secret_key == values["STRIPE_SECRET_KEY"]
    assert settings.stripe_webhook_secret == values["STRIPE_WEBHOOK_SECRET"]
    assert settings.sentry_dsn == values["SENTRY_DSN"]


def test_secret_file_conflict_and_failure_messages_never_disclose_values_or_paths(tmp_path: Path) -> None:
    secret_path = tmp_path / "very-sensitive-location"
    secret_path.write_text("never-print-this-value", encoding="utf-8")
    with patch.dict(
        os.environ,
        {"DATABASE_URL": "never-print-direct-value", "DATABASE_URL_FILE": str(secret_path)},
        clear=True,
    ):
        with pytest.raises(RuntimeError) as raised:
            BackendSettings.from_env()
    message = str(raised.value)
    assert "DATABASE_URL" in message
    assert "never-print" not in message
    assert str(secret_path) not in message

    missing_path = tmp_path / "missing-secret"
    with patch.dict(os.environ, {"REDIS_URL_FILE": str(missing_path)}, clear=True):
        with pytest.raises(RuntimeError) as missing:
            BackendSettings.from_env()
    assert "REDIS_URL" in str(missing.value)
    assert str(missing_path) not in str(missing.value)


def test_llm_env_secret_reference_resolves_matching_file(tmp_path: Path) -> None:
    secret_path = tmp_path / "provider-key"
    secret_path.write_text("provider-secret-from-file\n", encoding="utf-8")
    with patch.dict(os.environ, {"OPENAI_API_KEY_FILE": str(secret_path)}, clear=True):
        resolved = SecretProvider(BackendSettings(app_env="production")).resolve("env:OPENAI_API_KEY")
    assert resolved == "provider-secret-from-file"


def test_production_trusted_hosts_and_hsts_are_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="TRUSTED_HOSTS"):
        _production_settings(trusted_hosts=()).validate_for_startup()
    with pytest.raises(RuntimeError, match="universal wildcard"):
        _production_settings(trusted_hosts=("*",)).validate_for_startup()
    with pytest.raises(RuntimeError, match="HSTS_ENABLED"):
        _production_settings(hsts_enabled=False).validate_for_startup()
    with pytest.raises(RuntimeError, match="HSTS_MAX_AGE_SECONDS"):
        _production_settings(hsts_max_age_seconds=60).validate_for_startup()


def test_security_middleware_rejects_untrusted_hosts_and_emits_hsts() -> None:
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    from pairs_trading.backend.security import install_security_middleware

    app = fastapi.FastAPI()

    @app.get("/probe")
    def probe() -> dict[str, bool]:
        return {"ok": True}

    install_security_middleware(
        app,
        BackendSettings(
            app_env="production",
            trusted_hosts=("tradepilot.example",),
            hsts_enabled=True,
            hsts_max_age_seconds=31_536_000,
            rate_limit_enabled=False,
        ),
    )
    with testclient.TestClient(app, base_url="https://tradepilot.example") as client:
        response = client.get("/probe")
        rejected = client.get("/probe", headers={"Host": "attacker.example"})
    assert response.status_code == 200
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert rejected.status_code == 400


def test_production_documentation_covers_tls_secrets_migrations_and_rollback() -> None:
    document = (ROOT / "docs/production_deployment.md").read_text(encoding="utf-8").lower()
    for topic in (
        "tls",
        "secret",
        "sha256",
        "alembic upgrade head",
        "rollback",
        "read-only root",
        "ephemeral",
        "s3-compatible",
        "egress",
    ):
        assert topic in document
