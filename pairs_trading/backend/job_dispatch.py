from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import logging
from typing import Any

from .config import BackendSettings
from .job_queue import JOB_KINDS, enqueue_quant_job
from .observability import METRICS, safe_job_kind


LOGGER = logging.getLogger("pairs_trading.job_dispatch")
EnqueueFunction = Callable[..., dict[str, Any]]
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def classify_dispatch_error(error: BaseException) -> str:
    """Return a bounded class without reading or persisting exception text."""

    module = type(error).__module__.lower()
    name = type(error).__name__.lower()
    if module.startswith("redis") or name in {"connectionerror", "timeouterror"}:
        return "redis_unavailable"
    return "dispatch_error"


def _persist_dispatch(
    store: Any,
    *,
    kind: str,
    job_id: str,
    state: str,
    attempted_at_utc: str,
    rq_job_id: str | None = None,
    error_class: str | None = None,
) -> dict[str, Any] | None:
    payload = store.get_job(kind=kind, job_id=job_id)
    if payload is None:
        return None
    if not callable(getattr(store, "upsert_job", None)):
        # Lightweight controller test doubles and third-party stores predating
        # additive dispatch metadata can still participate in reconciliation.
        return payload
    updated = dict(payload)
    updated["dispatch_state"] = state
    updated["dispatch_attempted_at_utc"] = attempted_at_utc
    updated["dispatch_error_class"] = error_class
    if rq_job_id:
        updated["rq_job_id"] = rq_job_id
    if str(payload.get("status")) not in TERMINAL_STATUSES:
        updated["status"] = "queued"
        if state == "accepted":
            updated["stage"] = "queued"
            updated["message"] = "Accepted by Redis/RQ. Waiting for a worker claim."
        else:
            updated["stage"] = "dispatch_pending"
            updated["message"] = "Queue dispatch is pending. Durable reconciliation will retry safely."
    store.upsert_job(kind=kind, payload=updated)
    return updated


def mark_dispatch_accepted(
    store: Any,
    *,
    kind: str,
    job_id: str,
    result: dict[str, Any],
    attempted_at_utc: str | None = None,
) -> dict[str, Any] | None:
    return _persist_dispatch(
        store,
        kind=kind,
        job_id=job_id,
        state="accepted",
        attempted_at_utc=attempted_at_utc or _utc_now_iso(),
        rq_job_id=str(result.get("rq_job_id") or "") or None,
    )


def dispatch_initial_job(
    settings: BackendSettings,
    *,
    kind: str,
    job_id: str,
    metadata_store: Any,
    enqueue: EnqueueFunction = enqueue_quant_job,
) -> dict[str, Any] | None:
    """Attempt initial RQ dispatch once and leave failures durable/retryable."""

    normalized_kind = safe_job_kind(kind)
    if normalized_kind not in JOB_KINDS:
        raise ValueError(f"Unsupported queued job kind: {kind}")
    attempted_at = _utc_now_iso()
    try:
        result = enqueue(settings, kind=normalized_kind, job_id=job_id)
    except Exception as error:
        error_class = classify_dispatch_error(error)
        METRICS.inc(
            "tradepilot_job_dispatch_failures_total",
            {"kind": normalized_kind},
        )
        LOGGER.warning(
            "initial_job_dispatch_pending",
            extra={"kind": normalized_kind, "error_class": error_class},
        )
        return _persist_dispatch(
            metadata_store,
            kind=normalized_kind,
            job_id=job_id,
            state="pending",
            attempted_at_utc=attempted_at,
            error_class=error_class,
        )
    return mark_dispatch_accepted(
        metadata_store,
        kind=normalized_kind,
        job_id=job_id,
        result=result,
        attempted_at_utc=attempted_at,
    )


__all__ = [
    "classify_dispatch_error",
    "dispatch_initial_job",
    "mark_dispatch_accepted",
]
