from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Response

from ..config import BackendSettings
from ..observability import METRICS, metrics_authorized, safe_job_kind
from ...platform import build_metadata_store
from ..readiness import ReadinessChecker, check_any_role_instance_from_settings


_JOB_PAGE_SIZE = 200
_MAX_JOBS_PER_STATUS = 10_000


def _age_seconds(timestamp: Any) -> float:
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        return max(0.0, (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds())
    except (TypeError, ValueError):
        return 0.0


def _bounded_jobs(store: Any, *, kind: str, status: str) -> tuple[list[dict[str, Any]], bool]:
    jobs: list[dict[str, Any]] = []
    for offset in range(0, _MAX_JOBS_PER_STATUS, _JOB_PAGE_SIZE):
        page = store.list_jobs(kind=kind, status=status, limit=_JOB_PAGE_SIZE, offset=offset)
        jobs.extend(page)
        if len(page) < _JOB_PAGE_SIZE:
            return jobs, False
    return jobs, True


def collect_runtime_metrics(settings: BackendSettings) -> None:
    """Refresh bounded durable-state gauges immediately before exposition."""

    try:
        store = build_metadata_store(settings)
        truncated = False
        for kind in ("backtest", "paper", "sentiment", "market_research"):
            queued, queued_truncated = _bounded_jobs(store, kind=kind, status="queued")
            running, running_truncated = _bounded_jobs(store, kind=kind, status="running")
            truncated = truncated or queued_truncated or running_truncated
            labels = {"kind": safe_job_kind(kind)}
            METRICS.set_gauge("tradepilot_job_backlog", labels, len(queued))
            METRICS.set_gauge(
                "tradepilot_job_oldest_queued_age_seconds",
                labels,
                max((_age_seconds(job.get("created_at_utc")) for job in queued), default=0.0),
            )
            dispatch_pending = [
                job
                for job in queued
                if job.get("stage") == "dispatch_pending" or job.get("dispatch_state") == "pending"
            ]
            METRICS.set_gauge("tradepilot_job_dispatch_pending", labels, len(dispatch_pending))
            METRICS.set_gauge(
                "tradepilot_job_oldest_dispatch_pending_age_seconds",
                labels,
                max((_age_seconds(job.get("dispatch_attempted_at_utc") or job.get("created_at_utc")) for job in dispatch_pending), default=0.0),
            )
            METRICS.set_gauge("tradepilot_jobs_running", labels, len(running))
            METRICS.set_gauge(
                "tradepilot_jobs_stuck",
                labels,
                sum(
                    _age_seconds(job.get("heartbeat_at_utc") or job.get("started_at_utc")) > settings.job_lease_seconds
                    for job in running
                ),
            )
        METRICS.set_gauge("tradepilot_metrics_collection_truncated", {"collector": "jobs"}, int(truncated))
    except Exception:
        METRICS.set_gauge("tradepilot_metrics_collection_success", {"collector": "jobs"}, 0)
    else:
        METRICS.set_gauge("tradepilot_metrics_collection_success", {"collector": "jobs"}, 1)

    try:
        readiness = ReadinessChecker(settings).check()
        for component, result in readiness.get("components", {}).items():
            if component in {"database", "redis", "object_storage"}:
                METRICS.set_gauge(
                    "tradepilot_dependency_ready",
                    {"component": component},
                    1 if result.get("status") == "ok" else 0,
                )
    except Exception:
        for component in ("database", "redis", "object_storage"):
            METRICS.set_gauge("tradepilot_dependency_ready", {"component": component}, 0)
        METRICS.set_gauge("tradepilot_metrics_collection_success", {"collector": "readiness"}, 0)
    else:
        METRICS.set_gauge("tradepilot_metrics_collection_success", {"collector": "readiness"}, 1)

    for role in ("worker", "controller"):
        result = check_any_role_instance_from_settings(settings, role=role)
        METRICS.set_gauge("tradepilot_role_heartbeat_healthy", {"role": role}, 1 if result.get("healthy") else 0)
        METRICS.set_gauge("tradepilot_role_heartbeat_age_seconds", {"role": role}, float(result.get("age_seconds") or 0.0))


def build_metrics_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(tags=["internal"])

    @router.get("/internal/metrics", include_in_schema=False)
    def metrics(authorization: str | None = Header(default=None)) -> Response:
        if not settings.observability_metrics_enabled:
            raise HTTPException(status_code=404, detail="Not found")
        if not metrics_authorized(settings.observability_metrics_token, authorization):
            raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Bearer"})
        collect_runtime_metrics(settings)
        return Response(
            METRICS.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    return router


__all__ = ["build_metrics_router", "collect_runtime_metrics"]
