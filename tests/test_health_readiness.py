from __future__ import annotations

from pathlib import Path
import sqlite3
from time import monotonic, sleep
from types import SimpleNamespace
import sys
from unittest.mock import Mock, patch

import pytest
import yaml

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.readiness import (
    DependencyProbeError,
    ProbeResult,
    ReadinessChecker,
    _expected_alembic_heads,
    _probe_postgres,
    _probe_sqlite,
)
from pairs_trading.backend.storage import S3ArtifactStorage


def _configured_settings(**overrides: object) -> BackendSettings:
    values: dict[str, object] = {
        "database_url": "postgresql://db-user:db-password@database.invalid/tradepilot",
        "redis_url": "redis://redis-user:redis-password@redis.invalid/0",
        "s3_endpoint_url": "https://object-user:object-password@objects.invalid",
        "s3_bucket": "tradepilot-artifacts",
        "s3_access_key_id": "access-key",
        "s3_secret_access_key": "secret-key",
    }
    values.update(overrides)
    return BackendSettings(**values)  # type: ignore[arg-type]


def _ok(_settings: BackendSettings, _timeout: float) -> ProbeResult:
    return ProbeResult(status="ok", message="dependency reachable")


def test_readiness_success_has_sanitized_component_states_and_latencies() -> None:
    payload = ReadinessChecker(
        _configured_settings(),
        database_probe=_ok,
        redis_probe=_ok,
        s3_probe=_ok,
    ).check()

    assert payload["ready"] is True
    assert payload["status"] == "ready"
    assert set(payload["components"]) == {"database", "redis", "artifact_storage"}
    assert all(component["status"] == "ok" for component in payload["components"].values())
    assert all(isinstance(component["latency_ms"], int) for component in payload["components"].values())
    serialized = repr(payload)
    assert "db-password" not in serialized
    assert "redis-password" not in serialized
    assert "object-password" not in serialized


def test_required_failure_is_503_and_unexpected_error_is_redacted() -> None:
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    from pairs_trading.backend.routers.health import build_health_router

    secret = "postgresql://admin:super-secret@db.invalid/tradepilot"

    def failed(_settings: BackendSettings, _timeout: float) -> ProbeResult:
        raise RuntimeError(secret)

    checker = ReadinessChecker(
        _configured_settings(),
        database_probe=failed,
        redis_probe=_ok,
        s3_probe=_ok,
    )
    app = fastapi.FastAPI()
    app.include_router(build_health_router(_configured_settings(), readiness_checker=checker), prefix="/api")

    response = testclient.TestClient(app).get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["ready"] is False
    assert response.json()["components"]["database"]["message"] == "dependency probe failed"
    assert "super-secret" not in response.text


def test_liveness_and_compatibility_health_do_not_call_readiness_dependencies() -> None:
    fastapi = pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    from pairs_trading.backend.routers.health import build_health_router

    checker = Mock()
    checker.check.side_effect = AssertionError("readiness must not run")
    app = fastapi.FastAPI()
    app.include_router(build_health_router(BackendSettings(), readiness_checker=checker), prefix="/api")
    client = testclient.TestClient(app)

    for path in ("/api/health", "/api/health/live"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "pairs-trading-backend"}
    checker.check.assert_not_called()


def test_unconfigured_development_dependencies_are_degraded_without_probe_calls() -> None:
    probe = Mock(side_effect=AssertionError("unconfigured dependencies must not be probed"))
    payload = ReadinessChecker(
        BackendSettings(),
        database_probe=probe,
        redis_probe=probe,
        s3_probe=probe,
    ).check()

    assert payload["ready"] is True
    assert payload["status"] == "degraded"
    assert all(component["status"] == "degraded" for component in payload["components"].values())
    probe.assert_not_called()


def test_unconfigured_production_dependencies_fail_closed() -> None:
    payload = ReadinessChecker(BackendSettings(app_env="production")).check()

    assert payload["ready"] is False
    assert payload["status"] == "not_ready"
    assert all(component["required"] is True for component in payload["components"].values())
    assert all(component["status"] == "error" for component in payload["components"].values())


def test_readiness_timeout_is_bounded_and_reported_without_waiting_for_probe_shutdown() -> None:
    def slow(_settings: BackendSettings, _timeout: float) -> ProbeResult:
        sleep(0.25)
        return ProbeResult(status="ok", message="late")

    started = monotonic()
    payload = ReadinessChecker(
        _configured_settings(),
        timeout_seconds=0.05,
        database_probe=slow,
        redis_probe=_ok,
        s3_probe=_ok,
    ).check()
    elapsed = monotonic() - started

    assert elapsed < 0.20
    assert payload["ready"] is False
    assert payload["components"]["database"]["message"] == "dependency probe timed out"


