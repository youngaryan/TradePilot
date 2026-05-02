from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json_dump(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


@dataclass(frozen=True)
class MetadataCounts:
    jobs: int
    deployment_configs: int
    experiment_runs: int
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


class SQLiteMetadataStore:
    """Small durable metadata store for the modular-monolith stage.

    The heavy research outputs still belong in parquet/JSON artifacts. SQLite is
    used for operational metadata that should be easy to query from API routes,
    workers, and future admin screens without reading a directory tree.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
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
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
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
                    source TEXT NOT NULL,
                    path TEXT,
                    config_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS experiment_runs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    artifact_dir TEXT,
                    summary_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_experiment_runs_kind_created
                    ON experiment_runs(kind, created_at_utc DESC);

                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
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

                CREATE TABLE IF NOT EXISTS paper_agents (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
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
                    status TEXT NOT NULL,
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
                    current_period_end_utc TEXT,
                    usage_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
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
                """
            )
        self.ensure_demo_workspace()

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def stable_id(prefix: str, value: str) -> str:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:20]
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

    def ensure_demo_workspace(
        self,
        *,
        email: str = "demo@quantops.local",
        display_name: str = "Demo Quant",
        password_hash: str = "demo-password-hash",
    ) -> dict[str, Any]:
        """Seed a local-first workspace so the SaaS shell is usable immediately.

        Password verification lives in the backend service. The seed hash is
        intentionally a placeholder that the auth service accepts only for the
        documented demo password.
        """

        now = _utc_now_iso()
        user_id = self.stable_id("usr", email.casefold())
        org_id = self.stable_id("org", "quantops-demo")
        project_id = self.stable_id("prj", "quantops-demo-research")
        subscription_id = self.stable_id("sub", org_id)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO users (id, email, display_name, password_hash, created_at_utc, updated_at_utc)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, email.casefold(), display_name, password_hash, now, now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO organizations (
                    id, name, slug, owner_user_id, billing_email, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (org_id, "QuantOps Demo", "quantops-demo", user_id, email.casefold(), now, now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO organization_members (organization_id, user_id, role, created_at_utc)
                VALUES (?, ?, ?, ?)
                """,
                (org_id, user_id, "owner", now),
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
                (subscription_id, org_id, "pro_trial", "trialing", _json_dump({"backtests": 0, "paper_runs": 0}), now, now),
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
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.token_hash, s.user_id, s.created_at_utc, s.expires_at_utc,
                       u.email, u.display_name
                FROM auth_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ?
                """,
                (self.hash_token(token),),
            ).fetchone()
        return self._row_to_dict(row)

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
                    current_period_end_utc, usage_json, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    payload.get("current_period_end_utc"),
                    _json_dump(payload.get("usage", {})),
                    str(payload.get("created_at_utc") or now),
                    now,
                ),
            )
        return self.get_subscription(organization_id=organization_id) or {}

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

    def create_api_key_metadata(
        self,
        *,
        organization_id: str,
        name: str,
        provider: str,
        secret: str | None = None,
        secret_ref: str | None = None,
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        key_id = self.stable_id("key", f"{organization_id}:{provider}:{name}:{uuid4().hex}")
        masked = self._mask_secret(secret or secret_ref or "")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO api_keys (
                    id, organization_id, name, provider, masked_value, secret_ref,
                    status, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (key_id, organization_id, name, provider, masked, secret_ref, "active", now, now),
            )
            row = connection.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
        return dict(row)

    def list_api_keys(self, *, organization_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM api_keys WHERE organization_id = ? ORDER BY created_at_utc DESC",
                (organization_id,),
            ).fetchall()
        return [dict(row) for row in rows]

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
        agent_id = str(payload.get("id") or self.stable_id("agt", f"{organization_id}:{payload.get('name')}:{uuid4().hex}"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO paper_agents (
                    id, organization_id, project_id, name, pipeline, status, fake_cash,
                    config_json, latest_payload_json, warnings_json, created_at_utc, updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_id = excluded.project_id,
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
                    str(payload.get("name") or agent_id),
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
            row = connection.execute("SELECT * FROM paper_agents WHERE id = ?", (agent_id,)).fetchone()
        return self._paper_agent_row(row)

    def _paper_agent_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["config"] = _json_load(payload.pop("config_json"), {})
        payload["latest_payload"] = _json_load(payload.pop("latest_payload_json"), {})
        payload["warnings"] = _json_load(payload.pop("warnings_json"), [])
        return payload

    def list_paper_agents(self, *, organization_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM paper_agents WHERE organization_id = ? ORDER BY updated_at_utc DESC",
                (organization_id,),
            ).fetchall()
        return [self._paper_agent_row(row) for row in rows]

    def get_paper_agent(self, *, organization_id: str, agent_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_agents WHERE organization_id = ? AND id = ?",
                (organization_id, agent_id),
            ).fetchone()
        return None if row is None else self._paper_agent_row(row)

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
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, kind, status, stage, progress, request_json, payload_json,
                    error, created_at_utc, updated_at_utc, started_at_utc,
                    finished_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind = excluded.kind,
                    status = excluded.status,
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
                    kind,
                    str(payload.get("status", "unknown")),
                    payload.get("stage"),
                    float(payload.get("progress", 0.0) or 0.0),
                    _json_dump(payload.get("request", {})),
                    _json_dump(payload),
                    payload.get("error"),
                    str(payload.get("created_at_utc") or _utc_now_iso()),
                    str(payload.get("updated_at_utc") or _utc_now_iso()),
                    payload.get("started_at_utc"),
                    payload.get("finished_at_utc"),
                ),
            )

    def list_jobs(self, *, kind: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM jobs WHERE kind = ? ORDER BY created_at_utc DESC",
                (kind,),
            ).fetchall()
        return [_json_load(row["payload_json"], {}) for row in rows]

    def get_job(self, *, kind: str, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM jobs WHERE kind = ? AND id = ?",
                (kind, job_id),
            ).fetchone()
        return None if row is None else _json_load(row["payload_json"], {})

    def delete_job(self, *, kind: str, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM jobs WHERE kind = ? AND id = ?", (kind, job_id))

    def save_deployment_config(
        self,
        *,
        config_id: str,
        source: str,
        config: dict[str, Any],
        path: str | Path | None = None,
        created_at_utc: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO deployment_configs (id, source, path, config_json, created_at_utc)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source = excluded.source,
                    path = excluded.path,
                    config_json = excluded.config_json,
                    created_at_utc = excluded.created_at_utc
                """,
                (
                    config_id,
                    source,
                    str(path) if path is not None else None,
                    _json_dump(config),
                    created_at_utc or _utc_now_iso(),
                ),
            )

    def get_deployment_config(self, *, config_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, source, path, config_json, created_at_utc FROM deployment_configs WHERE id = ?",
                (config_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
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
        artifact_dir: str | Path | None = None,
        created_at_utc: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO experiment_runs (id, kind, artifact_dir, summary_json, created_at_utc)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind = excluded.kind,
                    artifact_dir = excluded.artifact_dir,
                    summary_json = excluded.summary_json,
                    created_at_utc = excluded.created_at_utc
                """,
                (
                    experiment_id,
                    kind,
                    str(artifact_dir) if artifact_dir is not None else None,
                    _json_dump(summary),
                    created_at_utc or _utc_now_iso(),
                ),
            )

    def list_experiment_runs(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT id, kind, artifact_dir, summary_json, created_at_utc FROM experiment_runs"
        params: tuple[str, ...] = tuple()
        if kind is not None:
            query += " WHERE kind = ?"
            params = (kind,)
        query += " ORDER BY created_at_utc DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                "id": row["id"],
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
        return MetadataCounts(
            jobs=jobs,
            deployment_configs=deployment_configs,
            experiment_runs=experiment_runs,
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
        )
