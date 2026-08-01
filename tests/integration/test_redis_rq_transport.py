from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
from typing import Any
from uuid import uuid4

import pytest

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.job_queue import QUEUE_NAME, enqueue_quant_job, quant_rq_job_id
from tests.integration.rq_probe import increment_probe


pytestmark = pytest.mark.integration


def test_real_redis_unique_concurrent_enqueue_creates_one_transport(redis_context: Any) -> None:
    from rq import Queue
    from rq.serializers import JSONSerializer

    context = redis_context
    logical_job_id = f"{context.prefix}-concurrent"
    rq_job_id = quant_rq_job_id("backtest", logical_job_id, 1)
    context.track_job(QUEUE_NAME, rq_job_id)
    settings = BackendSettings(redis_url=context.url)
    barrier = threading.Barrier(2)

    def enqueue() -> dict[str, Any]:
        barrier.wait(timeout=10)
        return enqueue_quant_job(settings, kind="backtest", job_id=logical_job_id, attempt=1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(enqueue), executor.submit(enqueue)]
        results = [future.result(timeout=15) for future in futures]

    queue = Queue(QUEUE_NAME, connection=context.connection, serializer=JSONSerializer)
    assert {result["rq_job_id"] for result in results} == {rq_job_id}
    assert sum(not result["already_enqueued"] for result in results) == 1
    assert queue.get_job_ids().count(rq_job_id) == 1
    queued = queue.fetch_job(rq_job_id)
    assert queued is not None
    assert str(getattr(queued.get_status(refresh=True), "value", queued.get_status(refresh=True))).lower() == "queued"


def test_real_rq_json_simple_worker_executes_importable_probe_once(redis_context: Any) -> None:
    from rq import Queue, SimpleWorker
    from rq.serializers import JSONSerializer

    context = redis_context
    queue_name = f"{context.prefix}:probe-queue"
    job_id = f"{context.prefix}:probe-job"
    probe_key = context.probe_key("worker")
    token = uuid4().hex
    context.track_job(queue_name, job_id)
    queue = Queue(queue_name, connection=context.connection, serializer=JSONSerializer)
    queue.enqueue(
        increment_probe,
        context.url,
        probe_key,
        token,
        job_id=job_id,
        unique=True,
        result_ttl=60,
        failure_ttl=60,
    )

    worker = SimpleWorker([queue], connection=context.connection, serializer=JSONSerializer)
    assert worker.work(burst=True, max_jobs=1, logging_level="WARNING") is True

    completed = queue.fetch_job(job_id)
    assert completed is not None
    assert completed.result == {"token": token, "count": 1}
    assert int(context.connection.get(probe_key)) == 1


def test_terminal_deterministic_transport_id_is_safely_reused(redis_context: Any) -> None:
    from rq import Queue
    from rq.job import JobStatus
    from rq.serializers import JSONSerializer

    context = redis_context
    logical_job_id = f"{context.prefix}-terminal-reuse"
    rq_job_id = quant_rq_job_id("paper", logical_job_id, 1)
    context.track_job(QUEUE_NAME, rq_job_id)
    settings = BackendSettings(redis_url=context.url)
    first = enqueue_quant_job(settings, kind="paper", job_id=logical_job_id, attempt=1)
    queue = Queue(QUEUE_NAME, connection=context.connection, serializer=JSONSerializer)
    terminal = queue.fetch_job(rq_job_id)
    assert terminal is not None
    queue.remove(rq_job_id)
    terminal.set_status(JobStatus.FINISHED)

    second = enqueue_quant_job(settings, kind="paper", job_id=logical_job_id, attempt=1)

    assert first["rq_job_id"] == second["rq_job_id"] == rq_job_id
    assert second["already_enqueued"] is False
    assert queue.get_job_ids().count(rq_job_id) == 1


def test_real_postgres_to_redis_to_worker_topology_with_fast_claimed_handler(
    postgres_context: Any,
    redis_context: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rq import Queue, SimpleWorker
    from rq.serializers import JSONSerializer

    from pairs_trading.backend import services, worker_tasks
    from pairs_trading.backend.services import BacktestJobRunner

    pg = postgres_context
    redis = redis_context
    job_id = pg.job_id("cross-topology")
    organization_id = pg.organization_id("topology")
    now = "2026-08-01T00:00:00Z"
    pg.store.upsert_job(
        kind="backtest",
        payload={
            "id": job_id,
            "organization_id": organization_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0.0,
            "request": {"pipeline": "buy_and_hold"},
            "created_at_utc": now,
            "updated_at_utc": now,
            "max_attempts": 3,
        },
    )
    rq_job_id = quant_rq_job_id("backtest", job_id, 1)
    redis.track_job(QUEUE_NAME, rq_job_id)

    def fast_run(
        self: Any,
        durable_job_id: str,
        _request: Any,
        _organization_id: str,
        _user_id: str | None = None,
    ) -> None:
        self._set_status(
            durable_job_id,
            "running",
            stage="integration_probe",
            progress=0.5,
            message="Fast deterministic integration handler running.",
        )
        self._set_status(
            durable_job_id,
            "completed",
            stage="completed",
            progress=1.0,
            result={"cross_topology": True},
            message="Fast deterministic integration handler completed.",
        )

    monkeypatch.setattr(BacktestJobRunner, "_run_job", fast_run)
    monkeypatch.setattr(worker_tasks, "build_metadata_store", lambda _settings: pg.store)
    monkeypatch.setattr(services, "build_metadata_store", lambda _settings: pg.store)
    monkeypatch.setenv("DATABASE_URL", pg.url)
    monkeypatch.setenv("REDIS_URL", redis.url)
    monkeypatch.setenv("ENABLE_IN_PROCESS_JOBS", "false")
    monkeypatch.setenv("ENABLE_DEMO_ACCOUNTS", "false")
    enqueue_quant_job(BackendSettings(redis_url=redis.url), kind="backtest", job_id=job_id, attempt=1)

    queue = Queue(QUEUE_NAME, connection=redis.connection, serializer=JSONSerializer)
    worker = SimpleWorker([queue], connection=redis.connection, serializer=JSONSerializer)
    assert worker.work(burst=True, max_jobs=1, logging_level="WARNING") is True

    durable = pg.store.get_job(kind="backtest", job_id=job_id, organization_id=organization_id)
    assert durable is not None
    assert durable["status"] == "completed"
    assert durable["result"] == {"cross_topology": True}
    assert durable["attempt"] == 1
    assert durable["worker_id"] is None
