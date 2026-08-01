from __future__ import annotations

from typing import Any

from .config import BackendSettings
from .observability import current_correlation_id, inject_trace_context


QUEUE_NAME = "quant_jobs"
JOB_KINDS = ("backtest", "paper", "sentiment", "market_research")
_ACTIVE_RQ_STATUSES = {"queued", "started", "deferred", "scheduled"}


def quant_rq_job_id(kind: str, job_id: str, attempt: int) -> str:
    normalized_kind = str(kind).strip().lower()
    normalized_job_id = str(job_id).strip()
    if normalized_kind not in JOB_KINDS:
        raise ValueError(f"Unsupported queued job kind: {kind}")
    if not normalized_job_id:
        raise ValueError("job_id must not be empty")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    return f"tradepilot:{normalized_kind}:{normalized_job_id}:attempt:{attempt}"


def _rq_status(job: Any) -> str:
    status = job.get_status(refresh=True)
    return str(getattr(status, "value", status)).lower()


def enqueue_quant_job(
    settings: BackendSettings,
    *,
    kind: str,
    job_id: str,
    attempt: int = 1,
) -> dict[str, Any]:
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is required when in-process jobs are disabled.")
    try:
        from redis import Redis
        from rq import Queue
        from rq.exceptions import DuplicateJobError
        from rq.serializers import JSONSerializer
    except ImportError as exc:  # pragma: no cover - depends on optional backend extras
        raise RuntimeError("Install backend queue dependencies with `pip install -e .[backend]`.") from exc

    from .worker_tasks import run_queued_job

    redis = Redis.from_url(settings.redis_url)
    queue = Queue(QUEUE_NAME, connection=redis, serializer=JSONSerializer)
    rq_job_id = quant_rq_job_id(kind, job_id, attempt)
    existing = queue.fetch_job(rq_job_id)
    if existing is not None and _rq_status(existing) in _ACTIVE_RQ_STATUSES:
        return {"queue": QUEUE_NAME, "rq_job_id": rq_job_id, "already_enqueued": True}
    if existing is not None:
        # The database still considers this logical attempt dispatchable. Remove
        # an expired terminal transport record before reusing its deterministic ID.
        existing.delete()
    try:
        trace_context = inject_trace_context()
        correlation_id = current_correlation_id()
        rq_job = queue.enqueue(
            run_queued_job,
            kind,
            job_id,
            job_id=rq_job_id,
            job_timeout="2h",
            result_ttl=86400,
            failure_ttl=86400,
            unique=True,
            meta={
                "trace_context": trace_context,
                **({"correlation_id": correlation_id} if correlation_id else {}),
            },
        )
    except DuplicateJobError:
        # RQ 2.10's unique enqueue is atomic. Another API/controller won the
        # race after our advisory fetch, so the deterministic dispatch exists.
        existing = queue.fetch_job(rq_job_id)
        if existing is None:
            raise RuntimeError("The deterministic queue dispatch raced but is unavailable.") from None
        return {"queue": QUEUE_NAME, "rq_job_id": rq_job_id, "already_enqueued": True}
    return {"queue": QUEUE_NAME, "rq_job_id": rq_job.id, "already_enqueued": False}


__all__ = ["JOB_KINDS", "QUEUE_NAME", "enqueue_quant_job", "quant_rq_job_id"]
