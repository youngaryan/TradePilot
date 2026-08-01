from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from unittest.mock import patch

import pytest

from apps.worker import job_control, rq_worker
from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.readiness import (
    check_any_role_instance_from_settings,
    check_role_heartbeat,
    publish_role_heartbeat,
    role_heartbeat_key,
    role_heartbeat_stale_after,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    def set(self, key: str, value: str, *, ex: int) -> None:
        self.values[key] = value
        self.expiries[key] = ex

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def scan_iter(self, *, match: str, count: int):
        del count
        prefix = match.removesuffix("*")
        yield from (key for key in self.values if key.startswith(prefix))


def test_role_heartbeat_is_minimal_ttl_backed_and_fresh() -> None:
    settings = BackendSettings(job_heartbeat_seconds=10, job_recovery_poll_seconds=20)
    redis = FakeRedis()
    now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

    publish_role_heartbeat(redis, settings, role="worker", now=now)
    result = check_role_heartbeat(redis, settings, role="worker", now=now + timedelta(seconds=5))

    key = role_heartbeat_key("worker")
    assert result == {"role": "worker", "healthy": True, "status": "healthy", "age_seconds": 5.0}
    assert redis.expiries[key] == role_heartbeat_stale_after(settings, "worker")
    assert json.loads(redis.values[key]) == {
        "role": "worker",
        "updated_at_utc": "2026-08-01T12:00:00Z",
    }


def test_old_or_missing_role_heartbeat_is_unhealthy() -> None:
    settings = BackendSettings(job_heartbeat_seconds=10)
    redis = FakeRedis()
    now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

    assert check_role_heartbeat(redis, settings, role="worker", now=now)["status"] == "missing"
    publish_role_heartbeat(redis, settings, role="worker", now=now - timedelta(seconds=31))
    result = check_role_heartbeat(redis, settings, role="worker", now=now)

    assert result["healthy"] is False
    assert result["status"] == "stale"


def test_invalid_heartbeat_payload_is_sanitized() -> None:
    redis = FakeRedis()
    redis.values[role_heartbeat_key("controller")] = "redis://user:password@redis.invalid broken"

    result = check_role_heartbeat(redis, BackendSettings(), role="controller")

    assert result == {"role": "controller", "healthy": False, "status": "invalid"}
    assert "password" not in repr(result)


def test_heartbeat_keys_are_instance_isolated_and_role_payload_must_match() -> None:
    settings = BackendSettings()
    redis = FakeRedis()
    now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

    first_key = role_heartbeat_key("worker", instance_id="container-a")
    second_key = role_heartbeat_key("worker", instance_id="container-b")
    assert first_key != second_key
    publish_role_heartbeat(redis, settings, role="worker", instance_id="container-a", now=now)
    assert check_role_heartbeat(
        redis,
        settings,
        role="worker",
        instance_id="container-b",
        now=now,
    )["status"] == "missing"

    redis.values[first_key] = json.dumps(
        {"role": "controller", "updated_at_utc": "2026-08-01T12:00:00Z"}
    )
    assert check_role_heartbeat(
        redis,
        settings,
        role="worker",
        instance_id="container-a",
        now=now,
    ) == {"role": "worker", "healthy": False, "status": "invalid"}


def test_central_role_status_aggregates_other_container_instances() -> None:
    settings = BackendSettings(redis_url="redis://private", job_heartbeat_seconds=10)
    redis = FakeRedis()
    publish_role_heartbeat(redis, settings, role="worker", instance_id="worker-container", now=datetime.now(UTC))
    with patch("pairs_trading.backend.readiness._redis_client", return_value=redis):
        result = check_any_role_instance_from_settings(settings, role="worker")
    assert result["healthy"] is True
    assert result["instances_seen"] == 1


@pytest.mark.parametrize(
    "module, role",
    [(rq_worker, "worker"), (job_control, "controller")],
)
def test_role_health_cli_exits_nonzero_for_stale_role(module: object, role: str, capsys: pytest.CaptureFixture[str]) -> None:
    settings = BackendSettings(redis_url="redis://user:password@redis.invalid/0")
    with (
        patch.object(module.BackendSettings, "from_env", return_value=settings),
        patch.object(module, "check_role_from_settings", return_value={"role": role, "healthy": False, "status": "stale"}),
        pytest.raises(SystemExit) as raised,
    ):
        module.main(["--healthcheck"])

    assert raised.value.code == 1
    output = capsys.readouterr().out
    assert '"status":"stale"' in output
    assert "password" not in output
