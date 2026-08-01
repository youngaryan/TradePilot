from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import socket
from threading import Event, Thread
from typing import Any
from uuid import uuid4

from ..platform import build_metadata_store
from .config import BackendSettings
from .schemas import BacktestRunRequest, MarketResearchRunRequest, SentimentAccumulationRequest
from .backtest_services import BacktestJobRunner
from .market_research_services import MarketResearchJobRunner
from .paper_services import PaperRunCommand, PaperRunJobRunner
from .sentiment_services import SentimentJobRunner
from .services import JobClaimCheckError, JobClaimLostError
from .observability import (
    bind_context,
    current_rq_correlation_id,
    current_rq_trace_context,
    log_exception,
    record_job,
    reset_context,
    safe_job_status,
    span,
    timed,
)
import logging


LOGGER = logging.getLogger("pairs_trading.worker")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _lease_expires_at(settings: BackendSettings) -> str:
    lease_seconds = max(1, int(settings.job_lease_seconds))
    return (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat().replace("+00:00", "Z")


def _worker_owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"


class _JobHeartbeat:
    def __init__(self, settings: BackendSettings, *, store: Any, kind: str, job_id: str, worker_id: str) -> None:
        self.settings = settings
        self.store = store
        self.kind = kind
        self.job_id = job_id
        self.worker_id = worker_id
        self.stop_event = Event()
        self.lost_event = Event()
        self.uncertain_event = Event()
        self.thread = Thread(target=self._run, name=f"job-heartbeat-{kind}-{job_id[:8]}", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=max(1.0, float(self.settings.job_heartbeat_seconds) + 1.0))

    def _run(self) -> None:
        interval = max(0.1, float(self.settings.job_heartbeat_seconds))
        while not self.stop_event.wait(interval):
            try:
                refreshed = self.store.heartbeat_job(
                    kind=self.kind,
                    job_id=self.job_id,
                    worker_id=self.worker_id,
                    heartbeat_at_utc=_utc_now_iso(),
                    lease_expires_at_utc=_lease_expires_at(self.settings),
                )
            except Exception as error:
                # A failed refresh is not proof that another worker owns the
                # claim, but publication must stop until ownership can be
                # confirmed again. Computation may continue in attempt-local
                # staging and a later successful heartbeat clears uncertainty.
                self.uncertain_event.set()
                log_exception(
                    LOGGER,
                    "job_heartbeat_refresh_failed",
                    error,
                    job_kind=self.kind,
                )
                continue
            if refreshed is None:
                self.lost_event.set()
                return
            self.uncertain_event.clear()

    def assert_publishable(self) -> None:
        if self.lost_event.is_set():
            raise JobClaimLostError(
                f"{self.kind} job claim is no longer owned by this worker: {self.job_id}"
            )
        if self.uncertain_event.is_set():
            raise JobClaimCheckError(
                f"{self.kind} job claim cannot currently be confirmed: {self.job_id}"
            )


def _execute_queued_job(kind: str, job_id: str) -> dict[str, Any]:
    settings = BackendSettings.from_env()
    store = build_metadata_store(settings)
    job = store.get_job(kind=kind, job_id=job_id)
    if job is None:
        raise ValueError(f"Queued job not found: {kind}/{job_id}")
    worker_id = _worker_owner_id()
    claimed = store.claim_job(
        kind=kind,
        job_id=job_id,
        worker_id=worker_id,
        lease_expires_at_utc=_lease_expires_at(settings),
    )
    if claimed is None:
        durable = store.get_job(kind=kind, job_id=job_id)
        if durable is None:
            raise ValueError(f"Queued job not found: {kind}/{job_id}")
        return durable

    job = claimed
    organization_id = str(job.get("organization_id") or "")
    request = job.get("request") or {}
    user_id = str(job.get("user_id") or "") or None
    heartbeat = _JobHeartbeat(settings, store=store, kind=kind, job_id=job_id, worker_id=worker_id)
    heartbeat.start()
    try:
        if not organization_id:
            raise ValueError(f"Queued job is missing organization_id: {kind}/{job_id}")

        if kind == "backtest":
            runner = BacktestJobRunner(
                settings,
                mark_interrupted_on_load=False,
                claimed_worker_id=worker_id,
                claimed_attempt=int(claimed.get("attempt") or 0),
                ownership_guard=heartbeat.assert_publishable,
            )
            runner._run_job(job_id, BacktestRunRequest.model_validate(request), organization_id, user_id)
        elif kind == "paper":
            runner = PaperRunJobRunner(
                settings,
                mark_interrupted_on_load=False,
                claimed_worker_id=worker_id,
            )
            command = PaperRunCommand(
                deployment_config_path=Path(request["deployment_config_path"]) if request.get("deployment_config_path") else None,
                deployment_config=request.get("deployment_config"),
                deployment_id=request.get("deployment_id"),
                project_id=request.get("project_id"),
                asof_date=request.get("asof_date"),
                asof_start=request.get("asof_start"),
                asof_end=request.get("asof_end"),
            )
            runner._run_job(job_id, command, organization_id)
        elif kind == "sentiment":
            runner = SentimentJobRunner(
                settings,
                mark_interrupted_on_load=False,
                claimed_worker_id=worker_id,
                claimed_attempt=int(claimed.get("attempt") or 0),
                ownership_guard=heartbeat.assert_publishable,
            )
            runner._run_job(job_id, SentimentAccumulationRequest.model_validate(request), organization_id)
        elif kind == "market_research":
            runner = MarketResearchJobRunner(
                settings,
                mark_interrupted_on_load=False,
                claimed_worker_id=worker_id,
                claimed_attempt=int(claimed.get("attempt") or 0),
                ownership_guard=heartbeat.assert_publishable,
            )
            runner._run_job(job_id, MarketResearchRunRequest.model_validate(request), organization_id)
        else:
            raise ValueError(f"Unsupported queued job kind: {kind}")
    except JobClaimLostError:
        return store.get_job(kind=kind, job_id=job_id) or {"id": job_id, "kind": kind}
    except Exception:
        current = store.get_job(kind=kind, job_id=job_id)
        if current is not None and current.get("status") == "running" and current.get("worker_id") == worker_id:
            attempts_remain = int(current.get("attempt") or 0) < int(current.get("max_attempts") or 3)
            store.release_job_claim(
                kind=kind,
                job_id=job_id,
                worker_id=worker_id,
                status="queued" if attempts_remain else "failed",
                updates={
                    "error": None if attempts_remain else "The worker failed before completing the job.",
                    "progress": float(current.get("progress") or 0.0) if attempts_remain else 1.0,
                    "message": "Worker execution failed; the job is waiting for a retry."
                    if attempts_remain
                    else "Worker execution failed after the maximum number of attempts.",
                },
            )
        raise RuntimeError(f"Queued {kind} worker execution failed.") from None
    finally:
        heartbeat.stop()

    return store.get_job(kind=kind, job_id=job_id) or {"id": job_id, "kind": kind}


def run_queued_job(kind: str, job_id: str) -> dict[str, Any]:
    """Execute a durable job inside propagated trace/log context."""

    started = timed()
    correlation_id = current_rq_correlation_id()
    token = bind_context(
        **({"correlation_id": correlation_id} if correlation_id else {}),
        job_kind=kind,
        job_id=job_id,
    )
    status = "failed"
    try:
        with span(
            f"job.{kind}",
            attributes={"job.kind": kind, "job.id": job_id},
            carrier=current_rq_trace_context(),
        ):
            LOGGER.info("job_execution_started", extra={"job_kind": kind})
            result = _execute_queued_job(kind, job_id)
            status = safe_job_status(str(result.get("status") or "unknown"))
            LOGGER.info("job_execution_completed", extra={"job_kind": kind, "job_status": status})
            return result
    except Exception as error:
        log_exception(LOGGER, "job_execution_failed", error, job_kind=kind)
        raise
    finally:
        record_job(kind=kind, status=status, duration_seconds=timed() - started)
        reset_context(token)
