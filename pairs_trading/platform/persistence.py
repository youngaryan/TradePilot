from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Protocol
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json_dump(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


class IdempotencyConflictError(ValueError):
    """Raised when an idempotency key is reused for a different payload."""


class QuotaReservationExceededError(ValueError):
    """Raised when an atomic usage reservation would exceed its limit."""

    def __init__(self, *, feature: str, limit: float, used: float) -> None:
        super().__init__("Quota reservation exceeds the configured allowance")
        self.feature = feature
        self.limit = limit
        self.used = used


def _canonical_safe_json(value: Any, *, field_name: str) -> str:
    # Import lazily: backend services depend on this platform module.
    from ..backend.job_security import sanitize_job_data

    sanitized = sanitize_job_data(value)
    if sanitized != value:
        raise ValueError(f"{field_name} contains sensitive credential fields")
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be finite JSON data") from exc


def _canonical_hash(serialized: str) -> str:
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _parse_utc_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


@dataclass(frozen=True)
class MetadataCounts:
    jobs: int
    deployment_configs: int
    experiment_runs: int
    artifacts: int = 0
    users: int = 0
    organizations: int = 0
    projects: int = 0
    experiments: int = 0
    paper_agents: int = 0
    datasets: int = 0
    api_keys: int = 0
    subscriptions: int = 0
    telemetry_events: int = 0
    refresh_runs: int = 0
    refresh_statuses: int = 0
    market_research_reports: int = 0


class MetadataStore(Protocol):
    """Operational metadata contract shared by API and workers.

    The codebase still uses this class dynamically, so the protocol intentionally
    stays broad: SQLiteMetadataStore and PostgresMetadataStore expose the same
    public methods while the backend imports them through build_metadata_store().
    """

    def counts(self) -> MetadataCounts:
        ...


class ClaimedDomainPublisher:
    """Authoritative domain writes performed while the owning job row is locked."""

    def __init__(self, store: "SQLiteMetadataStore", connection: Any) -> None:
        self.store = store
        self.connection = connection

    def upsert_artifact(self, *, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now_iso()
        artifact_type = str(payload.get("artifact_type") or payload.get("type") or "artifact")
        source_id = payload.get("source_id")
        key = str(payload.get("key") or payload.get("storage_key") or payload.get("uri") or "")
        artifact_id = str(payload.get("id") or self.store.stable_id("art", f"{organization_id}:{artifact_type}:{source_id}:{key}"))
        self.connection.execute(
            """
            INSERT INTO artifacts (
                id, organization_id, artifact_type, source_id, provider, storage_key, uri,
                file_count, byte_count, metadata_json, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                artifact_type = excluded.artifact_type, source_id = excluded.source_id,
                provider = excluded.provider, storage_key = excluded.storage_key, uri = excluded.uri,
                file_count = excluded.file_count, byte_count = excluded.byte_count,
                metadata_json = excluded.metadata_json, updated_at_utc = excluded.updated_at_utc
            """,
            (artifact_id, organization_id, artifact_type, source_id, str(payload.get("provider") or "unknown"),
             key, str(payload.get("uri") or ""), int(payload.get("file_count", 0) or 0),
             int(payload.get("byte_count", 0) or 0), _json_dump(payload.get("metadata", {})),
             str(payload.get("created_at_utc") or now), now),
        )
        row = self.connection.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return self.store._artifact_row(row)

    def upsert_dataset(self, *, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now_iso()
        dataset_id = str(payload.get("id") or self.store.stable_id("dst", f"{organization_id}:{payload.get('path')}:{payload.get('kind')}"))
        self.connection.execute(
            """
            INSERT INTO datasets (
                id, organization_id, project_id, name, kind, path, provider_json,
                schema_json, row_count, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name, kind = excluded.kind, path = excluded.path,
                provider_json = excluded.provider_json, schema_json = excluded.schema_json,
                row_count = excluded.row_count, updated_at_utc = excluded.updated_at_utc
            """,
            (dataset_id, organization_id, payload.get("project_id"),
             str(payload.get("name") or payload.get("path") or "Dataset"), str(payload.get("kind", "unknown")),
             str(payload.get("path", "")), _json_dump(payload.get("provider", {})),
             _json_dump(payload.get("schema", {})), int(payload.get("row_count", 0) or 0),
             str(payload.get("created_at_utc") or now), now),
        )
        row = self.connection.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        return self.store._dataset_row(row)

    def save_experiment_run(self, *, experiment_id: str, kind: str, summary: dict[str, Any], organization_id: str | None = None, artifact_dir: str | Path | None = None) -> None:
        self.connection.execute(
            """
            INSERT INTO experiment_runs (id, organization_id, kind, artifact_dir, summary_json, created_at_utc)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET organization_id = excluded.organization_id,
                kind = excluded.kind, artifact_dir = excluded.artifact_dir,
                summary_json = excluded.summary_json, created_at_utc = excluded.created_at_utc
            """,
            (experiment_id, organization_id, kind, str(artifact_dir) if artifact_dir is not None else None,
             _json_dump(summary), _utc_now_iso()),
        )

    def upsert_experiment(self, *, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now_iso()
        experiment_id = str(payload.get("id") or payload.get("experiment_id") or self.store.stable_id("exp", f"{organization_id}:{uuid4().hex}"))
        self.connection.execute(
            """
            INSERT INTO experiments (
                id, organization_id, project_id, job_id, name, pipeline, status, artifact_dir,
                summary_json, validation_json, lineage_json, readiness_json, trades_json,
                sentiment_json, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET project_id = excluded.project_id, job_id = excluded.job_id,
                name = excluded.name, pipeline = excluded.pipeline, status = excluded.status,
                artifact_dir = excluded.artifact_dir, summary_json = excluded.summary_json,
                validation_json = excluded.validation_json, lineage_json = excluded.lineage_json,
                readiness_json = excluded.readiness_json, trades_json = excluded.trades_json,
                sentiment_json = excluded.sentiment_json, updated_at_utc = excluded.updated_at_utc
            """,
            (experiment_id, organization_id, payload.get("project_id"), payload.get("job_id"),
             str(payload.get("name") or experiment_id), str(payload.get("pipeline") or "unknown"),
             str(payload.get("status") or "completed"), payload.get("artifact_dir"),
             _json_dump(payload.get("summary", {})), _json_dump(payload.get("validation", {})),
             _json_dump(payload.get("lineage", {})), _json_dump(payload.get("readiness", {})),
             _json_dump(payload.get("trades", [])), _json_dump(payload.get("sentiment", {})),
             str(payload.get("created_at_utc") or now), now),
        )
        row = self.connection.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
        return self.store._experiment_row(row)

    def upsert_committee_decision(self, *, payload: dict[str, Any]) -> None:
        normalized = dict(payload)
        decision_id = str(normalized["id"])
        organization_id = normalized.get("organization_id")
        now = str(normalized.get("timestamp") or _utc_now_iso())
        normalized.update({"id": decision_id, "kind": "committee_decision", "status": "completed"})
        self.connection.execute(
            """
            INSERT INTO jobs (
                id, organization_id, kind, status, version, attempt, max_attempts,
                worker_id, heartbeat_at_utc, lease_expires_at_utc, rq_job_id,
                stage, progress, request_json, payload_json, error,
                created_at_utc, updated_at_utc, started_at_utc, finished_at_utc
            ) VALUES (?, ?, 'committee_decision', 'completed', 0, 0, 1, NULL, NULL, NULL, NULL,
                      'completed', 1, '{}', ?, NULL, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET organization_id = excluded.organization_id,
                payload_json = excluded.payload_json, updated_at_utc = excluded.updated_at_utc,
                finished_at_utc = excluded.finished_at_utc
            """,
            (decision_id, organization_id, _json_dump(normalized), now, now, now, now),
        )

    def upsert_market_research_report(
        self, *, organization_id: str, user_id: str | None, payload: dict[str, Any]
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        ticker = str(payload.get("ticker") or report.get("ticker") or "UNKNOWN").upper()
        analysis_date = str(payload.get("analysis_date") or report.get("analysis_date") or now[:10])
        horizon = str(payload.get("horizon") or report.get("time_horizon") or "swing")
        job_id = payload.get("job_id")
        report_id = str(payload.get("id") or payload.get("report_id") or self.store.stable_id("mrr", f"{organization_id}:{user_id or 'machine'}:{job_id or uuid4().hex}"))
        status = str(payload.get("status") or "completed")
        source_references = payload.get("source_references", report.get("source_references", []))
        provider_metadata = payload.get("provider_metadata", report.get("metadata", {}))
        warnings = payload.get("warnings", report.get("warnings", []))
        summary = payload.get("summary", report.get("summary"))
        decision = payload.get("decision", report.get("decision"))
        confidence_value = payload.get("confidence", report.get("confidence"))
        completed_at = payload.get("completed_at_utc")
        if completed_at is None and status == "completed":
            completed_at = str(report.get("created_at_utc") or now)
        values = (
            report_id, organization_id, user_id, str(job_id) if job_id else None,
            payload.get("parent_report_id"), int(payload.get("version") or 1), ticker, analysis_date, horizon,
            str(payload.get("report_type") or "market_research_committee"),
            str(payload.get("title") or f"{ticker} {horizon} research - {analysis_date}"), status,
            str(decision) if decision is not None else None,
            int(confidence_value) if confidence_value is not None else None,
            str(summary) if summary is not None else None,
            str(payload.get("disclaimer") or report.get("disclaimer") or "For research and educational purposes only. Not financial advice."),
            _json_dump(context), _json_dump(report), _json_dump(source_references or []),
            _json_dump(provider_metadata or {}), _json_dump(warnings or []), payload.get("artifact_id"),
            payload.get("error"), str(payload.get("created_at_utc") or now),
            str(payload.get("updated_at_utc") or now), completed_at, payload.get("deleted_at_utc"),
        )
        self.connection.execute(
            """
            INSERT INTO market_research_reports (
                id, organization_id, user_id, job_id, parent_report_id, version,
                ticker, analysis_date, horizon, report_type, title, status,
                decision, confidence, summary, disclaimer, context_json, report_json,
                source_references_json, provider_metadata_json, warnings_json, artifact_id,
                error, created_at_utc, updated_at_utc, completed_at_utc, deleted_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET organization_id = excluded.organization_id,
                user_id = excluded.user_id, job_id = excluded.job_id,
                parent_report_id = excluded.parent_report_id, version = excluded.version,
                ticker = excluded.ticker, analysis_date = excluded.analysis_date,
                horizon = excluded.horizon, report_type = excluded.report_type, title = excluded.title,
                status = excluded.status, decision = excluded.decision, confidence = excluded.confidence,
                summary = excluded.summary, disclaimer = excluded.disclaimer,
                context_json = excluded.context_json, report_json = excluded.report_json,
                source_references_json = excluded.source_references_json,
                provider_metadata_json = excluded.provider_metadata_json, warnings_json = excluded.warnings_json,
                artifact_id = excluded.artifact_id, error = excluded.error,
                updated_at_utc = excluded.updated_at_utc,
                completed_at_utc = COALESCE(excluded.completed_at_utc, market_research_reports.completed_at_utc),
                deleted_at_utc = excluded.deleted_at_utc
            """,
            values,
        )
        row = self.connection.execute(
            "SELECT * FROM market_research_reports WHERE organization_id = ? AND id = ?",
            (organization_id, report_id),
        ).fetchone()
        return self.store._market_research_report_row(row)


class SQLiteMetadataStore:
    """Small durable metadata store for the modular-monolith stage.

    The heavy research outputs still belong in parquet/JSON artifacts. SQLite is
    used for operational metadata that should be easy to query from API routes,
    workers, and future admin screens without reading a directory tree.
    """

    def __init__(self, path: str | Path, *, enable_demo_accounts: bool = True) -> None:
        self.path = Path(path)
        self.enable_demo_accounts = enable_demo_accounts
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    worker_id TEXT,
                    heartbeat_at_utc TEXT,
                    lease_expires_at_utc TEXT,
                    rq_job_id TEXT,
                    stage TEXT,
                    progress REAL NOT NULL DEFAULT 0,
                    request_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    error TEXT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    started_at_utc TEXT,
                    finished_at_utc TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_kind_created
                    ON jobs(kind, created_at_utc DESC);

                CREATE INDEX IF NOT EXISTS idx_jobs_kind_status
                    ON jobs(kind, status);

                CREATE TABLE IF NOT EXISTS deployment_configs (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT,
                    source TEXT NOT NULL,
                    path TEXT,
                    config_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS experiment_runs (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT,
                    kind TEXT NOT NULL,
                    artifact_dir TEXT,
                    summary_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_experiment_runs_kind_created
                    ON experiment_runs(kind, created_at_utc DESC);

                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    artifact_type TEXT NOT NULL,
                    source_id TEXT,
                    provider TEXT NOT NULL,
                    storage_key TEXT NOT NULL,
                    uri TEXT NOT NULL,
                    file_count INTEGER NOT NULL DEFAULT 0,
                    byte_count INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_artifacts_org_type_updated
                    ON artifacts(organization_id, artifact_type, updated_at_utc DESC);

                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    status TEXT NOT NULL DEFAULT 'active',
                    email_verified_at_utc TEXT,
                    mfa_secret TEXT,
                    mfa_pending_secret TEXT,
                    mfa_enabled INTEGER NOT NULL DEFAULT 0,
                    mfa_last_counter INTEGER,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    last_login_at_utc TEXT
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at_utc TEXT NOT NULL,
                    expires_at_utc TEXT
                );

                CREATE TABLE IF NOT EXISTS auth_tokens (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    purpose TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at_utc TEXT NOT NULL,
                    expires_at_utc TEXT NOT NULL,
                    consumed_at_utc TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_auth_tokens_user_purpose
                    ON auth_tokens(user_id, purpose, created_at_utc DESC);

                CREATE TABLE IF NOT EXISTS organizations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    owner_user_id TEXT NOT NULL REFERENCES users(id),
                    billing_email TEXT,
                    stripe_customer_id TEXT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS organization_members (
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    PRIMARY KEY (organization_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    description TEXT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    UNIQUE (organization_id, slug)
                );

                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                    job_id TEXT,
                    name TEXT NOT NULL,
                    pipeline TEXT NOT NULL,
                    status TEXT NOT NULL,
                    artifact_dir TEXT,
                    summary_json TEXT NOT NULL,
                    validation_json TEXT NOT NULL,
                    lineage_json TEXT NOT NULL,
                    readiness_json TEXT NOT NULL,
                    trades_json TEXT NOT NULL,
                    sentiment_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_experiments_org_created
                    ON experiments(organization_id, created_at_utc DESC);

                CREATE TABLE IF NOT EXISTS paper_deployments (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    config_json TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    idempotency_key TEXT,
                    source TEXT NOT NULL,
                    legacy_config_id TEXT,
                    created_by_user_id TEXT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    UNIQUE (organization_id, id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_deployments_org_idempotency
                    ON paper_deployments(organization_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL;

                CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_deployments_org_legacy_config
                    ON paper_deployments(organization_id, legacy_config_id)
                    WHERE legacy_config_id IS NOT NULL;

                CREATE INDEX IF NOT EXISTS idx_paper_deployments_org_status_updated
                    ON paper_deployments(organization_id, status, updated_at_utc DESC);

                CREATE TABLE IF NOT EXISTS paper_agents (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                    deployment_id TEXT,
                    name TEXT NOT NULL,
                    pipeline TEXT NOT NULL,
                    status TEXT NOT NULL,
                    fake_cash REAL NOT NULL DEFAULT 0,
                    config_json TEXT NOT NULL,
                    latest_payload_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_paper_agents_org_updated
                    ON paper_agents(organization_id, updated_at_utc DESC);

                CREATE TABLE IF NOT EXISTS paper_runs (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    deployment_id TEXT NOT NULL,
                    job_id TEXT,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    deployment_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    asof_date TEXT,
                    run_index INTEGER NOT NULL DEFAULT 1,
                    request_json TEXT NOT NULL,
                    deployment_config_json TEXT NOT NULL,
                    batch_summary_json TEXT NOT NULL DEFAULT '{}',
                    aggregate_payload_json TEXT NOT NULL DEFAULT '{}',
                    artifact_id TEXT REFERENCES artifacts(id) ON DELETE SET NULL,
                    error TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    started_at_utc TEXT,
                    completed_at_utc TEXT,
                    FOREIGN KEY (organization_id, deployment_id)
                        REFERENCES paper_deployments(organization_id, id) ON DELETE RESTRICT,
                    UNIQUE (organization_id, deployment_id, idempotency_key)
                );

                CREATE INDEX IF NOT EXISTS idx_paper_runs_org_deployment_created
                    ON paper_runs(organization_id, deployment_id, created_at_utc DESC);

                CREATE INDEX IF NOT EXISTS idx_paper_runs_org_status_updated
                    ON paper_runs(organization_id, status, updated_at_utc DESC);

                CREATE INDEX IF NOT EXISTS idx_paper_runs_org_job_asof
                    ON paper_runs(organization_id, job_id, asof_date, run_index);

                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    provider_json TEXT NOT NULL,
                    schema_json TEXT NOT NULL,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_datasets_org_updated
                    ON datasets(organization_id, updated_at_utc DESC);

                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    masked_value TEXT NOT NULL,
                    secret_ref TEXT,
                    token_hash TEXT,
                    scopes_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    last_used_at_utc TEXT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_api_keys_org_provider
                    ON api_keys(organization_id, provider);

                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL UNIQUE REFERENCES organizations(id) ON DELETE CASCADE,
                    plan TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stripe_customer_id TEXT,
                    stripe_subscription_id TEXT,
                    stripe_event_created_at INTEGER NOT NULL DEFAULT 0,
                    stripe_event_id TEXT,
                    current_period_end_utc TEXT,
                    usage_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS organization_quotas (
                    organization_id TEXT PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
                    quotas_json TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS telemetry_events (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT,
                    user_id TEXT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    properties_json TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    consent TEXT NOT NULL,
                    occurred_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_telemetry_org_time
                    ON telemetry_events(organization_id, occurred_at_utc DESC);

                CREATE INDEX IF NOT EXISTS idx_telemetry_name_time
                    ON telemetry_events(name, occurred_at_utc DESC);

                CREATE TABLE IF NOT EXISTS refresh_statuses (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    last_success_at_utc TEXT,
                    last_attempt_at_utc TEXT,
                    next_due_at_utc TEXT NOT NULL,
                    latest_run_id TEXT,
                    last_error TEXT,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_refresh_status_due
                    ON refresh_statuses(next_due_at_utc, status);

                CREATE TABLE IF NOT EXISTS refresh_runs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    started_at_utc TEXT,
                    finished_at_utc TEXT,
                    locked_until_utc TEXT,
                    summary_json TEXT NOT NULL,
                    error TEXT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_refresh_runs_user_created
                    ON refresh_runs(user_id, created_at_utc DESC);

                CREATE TABLE IF NOT EXISTS stripe_events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    processed_at_utc TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL DEFAULT '',
                    event_created_at INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'processed',
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    max_attempts INTEGER NOT NULL DEFAULT 5,
                    claim_token TEXT,
                    claimed_at_utc TEXT,
                    last_error_code TEXT,
                    created_at_utc TEXT NOT NULL DEFAULT '',
                    updated_at_utc TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS usage_events (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    user_id TEXT,
                    feature TEXT NOT NULL,
                    quantity REAL NOT NULL DEFAULT 1,
                    properties_json TEXT NOT NULL,
                    occurred_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_usage_org_feature_time
                    ON usage_events(organization_id, feature, occurred_at_utc DESC);

                CREATE TABLE IF NOT EXISTS quota_usage_counters (
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    feature TEXT NOT NULL,
                    window_start_utc TEXT NOT NULL,
                    window_end_utc TEXT NOT NULL,
                    quantity REAL NOT NULL DEFAULT 0,
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (organization_id, feature, window_start_utc, window_end_utc)
                );

                CREATE INDEX IF NOT EXISTS idx_quota_counters_window
                    ON quota_usage_counters(window_end_utc, organization_id, feature);

                CREATE TABLE IF NOT EXISTS audit_log (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT,
                    actor_user_id TEXT,
                    action TEXT NOT NULL,
                    target_type TEXT,
                    target_id TEXT,
                    metadata_json TEXT NOT NULL,
                    occurred_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_org_time
                    ON audit_log(organization_id, occurred_at_utc DESC);

                CREATE TABLE IF NOT EXISTS user_strategies (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    root_strategy_id TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    validation_json TEXT NOT NULL,
                    approval_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    approved_at_utc TEXT,
                    disabled_at_utc TEXT,
                    deleted_at_utc TEXT,
                    backtest_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_user_strategies_owner_status
                    ON user_strategies(organization_id, owner_user_id, status, updated_at_utc DESC);

                CREATE INDEX IF NOT EXISTS idx_user_strategies_admin
                    ON user_strategies(status, risk_level, created_at_utc DESC);

                CREATE TABLE IF NOT EXISTS strategy_listings (
                    id TEXT PRIMARY KEY,
                    publisher_organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    publisher_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    source_user_strategy_id TEXT NOT NULL REFERENCES user_strategies(id) ON DELETE RESTRICT,
                    title TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    summary TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_version_id TEXT REFERENCES strategy_listing_versions(id) ON DELETE RESTRICT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    published_at_utc TEXT,
                    archived_at_utc TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_strategy_listings_public
                    ON strategy_listings(status, visibility, published_at_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_strategy_listings_publisher
                    ON strategy_listings(publisher_organization_id, updated_at_utc DESC);

                CREATE TABLE IF NOT EXISTS strategy_listing_versions (
                    id TEXT PRIMARY KEY,
                    listing_id TEXT NOT NULL REFERENCES strategy_listings(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    strategy_spec_json TEXT NOT NULL,
                    catalog_snapshot_json TEXT NOT NULL,
                    validation_snapshot_json TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    source_strategy_version INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_by_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                    created_at_utc TEXT NOT NULL,
                    UNIQUE(listing_id, version),
                    UNIQUE(listing_id, content_hash)
                );

                CREATE TABLE IF NOT EXISTS strategy_marketplace_subscriptions (
                    id TEXT PRIMARY KEY,
                    subscriber_organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    subscriber_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    listing_id TEXT NOT NULL REFERENCES strategy_listings(id) ON DELETE RESTRICT,
                    pinned_listing_version_id TEXT NOT NULL REFERENCES strategy_listing_versions(id) ON DELETE RESTRICT,
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    cancelled_at_utc TEXT,
                    UNIQUE(subscriber_organization_id, listing_id)
                );

                CREATE INDEX IF NOT EXISTS idx_marketplace_subscriptions_owner
                    ON strategy_marketplace_subscriptions(subscriber_organization_id, status, updated_at_utc DESC);

                CREATE TABLE IF NOT EXISTS market_research_reports (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    user_id TEXT,
                    job_id TEXT,
                    parent_report_id TEXT,
                    version INTEGER NOT NULL DEFAULT 1,
                    ticker TEXT NOT NULL,
                    analysis_date TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    report_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decision TEXT,
                    confidence INTEGER,
                    summary TEXT,
                    disclaimer TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    source_references_json TEXT NOT NULL,
                    provider_metadata_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    artifact_id TEXT,
                    error TEXT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    completed_at_utc TEXT,
                    deleted_at_utc TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_market_research_reports_user_created
                    ON market_research_reports(organization_id, user_id, created_at_utc DESC);

                CREATE INDEX IF NOT EXISTS idx_market_research_reports_ticker_created
                    ON market_research_reports(organization_id, user_id, ticker, created_at_utc DESC);

                CREATE INDEX IF NOT EXISTS idx_market_research_reports_status_created
                    ON market_research_reports(organization_id, user_id, status, created_at_utc DESC);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_market_research_reports_job
                    ON market_research_reports(job_id)
                    WHERE job_id IS NOT NULL;
                """
            )
        self._migrate_legacy_columns()
        if self.enable_demo_accounts:
            self.ensure_demo_workspace(
                display_name="Admin Demo Quant",
                role="admin",
                plan="pro",
                subscription_status="active",
                organization_role="owner",
            )
            self.ensure_demo_workspace(
                email="user@quantops.local",
                display_name="Normal Demo User",
                password_hash="demo-user-password-hash",
                role="user",
                organization_name="QuantOps Free Demo",
                organization_slug="quantops-free-demo",
                plan="free",
                subscription_status="free",
                organization_role="member",
            )

    def _migrate_legacy_columns(self) -> None:
        """Keep older local SQLite databases compatible with newer production rules."""

        with self._connect() as connection:
            existing = {row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
            if "role" not in existing:
                connection.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
            if "status" not in existing:
                connection.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
            if "email_verified_at_utc" not in existing:
                connection.execute("ALTER TABLE users ADD COLUMN email_verified_at_utc TEXT")
            if "mfa_secret" not in existing:
                connection.execute("ALTER TABLE users ADD COLUMN mfa_secret TEXT")
            if "mfa_enabled" not in existing:
                connection.execute("ALTER TABLE users ADD COLUMN mfa_enabled INTEGER NOT NULL DEFAULT 0")
            if "mfa_pending_secret" not in existing:
                connection.execute("ALTER TABLE users ADD COLUMN mfa_pending_secret TEXT")
            if "mfa_last_counter" not in existing:
                connection.execute("ALTER TABLE users ADD COLUMN mfa_last_counter INTEGER")
            table_columns = {
                table: {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
                for table in (
                    "jobs", "deployment_configs", "experiment_runs", "api_keys", "paper_agents",
                    "subscriptions", "stripe_events",
                )
            }
            if "organization_id" not in table_columns["jobs"]:
                connection.execute("ALTER TABLE jobs ADD COLUMN organization_id TEXT")
            job_claim_columns = {
                "version": "INTEGER NOT NULL DEFAULT 0",
                "attempt": "INTEGER NOT NULL DEFAULT 0",
                "max_attempts": "INTEGER NOT NULL DEFAULT 3",
                "worker_id": "TEXT",
                "heartbeat_at_utc": "TEXT",
                "lease_expires_at_utc": "TEXT",
                "rq_job_id": "TEXT",
            }
            for column, definition in job_claim_columns.items():
                if column not in table_columns["jobs"]:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
            if "organization_id" not in table_columns["deployment_configs"]:
                connection.execute("ALTER TABLE deployment_configs ADD COLUMN organization_id TEXT")
            if "organization_id" not in table_columns["experiment_runs"]:
                connection.execute("ALTER TABLE experiment_runs ADD COLUMN organization_id TEXT")
            if "deployment_id" not in table_columns["paper_agents"]:
                connection.execute("ALTER TABLE paper_agents ADD COLUMN deployment_id TEXT")
            if "token_hash" not in table_columns["api_keys"]:
                connection.execute("ALTER TABLE api_keys ADD COLUMN token_hash TEXT")
            if "scopes_json" not in table_columns["api_keys"]:
                connection.execute("ALTER TABLE api_keys ADD COLUMN scopes_json TEXT NOT NULL DEFAULT '[]'")
            if "last_used_at_utc" not in table_columns["api_keys"]:
                connection.execute("ALTER TABLE api_keys ADD COLUMN last_used_at_utc TEXT")
            subscription_columns = {
                "stripe_event_created_at": "INTEGER NOT NULL DEFAULT 0",
                "stripe_event_id": "TEXT",
            }
            for column, definition in subscription_columns.items():
                if column not in table_columns["subscriptions"]:
                    connection.execute(f"ALTER TABLE subscriptions ADD COLUMN {column} {definition}")
            stripe_event_columns = {
                "payload_hash": "TEXT NOT NULL DEFAULT ''",
                "event_created_at": "INTEGER NOT NULL DEFAULT 0",
                "status": "TEXT NOT NULL DEFAULT 'processed'",
                "attempt_count": "INTEGER NOT NULL DEFAULT 1",
                "max_attempts": "INTEGER NOT NULL DEFAULT 5",
                "claim_token": "TEXT",
                "claimed_at_utc": "TEXT",
                "last_error_code": "TEXT",
                "created_at_utc": "TEXT NOT NULL DEFAULT ''",
                "updated_at_utc": "TEXT NOT NULL DEFAULT ''",
            }
            for column, definition in stripe_event_columns.items():
                if column not in table_columns["stripe_events"]:
                    connection.execute(f"ALTER TABLE stripe_events ADD COLUMN {column} {definition}")
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_jobs_org_kind_created
                    ON jobs(organization_id, kind, created_at_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_org_kind_status
                    ON jobs(organization_id, kind, status);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_agents_org_deployment_name
                    ON paper_agents(organization_id, deployment_id, name)
                    WHERE deployment_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_paper_agents_org_deployment_updated
                    ON paper_agents(organization_id, deployment_id, updated_at_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_status_lease
                    ON jobs(status, lease_expires_at_utc);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_rq_job_id_unique
                    ON jobs(rq_job_id)
                    WHERE rq_job_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_experiment_runs_org_kind_created
                    ON experiment_runs(organization_id, kind, created_at_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_api_keys_token_hash
                    ON api_keys(token_hash);
                CREATE INDEX IF NOT EXISTS idx_stripe_events_status_updated
                    ON stripe_events(status, updated_at_utc);
                CREATE TABLE IF NOT EXISTS quota_usage_counters (
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    feature TEXT NOT NULL,
                    window_start_utc TEXT NOT NULL,
                    window_end_utc TEXT NOT NULL,
                    quantity REAL NOT NULL DEFAULT 0,
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (organization_id, feature, window_start_utc, window_end_utc)
                );
                CREATE INDEX IF NOT EXISTS idx_quota_counters_window
                    ON quota_usage_counters(window_end_utc, organization_id, feature);
                """
            )

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def stable_id(prefix: str, value: str) -> str:
        digest = hashlib.sha1(value.encode("utf-8"), usedforsecurity=False).hexdigest()[:20]
        return f"{prefix}_{digest}"

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return None if row is None else dict(row)

    @staticmethod
    def _mask_secret(secret: str) -> str:
        if not secret:
            return "empty"
        if len(secret) <= 8:
            return f"{secret[:2]}...{secret[-2:]}"
        return f"{secret[:4]}...{secret[-4:]}"

    @staticmethod
    def _slug(value: str) -> str:
        slug = "-".join(part for part in value.lower().replace("_", "-").split() if part)
        return "".join(char for char in slug if char.isalnum() or char == "-").strip("-") or "workspace"

    def ensure_demo_workspace(
        self,
        *,
        email: str = "demo@quantops.local",
        display_name: str = "Admin Demo Quant",
        password_hash: str = "demo-password-hash",
        role: str = "admin",
        organization_name: str = "QuantOps Demo",
        organization_slug: str = "quantops-demo",
        plan: str = "pro_trial",
        subscription_status: str = "trialing",
        organization_role: str = "owner",
    ) -> dict[str, Any]:
        """Seed a local-first workspace so the SaaS shell is usable immediately.

        Password verification lives in the backend service. The seed hash is
        intentionally a placeholder that the auth service accepts only for the
        documented demo password.
        """

        now = _utc_now_iso()
        user_id = self.stable_id("usr", email.casefold())
        org_id = self.stable_id("org", organization_slug)
        project_id = self.stable_id("prj", f"{organization_slug}-research")
        subscription_id = self.stable_id("sub", org_id)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO users (id, email, display_name, password_hash, role, status, created_at_utc, updated_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    role = excluded.role,
                    status = COALESCE(users.status, excluded.status),
                    updated_at_utc = excluded.updated_at_utc
                """,
                (user_id, email.casefold(), display_name, password_hash, role, "active", now, now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO organizations (
                    id, name, slug, owner_user_id, billing_email, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (org_id, organization_name, organization_slug, user_id, email.casefold(), now, now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO organization_members (organization_id, user_id, role, created_at_utc)
                VALUES (?, ?, ?, ?)
                """,
                (org_id, user_id, organization_role, now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO projects (
                    id, organization_id, name, slug, description, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    org_id,
                    "Default Research Workspace",
                    "default-research",
                    "Starter project for experiments, paper agents, and sentiment datasets.",
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO subscriptions (
                    id, organization_id, plan, status, usage_json, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (subscription_id, org_id, plan, subscription_status, _json_dump({"backtests": 0, "paper_runs": 0}), now, now),
            )
        return {"user_id": user_id, "organization_id": org_id, "project_id": project_id}

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE email = ?", (email.casefold(),)).fetchone()
        return self._row_to_dict(row)

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_dict(row)

    def create_user_workspace(
        self,
        *,
        email: str,
        display_name: str,
        password_hash: str,
        organization_name: str,
        role: str = "user",
        plan: str = "free",
        subscription_status: str = "free",
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        normalized_email = email.casefold().strip()
        user_id = self.stable_id("usr", normalized_email)
        base_slug = self._slug(organization_name)
        org_id = self.stable_id("org", f"{base_slug}:{normalized_email}")
        project_id = self.stable_id("prj", f"{org_id}:default-research")
        subscription_id = self.stable_id("sub", org_id)
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO users (
                        id, email, display_name, password_hash, role, status,
                        created_at_utc, updated_at_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, normalized_email, display_name, password_hash, role, "active", now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("An account already exists for this email.") from exc
            slug = base_slug
            suffix = 2
            while connection.execute("SELECT 1 FROM organizations WHERE slug = ?", (slug,)).fetchone() is not None:
                slug = f"{base_slug}-{suffix}"
                suffix += 1
            connection.execute(
                """
                INSERT INTO organizations (
                    id, name, slug, owner_user_id, billing_email, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (org_id, organization_name, slug, user_id, normalized_email, now, now),
            )
            connection.execute(
                """
                INSERT INTO organization_members (organization_id, user_id, role, created_at_utc)
                VALUES (?, ?, ?, ?)
                """,
                (org_id, user_id, "owner", now),
            )
            connection.execute(
                """
                INSERT INTO projects (
                    id, organization_id, name, slug, description, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    org_id,
                    "First Research Workspace",
                    "first-research-workspace",
                    "Starter project created during signup for experiments, datasets, and paper agents.",
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO subscriptions (
                    id, organization_id, plan, status, usage_json, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (subscription_id, org_id, plan, subscription_status, _json_dump({"backtests": 0, "paper_runs": 0}), now, now),
            )
        return {"user_id": user_id, "organization_id": org_id, "project_id": project_id}

    def count_active_admins(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND status = 'active'").fetchone()
        return int(row[0])

    def list_admin_users(
        self,
        *,
        search: str | None = None,
        role: str | None = None,
        status: str | None = None,
        sort_by: str = "created_at_utc",
        sort_dir: str = "desc",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        allowed_sorts = {
            "email": "u.email",
            "display_name": "u.display_name",
            "role": "u.role",
            "status": "u.status",
            "created_at_utc": "u.created_at_utc",
            "last_login_at_utc": "u.last_login_at_utc",
            "plan": "s.plan",
            "subscription_status": "s.status",
        }
        clauses: list[str] = []
        params: list[Any] = []
        if search:
            clauses.append("(u.email LIKE ? OR u.display_name LIKE ? OR o.name LIKE ?)")
            term = f"%{search.strip()}%"
            params.extend([term, term, term])
        if role:
            clauses.append("u.role = ?")
            params.append(role)
        if status:
            clauses.append("u.status = ?")
            params.append(status)
        query = """
            SELECT
                u.id, u.email, u.display_name, u.role, u.status,
                u.created_at_utc, u.updated_at_utc, u.last_login_at_utc,
                o.id AS organization_id, o.name AS organization_name,
                m.role AS organization_role,
                s.plan, s.status AS subscription_status, s.current_period_end_utc
            FROM users u
            LEFT JOIN organization_members m ON m.user_id = u.id
            LEFT JOIN organizations o ON o.id = m.organization_id
            LEFT JOIN subscriptions s ON s.organization_id = o.id
        """
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
        query += f" ORDER BY {allowed_sorts.get(sort_by, 'u.created_at_utc')} {direction} LIMIT ?"
        params.append(int(limit))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        seen: set[str] = set()
        users: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            if payload["id"] in seen:
                continue
            seen.add(payload["id"])
            users.append(payload)
        return users

    def update_user_role(self, *, user_id: str, role: str) -> dict[str, Any] | None:
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute("UPDATE users SET role = ?, updated_at_utc = ? WHERE id = ?", (role, now, user_id))
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_dict(row)

    def update_user_status(self, *, user_id: str, status: str) -> dict[str, Any] | None:
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute("UPDATE users SET status = ?, updated_at_utc = ? WHERE id = ?", (status, now, user_id))
            if status != "active":
                connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_dict(row)

    def mark_email_verified(self, *, user_id: str) -> dict[str, Any] | None:
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET email_verified_at_utc = ?, updated_at_utc = ? WHERE id = ?",
                (now, now, user_id),
            )
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_dict(row)

    def update_user_password(self, *, user_id: str, password_hash: str) -> dict[str, Any] | None:
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at_utc = ? WHERE id = ?",
                (password_hash, now, user_id),
            )
            connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_dict(row)

    def set_user_mfa_secret(self, *, user_id: str, secret: str, enabled: bool) -> dict[str, Any] | None:
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET mfa_secret = ?, mfa_enabled = ?, updated_at_utc = ? WHERE id = ?",
                (secret, 1 if enabled else 0, now, user_id),
            )
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_dict(row)

    def set_user_mfa_pending_secret(self, *, user_id: str, secret: str) -> dict[str, Any] | None:
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET mfa_pending_secret = ?, updated_at_utc = ? WHERE id = ?",
                (secret, now, user_id),
            )
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_dict(row)

    def advance_user_mfa_counter(
        self,
        *,
        user_id: str,
        counter: int,
        pending_secret: str | None = None,
        promote_pending: bool = False,
    ) -> dict[str, Any] | None:
        """Atomically reject TOTP replay and optionally promote a pending factor."""

        now = _utc_now_iso()
        with self._connect() as connection:
            if promote_pending:
                if pending_secret is None:
                    raise ValueError("pending_secret is required when promoting MFA")
                cursor = connection.execute(
                    """
                    UPDATE users
                    SET mfa_secret = mfa_pending_secret, mfa_pending_secret = NULL,
                        mfa_enabled = 1, mfa_last_counter = ?, updated_at_utc = ?
                    WHERE id = ? AND mfa_pending_secret = ?
                    """,
                    (counter, now, user_id, pending_secret),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE users SET mfa_last_counter = ?, updated_at_utc = ?
                    WHERE id = ? AND mfa_enabled = 1
                      AND (mfa_last_counter IS NULL OR mfa_last_counter < ?)
                    """,
                    (counter, now, user_id, counter),
                )
            if cursor.rowcount != 1:
                return None
            if promote_pending:
                connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_dict(row)

    def revoke_user_sessions(self, *, user_id: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        return int(cursor.rowcount)

    def create_auth_token(
        self,
        *,
        user_id: str,
        purpose: str,
        token: str,
        expires_at_utc: str,
    ) -> dict[str, Any]:
        token_id = self.stable_id("tok", f"{user_id}:{purpose}:{uuid4().hex}")
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO auth_tokens (
                    id, user_id, purpose, token_hash, created_at_utc, expires_at_utc, consumed_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (token_id, user_id, purpose, self.hash_token(token), now, expires_at_utc, None),
            )
            row = connection.execute("SELECT * FROM auth_tokens WHERE id = ?", (token_id,)).fetchone()
        return dict(row)

    def consume_auth_token(self, *, purpose: str, token: str) -> dict[str, Any] | None:
        token_hash = self.hash_token(token)
        now = _utc_now_iso()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM auth_tokens
                WHERE purpose = ? AND token_hash = ? AND consumed_at_utc IS NULL
                """,
                (purpose, token_hash),
            ).fetchone()
            payload = self._row_to_dict(row)
            expires_at = _parse_utc_iso(str(payload.get("expires_at_utc"))) if payload else None
            if payload is None or expires_at is None or expires_at <= datetime.now(UTC):
                return None
            connection.execute(
                "UPDATE auth_tokens SET consumed_at_utc = ? WHERE id = ?",
                (now, payload["id"]),
            )
        return payload

    def admin_metric_snapshot(self) -> dict[str, Any]:
        with self._connect() as connection:
            users_total = int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
            users_active = int(connection.execute("SELECT COUNT(*) FROM users WHERE status = 'active'").fetchone()[0])
            admins_active = int(connection.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND status = 'active'").fetchone()[0])
            signups_7d = int(connection.execute("SELECT COUNT(*) FROM users WHERE created_at_utc >= datetime('now', '-7 days')").fetchone()[0])
            signups_30d = int(connection.execute("SELECT COUNT(*) FROM users WHERE created_at_utc >= datetime('now', '-30 days')").fetchone()[0])
            active_7d = int(connection.execute("SELECT COUNT(*) FROM users WHERE last_login_at_utc >= datetime('now', '-7 days')").fetchone()[0])
            subscriptions = {
                row["status"]: int(row["count"])
                for row in connection.execute("SELECT status, COUNT(*) AS count FROM subscriptions GROUP BY status").fetchall()
            }
            plans = {
                row["plan"]: int(row["count"])
                for row in connection.execute("SELECT plan, COUNT(*) AS count FROM subscriptions GROUP BY plan").fetchall()
            }
        return {
            "users_total": users_total,
            "users_active": users_active,
            "admins_active": admins_active,
            "signups_7d": signups_7d,
            "signups_30d": signups_30d,
            "active_users_7d": active_7d,
            "subscriptions_by_status": subscriptions,
            "plans": plans,
        }

    def create_auth_session(self, *, user_id: str, token: str, expires_at_utc: str | None = None) -> None:
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO auth_sessions (token_hash, user_id, created_at_utc, expires_at_utc)
                VALUES (?, ?, ?, ?)
                """,
                (self.hash_token(token), user_id, now, expires_at_utc),
            )
            connection.execute("UPDATE users SET last_login_at_utc = ?, updated_at_utc = ? WHERE id = ?", (now, now, user_id))

    def get_auth_session(self, *, token: str) -> dict[str, Any] | None:
        token_hash = self.hash_token(token)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.token_hash, s.user_id, s.created_at_utc, s.expires_at_utc,
                       u.email, u.display_name
                FROM auth_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            payload = self._row_to_dict(row)
            expires_at = _parse_utc_iso(str(payload.get("expires_at_utc"))) if payload else None
            if expires_at is not None and expires_at <= datetime.now(UTC):
                connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
                return None
        return payload

    def delete_auth_session(self, *, token: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (self.hash_token(token),))

    def list_organizations_for_user(self, *, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT o.*, m.role
                FROM organizations o
                JOIN organization_members m ON m.organization_id = o.id
                WHERE m.user_id = ?
                ORDER BY o.created_at_utc ASC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_users_with_default_org(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT u.id AS user_id, u.email, u.display_name, o.id AS organization_id, o.name AS organization_name
                FROM users u
                JOIN organization_members m ON m.user_id = u.id
                JOIN organizations o ON o.id = m.organization_id
                WHERE m.role IN ('owner', 'admin', 'member')
                ORDER BY u.created_at_utc ASC, o.created_at_utc ASC
                """
            ).fetchall()
        seen: set[str] = set()
        users: list[dict[str, Any]] = []
        for row in rows:
            if row["user_id"] in seen:
                continue
            seen.add(row["user_id"])
            users.append(dict(row))
        return users

    def user_has_organization_access(self, *, user_id: str, organization_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM organization_members WHERE user_id = ? AND organization_id = ?",
                (user_id, organization_id),
            ).fetchone()
        return row is not None

    def get_default_organization_id(self, *, user_id: str) -> str | None:
        organizations = self.list_organizations_for_user(user_id=user_id)
        return str(organizations[0]["id"]) if organizations else None

    def list_projects(self, *, organization_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM projects WHERE organization_id = ? ORDER BY created_at_utc ASC",
                (organization_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_project(self, *, organization_id: str, name: str, description: str | None = None) -> dict[str, Any]:
        slug = "-".join(part for part in name.lower().replace("_", "-").split() if part) or "project"
        project_id = self.stable_id("prj", f"{organization_id}:{slug}:{uuid4().hex}")
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projects (id, organization_id, name, slug, description, created_at_utc, updated_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (project_id, organization_id, name, slug, description, now, now),
            )
            row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row)

    def get_subscription(self, *, organization_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM subscriptions WHERE organization_id = ?", (organization_id,)).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["usage"] = _json_load(payload.pop("usage_json"), {})
        return payload

    def upsert_subscription(self, *, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now_iso()
        subscription_id = str(payload.get("id") or self.stable_id("sub", organization_id))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO subscriptions (
                    id, organization_id, plan, status, stripe_customer_id, stripe_subscription_id,
                    stripe_event_created_at, stripe_event_id, current_period_end_utc,
                    usage_json, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(organization_id) DO UPDATE SET
                    plan = excluded.plan,
                    status = excluded.status,
                    stripe_customer_id = excluded.stripe_customer_id,
                    stripe_subscription_id = excluded.stripe_subscription_id,
                    current_period_end_utc = excluded.current_period_end_utc,
                    usage_json = excluded.usage_json,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    subscription_id,
                    organization_id,
                    str(payload.get("plan", "free")),
                    str(payload.get("status", "active")),
                    payload.get("stripe_customer_id"),
                    payload.get("stripe_subscription_id"),
                    int(payload.get("stripe_event_created_at", 0) or 0),
                    payload.get("stripe_event_id"),
                    payload.get("current_period_end_utc"),
                    _json_dump(payload.get("usage", {})),
                    str(payload.get("created_at_utc") or now),
                    now,
                ),
            )
        return self.get_subscription(organization_id=organization_id) or {}

    def apply_subscription_event(
        self,
        *,
        organization_id: str,
        payload: dict[str, Any],
        event_created_at: int,
        event_id: str,
    ) -> dict[str, Any]:
        if isinstance(event_created_at, bool) or not isinstance(event_created_at, int) or event_created_at <= 0:
            raise ValueError("Stripe event created timestamp must be a positive integer")
        normalized_event_id = str(event_id).strip()
        if not normalized_event_id:
            raise ValueError("Stripe event id must not be empty")
        now = _utc_now_iso()
        subscription_id = str(payload.get("id") or self.stable_id("sub", organization_id))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO subscriptions (
                    id, organization_id, plan, status, stripe_customer_id, stripe_subscription_id,
                    stripe_event_created_at, stripe_event_id, current_period_end_utc,
                    usage_json, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(organization_id) DO UPDATE SET
                    plan = excluded.plan,
                    status = excluded.status,
                    stripe_customer_id = excluded.stripe_customer_id,
                    stripe_subscription_id = excluded.stripe_subscription_id,
                    stripe_event_created_at = excluded.stripe_event_created_at,
                    stripe_event_id = excluded.stripe_event_id,
                    current_period_end_utc = excluded.current_period_end_utc,
                    usage_json = excluded.usage_json,
                    updated_at_utc = excluded.updated_at_utc
                WHERE excluded.stripe_event_created_at > subscriptions.stripe_event_created_at
                   OR (
                       excluded.stripe_event_created_at = subscriptions.stripe_event_created_at
                       AND excluded.stripe_event_id >= COALESCE(subscriptions.stripe_event_id, '')
                   )
                """,
                (
                    subscription_id,
                    organization_id,
                    str(payload.get("plan", "free")),
                    str(payload.get("status", "active")),
                    payload.get("stripe_customer_id"),
                    payload.get("stripe_subscription_id"),
                    event_created_at,
                    normalized_event_id,
                    payload.get("current_period_end_utc"),
                    _json_dump(payload.get("usage", {})),
                    str(payload.get("created_at_utc") or now),
                    now,
                ),
            )
            applied = cursor.rowcount == 1
            row = connection.execute(
                "SELECT * FROM subscriptions WHERE organization_id = ?",
                (organization_id,),
            ).fetchone()
        subscription = dict(row)
        subscription["usage"] = _json_load(subscription.pop("usage_json"), {})
        return {"applied": applied, "subscription": subscription}

    def claim_stripe_event(
        self,
        *,
        event_id: str,
        event_type: str,
        event_created_at: int,
        payload_hash: str,
        payload: dict[str, Any],
        max_attempts: int = 5,
        stale_after_seconds: int = 300,
    ) -> dict[str, Any]:
        if not event_id or not event_type or not payload_hash:
            raise ValueError("Stripe event identity is incomplete")
        if event_created_at <= 0 or max_attempts < 1 or stale_after_seconds < 1:
            raise ValueError("Stripe event retry metadata is invalid")
        payload_json = _canonical_safe_json(payload, field_name="Stripe event summary")
        now = _utc_now_iso()
        stale_before = (datetime.now(UTC) - timedelta(seconds=stale_after_seconds)).isoformat().replace("+00:00", "Z")
        claim_token = f"stripe_claim_{uuid4().hex}"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO stripe_events (
                    id, event_type, processed_at_utc, payload_json, payload_hash,
                    event_created_at, status, attempt_count, max_attempts, claim_token,
                    claimed_at_utc, last_error_code, created_at_utc, updated_at_utc
                ) VALUES (?, ?, '', ?, ?, ?, 'processing', 1, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    event_id,
                    event_type,
                    payload_json,
                    payload_hash,
                    event_created_at,
                    max_attempts,
                    claim_token,
                    now,
                    now,
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM stripe_events WHERE id = ?", (event_id,)).fetchone()
            if row is None:
                raise RuntimeError("Stripe event claim could not be persisted")
            if (
                str(row["event_type"]) != event_type
                or str(row["payload_hash"]) != payload_hash
                or int(row["event_created_at"] or 0) != event_created_at
            ):
                raise IdempotencyConflictError("Stripe event id was reused with different content")
            if row["claim_token"] == claim_token:
                return {"claimed": True, "duplicate": False, "exhausted": False, "claim_token": claim_token}
            if row["status"] == "processed":
                return {"claimed": False, "duplicate": True, "exhausted": False, "claim_token": None}
            attempts = int(row["attempt_count"] or 0)
            if attempts >= int(row["max_attempts"] or max_attempts):
                return {"claimed": False, "duplicate": False, "exhausted": True, "claim_token": None}
            retryable = row["status"] == "failed" or (
                row["status"] == "processing"
                and row["claimed_at_utc"] is not None
                and str(row["claimed_at_utc"]) <= stale_before
            )
            if not retryable:
                return {"claimed": False, "duplicate": False, "exhausted": False, "claim_token": None}
            cursor = connection.execute(
                """
                UPDATE stripe_events
                SET status = 'processing', attempt_count = attempt_count + 1,
                    claim_token = ?, claimed_at_utc = ?, last_error_code = NULL,
                    updated_at_utc = ?
                WHERE id = ? AND attempt_count = ? AND status = ?
                """,
                (claim_token, now, now, event_id, attempts, row["status"]),
            )
            claimed = cursor.rowcount == 1
        return {"claimed": claimed, "duplicate": False, "exhausted": False, "claim_token": claim_token if claimed else None}

    def complete_stripe_event(self, *, event_id: str, claim_token: str) -> bool:
        now = _utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE stripe_events
                SET status = 'processed', processed_at_utc = ?, claim_token = NULL,
                    claimed_at_utc = NULL, last_error_code = NULL, updated_at_utc = ?
                WHERE id = ? AND status = 'processing' AND claim_token = ?
                """,
                (now, now, event_id, claim_token),
            )
        return cursor.rowcount == 1

    def fail_stripe_event(self, *, event_id: str, claim_token: str, error_code: str) -> bool:
        now = _utc_now_iso()
        normalized_code = re.sub(r"[^a-z0-9_]+", "_", str(error_code).strip().lower())[:80] or "processing_failed"
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE stripe_events
                SET status = 'failed', claim_token = NULL, claimed_at_utc = NULL,
                    last_error_code = ?, updated_at_utc = ?
                WHERE id = ? AND status = 'processing' AND claim_token = ?
                """,
                (normalized_code, now, event_id, claim_token),
            )
        return cursor.rowcount == 1

    def get_stripe_event(self, *, event_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM stripe_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = _json_load(result.pop("payload_json"), {})
        return result

    def record_usage_event(
        self,
        *,
        organization_id: str,
        feature: str,
        quantity: float = 1.0,
        user_id: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        window_end = window_start + timedelta(days=1)
        reserved = self.reserve_usage_events(
            organization_id=organization_id,
            user_id=user_id,
            reservations=[
                {
                    "feature": feature,
                    "quantity": quantity,
                    "limit": -1.0,
                    "properties": properties or {},
                }
            ],
            window_start_utc=window_start.isoformat().replace("+00:00", "Z"),
            window_end_utc=window_end.isoformat().replace("+00:00", "Z"),
            occurred_at_utc=now.isoformat().replace("+00:00", "Z"),
        )
        return dict(reserved[0]["usage"])

    def reserve_usage_events(
        self,
        *,
        organization_id: str,
        reservations: list[dict[str, Any]],
        window_start_utc: str,
        window_end_utc: str,
        occurred_at_utc: str,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        window_start = _parse_utc_iso(window_start_utc)
        window_end = _parse_utc_iso(window_end_utc)
        occurred_at = _parse_utc_iso(occurred_at_utc)
        if window_start is None or window_end is None or occurred_at is None:
            raise ValueError("Quota windows and occurrence time must be ISO-8601 timestamps")
        if window_start >= window_end or not (window_start <= occurred_at < window_end):
            raise ValueError("Usage occurrence must be inside the half-open quota window")
        if not reservations:
            raise ValueError("At least one quota reservation is required")
        consolidated: dict[str, dict[str, Any]] = {}
        for reservation in reservations:
            feature = str(reservation.get("feature") or "").strip()
            quantity = reservation.get("quantity", 1.0)
            limit = reservation.get("limit")
            if not feature:
                raise ValueError("Quota feature must not be empty")
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(float(quantity)) or float(quantity) <= 0:
                raise ValueError("Quota quantity must be a finite positive number")
            if (
                isinstance(limit, bool)
                or not isinstance(limit, (int, float))
                or not math.isfinite(float(limit))
                or not (float(limit) == -1.0 or float(limit) > 0)
            ):
                raise ValueError("Quota limit must be finite and positive, or -1 for an internal unlimited reservation")
            if feature in consolidated:
                if float(consolidated[feature]["limit"]) != float(limit):
                    raise ValueError("Duplicate quota features must use the same limit")
                consolidated[feature]["quantity"] += float(quantity)
                consolidated[feature]["properties"].append(reservation.get("properties") or {})
            else:
                consolidated[feature] = {
                    "feature": feature,
                    "quantity": float(quantity),
                    "limit": float(limit),
                    "properties": [reservation.get("properties") or {}],
                }

        normalized_start = window_start.astimezone(UTC).isoformat().replace("+00:00", "Z")
        normalized_end = window_end.astimezone(UTC).isoformat().replace("+00:00", "Z")
        normalized_occurred = occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        results: list[dict[str, Any]] = []
        with self._connect() as connection:
            for reservation in sorted(consolidated.values(), key=lambda item: item["feature"]):
                feature = reservation["feature"]
                quantity = float(reservation["quantity"])
                limit = float(reservation["limit"])
                connection.execute(
                    """
                    INSERT OR IGNORE INTO quota_usage_counters (
                        organization_id, feature, window_start_utc, window_end_utc,
                        quantity, updated_at_utc
                    )
                    SELECT ?, ?, ?, ?, COALESCE(SUM(quantity), 0), ?
                    FROM usage_events
                    WHERE organization_id = ? AND feature = ?
                      AND occurred_at_utc >= ? AND occurred_at_utc < ?
                    """,
                    (
                        organization_id,
                        feature,
                        normalized_start,
                        normalized_end,
                        normalized_occurred,
                        organization_id,
                        feature,
                        normalized_start,
                        normalized_end,
                    ),
                )
                cursor = connection.execute(
                    """
                    UPDATE quota_usage_counters
                    SET quantity = quantity + ?, updated_at_utc = ?
                    WHERE organization_id = ? AND feature = ?
                      AND window_start_utc = ? AND window_end_utc = ?
                      AND (? < 0 OR quantity + ? <= ?)
                    RETURNING quantity
                    """,
                    (
                        quantity,
                        normalized_occurred,
                        organization_id,
                        feature,
                        normalized_start,
                        normalized_end,
                        limit,
                        quantity,
                        limit,
                    ),
                )
                updated = cursor.fetchone()
                if updated is None:
                    current = connection.execute(
                        """
                        SELECT quantity FROM quota_usage_counters
                        WHERE organization_id = ? AND feature = ?
                          AND window_start_utc = ? AND window_end_utc = ?
                        """,
                        (organization_id, feature, normalized_start, normalized_end),
                    ).fetchone()
                    used = float(current["quantity"] if current is not None else 0.0)
                    raise QuotaReservationExceededError(feature=feature, limit=limit, used=used)
                total = float(updated["quantity"])
                properties = reservation["properties"]
                event_properties = properties[0] if len(properties) == 1 else {"reservations": properties}
                event_id = self.stable_id("use", f"{organization_id}:{feature}:{uuid4().hex}")
                connection.execute(
                    """
                    INSERT INTO usage_events (
                        id, organization_id, user_id, feature, quantity,
                        properties_json, occurred_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        organization_id,
                        user_id,
                        feature,
                        quantity,
                        _json_dump(event_properties),
                        normalized_occurred,
                    ),
                )
                results.append(
                    {
                        "allowance": {
                            "feature": feature,
                            "limit": None if limit < 0 else limit,
                            "used": total,
                            "remaining": None if limit < 0 else max(limit - total, 0.0),
                            "bypassed": False,
                            "window_start_utc": normalized_start,
                            "window_end_utc": normalized_end,
                        },
                        "usage": {
                            "id": event_id,
                            "organization_id": organization_id,
                            "user_id": user_id,
                            "feature": feature,
                            "quantity": quantity,
                            "properties": event_properties,
                            "occurred_at_utc": normalized_occurred,
                        },
                    }
                )
        return results

    def usage_count_since(self, *, organization_id: str, feature: str, since_utc: str) -> float:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(quantity), 0) AS total
                FROM usage_events
                WHERE organization_id = ? AND feature = ? AND occurred_at_utc >= ?
                """,
                (organization_id, feature, since_utc),
            ).fetchone()
        return float(row["total"] if row is not None else 0.0)

    def usage_count_window(
        self,
        *,
        organization_id: str,
        feature: str,
        window_start_utc: str,
        window_end_utc: str,
    ) -> float:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(quantity), 0) AS total
                FROM usage_events
                WHERE organization_id = ? AND feature = ?
                  AND occurred_at_utc >= ? AND occurred_at_utc < ?
                """,
                (organization_id, feature, window_start_utc, window_end_utc),
            ).fetchone()
        return float(row["total"] if row is not None else 0.0)

    def get_organization_quotas(self, *, organization_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT quotas_json FROM organization_quotas WHERE organization_id = ?",
                (organization_id,),
            ).fetchone()
        return None if row is None else _json_load(row["quotas_json"], {})

    def upsert_organization_quotas(self, *, organization_id: str, quotas: dict[str, Any]) -> dict[str, Any]:
        normalized_quotas: dict[str, float] = {}
        for key, value in quotas.items():
            feature = str(key).strip()
            if (
                not feature
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0
            ):
                raise ValueError("Quota limits must be finite non-negative numbers keyed by feature")
            normalized_quotas[feature] = float(value)
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO organization_quotas (organization_id, quotas_json, updated_at_utc)
                VALUES (?, ?, ?)
                ON CONFLICT(organization_id) DO UPDATE SET
                    quotas_json = excluded.quotas_json,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (organization_id, _json_dump(normalized_quotas), now),
            )
        return {"organization_id": organization_id, "quotas": normalized_quotas, "updated_at_utc": now}

    def record_audit_log(
        self,
        *,
        action: str,
        organization_id: str | None = None,
        actor_user_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_id = self.stable_id("aud", f"{organization_id}:{actor_user_id}:{action}:{uuid4().hex}")
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_log (
                    id, organization_id, actor_user_id, action, target_type, target_id, metadata_json, occurred_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, organization_id, actor_user_id, action, target_type, target_id, _json_dump(metadata or {}), now),
            )
        return {
            "id": event_id,
            "organization_id": organization_id,
            "actor_user_id": actor_user_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "metadata": metadata or {},
            "occurred_at_utc": now,
        }

    def list_audit_log(
        self,
        *,
        organization_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit_log"
        clauses: list[str] = []
        values: list[Any] = []
        if organization_id:
            clauses.append("organization_id = ?")
            values.append(organization_id)
        if action:
            clauses.append("action = ?")
            values.append(action)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        values.extend([int(limit), int(offset)])
        query += " ORDER BY occurred_at_utc DESC, id DESC LIMIT ? OFFSET ?"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        records = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _json_load(item.pop("metadata_json"), {})
            records.append(item)
        return records

    def _user_strategy_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["spec"] = _json_load(payload.pop("spec_json"), {})
        payload["validation"] = _json_load(payload.pop("validation_json"), {})
        payload["approval"] = _json_load(payload.pop("approval_json"), {})
        return payload

    def create_user_strategy(
        self,
        *,
        organization_id: str,
        owner_user_id: str,
        spec: dict[str, Any],
        validation: dict[str, Any],
        approval: dict[str, Any],
        status: str = "active",
        risk_level: str = "medium",
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        strategy_id = self.stable_id("ustr", f"{organization_id}:{owner_user_id}:{uuid4().hex}")
        root_id = self.stable_id("ustr_root", f"{organization_id}:{owner_user_id}:{spec.get('name')}:{uuid4().hex}")
        approved_at = str(approval.get("approved_at_utc") or now)
        name = str(spec.get("name") or "User Strategy")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO user_strategies (
                    id, organization_id, owner_user_id, root_strategy_id, version, name,
                    status, risk_level, spec_json, validation_json, approval_json,
                    created_at_utc, updated_at_utc, approved_at_utc, disabled_at_utc,
                    deleted_at_utc, backtest_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_id,
                    organization_id,
                    owner_user_id,
                    root_id,
                    1,
                    name,
                    status,
                    risk_level,
                    _json_dump(spec),
                    _json_dump(validation),
                    _json_dump(approval),
                    now,
                    now,
                    approved_at,
                    None,
                    None,
                    0,
                ),
            )
            row = connection.execute("SELECT * FROM user_strategies WHERE id = ?", (strategy_id,)).fetchone()
        return self._user_strategy_row(row)

    def get_user_strategy(
        self,
        *,
        organization_id: str,
        strategy_id: str,
        owner_user_id: str | None = None,
        active_only: bool = False,
        include_deleted: bool = False,
    ) -> dict[str, Any] | None:
        clauses = ["organization_id = ?", "id = ?"]
        params: list[Any] = [organization_id, strategy_id]
        if owner_user_id is not None:
            clauses.append("owner_user_id = ?")
            params.append(owner_user_id)
        if active_only:
            clauses.append("status = 'active'")
        if not include_deleted:
            clauses.append("deleted_at_utc IS NULL")
        query = f"SELECT * FROM user_strategies WHERE {' AND '.join(clauses)}"
        with self._connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return None if row is None else self._user_strategy_row(row)

    def list_user_strategies(
        self,
        *,
        organization_id: str,
        owner_user_id: str,
        active_only: bool = True,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["organization_id = ?", "owner_user_id = ?", "deleted_at_utc IS NULL"]
        params: list[Any] = [organization_id, owner_user_id]
        if active_only:
            clauses.append("status = 'active'")
        params.append(int(limit))
        query = f"""
            SELECT * FROM user_strategies
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at_utc DESC
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._user_strategy_row(row) for row in rows]

    def list_admin_user_strategies(
        self,
        *,
        organization_id: str | None = None,
        owner_user_id: str | None = None,
        status: str | None = None,
        risk_level: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["us.deleted_at_utc IS NULL"]
        params: list[Any] = []
        if organization_id:
            clauses.append("us.organization_id = ?")
            params.append(organization_id)
        if owner_user_id:
            clauses.append("us.owner_user_id = ?")
            params.append(owner_user_id)
        if status:
            clauses.append("us.status = ?")
            params.append(status)
        if risk_level:
            clauses.append("us.risk_level = ?")
            params.append(risk_level)
        params.append(int(limit))
        query = f"""
            SELECT us.*, u.email AS owner_email, u.display_name AS owner_name
            FROM user_strategies us
            LEFT JOIN users u ON u.id = us.owner_user_id
            WHERE {' AND '.join(clauses)}
            ORDER BY us.created_at_utc DESC
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._user_strategy_row(row) for row in rows]

    def update_user_strategy_status(self, *, strategy_id: str, status: str) -> dict[str, Any] | None:
        now = _utc_now_iso()
        disabled_at = now if status == "disabled" else None
        deleted_at = now if status == "deleted" else None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE user_strategies
                SET status = ?,
                    disabled_at_utc = ?,
                    deleted_at_utc = COALESCE(?, deleted_at_utc),
                    updated_at_utc = ?
                WHERE id = ?
                """,
                (status, disabled_at, deleted_at, now, strategy_id),
            )
            row = connection.execute("SELECT * FROM user_strategies WHERE id = ?", (strategy_id,)).fetchone()
        return None if row is None else self._user_strategy_row(row)

    def increment_user_strategy_backtest_count(self, *, strategy_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE user_strategies
                SET backtest_count = backtest_count + 1,
                    updated_at_utc = ?
                WHERE id = ?
                """,
                (_utc_now_iso(), strategy_id),
            )

    @staticmethod
    def _marketplace_version_row(row: Any) -> dict[str, Any]:
        payload = dict(row)
        payload["strategy_spec"] = _json_load(payload.pop("strategy_spec_json"), {})
        payload["catalog_snapshot"] = _json_load(payload.pop("catalog_snapshot_json"), {})
        payload["validation_snapshot"] = _json_load(payload.pop("validation_snapshot_json"), {})
        return payload

    def create_strategy_listing(self, *, publisher_organization_id: str, publisher_user_id: str, source_user_strategy_id: str, title: str, slug: str, summary: str, visibility: str) -> dict[str, Any]:
        listing_id = self.stable_id("lst", f"{publisher_organization_id}:{slug}:{uuid4().hex}")
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_listings (
                    id, publisher_organization_id, publisher_user_id, source_user_strategy_id,
                    title, slug, summary, visibility, status, current_version_id,
                    created_at_utc, updated_at_utc, published_at_utc, archived_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', NULL, ?, ?, NULL, NULL)
                """,
                (listing_id, publisher_organization_id, publisher_user_id, source_user_strategy_id, title, slug, summary, visibility, now, now),
            )
            row = connection.execute("SELECT * FROM strategy_listings WHERE id = ?", (listing_id,)).fetchone()
        return dict(row)

    def get_strategy_listing(self, *, listing_id: str | None = None, slug: str | None = None) -> dict[str, Any] | None:
        if bool(listing_id) == bool(slug):
            raise ValueError("Provide exactly one listing id or slug")
        column, value = ("id", listing_id) if listing_id else ("slug", slug)
        with self._connect() as connection:
            row = connection.execute(f"SELECT * FROM strategy_listings WHERE {column} = ?", (value,)).fetchone()
        return None if row is None else dict(row)

    def list_strategy_listings(self, *, publisher_organization_id: str | None = None, statuses: tuple[str, ...] | None = None, visibility: str | None = None, search: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        query = "SELECT * FROM strategy_listings"
        clauses: list[str] = []
        params: list[Any] = []
        if publisher_organization_id:
            clauses.append("publisher_organization_id = ?")
            params.append(publisher_organization_id)
        if statuses:
            clauses.append("status IN (" + ",".join("?" for _ in statuses) + ")")
            params.extend(statuses)
        if visibility:
            clauses.append("visibility = ?")
            params.append(visibility)
        if search:
            clauses.append("(LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)")
            term = f"%{search.lower()}%"
            params.extend([term, term])
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY COALESCE(published_at_utc, updated_at_utc) DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def update_strategy_listing(self, *, listing_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"title", "summary", "visibility", "status", "source_user_strategy_id", "current_version_id", "published_at_utc", "archived_at_utc"}
        normalized = {key: value for key, value in updates.items() if key in allowed}
        if not normalized:
            return self.get_strategy_listing(listing_id=listing_id)
        normalized["updated_at_utc"] = _utc_now_iso()
        assignments = ", ".join(f"{key} = ?" for key in normalized)
        with self._connect() as connection:
            connection.execute(f"UPDATE strategy_listings SET {assignments} WHERE id = ?", (*normalized.values(), listing_id))
            row = connection.execute("SELECT * FROM strategy_listings WHERE id = ?", (listing_id,)).fetchone()
        return None if row is None else dict(row)

    def create_strategy_listing_version(self, *, listing_id: str, strategy_spec: dict[str, Any], catalog_snapshot: dict[str, Any], validation_snapshot: dict[str, Any], risk_level: str, source_strategy_version: int, content_hash: str, created_by_user_id: str) -> dict[str, Any]:
        now = _utc_now_iso()
        with self._connect() as connection:
            existing = connection.execute("SELECT * FROM strategy_listing_versions WHERE listing_id = ? AND content_hash = ?", (listing_id, content_hash)).fetchone()
            if existing is not None:
                return self._marketplace_version_row(existing)
            next_row = connection.execute("SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM strategy_listing_versions WHERE listing_id = ?", (listing_id,)).fetchone()
            version = int(next_row["next_version"])
            version_id = self.stable_id("lsv", f"{listing_id}:{version}:{content_hash}")
            connection.execute(
                """
                INSERT INTO strategy_listing_versions (
                    id, listing_id, version, strategy_spec_json, catalog_snapshot_json,
                    validation_snapshot_json, risk_level, source_strategy_version,
                    content_hash, created_by_user_id, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (version_id, listing_id, version, _json_dump(strategy_spec), _json_dump(catalog_snapshot), _json_dump(validation_snapshot), risk_level, source_strategy_version, content_hash, created_by_user_id, now),
            )
            row = connection.execute("SELECT * FROM strategy_listing_versions WHERE id = ?", (version_id,)).fetchone()
        return self._marketplace_version_row(row)

    def get_strategy_listing_version(self, *, version_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM strategy_listing_versions WHERE id = ?", (version_id,)).fetchone()
        return None if row is None else self._marketplace_version_row(row)

    def list_strategy_listing_versions(self, *, listing_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM strategy_listing_versions WHERE listing_id = ? ORDER BY version DESC", (listing_id,)).fetchall()
        return [self._marketplace_version_row(row) for row in rows]

    def upsert_marketplace_subscription(self, *, subscriber_organization_id: str, subscriber_user_id: str, listing_id: str, pinned_listing_version_id: str, status: str, idempotency_key: str) -> dict[str, Any]:
        now = _utc_now_iso()
        with self._connect() as connection:
            replay = connection.execute("SELECT * FROM strategy_marketplace_subscriptions WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if replay is not None:
                if str(replay["subscriber_organization_id"]) != subscriber_organization_id or str(replay["listing_id"]) != listing_id:
                    raise IdempotencyConflictError("Marketplace idempotency key was already used for another mutation")
                return dict(replay)
            existing = connection.execute("SELECT * FROM strategy_marketplace_subscriptions WHERE subscriber_organization_id = ? AND listing_id = ?", (subscriber_organization_id, listing_id)).fetchone()
            subscription_id = str(existing["id"]) if existing is not None else self.stable_id("mps", f"{subscriber_organization_id}:{listing_id}")
            created = str(existing["created_at_utc"]) if existing is not None else now
            connection.execute(
                """
                INSERT INTO strategy_marketplace_subscriptions (
                    id, subscriber_organization_id, subscriber_user_id, listing_id,
                    pinned_listing_version_id, status, idempotency_key, created_at_utc,
                    updated_at_utc, cancelled_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subscriber_organization_id, listing_id) DO UPDATE SET
                    subscriber_user_id = excluded.subscriber_user_id,
                    pinned_listing_version_id = excluded.pinned_listing_version_id,
                    status = excluded.status,
                    idempotency_key = excluded.idempotency_key,
                    updated_at_utc = excluded.updated_at_utc,
                    cancelled_at_utc = excluded.cancelled_at_utc
                """,
                (subscription_id, subscriber_organization_id, subscriber_user_id, listing_id, pinned_listing_version_id, status, idempotency_key, created, now, now if status == "cancelled" else None),
            )
            row = connection.execute("SELECT * FROM strategy_marketplace_subscriptions WHERE id = ?", (subscription_id,)).fetchone()
        return dict(row)

    def list_marketplace_subscriptions(self, *, subscriber_organization_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*, l.title AS listing_title, l.slug AS listing_slug, l.status AS listing_status
                FROM strategy_marketplace_subscriptions s
                JOIN strategy_listings l ON l.id = s.listing_id
                WHERE s.subscriber_organization_id = ?
                ORDER BY s.updated_at_utc DESC, s.id DESC
                """,
                (subscriber_organization_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_dataset(self, *, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now_iso()
        dataset_id = str(payload.get("id") or self.stable_id("dst", f"{organization_id}:{payload.get('path')}:{payload.get('kind')}"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO datasets (
                    id, organization_id, project_id, name, kind, path, provider_json,
                    schema_json, row_count, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    kind = excluded.kind,
                    path = excluded.path,
                    provider_json = excluded.provider_json,
                    schema_json = excluded.schema_json,
                    row_count = excluded.row_count,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    dataset_id,
                    organization_id,
                    payload.get("project_id"),
                    str(payload.get("name") or payload.get("path") or "Dataset"),
                    str(payload.get("kind", "unknown")),
                    str(payload.get("path", "")),
                    _json_dump(payload.get("provider", {})),
                    _json_dump(payload.get("schema", {})),
                    int(payload.get("row_count", 0) or 0),
                    str(payload.get("created_at_utc") or now),
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        return self._dataset_row(row)

    def _dataset_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["provider"] = _json_load(payload.pop("provider_json"), {})
        payload["schema"] = _json_load(payload.pop("schema_json"), {})
        return payload

    def list_datasets(self, *, organization_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM datasets WHERE organization_id = ? ORDER BY updated_at_utc DESC",
                (organization_id,),
            ).fetchall()
        return [self._dataset_row(row) for row in rows]

    def get_dataset(self, *, organization_id: str, dataset_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM datasets WHERE organization_id = ? AND id = ?",
                (organization_id, dataset_id),
            ).fetchone()
        return None if row is None else self._dataset_row(row)

    def create_api_key_metadata(
        self,
        *,
        organization_id: str,
        name: str,
        provider: str,
        secret: str | None = None,
        secret_ref: str | None = None,
        token_hash: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        key_id = self.stable_id("key", f"{organization_id}:{provider}:{name}:{uuid4().hex}")
        masked = self._mask_secret(secret or secret_ref or "")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO api_keys (
                    id, organization_id, name, provider, masked_value, secret_ref,
                    token_hash, scopes_json, status, last_used_at_utc, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (key_id, organization_id, name, provider, masked, secret_ref, token_hash, _json_dump(scopes or []), "active", None, now, now),
            )
            row = connection.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
        return self._api_key_row(row)

    def _api_key_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["scopes"] = _json_load(payload.pop("scopes_json", "[]"), [])
        payload.pop("token_hash", None)
        return payload

    def list_api_keys(self, *, organization_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM api_keys WHERE organization_id = ? ORDER BY created_at_utc DESC",
                (organization_id,),
            ).fetchall()
        return [self._api_key_row(row) for row in rows]

    def get_api_key_by_token_hash(self, *, token_hash: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM api_keys WHERE token_hash = ? AND status = 'active'",
                (token_hash,),
            ).fetchone()
            if row is not None:
                connection.execute(
                    "UPDATE api_keys SET last_used_at_utc = ?, updated_at_utc = ? WHERE id = ?",
                    (_utc_now_iso(), _utc_now_iso(), row["id"]),
                )
        return None if row is None else self._api_key_row(row)

    def upsert_experiment(self, *, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now_iso()
        experiment_id = str(payload.get("id") or payload.get("experiment_id") or self.stable_id("exp", f"{organization_id}:{uuid4().hex}"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO experiments (
                    id, organization_id, project_id, job_id, name, pipeline, status, artifact_dir,
                    summary_json, validation_json, lineage_json, readiness_json, trades_json,
                    sentiment_json, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_id = excluded.project_id,
                    job_id = excluded.job_id,
                    name = excluded.name,
                    pipeline = excluded.pipeline,
                    status = excluded.status,
                    artifact_dir = excluded.artifact_dir,
                    summary_json = excluded.summary_json,
                    validation_json = excluded.validation_json,
                    lineage_json = excluded.lineage_json,
                    readiness_json = excluded.readiness_json,
                    trades_json = excluded.trades_json,
                    sentiment_json = excluded.sentiment_json,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    experiment_id,
                    organization_id,
                    payload.get("project_id"),
                    payload.get("job_id"),
                    str(payload.get("name") or experiment_id),
                    str(payload.get("pipeline") or "unknown"),
                    str(payload.get("status") or "completed"),
                    payload.get("artifact_dir"),
                    _json_dump(payload.get("summary", {})),
                    _json_dump(payload.get("validation", {})),
                    _json_dump(payload.get("lineage", {})),
                    _json_dump(payload.get("readiness", {})),
                    _json_dump(payload.get("trades", [])),
                    _json_dump(payload.get("sentiment", {})),
                    str(payload.get("created_at_utc") or now),
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
        return self._experiment_row(row)

    def _experiment_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["summary"] = _json_load(payload.pop("summary_json"), {})
        payload["validation"] = _json_load(payload.pop("validation_json"), {})
        payload["lineage"] = _json_load(payload.pop("lineage_json"), {})
        payload["readiness"] = _json_load(payload.pop("readiness_json"), {})
        payload["trades"] = _json_load(payload.pop("trades_json"), [])
        payload["sentiment"] = _json_load(payload.pop("sentiment_json"), {})
        return payload

    def list_experiments(self, *, organization_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM experiments WHERE organization_id = ? ORDER BY created_at_utc DESC LIMIT ?",
                (organization_id, limit),
            ).fetchall()
        return [self._experiment_row(row) for row in rows]

    def get_experiment(self, *, organization_id: str, experiment_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiments WHERE organization_id = ? AND id = ?",
                (organization_id, experiment_id),
            ).fetchone()
        return None if row is None else self._experiment_row(row)

    def upsert_paper_agent(self, *, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now_iso()
        deployment_id = str(payload.get("deployment_id") or "") or None
        name = str(payload.get("name") or "paper_agent")
        agent_id = str(payload.get("id") or self.stable_id("agt", f"{organization_id}:{deployment_id or 'legacy'}:{name}"))
        with self._connect() as connection:
            if deployment_id is not None:
                deployment = connection.execute(
                    "SELECT id FROM paper_deployments WHERE organization_id = ? AND id = ?",
                    (organization_id, deployment_id),
                ).fetchone()
                if deployment is None:
                    raise ValueError(f"Paper deployment not found for organization: {deployment_id}")
            existing = connection.execute("SELECT organization_id FROM paper_agents WHERE id = ?", (agent_id,)).fetchone()
            if existing is not None and existing["organization_id"] != organization_id:
                raise ValueError("Paper agent id is already owned by another organization")
            connection.execute(
                """
                INSERT INTO paper_agents (
                    id, organization_id, project_id, deployment_id, name, pipeline, status, fake_cash,
                    config_json, latest_payload_json, warnings_json, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_id = excluded.project_id,
                    deployment_id = excluded.deployment_id,
                    name = excluded.name,
                    pipeline = excluded.pipeline,
                    status = excluded.status,
                    fake_cash = excluded.fake_cash,
                    config_json = excluded.config_json,
                    latest_payload_json = excluded.latest_payload_json,
                    warnings_json = excluded.warnings_json,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    agent_id,
                    organization_id,
                    payload.get("project_id"),
                    deployment_id,
                    name,
                    str(payload.get("pipeline") or "unknown"),
                    str(payload.get("status") or "idle"),
                    float(payload.get("fake_cash", 0.0) or 0.0),
                    _json_dump(payload.get("config", {})),
                    _json_dump(payload.get("latest_payload", {})),
                    _json_dump(payload.get("warnings", [])),
                    str(payload.get("created_at_utc") or now),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM paper_agents WHERE organization_id = ? AND id = ?",
                (organization_id, agent_id),
            ).fetchone()
        return self._paper_agent_row(row)

    def _paper_agent_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["config"] = _json_load(payload.pop("config_json"), {})
        payload["latest_payload"] = _json_load(payload.pop("latest_payload_json"), {})
        payload["warnings"] = _json_load(payload.pop("warnings_json"), [])
        return payload

    def list_paper_agents(self, *, organization_id: str, deployment_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM paper_agents WHERE organization_id = ?"
        params: list[Any] = [organization_id]
        if deployment_id is not None:
            query += " AND deployment_id = ?"
            params.append(deployment_id)
        query += " ORDER BY updated_at_utc DESC"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._paper_agent_row(row) for row in rows]

    def get_paper_agent(
        self,
        *,
        organization_id: str,
        agent_id: str,
        deployment_id: str | None = None,
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM paper_agents WHERE organization_id = ? AND id = ?"
        params: list[Any] = [organization_id, agent_id]
        if deployment_id is not None:
            query += " AND deployment_id = ?"
            params.append(deployment_id)
        with self._connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return None if row is None else self._paper_agent_row(row)

    @staticmethod
    def _paper_deployment_row(row: Any) -> dict[str, Any]:
        payload = dict(row)
        payload["config"] = _json_load(payload.pop("config_json"), {})
        return payload

    @staticmethod
    def _paper_run_row(row: Any) -> dict[str, Any]:
        payload = dict(row)
        payload["request"] = _json_load(payload.pop("request_json"), {})
        payload["deployment_config"] = _json_load(payload.pop("deployment_config_json"), {})
        payload["batch_summary"] = _json_load(payload.pop("batch_summary_json"), {})
        payload["aggregate_payload"] = _json_load(payload.pop("aggregate_payload_json"), {})
        return payload

    @staticmethod
    def _validate_paper_deployment_status(status: str) -> str:
        normalized = str(status).strip().lower()
        if normalized not in {"active", "paused", "archived"}:
            raise ValueError("Paper deployment status must be active, paused, or archived")
        return normalized

    @staticmethod
    def _validate_paper_run_status(status: str) -> str:
        normalized = str(status).strip().lower()
        if normalized not in {"queued", "running", "completed", "failed", "interrupted"}:
            raise ValueError("Paper run status is not recognized")
        return normalized

    @staticmethod
    def _validate_page(*, limit: int, offset: int) -> tuple[int, int]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer between 1 and 200")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        return limit, offset

    @staticmethod
    def _validate_project_scope(connection: Any, *, organization_id: str, project_id: str | None) -> None:
        if project_id is None:
            return
        project = connection.execute(
            "SELECT id FROM projects WHERE organization_id = ? AND id = ?",
            (organization_id, project_id),
        ).fetchone()
        if project is None:
            raise ValueError(f"Project not found for organization: {project_id}")

    def create_paper_deployment(
        self,
        *,
        organization_id: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        config_json = _canonical_safe_json(payload.get("config", {}), field_name="paper deployment config")
        config_hash = _canonical_hash(config_json)
        normalized_key = str(idempotency_key or payload.get("idempotency_key") or "").strip() or None
        project_id = str(payload.get("project_id") or "").strip() or None
        name = str(payload.get("name") or "Paper deployment").strip()
        if not name:
            raise ValueError("Paper deployment name must not be empty")
        status = self._validate_paper_deployment_status(str(payload.get("status") or "active"))
        source = str(payload.get("source") or "api").strip() or "api"
        legacy_config_id = str(payload.get("legacy_config_id") or "").strip() or None
        deployment_id = str(
            payload.get("id")
            or (
                self.stable_id("pdep", f"{organization_id}:{normalized_key}")
                if normalized_key
                else f"pdep_{uuid4().hex}"
            )
        )
        now = _utc_now_iso()
        with self._connect() as connection:
            self._validate_project_scope(connection, organization_id=organization_id, project_id=project_id)
            connection.execute(
                """
                INSERT OR IGNORE INTO paper_deployments (
                    id, organization_id, project_id, name, status, version, config_json,
                    config_hash, idempotency_key, source, legacy_config_id,
                    created_by_user_id, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deployment_id,
                    organization_id,
                    project_id,
                    name,
                    status,
                    config_json,
                    config_hash,
                    normalized_key,
                    source,
                    legacy_config_id,
                    payload.get("created_by_user_id"),
                    str(payload.get("created_at_utc") or now),
                    now,
                ),
            )
            if normalized_key is not None:
                row = connection.execute(
                    "SELECT * FROM paper_deployments WHERE organization_id = ? AND idempotency_key = ?",
                    (organization_id, normalized_key),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM paper_deployments WHERE organization_id = ? AND id = ?",
                    (organization_id, deployment_id),
                ).fetchone()
        if row is None:
            raise IdempotencyConflictError("Paper deployment id or legacy config is already in use")
        existing = self._paper_deployment_row(row)
        if (
            existing["config_hash"] != config_hash
            or existing["name"] != name
            or existing["project_id"] != project_id
            or existing["status"] != status
            or existing["source"] != source
            or existing["legacy_config_id"] != legacy_config_id
            or existing["created_by_user_id"] != payload.get("created_by_user_id")
        ):
            raise IdempotencyConflictError("Paper deployment id or idempotency key was reused with a different payload")
        return existing

    def get_paper_deployment(self, *, organization_id: str, deployment_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_deployments WHERE organization_id = ? AND id = ?",
                (organization_id, deployment_id),
            ).fetchone()
        return None if row is None else self._paper_deployment_row(row)

    def list_paper_deployments(
        self,
        *,
        organization_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit, offset = self._validate_page(limit=limit, offset=offset)
        query = "SELECT * FROM paper_deployments WHERE organization_id = ?"
        params: list[Any] = [organization_id]
        if status is not None:
            query += " AND status = ?"
            params.append(self._validate_paper_deployment_status(status))
        query += " ORDER BY updated_at_utc DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._paper_deployment_row(row) for row in rows]

    def update_paper_deployment(
        self,
        *,
        organization_id: str,
        deployment_id: str,
        expected_version: int,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        allowed = {"project_id", "name", "status", "config", "source"}
        unknown = set(updates).difference(allowed)
        if unknown:
            raise ValueError(f"Unsupported paper deployment updates: {', '.join(sorted(unknown))}")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_deployments WHERE organization_id = ? AND id = ?",
                (organization_id, deployment_id),
            ).fetchone()
            if row is None or int(row["version"]) != int(expected_version):
                return None
            current = self._paper_deployment_row(row)
            project_id = (
                str(updates.get("project_id") or "").strip() or None
                if "project_id" in updates
                else current["project_id"]
            )
            self._validate_project_scope(connection, organization_id=organization_id, project_id=project_id)
            name = str(updates.get("name", current["name"])).strip()
            if not name:
                raise ValueError("Paper deployment name must not be empty")
            status = self._validate_paper_deployment_status(str(updates.get("status", current["status"])))
            source = str(updates.get("source", current["source"])).strip() or current["source"]
            config_json = (
                _canonical_safe_json(updates["config"], field_name="paper deployment config")
                if "config" in updates
                else _canonical_safe_json(current["config"], field_name="paper deployment config")
            )
            now = _utc_now_iso()
            cursor = connection.execute(
                """
                UPDATE paper_deployments
                SET project_id = ?, name = ?, status = ?, config_json = ?, config_hash = ?,
                    source = ?, version = version + 1, updated_at_utc = ?
                WHERE organization_id = ? AND id = ? AND version = ?
                """,
                (
                    project_id,
                    name,
                    status,
                    config_json,
                    _canonical_hash(config_json),
                    source,
                    now,
                    organization_id,
                    deployment_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                return None
            updated = connection.execute(
                "SELECT * FROM paper_deployments WHERE organization_id = ? AND id = ?",
                (organization_id, deployment_id),
            ).fetchone()
        return self._paper_deployment_row(updated)

    def create_paper_run(
        self,
        *,
        organization_id: str,
        deployment_id: str,
        idempotency_key: str,
        request: dict[str, Any],
        job_id: str | None = None,
        asof_date: str | None = None,
        run_index: int = 1,
        run_id: str | None = None,
        status: str = "queued",
    ) -> dict[str, Any]:
        normalized_key = str(idempotency_key).strip()
        if not normalized_key:
            raise ValueError("Paper run idempotency_key must not be empty")
        if isinstance(run_index, bool) or not isinstance(run_index, int) or run_index < 1:
            raise ValueError("Paper run_index must be a positive integer")
        normalized_status = self._validate_paper_run_status(status)
        if normalized_status not in {"queued", "running"}:
            raise ValueError("A paper run must be created as queued or running")
        request_json = _canonical_safe_json(request, field_name="paper run request")
        request_hash = _canonical_hash(request_json)
        paper_run_id = str(run_id or self.stable_id("prun", f"{organization_id}:{deployment_id}:{normalized_key}"))
        now = _utc_now_iso()
        with self._connect() as connection:
            deployment = connection.execute(
                "SELECT * FROM paper_deployments WHERE organization_id = ? AND id = ?",
                (organization_id, deployment_id),
            ).fetchone()
            if deployment is None:
                raise ValueError(f"Paper deployment not found for organization: {deployment_id}")
            connection.execute(
                """
                INSERT OR IGNORE INTO paper_runs (
                    id, organization_id, deployment_id, job_id, idempotency_key,
                    request_hash, deployment_version, status, asof_date, run_index,
                    request_json, deployment_config_json, batch_summary_json,
                    aggregate_payload_json, version, created_at_utc, updated_at_utc,
                    started_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', '{}', 1, ?, ?, ?)
                """,
                (
                    paper_run_id,
                    organization_id,
                    deployment_id,
                    job_id,
                    normalized_key,
                    request_hash,
                    int(deployment["version"]),
                    normalized_status,
                    asof_date,
                    run_index,
                    request_json,
                    deployment["config_json"],
                    now,
                    now,
                    now if normalized_status == "running" else None,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM paper_runs
                WHERE organization_id = ? AND deployment_id = ? AND idempotency_key = ?
                """,
                (organization_id, deployment_id, normalized_key),
            ).fetchone()
        if row is None:
            raise IdempotencyConflictError("Paper run id is already in use")
        existing = self._paper_run_row(row)
        if (
            existing["request_hash"] != request_hash
            or existing["job_id"] != job_id
            or existing["asof_date"] != asof_date
            or int(existing["run_index"]) != run_index
        ):
            raise IdempotencyConflictError("Paper run idempotency key was reused with a different payload")
        return existing

    def get_paper_run(self, *, organization_id: str, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_runs WHERE organization_id = ? AND id = ?",
                (organization_id, run_id),
            ).fetchone()
        return None if row is None else self._paper_run_row(row)

    def list_paper_runs(
        self,
        *,
        organization_id: str,
        deployment_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit, offset = self._validate_page(limit=limit, offset=offset)
        query = "SELECT * FROM paper_runs WHERE organization_id = ?"
        params: list[Any] = [organization_id]
        if deployment_id is not None:
            query += " AND deployment_id = ?"
            params.append(deployment_id)
        if status is not None:
            query += " AND status = ?"
            params.append(self._validate_paper_run_status(status))
        query += " ORDER BY created_at_utc DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._paper_run_row(row) for row in rows]

    def update_paper_run_status(
        self,
        *,
        organization_id: str,
        run_id: str,
        expected_version: int,
        status: str,
    ) -> dict[str, Any] | None:
        next_status = self._validate_paper_run_status(status)
        if next_status not in {"queued", "running"}:
            raise ValueError("Use complete_paper_run or fail_paper_run for terminal status")
        now = _utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE paper_runs
                SET status = ?, version = version + 1, updated_at_utc = ?,
                    started_at_utc = CASE WHEN ? = 'running' THEN COALESCE(started_at_utc, ?) ELSE started_at_utc END
                WHERE organization_id = ? AND id = ? AND version = ?
                  AND status IN ('queued', 'running')
                """,
                (next_status, now, next_status, now, organization_id, run_id, expected_version),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM paper_runs WHERE organization_id = ? AND id = ?",
                (organization_id, run_id),
            ).fetchone()
        return self._paper_run_row(row)

    def retry_paper_run(
        self,
        *,
        organization_id: str,
        run_id: str,
        expected_version: int,
        status: str = "running",
    ) -> dict[str, Any] | None:
        """CAS-resume a non-completed run without changing its durable identity."""

        next_status = self._validate_paper_run_status(status)
        if next_status not in {"queued", "running"}:
            raise ValueError("A retried paper run must become queued or running")
        now = _utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE paper_runs
                SET status = ?, error = NULL, completed_at_utc = NULL,
                    artifact_id = NULL, batch_summary_json = '{}', aggregate_payload_json = '{}',
                    version = version + 1, updated_at_utc = ?,
                    started_at_utc = CASE WHEN ? = 'running' THEN ? ELSE NULL END
                WHERE organization_id = ? AND id = ? AND version = ?
                  AND status IN ('running', 'failed', 'interrupted')
                """,
                (next_status, now, next_status, now, organization_id, run_id, expected_version),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM paper_runs WHERE organization_id = ? AND id = ?",
                (organization_id, run_id),
            ).fetchone()
        return self._paper_run_row(row)

    def complete_paper_run(
        self,
        *,
        organization_id: str,
        run_id: str,
        expected_version: int,
        batch_summary: dict[str, Any],
        aggregate_payload: dict[str, Any],
        artifact_id: str | None = None,
    ) -> dict[str, Any] | None:
        batch_json = _canonical_safe_json(batch_summary, field_name="paper batch summary")
        aggregate_json = _canonical_safe_json(aggregate_payload, field_name="paper aggregate payload")
        now = _utc_now_iso()
        with self._connect() as connection:
            if artifact_id is not None:
                artifact = connection.execute(
                    "SELECT id FROM artifacts WHERE organization_id = ? AND id = ?",
                    (organization_id, artifact_id),
                ).fetchone()
                if artifact is None:
                    raise ValueError(f"Artifact not found for organization: {artifact_id}")
            cursor = connection.execute(
                """
                UPDATE paper_runs
                SET status = 'completed', batch_summary_json = ?, aggregate_payload_json = ?,
                    artifact_id = ?, error = NULL, version = version + 1,
                    updated_at_utc = ?, completed_at_utc = ?
                WHERE organization_id = ? AND id = ? AND version = ?
                  AND status IN ('queued', 'running')
                """,
                (batch_json, aggregate_json, artifact_id, now, now, organization_id, run_id, expected_version),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM paper_runs WHERE organization_id = ? AND id = ?",
                (organization_id, run_id),
            ).fetchone()
        return self._paper_run_row(row)

    def fail_paper_run(
        self,
        *,
        organization_id: str,
        run_id: str,
        expected_version: int,
        error: str,
        status: str = "failed",
    ) -> dict[str, Any] | None:
        terminal_status = self._validate_paper_run_status(status)
        if terminal_status not in {"failed", "interrupted"}:
            raise ValueError("Paper run failure status must be failed or interrupted")
        now = _utc_now_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE paper_runs
                SET status = ?, error = ?, version = version + 1,
                    updated_at_utc = ?, completed_at_utc = ?
                WHERE organization_id = ? AND id = ? AND version = ?
                  AND status IN ('queued', 'running')
                """,
                (terminal_status, str(error), now, now, organization_id, run_id, expected_version),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM paper_runs WHERE organization_id = ? AND id = ?",
                (organization_id, run_id),
            ).fetchone()
        return self._paper_run_row(row)

    def get_latest_completed_paper_run(
        self,
        *,
        organization_id: str,
        deployment_id: str | None = None,
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM paper_runs WHERE organization_id = ? AND status = 'completed'"
        params: list[Any] = [organization_id]
        if deployment_id is not None:
            query += " AND deployment_id = ?"
            params.append(deployment_id)
        query += " ORDER BY completed_at_utc DESC, created_at_utc DESC, id DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return None if row is None else self._paper_run_row(row)

    def record_telemetry_event(
        self,
        *,
        event_id: str,
        name: str,
        category: str,
        properties: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        consent: str = "unknown",
        organization_id: str | None = None,
        user_id: str | None = None,
        occurred_at_utc: str | None = None,
    ) -> dict[str, Any]:
        occurred = occurred_at_utc or _utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO telemetry_events (
                    id, organization_id, user_id, name, category, properties_json,
                    context_json, consent, occurred_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    organization_id,
                    user_id,
                    name,
                    category,
                    _json_dump(properties or {}),
                    _json_dump(context or {}),
                    consent,
                    occurred,
                ),
            )
            row = connection.execute("SELECT * FROM telemetry_events WHERE id = ?", (event_id,)).fetchone()
        return self._telemetry_row(row)

    def _telemetry_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["properties"] = _json_load(payload.pop("properties_json"), {})
        payload["context"] = _json_load(payload.pop("context_json"), {})
        return payload

    def list_telemetry_events(self, *, organization_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM telemetry_events"
        params: tuple[Any, ...]
        if organization_id:
            query += " WHERE organization_id = ?"
            params = (organization_id, int(limit))
        else:
            params = (int(limit),)
        query += " ORDER BY occurred_at_utc DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._telemetry_row(row) for row in rows]

    def get_refresh_status(self, *, user_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM refresh_statuses WHERE user_id = ?", (user_id,)).fetchone()
        return self._row_to_dict(row)

    def list_refresh_statuses(self, *, organization_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM refresh_statuses"
        params: tuple[Any, ...] = tuple()
        if organization_id:
            query += " WHERE organization_id = ?"
            params = (organization_id,)
        query += " ORDER BY next_due_at_utc ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_refresh_run_by_key(self, *, idempotency_key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM refresh_runs WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        return None if row is None else self._refresh_run_row(row)

    def create_refresh_run(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        user_id: str,
        organization_id: str,
        max_attempts: int,
        locked_until_utc: str,
    ) -> tuple[dict[str, Any], bool]:
        now = _utc_now_iso()
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO refresh_runs (
                        id, idempotency_key, user_id, organization_id, status, attempt,
                        max_attempts, locked_until_utc, summary_json, created_at_utc, updated_at_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        idempotency_key,
                        user_id,
                        organization_id,
                        "queued",
                        0,
                        max_attempts,
                        locked_until_utc,
                        _json_dump({}),
                        now,
                        now,
                    ),
                )
                created = True
            except sqlite3.IntegrityError:
                created = False
            row = connection.execute("SELECT * FROM refresh_runs WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        return self._refresh_run_row(row), created

    def update_refresh_run(self, *, run_id: str, **updates: Any) -> dict[str, Any] | None:
        allowed = {
            "status",
            "attempt",
            "started_at_utc",
            "finished_at_utc",
            "locked_until_utc",
            "summary",
            "error",
        }
        assignments: list[str] = []
        params: list[Any] = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            column = "summary_json" if key == "summary" else key
            assignments.append(f"{column} = ?")
            params.append(_json_dump(value) if key == "summary" else value)
        if not assignments:
            return self.get_refresh_run(run_id=run_id)
        assignments.append("updated_at_utc = ?")
        params.append(_utc_now_iso())
        params.append(run_id)
        with self._connect() as connection:
            connection.execute(f"UPDATE refresh_runs SET {', '.join(assignments)} WHERE id = ?", tuple(params))
            row = connection.execute("SELECT * FROM refresh_runs WHERE id = ?", (run_id,)).fetchone()
        return None if row is None else self._refresh_run_row(row)

    def get_refresh_run(self, *, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM refresh_runs WHERE id = ?", (run_id,)).fetchone()
        return None if row is None else self._refresh_run_row(row)

    def _refresh_run_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["summary"] = _json_load(payload.pop("summary_json"), {})
        return payload

    def upsert_refresh_status(
        self,
        *,
        user_id: str,
        organization_id: str,
        status: str,
        latest_run_id: str | None,
        next_due_at_utc: str,
        last_success_at_utc: str | None = None,
        last_attempt_at_utc: str | None = None,
        last_error: str | None = None,
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO refresh_statuses (
                    user_id, organization_id, status, last_success_at_utc,
                    last_attempt_at_utc, next_due_at_utc, latest_run_id, last_error,
                    updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    organization_id = excluded.organization_id,
                    status = excluded.status,
                    last_success_at_utc = COALESCE(excluded.last_success_at_utc, refresh_statuses.last_success_at_utc),
                    last_attempt_at_utc = COALESCE(excluded.last_attempt_at_utc, refresh_statuses.last_attempt_at_utc),
                    next_due_at_utc = excluded.next_due_at_utc,
                    latest_run_id = excluded.latest_run_id,
                    last_error = excluded.last_error,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    user_id,
                    organization_id,
                    status,
                    last_success_at_utc,
                    last_attempt_at_utc,
                    next_due_at_utc,
                    latest_run_id,
                    last_error,
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM refresh_statuses WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row)

    def list_refresh_runs(self, *, user_id: str | None = None, organization_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM refresh_runs"
        clauses: list[str] = []
        params: list[Any] = []
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if organization_id:
            clauses.append("organization_id = ?")
            params.append(organization_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at_utc DESC LIMIT ?"
        params.append(int(limit))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._refresh_run_row(row) for row in rows]

    def upsert_job(self, *, kind: str, payload: dict[str, Any]) -> None:
        organization_id = payload.get("organization_id")
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT version, attempt, max_attempts, worker_id, heartbeat_at_utc,
                       lease_expires_at_utc, rq_job_id
                FROM jobs WHERE id = ?
                """,
                (str(payload["id"]),),
            ).fetchone()
            claim_defaults = {
                "version": 0,
                "attempt": 0,
                "max_attempts": 3,
                "worker_id": None,
                "heartbeat_at_utc": None,
                "lease_expires_at_utc": None,
                "rq_job_id": None,
            }
            claim_metadata = {
                key: payload[key] if key in payload else (existing[key] if existing is not None else default)
                for key, default in claim_defaults.items()
            }
            claim_metadata["version"] = int(claim_metadata["version"] or 0)
            claim_metadata["attempt"] = int(claim_metadata["attempt"] or 0)
            claim_metadata["max_attempts"] = int(claim_metadata["max_attempts"] or 3)
            if claim_metadata["version"] < 0 or claim_metadata["attempt"] < 0 or claim_metadata["max_attempts"] < 1:
                raise ValueError("Job version/attempt must be non-negative and max_attempts must be positive")
            normalized_payload = dict(payload)
            normalized_payload.update(claim_metadata)
            connection.execute(
                """
                INSERT INTO jobs (
                    id, organization_id, kind, status, version, attempt, max_attempts,
                    worker_id, heartbeat_at_utc, lease_expires_at_utc, rq_job_id,
                    stage, progress, request_json, payload_json, error, created_at_utc,
                    updated_at_utc, started_at_utc, finished_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    organization_id = excluded.organization_id,
                    kind = excluded.kind,
                    status = excluded.status,
                    version = excluded.version,
                    attempt = excluded.attempt,
                    max_attempts = excluded.max_attempts,
                    worker_id = excluded.worker_id,
                    heartbeat_at_utc = excluded.heartbeat_at_utc,
                    lease_expires_at_utc = excluded.lease_expires_at_utc,
                    rq_job_id = excluded.rq_job_id,
                    stage = excluded.stage,
                    progress = excluded.progress,
                    request_json = excluded.request_json,
                    payload_json = excluded.payload_json,
                    error = excluded.error,
                    created_at_utc = excluded.created_at_utc,
                    updated_at_utc = excluded.updated_at_utc,
                    started_at_utc = excluded.started_at_utc,
                    finished_at_utc = excluded.finished_at_utc
                """,
                (
                    str(payload["id"]),
                    str(organization_id) if organization_id else None,
                    kind,
                    str(payload.get("status", "unknown")),
                    claim_metadata["version"],
                    claim_metadata["attempt"],
                    claim_metadata["max_attempts"],
                    claim_metadata["worker_id"],
                    claim_metadata["heartbeat_at_utc"],
                    claim_metadata["lease_expires_at_utc"],
                    claim_metadata["rq_job_id"],
                    payload.get("stage"),
                    float(payload.get("progress", 0.0) or 0.0),
                    _json_dump(payload.get("request", {})),
                    _json_dump(normalized_payload),
                    payload.get("error"),
                    str(payload.get("created_at_utc") or _utc_now_iso()),
                    str(payload.get("updated_at_utc") or _utc_now_iso()),
                    payload.get("started_at_utc"),
                    payload.get("finished_at_utc"),
                ),
            )

    def list_jobs(
        self,
        *,
        kind: str,
        organization_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200):
            raise ValueError("limit must be an integer between 1 and 200")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if limit is None and offset:
            raise ValueError("offset requires an explicit limit")
        if status is not None and status not in {"queued", "running", "completed", "failed", "interrupted", "unknown"}:
            raise ValueError("status is not a recognized job status")

        query = "SELECT * FROM jobs WHERE kind = ?"
        params: list[Any] = [kind]
        if organization_id is not None:
            query += " AND organization_id = ?"
            params.append(organization_id)
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at_utc DESC, id DESC"
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._job_payload_from_row(row) for row in rows]

    def get_job(self, *, kind: str, job_id: str, organization_id: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM jobs WHERE kind = ? AND id = ?"
        params: list[Any] = [kind, job_id]
        if organization_id is not None:
            query += " AND organization_id = ?"
            params.append(organization_id)
        with self._connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return None if row is None else self._job_payload_from_row(row)

    @staticmethod
    def _job_payload_from_row(row: Any) -> dict[str, Any]:
        payload = dict(_json_load(row["payload_json"], {}))
        payload["kind"] = str(row["kind"])
        for field in (
            "status",
            "version",
            "attempt",
            "max_attempts",
            "worker_id",
            "heartbeat_at_utc",
            "lease_expires_at_utc",
            "rq_job_id",
            "updated_at_utc",
            "started_at_utc",
            "finished_at_utc",
        ):
            payload[field] = row[field]
        return payload

    @staticmethod
    def _normalized_utc_iso(value: str, *, field_name: str) -> str:
        parsed = _parse_utc_iso(value)
        if parsed is None:
            raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _validated_worker_id(worker_id: str) -> str:
        normalized = str(worker_id).strip()
        if not normalized:
            raise ValueError("worker_id must not be empty")
        return normalized

    def _job_claim_row(self, *, kind: str, job_id: str) -> Any | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM jobs WHERE kind = ? AND id = ?",
                (kind, job_id),
            ).fetchone()

    def _conditional_job_transition(
        self,
        *,
        kind: str,
        job_id: str,
        payload: dict[str, Any],
        expected_status: str,
        expected_version: int,
        expected_worker_id: str | None = None,
        require_attempt_available: bool = False,
        require_unexpired_at_utc: str | None = None,
    ) -> bool:
        query = """
            UPDATE jobs SET
                status = ?, version = ?, attempt = ?, max_attempts = ?, worker_id = ?,
                heartbeat_at_utc = ?, lease_expires_at_utc = ?, rq_job_id = ?,
                stage = ?, progress = ?, payload_json = ?, error = ?, updated_at_utc = ?,
                started_at_utc = ?, finished_at_utc = ?
            WHERE kind = ? AND id = ? AND status = ? AND version = ?
        """
        params: list[Any] = [
            payload["status"],
            int(payload["version"]),
            int(payload["attempt"]),
            int(payload["max_attempts"]),
            payload.get("worker_id"),
            payload.get("heartbeat_at_utc"),
            payload.get("lease_expires_at_utc"),
            payload.get("rq_job_id"),
            payload.get("stage"),
            float(payload.get("progress", 0.0) or 0.0),
            _json_dump(payload),
            payload.get("error"),
            payload["updated_at_utc"],
            payload.get("started_at_utc"),
            payload.get("finished_at_utc"),
            kind,
            job_id,
            expected_status,
            expected_version,
        ]
        if expected_worker_id is not None:
            query += " AND worker_id = ?"
            params.append(expected_worker_id)
        if require_attempt_available:
            query += " AND attempt < max_attempts"
        if require_unexpired_at_utc is not None:
            comparison_time = _parse_utc_iso(require_unexpired_at_utc)
            if comparison_time is None:
                raise ValueError("require_unexpired_at_utc must be an ISO-8601 timestamp")
            normalized_comparison = comparison_time.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
            query += """
                AND lease_expires_at_utc IS NOT NULL
                AND CASE
                    WHEN lease_expires_at_utc LIKE '%.%Z' THEN lease_expires_at_utc
                    ELSE substr(lease_expires_at_utc, 1, length(lease_expires_at_utc) - 1) || '.000000Z'
                END > ?
            """
            params.append(normalized_comparison)
        with self._connect() as connection:
            cursor = connection.execute(query, tuple(params))
            return cursor.rowcount == 1

    @staticmethod
    def _has_live_job_claim(row: Any, *, worker_id: str, now_utc: str) -> bool:
        if row is None or row["status"] != "running" or row["worker_id"] != worker_id:
            return False
        lease = _parse_utc_iso(row["lease_expires_at_utc"])
        now = _parse_utc_iso(now_utc)
        return lease is not None and now is not None and lease > now

    def assert_job_claim(self, *, kind: str, job_id: str, worker_id: str) -> dict[str, Any] | None:
        """Return the durable job only while ``worker_id`` owns a live lease.

        This read is intentionally reusable at domain publication boundaries.
        A storage exception is allowed to propagate: callers must treat an
        unavailable ownership check as uncertainty and publish nothing.
        """

        owner = self._validated_worker_id(worker_id)
        now = _utc_now_iso()
        row = self._job_claim_row(kind=kind, job_id=job_id)
        if not self._has_live_job_claim(row, worker_id=owner, now_utc=now):
            return None
        return self._job_payload_from_row(row)

    def _begin_claimed_publication(self, connection: Any) -> None:
        # Serialize SQLite publishers with claim recovery/heartbeats before the
        # ownership read. Postgres overrides this with a row-level lock.
        connection.execute("BEGIN IMMEDIATE")

    def _claimed_publication_row(self, connection: Any, *, kind: str, job_id: str) -> Any:
        return connection.execute(
            "SELECT * FROM jobs WHERE kind = ? AND id = ?",
            (kind, job_id),
        ).fetchone()

    def publish_claimed_job(
        self,
        *,
        kind: str,
        job_id: str,
        worker_id: str,
        publisher: Callable[[ClaimedDomainPublisher], Any],
    ) -> tuple[bool, Any]:
        """Run authoritative domain writes under the owning live job lock.

        Expensive compute and object-store uploads must happen before this call
        in deterministic, immutable attempt locations. A false result means the
        caller lost ownership and no authoritative database row was changed.
        It may leave an unreachable attempt blob, which lifecycle cleanup can
        remove because no artifact, dataset, experiment, or report references it.
        """

        owner = self._validated_worker_id(worker_id)
        with self._connect() as connection:
            self._begin_claimed_publication(connection)
            row = self._claimed_publication_row(connection, kind=kind, job_id=job_id)
            if not self._has_live_job_claim(row, worker_id=owner, now_utc=_utc_now_iso()):
                return False, None
            return True, publisher(ClaimedDomainPublisher(self, connection))

    @staticmethod
    def _claimed_job_immutable_fields() -> set[str]:
        return {
            "id",
            "kind",
            "organization_id",
            "user_id",
            "request",
            "created_at_utc",
            "status",
            "version",
            "attempt",
            "max_attempts",
            "worker_id",
            "heartbeat_at_utc",
            "lease_expires_at_utc",
            "rq_job_id",
        }

    def claim_job(
        self,
        *,
        kind: str,
        job_id: str,
        worker_id: str,
        lease_expires_at_utc: str,
    ) -> dict[str, Any] | None:
        owner = self._validated_worker_id(worker_id)
        lease = self._normalized_utc_iso(lease_expires_at_utc, field_name="lease_expires_at_utc")
        row = self._job_claim_row(kind=kind, job_id=job_id)
        if row is None or row["status"] != "queued" or int(row["attempt"] or 0) >= int(row["max_attempts"] or 3):
            return None
        now = _utc_now_iso()
        payload = self._job_payload_from_row(row)
        payload.update(
            {
                "status": "running",
                "version": int(row["version"] or 0) + 1,
                "attempt": int(row["attempt"] or 0) + 1,
                "max_attempts": int(row["max_attempts"] or 3),
                "worker_id": owner,
                "heartbeat_at_utc": now,
                "lease_expires_at_utc": lease,
                "rq_job_id": row["rq_job_id"],
                "updated_at_utc": now,
                "started_at_utc": payload.get("started_at_utc") or now,
            }
        )
        claimed = self._conditional_job_transition(
            kind=kind,
            job_id=job_id,
            payload=payload,
            expected_status="queued",
            expected_version=int(row["version"] or 0),
            require_attempt_available=True,
        )
        return payload if claimed else None

    def heartbeat_job(
        self,
        *,
        kind: str,
        job_id: str,
        worker_id: str,
        lease_expires_at_utc: str,
        heartbeat_at_utc: str | None = None,
    ) -> dict[str, Any] | None:
        owner = self._validated_worker_id(worker_id)
        lease = self._normalized_utc_iso(lease_expires_at_utc, field_name="lease_expires_at_utc")
        heartbeat = self._normalized_utc_iso(heartbeat_at_utc, field_name="heartbeat_at_utc") if heartbeat_at_utc else _utc_now_iso()
        validation_now = _utc_now_iso()
        lease_time = _parse_utc_iso(lease)
        now_time = _parse_utc_iso(validation_now)
        if lease_time is None or now_time is None or lease_time <= now_time:
            return None
        for _ in range(5):
            now = _utc_now_iso()
            current_time = _parse_utc_iso(now)
            if current_time is None or lease_time <= current_time:
                return None
            row = self._job_claim_row(kind=kind, job_id=job_id)
            if not self._has_live_job_claim(row, worker_id=owner, now_utc=now):
                return None
            payload = self._job_payload_from_row(row)
            payload.update(
                {
                    "version": int(row["version"] or 0) + 1,
                    "worker_id": owner,
                    "heartbeat_at_utc": heartbeat,
                    "lease_expires_at_utc": lease,
                    "updated_at_utc": heartbeat,
                }
            )
            updated = self._conditional_job_transition(
                kind=kind,
                job_id=job_id,
                payload=payload,
                expected_status="running",
                expected_version=int(row["version"] or 0),
                expected_worker_id=owner,
                require_unexpired_at_utc=now,
            )
            if updated:
                return payload
        return None

    def update_claimed_job(
        self,
        *,
        kind: str,
        job_id: str,
        worker_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        owner = self._validated_worker_id(worker_id)
        forbidden = self._claimed_job_immutable_fields().intersection(updates)
        if forbidden:
            raise ValueError(f"Claimed job updates cannot change: {', '.join(sorted(forbidden))}")
        safe_updates = dict(updates)
        for _ in range(5):
            now = _utc_now_iso()
            row = self._job_claim_row(kind=kind, job_id=job_id)
            if not self._has_live_job_claim(row, worker_id=owner, now_utc=now):
                return None
            payload = self._job_payload_from_row(row)
            payload.update(safe_updates)
            if "progress" in safe_updates:
                current_progress = float(row["progress"] or 0.0)
                requested_progress = float(safe_updates["progress"] or 0.0)
                payload["progress"] = max(current_progress, min(max(requested_progress, 0.0), 1.0))
            payload.update(
                {
                    "status": "running",
                    "version": int(row["version"] or 0) + 1,
                    "attempt": int(row["attempt"] or 0),
                    "max_attempts": int(row["max_attempts"] or 3),
                    "worker_id": owner,
                    "heartbeat_at_utc": row["heartbeat_at_utc"],
                    "lease_expires_at_utc": row["lease_expires_at_utc"],
                    "rq_job_id": row["rq_job_id"],
                    "updated_at_utc": now,
                }
            )
            changed = self._conditional_job_transition(
                kind=kind,
                job_id=job_id,
                payload=payload,
                expected_status="running",
                expected_version=int(row["version"] or 0),
                expected_worker_id=owner,
                require_unexpired_at_utc=now,
            )
            if changed:
                return payload
        return None

    def release_job_claim(
        self,
        *,
        kind: str,
        job_id: str,
        worker_id: str,
        status: str = "queued",
        updates: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        owner = self._validated_worker_id(worker_id)
        if status not in {"queued", "completed", "failed", "interrupted"}:
            raise ValueError("release status must be queued, completed, failed, or interrupted")
        safe_updates = dict(updates or {})
        forbidden = self._claimed_job_immutable_fields().intersection(safe_updates)
        if forbidden:
            raise ValueError(f"Claimed job updates cannot change: {', '.join(sorted(forbidden))}")
        for _ in range(5):
            now = _utc_now_iso()
            row = self._job_claim_row(kind=kind, job_id=job_id)
            if not self._has_live_job_claim(row, worker_id=owner, now_utc=now):
                return None
            attempt = int(row["attempt"] or 0)
            max_attempts = int(row["max_attempts"] or 3)
            next_status = "failed" if status == "queued" and attempt >= max_attempts else status
            payload = self._job_payload_from_row(row)
            payload.update(safe_updates)
            payload.update(
                {
                    "status": next_status,
                    "version": int(row["version"] or 0) + 1,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "worker_id": None,
                    "heartbeat_at_utc": row["heartbeat_at_utc"],
                    "lease_expires_at_utc": None,
                    "rq_job_id": row["rq_job_id"],
                    "updated_at_utc": now,
                }
            )
            if next_status == "queued":
                payload.update({"stage": "queued", "finished_at_utc": None})
            else:
                payload["finished_at_utc"] = payload.get("finished_at_utc") or now
            if next_status != status:
                payload.update(
                    {
                        "stage": "failed",
                        "progress": 1.0,
                        "message": "Job reached the maximum number of attempts and cannot be requeued.",
                        "error": payload.get("error") or "Maximum job attempts exhausted.",
                    }
                )
            released = self._conditional_job_transition(
                kind=kind,
                job_id=job_id,
                payload=payload,
                expected_status="running",
                expected_version=int(row["version"] or 0),
                expected_worker_id=owner,
                require_unexpired_at_utc=now,
            )
            if released:
                return payload
        return None

    def recover_expired_jobs(self, *, now_utc: str, limit: int = 100) -> list[dict[str, Any]]:
        now = self._normalized_utc_iso(now_utc, field_name="now_utc")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer between 1 and 200")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'running'
                  AND lease_expires_at_utc IS NOT NULL
                  AND lease_expires_at_utc <= ?
                ORDER BY lease_expires_at_utc, created_at_utc, id
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        recovered: list[dict[str, Any]] = []
        for row in rows:
            attempt = int(row["attempt"] or 0)
            max_attempts = int(row["max_attempts"] or 3)
            next_status = "queued" if attempt < max_attempts else "interrupted"
            payload = self._job_payload_from_row(row)
            payload.update(
                {
                    "status": next_status,
                    "stage": next_status,
                    "version": int(row["version"] or 0) + 1,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "worker_id": None,
                    "heartbeat_at_utc": row["heartbeat_at_utc"],
                    "lease_expires_at_utc": None,
                    "rq_job_id": row["rq_job_id"],
                    "updated_at_utc": now,
                    "message": "Worker lease expired; job returned to the queue."
                    if next_status == "queued"
                    else "Worker lease expired after the maximum number of attempts.",
                }
            )
            if next_status == "queued":
                payload["finished_at_utc"] = None
            else:
                payload.update({"progress": 1.0, "finished_at_utc": now})
            changed = self._conditional_job_transition(
                kind=str(row["kind"]),
                job_id=str(row["id"]),
                payload=payload,
                expected_status="running",
                expected_version=int(row["version"] or 0),
                expected_worker_id=row["worker_id"],
            )
            if changed:
                recovered.append(payload)
        return recovered

    def delete_job(self, *, kind: str, job_id: str, organization_id: str | None = None) -> None:
        query = "DELETE FROM jobs WHERE kind = ? AND id = ?"
        params: list[Any] = [kind, job_id]
        if organization_id is not None:
            query += " AND organization_id = ?"
            params.append(organization_id)
        with self._connect() as connection:
            connection.execute(query, tuple(params))

    def upsert_artifact(self, *, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = _utc_now_iso()
        artifact_type = str(payload.get("artifact_type") or payload.get("type") or "artifact")
        source_id = payload.get("source_id")
        key = str(payload.get("key") or payload.get("storage_key") or payload.get("uri") or "")
        artifact_id = str(payload.get("id") or self.stable_id("art", f"{organization_id}:{artifact_type}:{source_id}:{key}"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts (
                    id, organization_id, artifact_type, source_id, provider, storage_key, uri,
                    file_count, byte_count, metadata_json, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    artifact_type = excluded.artifact_type,
                    source_id = excluded.source_id,
                    provider = excluded.provider,
                    storage_key = excluded.storage_key,
                    uri = excluded.uri,
                    file_count = excluded.file_count,
                    byte_count = excluded.byte_count,
                    metadata_json = excluded.metadata_json,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    artifact_id,
                    organization_id,
                    artifact_type,
                    source_id,
                    str(payload.get("provider") or "unknown"),
                    key,
                    str(payload.get("uri") or ""),
                    int(payload.get("file_count", 0) or 0),
                    int(payload.get("byte_count", 0) or 0),
                    _json_dump(payload.get("metadata", {})),
                    str(payload.get("created_at_utc") or now),
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return self._artifact_row(row)

    def _artifact_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["metadata"] = _json_load(payload.pop("metadata_json"), {})
        payload["type"] = payload.get("artifact_type")
        payload["key"] = payload.get("storage_key")
        return payload

    def get_artifact(self, *, organization_id: str, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE organization_id = ? AND id = ?",
                (organization_id, artifact_id),
            ).fetchone()
        return None if row is None else self._artifact_row(row)

    def list_artifacts(self, *, organization_id: str, artifact_type: str | None = None, source_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM artifacts WHERE organization_id = ?"
        params: list[Any] = [organization_id]
        if artifact_type is not None:
            query += " AND artifact_type = ?"
            params.append(artifact_type)
        if source_id is not None:
            query += " AND source_id = ?"
            params.append(source_id)
        query += " ORDER BY updated_at_utc DESC LIMIT ?"
        params.append(int(limit))
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._artifact_row(row) for row in rows]

    def _market_research_report_row(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["context"] = _json_load(payload.pop("context_json"), {})
        payload["report"] = _json_load(payload.pop("report_json"), {})
        payload["source_references"] = _json_load(payload.pop("source_references_json"), [])
        payload["provider_metadata"] = _json_load(payload.pop("provider_metadata_json"), {})
        payload["warnings"] = _json_load(payload.pop("warnings_json"), [])
        payload["report_id"] = payload["id"]
        return payload

    def upsert_market_research_report(
        self,
        *,
        organization_id: str,
        user_id: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        ticker = str(payload.get("ticker") or report.get("ticker") or "UNKNOWN").upper()
        analysis_date = str(payload.get("analysis_date") or report.get("analysis_date") or now[:10])
        horizon = str(payload.get("horizon") or report.get("time_horizon") or "swing")
        job_id = payload.get("job_id")
        report_id = str(
            payload.get("id")
            or payload.get("report_id")
            or self.stable_id("mrr", f"{organization_id}:{user_id or 'machine'}:{job_id or uuid4().hex}")
        )
        status = str(payload.get("status") or "completed")
        title = str(payload.get("title") or f"{ticker} {horizon} research - {analysis_date}")
        disclaimer = str(payload.get("disclaimer") or report.get("disclaimer") or "For research and educational purposes only. Not financial advice.")
        source_references = payload.get("source_references")
        if source_references is None:
            source_references = report.get("source_references", [])
        provider_metadata = payload.get("provider_metadata")
        if provider_metadata is None:
            provider_metadata = report.get("metadata", {})
        warnings = payload.get("warnings")
        if warnings is None:
            warnings = report.get("warnings", [])
        summary = payload.get("summary")
        if summary is None:
            summary = report.get("summary")
        decision = payload.get("decision")
        if decision is None:
            decision = report.get("decision")
        confidence_value = payload.get("confidence")
        if confidence_value is None:
            confidence_value = report.get("confidence")
        confidence = int(confidence_value) if confidence_value is not None else None
        completed_at = payload.get("completed_at_utc")
        if completed_at is None and status == "completed":
            completed_at = str(report.get("created_at_utc") or now)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO market_research_reports (
                    id, organization_id, user_id, job_id, parent_report_id, version,
                    ticker, analysis_date, horizon, report_type, title, status,
                    decision, confidence, summary, disclaimer, context_json, report_json,
                    source_references_json, provider_metadata_json, warnings_json, artifact_id,
                    error, created_at_utc, updated_at_utc, completed_at_utc, deleted_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    organization_id = excluded.organization_id,
                    user_id = excluded.user_id,
                    job_id = excluded.job_id,
                    parent_report_id = excluded.parent_report_id,
                    version = excluded.version,
                    ticker = excluded.ticker,
                    analysis_date = excluded.analysis_date,
                    horizon = excluded.horizon,
                    report_type = excluded.report_type,
                    title = excluded.title,
                    status = excluded.status,
                    decision = excluded.decision,
                    confidence = excluded.confidence,
                    summary = excluded.summary,
                    disclaimer = excluded.disclaimer,
                    context_json = excluded.context_json,
                    report_json = excluded.report_json,
                    source_references_json = excluded.source_references_json,
                    provider_metadata_json = excluded.provider_metadata_json,
                    warnings_json = excluded.warnings_json,
                    artifact_id = excluded.artifact_id,
                    error = excluded.error,
                    updated_at_utc = excluded.updated_at_utc,
                    completed_at_utc = COALESCE(excluded.completed_at_utc, market_research_reports.completed_at_utc),
                    deleted_at_utc = excluded.deleted_at_utc
                """,
                (
                    report_id,
                    organization_id,
                    user_id,
                    str(job_id) if job_id else None,
                    payload.get("parent_report_id"),
                    int(payload.get("version") or 1),
                    ticker,
                    analysis_date,
                    horizon,
                    str(payload.get("report_type") or "market_research_committee"),
                    title,
                    status,
                    str(decision) if decision is not None else None,
                    confidence,
                    str(summary) if summary is not None else None,
                    disclaimer,
                    _json_dump(context),
                    _json_dump(report),
                    _json_dump(source_references or []),
                    _json_dump(provider_metadata or {}),
                    _json_dump(warnings or []),
                    payload.get("artifact_id"),
                    payload.get("error"),
                    str(payload.get("created_at_utc") or now),
                    str(payload.get("updated_at_utc") or now),
                    completed_at,
                    payload.get("deleted_at_utc"),
                ),
            )
            row = connection.execute(
                "SELECT * FROM market_research_reports WHERE organization_id = ? AND id = ?",
                (organization_id, report_id),
            ).fetchone()
        return self._market_research_report_row(row)

    def list_market_research_reports(
        self,
        *,
        organization_id: str,
        user_id: str,
        search: str | None = None,
        ticker: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = ["organization_id = ?", "user_id = ?"]
        params: list[Any] = [organization_id, user_id]
        if not include_deleted:
            clauses.append("deleted_at_utc IS NULL")
        if search and search.strip():
            term = f"%{search.strip()}%"
            clauses.append("(title LIKE ? OR ticker LIKE ? OR summary LIKE ?)")
            params.extend([term, term.upper(), term])
        if ticker and ticker.strip():
            clauses.append("ticker = ?")
            params.append(ticker.strip().upper())
        if status and status.strip():
            clauses.append("status = ?")
            params.append(status.strip())
        params.extend([max(1, min(int(limit), 200)), max(0, int(offset))])
        query = f"""
            SELECT * FROM market_research_reports
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at_utc DESC
            LIMIT ? OFFSET ?
        """
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._market_research_report_row(row) for row in rows]

    def get_market_research_report(
        self,
        *,
        organization_id: str,
        report_id: str,
        user_id: str,
        include_deleted: bool = False,
    ) -> dict[str, Any] | None:
        query = """
            SELECT * FROM market_research_reports
            WHERE organization_id = ? AND user_id = ? AND id = ?
        """
        params: list[Any] = [organization_id, user_id, report_id]
        if not include_deleted:
            query += " AND deleted_at_utc IS NULL"
        with self._connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return None if row is None else self._market_research_report_row(row)

    def soft_delete_market_research_report(
        self,
        *,
        organization_id: str,
        report_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE market_research_reports
                SET deleted_at_utc = COALESCE(deleted_at_utc, ?),
                    updated_at_utc = ?,
                    status = CASE WHEN status = 'completed' THEN status ELSE 'deleted' END
                WHERE organization_id = ? AND user_id = ? AND id = ?
                """,
                (now, now, organization_id, user_id, report_id),
            )
            row = connection.execute(
                "SELECT * FROM market_research_reports WHERE organization_id = ? AND user_id = ? AND id = ?",
                (organization_id, user_id, report_id),
            ).fetchone()
        return None if row is None else self._market_research_report_row(row)

    def save_deployment_config(
        self,
        *,
        config_id: str,
        source: str,
        config: dict[str, Any],
        organization_id: str | None = None,
        path: str | Path | None = None,
        created_at_utc: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO deployment_configs (id, organization_id, source, path, config_json, created_at_utc)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    organization_id = excluded.organization_id,
                    source = excluded.source,
                    path = excluded.path,
                    config_json = excluded.config_json,
                    created_at_utc = excluded.created_at_utc
                """,
                (
                    config_id,
                    organization_id,
                    source,
                    str(path) if path is not None else None,
                    _json_dump(config),
                    created_at_utc or _utc_now_iso(),
                ),
            )

    def get_deployment_config(self, *, config_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, organization_id, source, path, config_json, created_at_utc FROM deployment_configs WHERE id = ?",
                (config_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "organization_id": row["organization_id"],
            "source": row["source"],
            "path": row["path"],
            "config": _json_load(row["config_json"], {}),
            "created_at_utc": row["created_at_utc"],
        }

    def save_experiment_run(
        self,
        *,
        experiment_id: str,
        kind: str,
        summary: dict[str, Any],
        organization_id: str | None = None,
        artifact_dir: str | Path | None = None,
        created_at_utc: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO experiment_runs (id, organization_id, kind, artifact_dir, summary_json, created_at_utc)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    organization_id = excluded.organization_id,
                    kind = excluded.kind,
                    artifact_dir = excluded.artifact_dir,
                    summary_json = excluded.summary_json,
                    created_at_utc = excluded.created_at_utc
                """,
                (
                    experiment_id,
                    organization_id,
                    kind,
                    str(artifact_dir) if artifact_dir is not None else None,
                    _json_dump(summary),
                    created_at_utc or _utc_now_iso(),
                ),
            )

    def list_experiment_runs(self, *, kind: str | None = None, organization_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT id, organization_id, kind, artifact_dir, summary_json, created_at_utc FROM experiment_runs"
        clauses: list[str] = []
        params: list[Any] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if organization_id is not None:
            clauses.append("organization_id = ?")
            params.append(organization_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at_utc DESC"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [
            {
                "id": row["id"],
                "organization_id": row["organization_id"],
                "kind": row["kind"],
                "artifact_dir": row["artifact_dir"],
                "summary": _json_load(row["summary_json"], {}),
                "created_at_utc": row["created_at_utc"],
            }
            for row in rows
        ]

    def counts(self) -> MetadataCounts:
        with self._connect() as connection:
            jobs = int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
            deployment_configs = int(connection.execute("SELECT COUNT(*) FROM deployment_configs").fetchone()[0])
            experiment_runs = int(connection.execute("SELECT COUNT(*) FROM experiment_runs").fetchone()[0])
            artifacts = int(connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0])
            users = int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
            organizations = int(connection.execute("SELECT COUNT(*) FROM organizations").fetchone()[0])
            projects = int(connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
            experiments = int(connection.execute("SELECT COUNT(*) FROM experiments").fetchone()[0])
            paper_agents = int(connection.execute("SELECT COUNT(*) FROM paper_agents").fetchone()[0])
            datasets = int(connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0])
            api_keys = int(connection.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0])
            subscriptions = int(connection.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0])
            telemetry_events = int(connection.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0])
            refresh_runs = int(connection.execute("SELECT COUNT(*) FROM refresh_runs").fetchone()[0])
            refresh_statuses = int(connection.execute("SELECT COUNT(*) FROM refresh_statuses").fetchone()[0])
            market_research_reports = int(connection.execute("SELECT COUNT(*) FROM market_research_reports WHERE deleted_at_utc IS NULL").fetchone()[0])
        return MetadataCounts(
            jobs=jobs,
            deployment_configs=deployment_configs,
            experiment_runs=experiment_runs,
            artifacts=artifacts,
            users=users,
            organizations=organizations,
            projects=projects,
            experiments=experiments,
            paper_agents=paper_agents,
            datasets=datasets,
            api_keys=api_keys,
            subscriptions=subscriptions,
            telemetry_events=telemetry_events,
            refresh_runs=refresh_runs,
            refresh_statuses=refresh_statuses,
            market_research_reports=market_research_reports,
        )


class _CompatRow(dict):
    """Dict row with sqlite-like integer indexing for legacy store methods."""

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _CompatCursor:
    def __init__(self, cursor) -> None:
        self.cursor = cursor

    def fetchone(self):
        row = self.cursor.fetchone()
        return None if row is None else _CompatRow(row)

    def fetchall(self):
        return [_CompatRow(row) for row in self.cursor.fetchall()]

    @property
    def rowcount(self) -> int:
        return int(self.cursor.rowcount)


class _EmptyCursor:
    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _PostgresCompatConnection:
    def __init__(self, raw_connection) -> None:
        self.raw_connection = raw_connection

    @staticmethod
    def _translate(sql: str) -> tuple[str, bool]:
        statement = sql.strip()
        # SQLite's INTEGER is unbounded for these Unix timestamps, while
        # PostgreSQL INTEGER overflows in 2038. Keep the compatibility schema
        # aligned with the Alembic BigInteger columns.
        statement = re.sub(r"\bstripe_event_created_at\s+INTEGER\b", "stripe_event_created_at BIGINT", statement)
        statement = re.sub(r"\bevent_created_at\s+INTEGER\b", "event_created_at BIGINT", statement)
        ignored_insert = bool(re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", statement, flags=re.IGNORECASE))
        statement = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", statement, flags=re.IGNORECASE)
        if ignored_insert:
            statement = statement.rstrip(";") + " ON CONFLICT DO NOTHING"
        statement = statement.replace("?", "%s")
        return statement, ignored_insert

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None):
        statement = sql.strip()
        if not statement:
            return _EmptyCursor()
        if statement.upper().startswith("PRAGMA"):
            return _EmptyCursor()
        translated, _ = self._translate(statement)
        try:
            cursor = self.raw_connection.cursor()
            cursor.execute(translated, tuple(params or ()))
            return _CompatCursor(cursor)
        except Exception as exc:
            if exc.__class__.__name__ in {"UniqueViolation", "IntegrityError"}:
                self.raw_connection.rollback()
                raise sqlite3.IntegrityError(str(exc)) from exc
            raise

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)


class PostgresMetadataStore(SQLiteMetadataStore):
    """Postgres-backed metadata store used by production API and workers.

    The application-level methods intentionally remain compatible with
    SQLiteMetadataStore so the routers/services do not need database-specific
    branches. Alembic owns production schema creation; this class can also
    create a compatible schema for local smoke tests and one-off environments.
    """

    def __init__(self, database_url: str, *, enable_demo_accounts: bool = False, initialize: bool = True) -> None:
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        self.enable_demo_accounts = enable_demo_accounts
        if initialize:
            self._initialize()

    @contextmanager
    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except Exception as exc:  # pragma: no cover - dependency checked in production image
            raise RuntimeError("psycopg is required for PostgresMetadataStore.") from exc

        raw_connection = psycopg.connect(self.database_url, row_factory=dict_row)
        connection = _PostgresCompatConnection(raw_connection)
        try:
            yield connection
            raw_connection.commit()
        except Exception:
            raw_connection.rollback()
            raise
        finally:
            raw_connection.close()

    def _begin_claimed_publication(self, connection: Any) -> None:
        # psycopg starts the transaction on the locking SELECT below.
        del connection

    def _claimed_publication_row(self, connection: Any, *, kind: str, job_id: str) -> Any:
        return connection.execute(
            "SELECT * FROM jobs WHERE kind = ? AND id = ? FOR UPDATE",
            (kind, job_id),
        ).fetchone()

    def _migrate_legacy_columns(self) -> None:
        required_by_table = {
            "users": {
                "role": "TEXT NOT NULL DEFAULT 'user'",
                "status": "TEXT NOT NULL DEFAULT 'active'",
                "email_verified_at_utc": "TEXT",
                "mfa_secret": "TEXT",
                "mfa_pending_secret": "TEXT",
                "mfa_enabled": "INTEGER NOT NULL DEFAULT 0",
                "mfa_last_counter": "INTEGER",
            },
            "jobs": {
                "organization_id": "TEXT",
                "version": "INTEGER NOT NULL DEFAULT 0",
                "attempt": "INTEGER NOT NULL DEFAULT 0",
                "max_attempts": "INTEGER NOT NULL DEFAULT 3",
                "worker_id": "TEXT",
                "heartbeat_at_utc": "TEXT",
                "lease_expires_at_utc": "TEXT",
                "rq_job_id": "TEXT",
            },
            "deployment_configs": {"organization_id": "TEXT"},
            "experiment_runs": {"organization_id": "TEXT"},
            "paper_agents": {"deployment_id": "TEXT"},
            "subscriptions": {
                "stripe_event_created_at": "BIGINT NOT NULL DEFAULT 0",
                "stripe_event_id": "TEXT",
            },
            "stripe_events": {
                "payload_hash": "TEXT NOT NULL DEFAULT ''",
                "event_created_at": "BIGINT NOT NULL DEFAULT 0",
                "status": "TEXT NOT NULL DEFAULT 'processed'",
                "attempt_count": "INTEGER NOT NULL DEFAULT 1",
                "max_attempts": "INTEGER NOT NULL DEFAULT 5",
                "claim_token": "TEXT",
                "claimed_at_utc": "TEXT",
                "last_error_code": "TEXT",
                "created_at_utc": "TEXT NOT NULL DEFAULT ''",
                "updated_at_utc": "TEXT NOT NULL DEFAULT ''",
            },
            "api_keys": {
                "token_hash": "TEXT",
                "scopes_json": "TEXT NOT NULL DEFAULT '[]'",
                "last_used_at_utc": "TEXT",
            },
        }
        with self._connect() as connection:
            for table, required_columns in required_by_table.items():
                rows = connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = %s
                    """,
                    (table,),
                ).fetchall()
                existing = {str(row["column_name"]) for row in rows}
                for column, definition in required_columns.items():
                    if column not in existing:
                        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status_lease ON jobs(status, lease_expires_at_utc)"
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_rq_job_id_unique
                ON jobs(rq_job_id) WHERE rq_job_id IS NOT NULL
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_agents_org_deployment_name
                ON paper_agents(organization_id, deployment_id, name)
                WHERE deployment_id IS NOT NULL
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_stripe_events_status_updated ON stripe_events(status, updated_at_utc)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quota_usage_counters (
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    feature TEXT NOT NULL,
                    window_start_utc TEXT NOT NULL,
                    window_end_utc TEXT NOT NULL,
                    quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (organization_id, feature, window_start_utc, window_end_utc)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quota_counters_window ON quota_usage_counters(window_end_utc, organization_id, feature)"
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_paper_agents_org_deployment_updated
                ON paper_agents(organization_id, deployment_id, updated_at_utc DESC)
                """
            )

    def admin_metric_snapshot(self) -> dict[str, Any]:
        since_7d = datetime.now(UTC).replace(microsecond=0)
        since_7d = since_7d.replace(tzinfo=UTC)
        since_30d = since_7d
        since_7d_iso = (since_7d.timestamp() - 7 * 24 * 3600)
        since_30d_iso = (since_30d.timestamp() - 30 * 24 * 3600)
        cutoff_7d = datetime.fromtimestamp(since_7d_iso, tz=UTC).isoformat().replace("+00:00", "Z")
        cutoff_30d = datetime.fromtimestamp(since_30d_iso, tz=UTC).isoformat().replace("+00:00", "Z")
        with self._connect() as connection:
            users_total = int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
            users_active = int(connection.execute("SELECT COUNT(*) FROM users WHERE status = 'active'").fetchone()[0])
            admins_active = int(connection.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND status = 'active'").fetchone()[0])
            signups_7d = int(connection.execute("SELECT COUNT(*) FROM users WHERE created_at_utc >= ?", (cutoff_7d,)).fetchone()[0])
            signups_30d = int(connection.execute("SELECT COUNT(*) FROM users WHERE created_at_utc >= ?", (cutoff_30d,)).fetchone()[0])
            active_7d = int(connection.execute("SELECT COUNT(*) FROM users WHERE last_login_at_utc >= ?", (cutoff_7d,)).fetchone()[0])
            subscriptions = {
                row["status"]: int(row["count"])
                for row in connection.execute("SELECT status, COUNT(*) AS count FROM subscriptions GROUP BY status").fetchall()
            }
            plans = {
                row["plan"]: int(row["count"])
                for row in connection.execute("SELECT plan, COUNT(*) AS count FROM subscriptions GROUP BY plan").fetchall()
            }
        return {
            "users_total": users_total,
            "users_active": users_active,
            "admins_active": admins_active,
            "signups_7d": signups_7d,
            "signups_30d": signups_30d,
            "active_users_7d": active_7d,
            "subscriptions_by_status": subscriptions,
            "plans": plans,
        }


def build_metadata_store(settings: Any) -> MetadataStore:
    database_url = getattr(settings, "database_url", None)
    is_production = bool(getattr(settings, "is_production", False))
    enable_demo_accounts = bool(getattr(settings, "enable_demo_accounts", False))
    if database_url:
        normalized = str(database_url)
        if normalized.startswith(("postgresql://", "postgresql+psycopg://")):
            return PostgresMetadataStore(
                normalized,
                enable_demo_accounts=enable_demo_accounts,
                initialize=(not is_production or enable_demo_accounts),
            )
        if is_production:
            raise RuntimeError("Production metadata requires a postgresql DATABASE_URL.")
    if is_production:
        raise RuntimeError("Production metadata requires DATABASE_URL.")
    return SQLiteMetadataStore(getattr(settings, "metadata_db_path"), enable_demo_accounts=enable_demo_accounts)
