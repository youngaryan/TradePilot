from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest.mock import patch

import pytest
import yaml

from apps.worker.job_control import MAX_CONTROL_BACKOFF_SECONDS, reconcile_queued_jobs, run_forever, run_recovery_pass
from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.job_queue import enqueue_quant_job, quant_rq_job_id


def test_quant_rq_job_id_is_deterministic_per_attempt() -> None:
    assert quant_rq_job_id("backtest", "job-123", 2) == "tradepilot:backtest:job-123:attempt:2"
    assert quant_rq_job_id("backtest", "job-123", 2) == quant_rq_job_id("backtest", "job-123", 2)
    assert quant_rq_job_id("backtest", "job-123", 1) != quant_rq_job_id("backtest", "job-123", 2)
    with pytest.raises(ValueError):
        quant_rq_job_id("unknown", "job-123", 1)
    with pytest.raises(ValueError):
        quant_rq_job_id("paper", "job-123", 0)


def test_job_runtime_settings_are_validated() -> None:
    BackendSettings().validate_job_runtime()
    invalid = (
        (BackendSettings(job_lease_seconds=0), "JOB_LEASE_SECONDS must be positive"),
        (BackendSettings(job_heartbeat_seconds=0), "JOB_HEARTBEAT_SECONDS must be positive"),
        (BackendSettings(job_lease_seconds=60, job_heartbeat_seconds=30), "less than half"),
        (BackendSettings(job_recovery_poll_seconds=0), "JOB_RECOVERY_POLL_SECONDS must be positive"),
        (BackendSettings(job_max_attempts=0), "JOB_MAX_ATTEMPTS must be at least 1"),
        (BackendSettings(job_recovery_batch_size=201), "between 1 and 200"),
    )
    for settings, message in invalid:
        with pytest.raises(RuntimeError, match=message):
            settings.validate_job_runtime()


class _FakeRQJob:
    def __init__(self, job_id: str, status: str) -> None:
        self.id = job_id
        self.status = status
        self.deleted = False

    def get_status(self, *, refresh: bool) -> str:
        assert refresh is True
        return self.status

    def delete(self) -> None:
        self.deleted = True


class _DuplicateJobError(Exception):
    pass


class _JSONSerializer:
    pass


def _rq_modules(queue_type: type[object]) -> dict[str, object]:
    return {
        "redis": SimpleNamespace(Redis=SimpleNamespace(from_url=lambda _url: object())),
        "rq": SimpleNamespace(Queue=queue_type),
        "rq.exceptions": SimpleNamespace(DuplicateJobError=_DuplicateJobError),
        "rq.serializers": SimpleNamespace(JSONSerializer=_JSONSerializer),
    }