def test_postgres_probe_uses_only_selects_and_checks_alembic_head() -> None:
    statements: list[str] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, statement: str) -> None:
            statements.append(statement)

        def fetchone(self) -> tuple[int]:
            return (1,)

        def fetchall(self) -> list[tuple[str]]:
            return [("0006_paper_deployments_runs",)]

    class Connection:
        closed = False

        def cursor(self) -> Cursor:
            return Cursor()

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    connect = Mock(return_value=connection)
    with (
        patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=connect)}),
        patch("pairs_trading.backend.readiness._expected_alembic_heads", return_value={"0006_paper_deployments_runs"}),
    ):
        _probe_postgres("postgresql+psycopg://user:secret@db.invalid/app", 0.5)

    assert statements == ["SELECT 1", "SELECT version_num FROM alembic_version"]
    assert all(token not in " ".join(statements).upper() for token in ("CREATE", "ALTER", "INSERT", "UPDATE", "DELETE"))
    options = connect.call_args.kwargs["options"]
    assert "default_transaction_read_only=on" in options
    assert "statement_timeout=500" in options
    assert "lock_timeout=500" in options
    assert connection.closed is True


def test_postgres_probe_rejects_schema_revision_mismatch() -> None:
    class Cursor:
        calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, _statement: str) -> None:
            self.calls += 1

        def fetchone(self) -> tuple[int]:
            return (1,)

        def fetchall(self) -> list[tuple[str]]:
            return [("0001_secure_v1_metadata",)]

    connection = SimpleNamespace(cursor=lambda: Cursor(), close=lambda: None)
    with (
        patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=lambda *_args, **_kwargs: connection)}),
        patch("pairs_trading.backend.readiness._expected_alembic_heads", return_value={"0006_paper_deployments_runs"}),
    ):
        with pytest.raises(DependencyProbeError, match="migration head"):
            _probe_postgres("postgresql://db.invalid/app", 0.5)


def test_repository_alembic_head_is_resolved_without_running_migrations() -> None:
    pytest.importorskip("alembic")

    assert _expected_alembic_heads() == {"0009_strategy_marketplace"}


def test_sqlite_probe_opens_existing_database_read_only(tmp_path: Path) -> None:
    database_path = tmp_path / "readiness.sqlite3"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO alembic_version(version_num) VALUES (?)",
            ("0006_paper_deployments_runs",),
        )
        connection.commit()
    finally:
        connection.close()
    before = database_path.read_bytes()

    with patch(
        "pairs_trading.backend.readiness._expected_alembic_heads",
        return_value={"0006_paper_deployments_runs"},
    ):
        _probe_sqlite(f"sqlite:///{database_path}", 0.25)

    assert database_path.read_bytes() == before


def test_s3_probe_is_head_only_and_never_creates_a_bucket() -> None:
    client = Mock()
    config_options: list[dict[str, object]] = []
    storage = S3ArtifactStorage(
        bucket="existing-bucket",
        endpoint_url="https://objects.invalid",
        access_key_id="access",
        secret_access_key="secret",
    )

    def fake_config(**kwargs: object) -> dict[str, object]:
        config_options.append(kwargs)
        return kwargs

    fake_config_module = SimpleNamespace(Config=fake_config)
    with (
        patch.dict(sys.modules, {"botocore": SimpleNamespace(config=fake_config_module), "botocore.config": fake_config_module}),
        patch.object(S3ArtifactStorage, "_client", return_value=client),
    ):
        storage.probe_bucket(timeout_seconds=0.25)

    client.head_bucket.assert_called_once_with(Bucket="existing-bucket")
    client.create_bucket.assert_not_called()
    assert config_options == [
        {
            "connect_timeout": 0.25,
            "read_timeout": 0.25,
            "retries": {"max_attempts": 0},
        }
    ]


@pytest.mark.parametrize("compose_path", ["docker-compose.yml", "docker-compose.shared.yml"])
def test_compose_health_topology_uses_readiness_and_explicit_initializers(compose_path: str) -> None:
    services = yaml.safe_load(Path(compose_path).read_text(encoding="utf-8"))["services"]

    assert "/api/health/ready" in " ".join(services["api"]["healthcheck"]["test"])
    assert services["worker"]["healthcheck"]["test"][-1] == "--healthcheck"
    assert services["job-control"]["healthcheck"]["test"][-1] == "--healthcheck"
    assert services["web"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["api"]["depends_on"]["minio-init"]["condition"] == "service_completed_successfully"
    assert services["minio-init"]["restart"] == "no"
    assert "/minio/health/ready" in " ".join(services["minio"]["healthcheck"]["test"])
    assert "mc mb --ignore-existing" in services["minio-init"]["command"][0]
    assert "create_bucket" not in " ".join(services["api"]["healthcheck"]["test"])


def test_ci_real_infrastructure_gate_cannot_initialize_schema_or_silently_skip() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "initialize=False" in workflow
    assert 'REQUIRE_REAL_INTEGRATION: "true"' in workflow
    assert "pytest -q --strict-markers -m integration" in workflow
