from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import random
from threading import Event, Thread
from time import sleep
from typing import Any
from uuid import uuid4

from ..platform import build_metadata_store
from .config import BackendSettings
from .saas import SaaSService
from .schemas import TelemetryEventRequest


logger = logging.getLogger("pairs_trading.telemetry")


SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "email",
    "phone",
    "address",
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def sanitize_payload(value: Any, *, depth: int = 0) -> Any:
    """Remove secrets/PII-ish fields and cap payload size before storage."""

    if depth > 4:
        return "[truncated]"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)[:80]
            if any(fragment in key.lower() for fragment in SENSITIVE_KEY_FRAGMENTS):
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = sanitize_payload(raw_value, depth=depth + 1)
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, str):
        cleaned = value.replace("\r", " ").replace("\n", " ")
        return cleaned[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:300]


@dataclass(frozen=True)
class TelemetryContext:
    user_id: str | None = None
    organization_id: str | None = None
    country: str | None = None


class TelemetryService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.store = build_metadata_store(settings)

    def track(self, request: TelemetryEventRequest, *, context: TelemetryContext | None = None) -> dict[str, Any]:
        if not self.settings.telemetry_enabled:
            return {"stored": False, "reason": "telemetry_disabled"}
        if request.consent == "denied" and request.category not in {"system", "security", "error"}:
            return {"stored": False, "reason": "consent_denied"}
        if random.random() > max(0.0, min(1.0, self.settings.telemetry_sample_rate)):
            return {"stored": False, "reason": "sampled_out"}

        scoped = context or TelemetryContext()
        payload_context = sanitize_payload(
            {
                **request.context,
                "anonymous_id": request.anonymous_id,
                "has_user": bool(scoped.user_id),
                "has_organization": bool(scoped.organization_id),
                "visitor_country": scoped.country,
            }
        )
        event = self.store.record_telemetry_event(
            event_id=uuid4().hex,
            name=normalize_event_name(request.name),
            category=normalize_event_name(request.category),
            properties=sanitize_payload(request.properties),
            context=payload_context,
            consent=request.consent,
            organization_id=scoped.organization_id,
            user_id=scoped.user_id,
            occurred_at_utc=request.occurred_at_utc or utc_now_iso(),
        )
        return {"stored": True, "event": event}

    def list_events(self, *, organization_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list_telemetry_events(organization_id=organization_id, limit=limit)


class DailyRefreshService:
    """Local-first 24-hour per-user refresh coordinator.

    In production this same service should be called by a worker queue or
    managed scheduler. In the local app it can be triggered manually or by the
    optional lightweight scheduler thread.
    """

    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.store = build_metadata_store(settings)
        self.telemetry = TelemetryService(settings)

    def due_users(self, *, limit: int = 100, force: bool = False) -> list[dict[str, Any]]:
        now = utc_now()
        users = self.store.list_users_with_default_org()
        due: list[dict[str, Any]] = []
        for user in users:
            status = self.store.get_refresh_status(user_id=user["user_id"])
            due_at = parse_utc(status.get("next_due_at_utc")) if status else None
            if force or status is None or due_at is None or due_at <= now:
                due.append({**user, "refresh_status": status})
            if len(due) >= limit:
                break
        return due

    def run_due_users(self, *, limit: int = 100, force: bool = False) -> dict[str, Any]:
        runs = [self.run_for_user(user_id=user["user_id"], organization_id=user["organization_id"], force=force) for user in self.due_users(limit=limit, force=force)]
        return {
            "status": "completed",
            "checked_at_utc": utc_now_iso(),
            "run_count": len(runs),
            "runs": runs,
        }

    def run_for_user(self, *, user_id: str, organization_id: str, force: bool = False) -> dict[str, Any]:
        now = utc_now()
        current_status = self.store.get_refresh_status(user_id=user_id)
        due_at = parse_utc(current_status.get("next_due_at_utc")) if current_status else None
        if not force and due_at is not None and due_at > now:
            return {"status": "skipped_not_due", "user_id": user_id, "next_due_at_utc": due_at.isoformat().replace("+00:00", "Z")}

        idempotency_key = f"daily_refresh:{user_id}:{now.date().isoformat()}"
        run, created = self.store.create_refresh_run(
            run_id=uuid4().hex,
            idempotency_key=idempotency_key,
            user_id=user_id,
            organization_id=organization_id,
            max_attempts=self.settings.refresh_max_attempts,
            locked_until_utc=(now + timedelta(minutes=self.settings.refresh_lock_minutes)).isoformat().replace("+00:00", "Z"),
        )
        lock_expires = parse_utc(run.get("locked_until_utc"))
        lock_active = lock_expires is not None and lock_expires > now
        if not created and (run["status"] == "succeeded" or (run["status"] in {"queued", "running"} and lock_active)):
            self.telemetry.track(
                TelemetryEventRequest(
                    name="data_refresh_deduplicated",
                    category="refresh",
                    properties={"run_id": run["id"], "status": run["status"]},
                    consent="system",
                ),
                context=TelemetryContext(user_id=user_id, organization_id=organization_id),
            )
            return {**run, "deduplicated": True}

        return self._execute_run(run_id=run["id"], user_id=user_id, organization_id=organization_id)

    def _execute_run(self, *, run_id: str, user_id: str, organization_id: str) -> dict[str, Any]:
        last_error: str | None = None
        max_attempts = max(1, self.settings.refresh_max_attempts)
        for attempt in range(1, max_attempts + 1):
            started = utc_now_iso()
            self.store.update_refresh_run(run_id=run_id, status="running", attempt=attempt, started_at_utc=started, error=None)
            self.store.upsert_refresh_status(
                user_id=user_id,
                organization_id=organization_id,
                status="running",
                latest_run_id=run_id,
                last_attempt_at_utc=started,
                next_due_at_utc=(utc_now() + timedelta(hours=self.settings.refresh_interval_hours)).isoformat().replace("+00:00", "Z"),
            )
            self.telemetry.track(
                TelemetryEventRequest(
                    name="data_refresh_started",
                    category="refresh",
                    properties={"run_id": run_id, "attempt": attempt},
                    consent="system",
                ),
                context=TelemetryContext(user_id=user_id, organization_id=organization_id),
            )
            try:
                summary = self._refresh_user_data(organization_id=organization_id)
                finished = utc_now_iso()
                next_due = (utc_now() + timedelta(hours=self.settings.refresh_interval_hours)).isoformat().replace("+00:00", "Z")
                run = self.store.update_refresh_run(
                    run_id=run_id,
                    status="succeeded",
                    attempt=attempt,
                    finished_at_utc=finished,
                    locked_until_utc=None,
                    summary=summary,
                    error=None,
                )
                self.store.upsert_refresh_status(
                    user_id=user_id,
                    organization_id=organization_id,
                    status="succeeded",
                    latest_run_id=run_id,
                    last_success_at_utc=finished,
                    last_attempt_at_utc=finished,
                    next_due_at_utc=next_due,
                    last_error=None,
                )
                self.telemetry.track(
                    TelemetryEventRequest(
                        name="data_refresh_succeeded",
                        category="refresh",
                        properties={"run_id": run_id, "attempt": attempt, **summary},
                        consent="system",
                    ),
                    context=TelemetryContext(user_id=user_id, organization_id=organization_id),
                )
                return run or {}
            except Exception as exc:  # pragma: no cover - covered via route-level behavior
                last_error = str(exc)
                self.telemetry.track(
                    TelemetryEventRequest(
                        name="data_refresh_attempt_failed",
                        category="refresh",
                        properties={"run_id": run_id, "attempt": attempt, "error_type": type(exc).__name__},
                        consent="system",
                    ),
                    context=TelemetryContext(user_id=user_id, organization_id=organization_id),
                )
                if attempt < max_attempts:
                    sleep(min(2 ** attempt, 10))

        finished = utc_now_iso()
        next_due = (utc_now() + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        run = self.store.update_refresh_run(
            run_id=run_id,
            status="failed",
            finished_at_utc=finished,
            locked_until_utc=None,
            error=last_error,
            summary={"error": "refresh_failed", "attempts": max_attempts},
        )
        self.store.upsert_refresh_status(
            user_id=user_id,
            organization_id=organization_id,
            status="failed",
            latest_run_id=run_id,
            last_attempt_at_utc=finished,
            next_due_at_utc=next_due,
            last_error=last_error,
        )
        return run or {}

    def _refresh_user_data(self, *, organization_id: str) -> dict[str, Any]:
        service = SaaSService(self.settings)
        service.sync_default_datasets(organization_id=organization_id)
        service.sync_experiment_runs(organization_id=organization_id)
        workspace = service.workspace_payload(organization_id=organization_id)
        return {
            "dataset_count": len(workspace.get("datasets", [])),
            "experiment_count": len(workspace.get("experiments", [])),
            "paper_agent_count": len(workspace.get("paper_agents", [])),
        }

    def status_payload(self, *, organization_id: str | None = None) -> dict[str, Any]:
        return {
            "interval_hours": self.settings.refresh_interval_hours,
            "max_attempts": self.settings.refresh_max_attempts,
            "scheduler_enabled": self.settings.refresh_scheduler_enabled,
            "statuses": self.store.list_refresh_statuses(organization_id=organization_id),
            "recent_runs": self.store.list_refresh_runs(organization_id=organization_id, limit=50),
        }


class DailyRefreshScheduler:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.service = DailyRefreshService(settings)
        self.stop_event = Event()
        self.thread: Thread | None = None

    def start(self) -> None:
        if not self.settings.refresh_scheduler_enabled or self.thread is not None:
            return
        self.thread = Thread(target=self._loop, name="daily-refresh-scheduler", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.service.run_due_users(limit=100)
            except Exception:
                logger.exception("daily_refresh_scheduler_failed")
            self.stop_event.wait(max(60, self.settings.refresh_scheduler_poll_seconds))


def normalize_event_name(value: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "_" for char in value.strip())
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_") or "unknown_event"
