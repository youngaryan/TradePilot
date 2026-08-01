from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import socket
import sqlite3
from threading import Event, Thread
from time import monotonic
from typing import Any, Callable

from .config import BackendSettings
from .storage import S3ArtifactStorage


LOGGER = logging.getLogger("pairs_trading.health")
DEFAULT_PROBE_TIMEOUT_SECONDS = 2.0
HEARTBEAT_KEY_PREFIX = "tradepilot:role-heartbeat"
VALID_HEARTBEAT_ROLES = frozenset({"worker", "controller"})


class DependencyProbeError(RuntimeError):
    """A dependency failed a probe with a safe, public explanation."""


@dataclass(frozen=True)
class ProbeResult:
    status: str
    message: str


Probe = Callable[[BackendSettings, float], ProbeResult]


def _expected_alembic_heads() -> set[str]:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except ImportError as exc:  # pragma: no cover - backend images install Alembic
        raise DependencyProbeError("database migration metadata is unavailable") from exc

    configured_path = Path(os.getenv("ALEMBIC_CONFIG", "alembic.ini"))
    candidates = (
        configured_path if configured_path.is_absolute() else Path.cwd() / configured_path,
        Path(__file__).resolve().parents[2] / "alembic.ini",
    )
    config_path = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
    if config_path is None:
        raise DependencyProbeError("database migration metadata is unavailable")
    config = Config(str(config_path))
    script_location = Path(config.get_main_option("script_location") or "migrations")
    if not script_location.is_absolute():
        script_location = config_path.parent / script_location
    config.set_main_option("script_location", str(script_location.resolve()))
    heads = set(ScriptDirectory.from_config(config).get_heads())
    if not heads:
        raise DependencyProbeError("database migration metadata has no head revision")
    return heads


def _validate_migration_revision(rows: list[object]) -> None:
    current = {
        str(row[0] if not isinstance(row, dict) else row.get("version_num") or "").strip()
        for row in rows
    }
    current.discard("")
    if current != _expected_alembic_heads():
        raise DependencyProbeError("database schema is not at the application migration head")


def _probe_sqlite(database_url: str, timeout_seconds: float) -> None:
    raw_path = database_url.removeprefix("sqlite:///")
    if raw_path == database_url or not raw_path:
        raise DependencyProbeError("configured database URL type is unsupported")
    database_path = Path(raw_path).expanduser().resolve()
    uri = f"file:{database_path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=timeout_seconds)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("SELECT 1").fetchone()
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        _validate_migration_revision(list(rows))
    finally:
        connection.close()


