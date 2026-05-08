from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Protocol
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json_dump(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


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


class MetadataStore(Protocol):
    """Operational metadata contract shared by API and workers.

    The codebase still uses this class dynamically, so the protocol intentionally
    stays broad: SQLiteMetadataStore and PostgresMetadataStore expose the same
    public methods while the backend imports them through build_metadata_store().
    """

    def counts(self) -> MetadataCounts:
        ...


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
                    mfa_enabled INTEGER NOT NULL DEFAULT 0,
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
                    payload_json TEXT NOT NULL
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
            table_columns = {
                table: {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
                for table in ("jobs", "deployment_configs", "experiment_runs", "api_keys")
            }
            if "organization_id" not in table_columns["jobs"]:
                connection.execute("ALTER TABLE jobs ADD COLUMN organization_id TEXT")
            if "organization_id" not in table_columns["deployment_configs"]:
                connection.execute("ALTER TABLE deployment_configs ADD COLUMN organization_id TEXT")
            if "organization_id" not in table_columns["experiment_runs"]:
                connection.execute("ALTER TABLE experiment_runs ADD COLUMN organization_id TEXT")
            if "token_hash" not in table_columns["api_keys"]:
                connection.execute("ALTER TABLE api_keys ADD COLUMN token_hash TEXT")
            if "scopes_json" not in table_columns["api_keys"]:
                connection.execute("ALTER TABLE api_keys ADD COLUMN scopes_json TEXT NOT NULL DEFAULT '[]'")
            if "last_used_at_utc" not in table_columns["api_keys"]:
                connection.execute("ALTER TABLE api_keys ADD COLUMN last_used_at_utc TEXT")
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_jobs_org_kind_created
                    ON jobs(organization_id, kind, created_at_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_org_kind_status
                    ON jobs(organization_id, kind, status);
                CREATE INDEX IF NOT EXISTS idx_experiment_runs_org_kind_created
                    ON experiment_runs(organization_id, kind, created_at_utc DESC);
                CREATE INDEX IF NOT EXISTS idx_api_keys_token_hash
                    ON api_keys(token_hash);
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

    def record_stripe_event(self, *, event_id: str, event_type: str, payload: dict[str, Any]) -> bool:
        """Return True only when a Stripe event is seen for the first time."""

        now = _utc_now_iso()
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO stripe_events (id, event_type, processed_at_utc, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (event_id, event_type, now, _json_dump(payload)),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def record_usage_event(
        self,
        *,
        organization_id: str,
        feature: str,
        quantity: float = 1.0,
        user_id: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_id = self.stable_id("use", f"{organization_id}:{feature}:{uuid4().hex}")
        now = _utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO usage_events (
                    id, organization_id, user_id, feature, quantity, properties_json, occurred_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, organization_id, user_id, feature, float(quantity), _json_dump(properties or {}), now),
            )
        return {
            "id": event_id,
            "organization_id": organization_id,
            "user_id": user_id,
            "feature": feature,
            "quantity": float(quantity),
            "properties": properties or {},
            "occurred_at_utc": now,
        }

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

    def get_organization_quotas(self, *, organization_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT quotas_json FROM organization_quotas WHERE organization_id = ?",
                (organization_id,),
            ).fetchone()
        return None if row is None else _json_load(row["quotas_json"], {})

    def upsert_organization_quotas(self, *, organization_id: str, quotas: dict[str, Any]) -> dict[str, Any]:
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
                (organization_id, _json_dump(quotas), now),
            )
        return {"organization_id": organization_id, "quotas": quotas, "updated_at_utc": now}

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

    def list_audit_log(self, *, organization_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit_log"
        params: tuple[Any, ...]
        if organization_id:
            query += " WHERE organization_id = ?"
            params = (organization_id, int(limit))
        else:
            params = (int(limit),)
        query += " ORDER BY occurred_at_utc DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
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
        organization_id = payload.get("organization_id")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, organization_id, kind, status, stage, progress, request_json, payload_json,
                    error, created_at_utc, updated_at_utc, started_at_utc,
                    finished_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    organization_id = excluded.organization_id,
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
                    str(organization_id) if organization_id else None,
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

    def list_jobs(self, *, kind: str, organization_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT payload_json FROM jobs WHERE kind = ?"
        params: list[Any] = [kind]
        if organization_id is not None:
            query += " AND organization_id = ?"
            params.append(organization_id)
        query += " ORDER BY created_at_utc DESC"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [_json_load(row["payload_json"], {}) for row in rows]

    def get_job(self, *, kind: str, job_id: str, organization_id: str | None = None) -> dict[str, Any] | None:
        query = "SELECT payload_json FROM jobs WHERE kind = ? AND id = ?"
        params: list[Any] = [kind, job_id]
        if organization_id is not None:
            query += " AND organization_id = ?"
            params.append(organization_id)
        with self._connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return None if row is None else _json_load(row["payload_json"], {})

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

    def _migrate_legacy_columns(self) -> None:
        required_by_table = {
            "users": {
                "role": "TEXT NOT NULL DEFAULT 'user'",
                "status": "TEXT NOT NULL DEFAULT 'active'",
                "email_verified_at_utc": "TEXT",
                "mfa_secret": "TEXT",
                "mfa_enabled": "INTEGER NOT NULL DEFAULT 0",
            },
            "jobs": {"organization_id": "TEXT"},
            "deployment_configs": {"organization_id": "TEXT"},
            "experiment_runs": {"organization_id": "TEXT"},
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
