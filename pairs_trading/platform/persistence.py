from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any


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
                """
            )

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
        return MetadataCounts(
            jobs=jobs,
            deployment_configs=deployment_configs,
            experiment_runs=experiment_runs,
        )