def test_enqueue_treats_active_deterministic_rq_job_as_dispatched() -> None:
    expected_id = quant_rq_job_id("paper", "job-active", 1)
    existing = _FakeRQJob(expected_id, "queued")

    class FakeQueue:
        def __init__(self, _name: str, *, connection: object, serializer: object) -> None:
            assert connection is not None
            assert serializer is _JSONSerializer

        def fetch_job(self, job_id: str) -> _FakeRQJob | None:
            assert job_id == expected_id
            return existing

        def enqueue(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("active deterministic job must not be enqueued twice")

    with patch.dict(sys.modules, _rq_modules(FakeQueue)):
        result = enqueue_quant_job(
            BackendSettings(redis_url="redis://test"),
            kind="paper",
            job_id="job-active",
        )

    assert result == {"queue": "quant_jobs", "rq_job_id": expected_id, "already_enqueued": True}


def test_enqueue_uses_atomic_unique_json_dispatch() -> None:
    expected_id = quant_rq_job_id("backtest", "job-new", 2)
    calls: list[dict[str, object]] = []

    class FakeQueue:
        def __init__(self, _name: str, *, connection: object, serializer: object) -> None:
            assert connection is not None
            assert serializer is _JSONSerializer

        def fetch_job(self, _job_id: str) -> None:
            return None

        def enqueue(self, *_args: object, **kwargs: object) -> object:
            calls.append(kwargs)
            return SimpleNamespace(id=expected_id)

    with patch.dict(sys.modules, _rq_modules(FakeQueue)):
        result = enqueue_quant_job(
            BackendSettings(redis_url="redis://test"),
            kind="backtest",
            job_id="job-new",
            attempt=2,
        )

    assert calls == [
        {
            "job_id": expected_id,
            "job_timeout": "2h",
            "result_ttl": 86400,
                "failure_ttl": 86400,
                "unique": True,
                "meta": {"trace_context": {}},
            }
    ]
    assert result["already_enqueued"] is False


def test_duplicate_unique_enqueue_is_idempotent() -> None:
    expected_id = quant_rq_job_id("paper", "job-race", 1)
    winner = _FakeRQJob(expected_id, "queued")

    class FakeQueue:
        fetches = 0

        def __init__(self, _name: str, *, connection: object, serializer: object) -> None:
            assert connection is not None
            assert serializer is _JSONSerializer

        def fetch_job(self, _job_id: str) -> _FakeRQJob | None:
            self.fetches += 1
            return None if self.fetches == 1 else winner

        def enqueue(self, *_args: object, **_kwargs: object) -> object:
            raise _DuplicateJobError()

    with patch.dict(sys.modules, _rq_modules(FakeQueue)):
        result = enqueue_quant_job(BackendSettings(redis_url="redis://test"), kind="paper", job_id="job-race")

    assert result == {"queue": "quant_jobs", "rq_job_id": expected_id, "already_enqueued": True}


def test_worker_uses_the_same_json_serializer() -> None:
    from apps.worker import rq_worker

    observed: dict[str, object] = {}

    class FakeWorker:
        def __init__(self, queues: list[str], *, connection: object, serializer: object) -> None:
            observed.update({"queues": queues, "connection": connection, "serializer": serializer})

        def work(self, *, with_scheduler: bool) -> None:
            observed["with_scheduler"] = with_scheduler

    fake_modules = {
        "redis": SimpleNamespace(Redis=SimpleNamespace(from_url=lambda _url: object())),
        "rq": SimpleNamespace(Worker=FakeWorker),
        "rq.serializers": SimpleNamespace(JSONSerializer=_JSONSerializer),
    }
    settings = BackendSettings(
        enable_in_process_jobs=False,
        redis_url="redis://queue.invalid/0",
        database_url="postgresql://db.invalid/app",
    )
    with (
        patch.dict(sys.modules, fake_modules),
        patch("apps.worker.rq_worker.BackendSettings.from_env", return_value=settings),
    ):
        rq_worker.main()

    assert observed["queues"] == ["quant_jobs"]
    assert observed["serializer"] is _JSONSerializer
    assert observed["with_scheduler"] is True


class _FakeStore:
    def __init__(
        self,
        *,
        jobs: dict[str, list[dict[str, object]]],
        recovered: list[dict[str, object]] | None = None,
        recovered_kinds: dict[str, str] | None = None,
    ) -> None:
        self.jobs = jobs
        self.recovered = recovered or []
        self.recovered_kinds = recovered_kinds or {}
        self.list_calls: list[tuple[str, str | None, int, int]] = []

    def recover_expired_jobs(self, *, now_utc: str, limit: int) -> list[dict[str, object]]:
        assert now_utc
        assert limit > 0
        return list(self.recovered)

    def list_jobs(self, *, kind: str, status: str | None = None, limit: int, offset: int) -> list[dict[str, object]]:
        self.list_calls.append((kind, status, limit, offset))
        rows = self.jobs.get(kind, [])
        if status is not None:
            rows = [payload for payload in rows if payload.get("status") == status]
        return rows[offset : offset + limit]

    def get_job(self, *, kind: str, job_id: str) -> dict[str, object] | None:
        for payload in self.jobs.get(kind, []):
            if payload.get("id") == job_id:
                return payload
        if self.recovered_kinds.get(job_id) == kind:
            return next((payload for payload in self.recovered if payload.get("id") == job_id), None)
        return None


def test_reconcile_is_status_filtered_fair_and_bounded() -> None:
    backtests = [
        {"id": f"job-{index}", "kind": "backtest", "status": "queued", "attempt": 0}
        for index in range(201)
    ]
    backtests.append({"id": "done", "kind": "backtest", "status": "completed", "attempt": 1})
    store = _FakeStore(
        jobs={
            "backtest": backtests,
            "paper": [{"id": "paper-1", "status": "queued", "attempt": 0}],
            "sentiment": [{"id": "sentiment-1", "status": "queued", "attempt": 0}],
            "market_research": [{"id": "research-1", "status": "queued", "attempt": 0}],
        }
    )
    dispatched: list[tuple[str, str, int]] = []

    def enqueue(_settings: BackendSettings, *, kind: str, job_id: str, attempt: int) -> dict[str, object]:
        dispatched.append((kind, job_id, attempt))
        return {"already_enqueued": False}

    summary = reconcile_queued_jobs(
        BackendSettings(job_recovery_batch_size=8),
        store=store,
        enqueue=enqueue,
    )

    assert summary["queued"] == 5
    assert summary["dispatched"] == 5
    assert len(dispatched) == 5
    assert set(attempt for _, _, attempt in dispatched) == {1}
    assert all(status == "queued" and offset == 0 for _, status, _, offset in store.list_calls)
    assert sum(limit for _, _, limit, _ in store.list_calls) == 8


def test_recovery_retries_queued_rows_but_not_terminal_interruptions() -> None:
    recovered = [
        {"id": "retry", "status": "queued", "attempt": 1, "max_attempts": 3},
        {"id": "exhausted", "kind": "paper", "status": "interrupted", "attempt": 3, "max_attempts": 3},
    ]
    store = _FakeStore(jobs={}, recovered=recovered, recovered_kinds={"retry": "sentiment"})
    dispatched: list[tuple[str, str, int]] = []

    def enqueue(_settings: BackendSettings, *, kind: str, job_id: str, attempt: int) -> dict[str, object]:
        dispatched.append((kind, job_id, attempt))
        return {"already_enqueued": False}

    summary = run_recovery_pass(
        BackendSettings(),
        store=store,
        enqueue=enqueue,
        now_utc="2026-08-01T12:00:00Z",
    )

    assert dispatched == [("sentiment", "retry", 2)]
    assert summary == {
        "recovered": 2,
        "redispatched": 1,
        "interrupted": 1,
        "already_enqueued": 0,
        "errors": 0,
    }


def test_dispatch_errors_are_generic_and_logs_do_not_leak_secrets(caplog: pytest.LogCaptureFixture) -> None:
    secret = "redis://queue-user:queue-password@queue.invalid/0"
    store = _FakeStore(jobs={"backtest": [{"id": "job-1", "status": "queued", "attempt": 0}]})

    def failing_enqueue(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError(secret)

    with caplog.at_level(logging.WARNING, logger="pairs_trading.job_control"):
        summary = reconcile_queued_jobs(
            BackendSettings(job_recovery_batch_size=4),
            store=store,
            enqueue=failing_enqueue,
        )

    assert summary["errors"] == 1
    assert secret not in repr(summary)
    assert secret not in caplog.text
    assert "queue-password" not in caplog.text


class _StopAfterWaits:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return len(self.waits) >= self.limit

    def wait(self, delay: float) -> bool:
        self.waits.append(delay)
        return self.is_set()


def test_controller_survives_transient_failures_with_bounded_backoff(caplog: pytest.LogCaptureFixture) -> None:
    settings = BackendSettings(
        enable_in_process_jobs=False,
        redis_url="redis://queue.invalid/0",
        database_url="postgresql://db.invalid/app",
        job_recovery_poll_seconds=20,
    )
    stopped = _StopAfterWaits(6)
    failures = [RuntimeError("postgresql://user:password@db.invalid/app failed")] * 5
    with (
        patch("apps.worker.job_control.run_control_pass", side_effect=[*failures, {"ok": True}]),
        caplog.at_level(logging.INFO, logger="pairs_trading.job_control"),
    ):
        run_forever(settings, stop_event=stopped)  # type: ignore[arg-type]

    assert stopped.waits == [20, 40, 80, 160, MAX_CONTROL_BACKOFF_SECONDS, 20]
    assert "password" not in caplog.text


@pytest.mark.parametrize(
    "settings, message",
    [
        (BackendSettings(redis_url="redis://queue", database_url="postgresql://db/app"), "ENABLE_IN_PROCESS_JOBS"),
        (BackendSettings(enable_in_process_jobs=False, database_url="postgresql://db/app"), "REDIS_URL"),
        (BackendSettings(enable_in_process_jobs=False, redis_url="redis://queue"), "DATABASE_URL"),
    ],
)
def test_external_job_roles_fail_fast(settings: BackendSettings, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        settings.validate_external_job_runtime(role="controller")


def _production_settings(**overrides: object) -> BackendSettings:
    values: dict[str, object] = {
        "app_env": "production",
        "app_base_url": "https://tradepilot.example",
        "database_url": "postgresql://db.example/tradepilot",
        "redis_url": "redis://redis:6379/0",
        "session_secret": "s" * 40,
        "csrf_secret": "c" * 40,
        "enable_demo_accounts": False,
        "enable_in_process_jobs": False,
        "cookie_secure": True,
        "cors_origins": ("https://tradepilot.example",),
        "stripe_secret_key": "sk_live_configured",
        "stripe_webhook_secret": "whsec_configured",
        "stripe_price_pro_monthly": "price_configured",
        "s3_endpoint_url": "https://s3.example",
        "s3_bucket": "tradepilot",
        "s3_access_key_id": "access-key",
        "s3_secret_access_key": "secret-key",
        "smtp_host": "smtp.example",
        "smtp_port": 587,
        "email_from": "system@tradepilot.example",
        "market_research_data_provider": "cached_yahoo",
        "market_research_allow_demo_fallback": False,
    }
    values.update(overrides)
    return BackendSettings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"session_secret": "replace-with-random-session-secret-at-least-32-characters"}, "SESSION_SECRET"),
        ({"csrf_secret": "short"}, "CSRF_SECRET"),
        ({"session_secret": "x" * 40, "csrf_secret": "x" * 40}, "distinct"),
    ],
)
def test_production_rejects_placeholder_weak_or_equal_secrets(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        _production_settings(**overrides).validate_for_startup()


@pytest.mark.parametrize("compose_path", ["docker-compose.yml", "docker-compose.shared.yml"])
def test_compose_uses_one_migration_owner_and_external_queue_topology(compose_path: str) -> None:
    compose = yaml.safe_load(Path(compose_path).read_text(encoding="utf-8"))
    services = compose["services"]
    assert "ports" not in services["redis"]
    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]
    assert services["migrate"]["restart"] == "no"
    for name in ("api", "worker", "job-control"):
        service = services[name]
        assert service["environment"]["ENABLE_IN_PROCESS_JOBS"] == "false"
        assert service["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
        assert service["restart"] == "unless-stopped"
    assert services["api"]["environment"]["RUN_DB_MIGRATIONS"] == "false"
    assert "alembic" not in " ".join(services["worker"]["command"])
