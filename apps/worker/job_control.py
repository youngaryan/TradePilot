from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
import logging
import signal
import sys
from threading import Event
from typing import Any

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.job_queue import JOB_KINDS, enqueue_quant_job
from pairs_trading.backend.job_dispatch import mark_dispatch_accepted
from pairs_trading.backend.readiness import RoleHeartbeat, check_role_from_settings
from pairs_trading.backend.observability import METRICS, configure_role_observability, log_exception, record_controller_result, span, start_metrics_server, timed
from pairs_trading.platform import build_metadata_store


LOGGER = logging.getLogger("pairs_trading.job_control")
EnqueueFunction = Callable[..., dict[str, Any]]
MAX_CONTROL_BACKOFF_SECONDS = 300


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _dispatch_payload(
    settings: BackendSettings,
    payload: dict[str, Any],
    *,
    enqueue: EnqueueFunction,
) -> dict[str, Any]:
    kind = str(payload.get("kind") or "")
    job_id = str(payload.get("id") or "")
    attempt = int(payload.get("attempt") or 0) + 1
    return enqueue(settings, kind=kind, job_id=job_id, attempt=attempt)


def _ensure_payload_kind(store: Any, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("kind") in JOB_KINDS:
        return payload
    job_id = str(payload.get("id") or "")
    for kind in JOB_KINDS:
        if store.get_job(kind=kind, job_id=job_id) is not None:
            return {**payload, "kind": kind}
    raise ValueError(f"Unable to resolve recovered job kind: {job_id}")


def run_recovery_pass(
    settings: BackendSettings,
    *,
    store: Any | None = None,
    enqueue: EnqueueFunction | None = None,
    now_utc: str | None = None,
) -> dict[str, Any]:
    """Recover one bounded batch of expired leases and redispatch retryable rows."""

    metadata_store = store or build_metadata_store(settings)
    dispatch = enqueue or enqueue_quant_job
    recovered = metadata_store.recover_expired_jobs(
        now_utc=now_utc or _utc_now_iso(),
        limit=settings.job_recovery_batch_size,
    )
    summary: dict[str, Any] = {
        "recovered": len(recovered),
        "redispatched": 0,
        "interrupted": 0,
        "already_enqueued": 0,
        "errors": 0,
    }
    for payload in recovered:
        if payload.get("status") != "queued":
            summary["interrupted"] += 1
            continue
        try:
            result = _dispatch_payload(
                settings,
                _ensure_payload_kind(metadata_store, payload),
                enqueue=dispatch,
            )
            summary["redispatched"] += 1
            summary["already_enqueued"] += int(bool(result.get("already_enqueued")))
            resolved = _ensure_payload_kind(metadata_store, payload)
            mark_dispatch_accepted(
                metadata_store,
                kind=str(resolved["kind"]),
                job_id=str(resolved["id"]),
                result=result,
            )
        except Exception:  # next pass retries the durable queued row
            summary["errors"] += 1
            LOGGER.warning("job_recovery_dispatch_failed")
    return summary


def reconcile_queued_jobs(
    settings: BackendSettings,
    *,
    store: Any | None = None,
    enqueue: EnqueueFunction | None = None,
    start_kind: int = 0,
) -> dict[str, Any]:
    """Dispatch one fair, bounded batch of durable queued rows."""

    metadata_store = store or build_metadata_store(settings)
    dispatch = enqueue or enqueue_quant_job
    summary: dict[str, Any] = {"scanned": 0, "queued": 0, "dispatched": 0, "already_enqueued": 0, "errors": 0}
    normalized_start = int(start_kind) % len(JOB_KINDS)
    ordered_kinds = JOB_KINDS[normalized_start:] + JOB_KINDS[:normalized_start]
    per_kind, remainder = divmod(settings.job_recovery_batch_size, len(JOB_KINDS))
    for index, kind in enumerate(ordered_kinds):
        kind_limit = per_kind + int(index < remainder)
        if kind_limit == 0:
            continue
        page = metadata_store.list_jobs(kind=kind, status="queued", limit=kind_limit, offset=0)
        summary["scanned"] += len(page)
        summary["queued"] += len(page)
        for payload in page:
            dispatch_payload = dict(payload)
            dispatch_payload.setdefault("kind", kind)
            try:
                result = _dispatch_payload(settings, dispatch_payload, enqueue=dispatch)
                summary["dispatched"] += 1
                summary["already_enqueued"] += int(bool(result.get("already_enqueued")))
                mark_dispatch_accepted(
                    metadata_store,
                    kind=kind,
                    job_id=str(dispatch_payload["id"]),
                    result=result,
                )
            except Exception:  # next pass retries the same deterministic attempt
                summary["errors"] += 1
                LOGGER.warning("queued_job_reconciliation_failed")
    return summary


def run_control_pass(
    settings: BackendSettings,
    *,
    store: Any | None = None,
    enqueue: EnqueueFunction | None = None,
    now_utc: str | None = None,
    reconciliation_start_kind: int = 0,
) -> dict[str, Any]:
    metadata_store = store or build_metadata_store(settings)
    return {
        "recovery": run_recovery_pass(settings, store=metadata_store, enqueue=enqueue, now_utc=now_utc),
        "reconciliation": reconcile_queued_jobs(
            settings,
            store=metadata_store,
            enqueue=enqueue,
            start_kind=reconciliation_start_kind,
        ),
    }


def run_forever(settings: BackendSettings, *, stop_event: Event | None = None) -> None:
    settings.validate_external_job_runtime(role="controller")
    stopped = stop_event or Event()
    consecutive_failures = 0
    reconciliation_start_kind = 0
    base_delay = max(1, int(settings.job_recovery_poll_seconds))
    while not stopped.is_set():
        started = timed()
        try:
            with span("controller.control_pass", attributes={"controller.start_kind": reconciliation_start_kind}):
                summary = run_control_pass(settings, reconciliation_start_kind=reconciliation_start_kind)
        except Exception as error:
            consecutive_failures += 1
            exponent = min(consecutive_failures - 1, 8)
            delay = min(MAX_CONTROL_BACKOFF_SECONDS, base_delay * (2**exponent))
            METRICS.inc("tradepilot_controller_passes_total", {"status": "failed"})
            log_exception(LOGGER, "job_control_pass_failed", error, consecutive_failures=consecutive_failures, retry_delay_seconds=delay)
        else:
            consecutive_failures = 0
            delay = base_delay
            reconciliation_start_kind = (reconciliation_start_kind + 1) % len(JOB_KINDS)
            METRICS.inc("tradepilot_controller_passes_total", {"status": "completed"})
            record_controller_result(summary, duration_seconds=timed() - started)
            LOGGER.info("job_control_pass_completed", extra={"summary": summary})
        stopped.wait(delay)


def healthcheck(settings: BackendSettings | None = None) -> bool:
    result = check_role_from_settings(settings or BackendSettings.from_env(), role="controller")
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return bool(result["healthy"])


def main(argv: list[str] | None = None) -> None:
    args = list(argv or [])
    settings = BackendSettings.from_env()
    if args == ["--healthcheck"]:
        if not healthcheck(settings):
            raise SystemExit(1)
        return
    if args:
        raise SystemExit("Usage: python -m apps.worker.job_control [--healthcheck]")
    settings.validate_external_job_runtime(role="controller")
    configure_role_observability(settings, role="controller")
    metrics_server = start_metrics_server(settings)
    stopped = Event()

    def stop(_signum: int, _frame: Any) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        from redis import Redis
    except ImportError as exc:  # pragma: no cover - optional deployment dependency
        raise RuntimeError("Install worker dependencies with `pip install -e .[backend]`.") from exc
    redis = Redis.from_url(settings.redis_url)
    heartbeat = RoleHeartbeat(redis, settings, role="controller")
    heartbeat.start()
    try:
        run_forever(settings, stop_event=stopped)
    finally:
        heartbeat.stop()
        if metrics_server is not None:
            metrics_server.stop()


if __name__ == "__main__":
    main(sys.argv[1:])