def _probe_postgres(database_url: str, timeout_seconds: float) -> None:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - backend images install psycopg
        raise DependencyProbeError("database driver is unavailable") from exc

    normalized_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    statement_timeout_ms = max(50, math.ceil(timeout_seconds * 1000))
    connection = psycopg.connect(
        normalized_url,
        connect_timeout=max(1, math.ceil(timeout_seconds)),
        autocommit=True,
        options=(
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={statement_timeout_ms} "
            f"-c lock_timeout={statement_timeout_ms}"
        ),
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.execute("SELECT version_num FROM alembic_version")
            rows = list(cursor.fetchall())
        _validate_migration_revision(rows)
    finally:
        connection.close()


def probe_database(settings: BackendSettings, timeout_seconds: float) -> ProbeResult:
    database_url = str(settings.database_url or "").strip()
    if database_url.startswith("sqlite:///"):
        _probe_sqlite(database_url, timeout_seconds)
    elif database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        _probe_postgres(database_url, timeout_seconds)
    else:
        raise DependencyProbeError("configured database URL type is unsupported")
    return ProbeResult(status="ok", message="database reachable and schema migration is current")


def _redis_client(settings: BackendSettings, timeout_seconds: float):
    try:
        from redis import Redis
    except ImportError as exc:  # pragma: no cover - backend images install redis
        raise DependencyProbeError("Redis client is unavailable") from exc
    return Redis.from_url(
        str(settings.redis_url),
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
        decode_responses=True,
    )


def probe_redis(settings: BackendSettings, timeout_seconds: float) -> ProbeResult:
    client = _redis_client(settings, timeout_seconds)
    if client.ping() is not True:
        raise DependencyProbeError("Redis did not acknowledge the readiness probe")
    return ProbeResult(status="ok", message="Redis reachable")


def probe_s3(settings: BackendSettings, timeout_seconds: float) -> ProbeResult:
    storage = S3ArtifactStorage(
        bucket=str(settings.s3_bucket or ""),
        endpoint_url=settings.s3_endpoint_url,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key,
    )
    storage.probe_bucket(timeout_seconds=timeout_seconds)
    return ProbeResult(status="ok", message="artifact bucket reachable")


class ReadinessChecker:
    def __init__(
        self,
        settings: BackendSettings,
        *,
        timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
        database_probe: Probe = probe_database,
        redis_probe: Probe = probe_redis,
        s3_probe: Probe = probe_s3,
    ) -> None:
        self.settings = settings
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.probes = {
            "database": database_probe,
            "redis": redis_probe,
            "artifact_storage": s3_probe,
        }

    def _configured(self) -> dict[str, bool]:
        return {
            "database": bool(str(self.settings.database_url or "").strip()),
            "redis": bool(str(self.settings.redis_url or "").strip()),
            "artifact_storage": bool(str(self.settings.s3_bucket or "").strip()),
        }

    def check(self) -> dict[str, Any]:
        started = monotonic()
        configured = self._configured()
        components: dict[str, dict[str, Any]] = {}
        executor = ThreadPoolExecutor(max_workers=len(self.probes), thread_name_prefix="readiness-probe")
        futures: dict[Future[ProbeResult], tuple[str, float]] = {}
        try:
            for name, probe in self.probes.items():
                required = configured[name] or self.settings.is_production
                if not configured[name]:
                    components[name] = {
                        "status": "error" if required else "degraded",
                        "required": required,
                        "latency_ms": 0,
                        "message": "dependency is not configured",
                    }
                    continue
                component_started = monotonic()
                future = executor.submit(probe, self.settings, self.timeout_seconds)
                futures[future] = (name, component_started)

            done, pending = wait(set(futures), timeout=self.timeout_seconds)
            for future in done:
                name, component_started = futures[future]
                required = configured[name] or self.settings.is_production
                latency_ms = max(0, round((monotonic() - component_started) * 1000))
                try:
                    result = future.result()
                except DependencyProbeError as exc:
                    components[name] = {
                        "status": "error",
                        "required": required,
                        "latency_ms": latency_ms,
                        "message": str(exc),
                    }
                except Exception:
                    components[name] = {
                        "status": "error",
                        "required": required,
                        "latency_ms": latency_ms,
                        "message": "dependency probe failed",
                    }
                else:
                    components[name] = {
                        "status": result.status,
                        "required": required,
                        "latency_ms": latency_ms,
                        "message": result.message,
                    }
            for future in pending:
                future.cancel()
                name, component_started = futures[future]
                components[name] = {
                    "status": "error",
                    "required": configured[name] or self.settings.is_production,
                    "latency_ms": max(0, round((monotonic() - component_started) * 1000)),
                    "message": "dependency probe timed out",
                }
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        unavailable = any(
            component["required"] and component["status"] != "ok"
            for component in components.values()
        )
        degraded = any(component["status"] == "degraded" for component in components.values())
        return {
            "status": "not_ready" if unavailable else ("degraded" if degraded else "ready"),
            "ready": not unavailable,
            "service": "pairs-trading-backend",
            "latency_ms": max(0, round((monotonic() - started) * 1000)),
            "components": components,
        }


def _normalized_role(role: str) -> str:
    normalized = str(role).strip().lower()
    if normalized not in VALID_HEARTBEAT_ROLES:
        raise ValueError("role must be worker or controller")
    return normalized


def _heartbeat_instance_token(instance_id: str | None = None) -> str:
    raw = str(instance_id or os.getenv("ROLE_HEARTBEAT_INSTANCE") or socket.gethostname()).strip()
    if not raw:
        raw = "default"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def role_heartbeat_key(role: str, *, instance_id: str | None = None) -> str:
    return f"{HEARTBEAT_KEY_PREFIX}:{_normalized_role(role)}:{_heartbeat_instance_token(instance_id)}"


def role_heartbeat_stale_after(settings: BackendSettings, role: str) -> int:
    normalized = _normalized_role(role)
    cadence = settings.job_heartbeat_seconds
    if normalized == "controller":
        cadence = max(cadence, settings.job_recovery_poll_seconds)
    return max(3, int(cadence) * 3)


def publish_role_heartbeat(
    client: Any,
    settings: BackendSettings,
    *,
    role: str,
    instance_id: str | None = None,
    now: datetime | None = None,
) -> None:
    normalized = _normalized_role(role)
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    payload = json.dumps(
        {
            "role": normalized,
            "updated_at_utc": timestamp.isoformat().replace("+00:00", "Z"),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    client.set(
        role_heartbeat_key(normalized, instance_id=instance_id),
        payload,
        ex=role_heartbeat_stale_after(settings, normalized),
    )


def check_role_heartbeat(
    client: Any,
    settings: BackendSettings,
    *,
    role: str,
    instance_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized = _normalized_role(role)
    try:
        raw = client.get(role_heartbeat_key(normalized, instance_id=instance_id))
        if raw is None:
            return {"role": normalized, "healthy": False, "status": "missing"}
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(str(raw))
        if payload.get("role") != normalized:
            return {"role": normalized, "healthy": False, "status": "invalid"}
        updated = datetime.fromisoformat(str(payload["updated_at_utc"]).replace("Z", "+00:00"))
        age_seconds = max(0.0, ((now or datetime.now(UTC)) - updated.astimezone(UTC)).total_seconds())
    except Exception:
        return {"role": normalized, "healthy": False, "status": "invalid"}
    healthy = age_seconds <= role_heartbeat_stale_after(settings, normalized)
    return {
        "role": normalized,
        "healthy": healthy,
        "status": "healthy" if healthy else "stale",
        "age_seconds": round(age_seconds, 3),
    }


def check_role_from_settings(settings: BackendSettings, *, role: str) -> dict[str, Any]:
    if not settings.redis_url:
        return {"role": _normalized_role(role), "healthy": False, "status": "unconfigured"}
    try:
        client = _redis_client(settings, DEFAULT_PROBE_TIMEOUT_SECONDS)
        return check_role_heartbeat(client, settings, role=role)
    except Exception:
        return {"role": _normalized_role(role), "healthy": False, "status": "unavailable"}


def check_any_role_instance_from_settings(settings: BackendSettings, *, role: str) -> dict[str, Any]:
    """Report whether any bounded, independently keyed role instance is fresh.

    Role-local container health checks intentionally use ``check_role_from_settings``
    and the current hostname. Central API metrics cannot know worker hostnames, so
    they scan the private Redis heartbeat namespace and aggregate only freshness.
    """

    normalized = _normalized_role(role)
    if not settings.redis_url:
        return {"role": normalized, "healthy": False, "status": "unconfigured"}
    try:
        client = _redis_client(settings, DEFAULT_PROBE_TIMEOUT_SECONDS)
        freshest_age: float | None = None
        instances_seen = 0
        for key in client.scan_iter(match=f"{HEARTBEAT_KEY_PREFIX}:{normalized}:*", count=100):
            if instances_seen >= 1_000:
                break
            instances_seen += 1
            try:
                raw = client.get(key)
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                payload = json.loads(str(raw))
                if payload.get("role") != normalized:
                    continue
                updated = datetime.fromisoformat(str(payload["updated_at_utc"]).replace("Z", "+00:00"))
                age = max(0.0, (datetime.now(UTC) - updated.astimezone(UTC)).total_seconds())
            except Exception:
                continue
            freshest_age = age if freshest_age is None else min(freshest_age, age)
    except Exception:
        return {"role": normalized, "healthy": False, "status": "unavailable"}
    if freshest_age is None:
        return {"role": normalized, "healthy": False, "status": "missing", "instances_seen": instances_seen}
    healthy = freshest_age <= role_heartbeat_stale_after(settings, normalized)
    return {
        "role": normalized,
        "healthy": healthy,
        "status": "healthy" if healthy else "stale",
        "age_seconds": round(freshest_age, 3),
        "instances_seen": instances_seen,
    }


class RoleHeartbeat:
    def __init__(
        self,
        client: Any,
        settings: BackendSettings,
        *,
        role: str,
        instance_id: str | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        self.role = _normalized_role(role)
        self.instance_id = instance_id
        self._stopped = Event()
        self._thread = Thread(
            target=self._run,
            name=f"{self.role}-role-heartbeat",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread.is_alive():
            self._thread.join(timeout=min(2.0, max(0.1, float(self.settings.job_heartbeat_seconds))))

    def _run(self) -> None:
        interval = max(1.0, float(self.settings.job_heartbeat_seconds))
        while not self._stopped.is_set():
            try:
                publish_role_heartbeat(
                    self.client,
                    self.settings,
                    role=self.role,
                    instance_id=self.instance_id,
                )
            except Exception:
                LOGGER.warning("role_heartbeat_publish_failed", extra={"role": self.role})
            self._stopped.wait(interval)
