from __future__ import annotations

from typing import Any

from .config import BackendSettings


QUEUE_NAME = "quant_jobs"


def enqueue_quant_job(settings: BackendSettings, *, kind: str, job_id: str) -> dict[str, Any]:
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is required when in-process jobs are disabled.")
    try:
        from redis import Redis
        from rq import Queue
    except ImportError as exc:  # pragma: no cover - depends on optional backend extras
        raise RuntimeError("Install backend queue dependencies with `pip install -e .[backend]`.") from exc

    from .worker_tasks import run_queued_job

    redis = Redis.from_url(settings.redis_url)
    queue = Queue(QUEUE_NAME, connection=redis)
    rq_job = queue.enqueue(run_queued_job, kind, job_id, job_timeout="2h", result_ttl=86400, failure_ttl=86400)
    return {"queue": QUEUE_NAME, "rq_job_id": rq_job.id}
