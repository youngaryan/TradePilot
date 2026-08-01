from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock


PAPER_LEDGER_SCHEMA_VERSION = 2
LEGACY_LOCAL_SCOPE = None
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_SENSITIVE_EXACT_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "token",
    "secret",
    "password",
    "credential",
}
_SENSITIVE_KEY_SUFFIXES = (
    "_api_key",
    "_access_token",
    "_secret",
    "_password",
    "_credential",
)


def _validate_identifier(value: str, *, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if len(normalized) > 256:
        raise ValueError(f"{field_name} must be 256 characters or fewer")
    if normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
        raise ValueError(f"{field_name} contains an unsafe path component")
    if any(ord(character) < 32 for character in normalized):
        raise ValueError(f"{field_name} contains control characters")
    stem = normalized.rstrip(". ").split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{field_name} uses a reserved filesystem name")
    return normalized


def _safe_key(value: str, *, field_name: str, prefix: str) -> str:
    normalized = _validate_identifier(value, field_name=field_name)
    digest = sha256(normalized.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


@dataclass(frozen=True)
class PaperStateScope:
    organization_id: str
    deployment_id: str
    project_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "organization_id",
            _validate_identifier(self.organization_id, field_name="organization_id"),
        )
        object.__setattr__(
            self,
            "deployment_id",
            _validate_identifier(self.deployment_id, field_name="deployment_id"),
        )
        if self.project_id is not None:
            object.__setattr__(
                self,
                "project_id",
                _validate_identifier(self.project_id, field_name="project_id"),
            )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "organization_id": self.organization_id,
            "deployment_id": self.deployment_id,
            "project_id": self.project_id,
        }


def ledger_key(strategy_name: str) -> str:
    return _safe_key(strategy_name, field_name="strategy_name", prefix="ledger")


def _scope_parts(scope: PaperStateScope) -> tuple[str, ...]:
    parts = (
        "organizations",
        _safe_key(scope.organization_id, field_name="organization_id", prefix="org"),
    )
    if scope.project_id is not None:
        parts += (
            "projects",
            _safe_key(scope.project_id, field_name="project_id", prefix="project"),
        )
    return parts + (
        "deployments",
        _safe_key(scope.deployment_id, field_name="deployment_id", prefix="deployment"),
    )


def _contained_path(root: str | Path, *parts: str) -> Path:
    root_path = Path(root).resolve()
    candidate = root_path.joinpath(*parts).resolve()
    if candidate != root_path and root_path not in candidate.parents:
        raise ValueError("Resolved paper path escapes its configured root")
    return candidate


def resolve_scoped_state_dir(root: str | Path, scope: PaperStateScope) -> Path:
    return _contained_path(root, *_scope_parts(scope), "ledgers")


def resolve_scoped_artifact_root(root: str | Path, scope: PaperStateScope) -> Path:
    return _contained_path(root, *_scope_parts(scope), "runs")


def _sanitized_config(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, child in value.items():
            normalized_key = str(key).strip().casefold()
            if normalized_key in _SENSITIVE_EXACT_KEYS or normalized_key.endswith(_SENSITIVE_KEY_SUFFIXES):
                continue
            sanitized[str(key)] = _sanitized_config(child)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitized_config(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def stable_deployment_id(config: Any) -> str:
    canonical = json.dumps(
        _sanitized_config(config),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return f"deployment-{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def atomic_write_json(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".tmp-{os.getpid()}-{uuid4().hex[:12]}"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def read_json(path: str | Path) -> Any:
    source = Path(path)
    if not source.exists():
        return None
    return json.loads(source.read_text(encoding="utf-8"))


def migrate_legacy_ledger(
    *,
    legacy_state_dir: str | Path,
    scoped_state_dir: str | Path,
    scope: PaperStateScope,
    strategy_name: str,
    owner_organization_id: str | None,
    enabled: bool = False,
) -> dict[str, Any]:
    """Copy one explicitly owned legacy ledger into a scoped canonical ledger.

    Migration never runs implicitly. Source files remain untouched and a checksum
    marker makes a successful copy idempotent and auditable.
    """

    if not enabled:
        return {"status": "disabled"}
    if owner_organization_id is None:
        raise ValueError("Legacy migration requires an explicit owner organization mapping")
    owner = _validate_identifier(owner_organization_id, field_name="owner_organization_id")
    if owner != scope.organization_id:
        raise PermissionError("Legacy ledger owner does not match the target organization")

    safe_legacy_name = _validate_identifier(strategy_name, field_name="strategy_name")
    source_dir = Path(legacy_state_dir).resolve()
    source_state = _contained_path(source_dir, f"{safe_legacy_name}.json")
    source_orders = _contained_path(source_dir, f"{safe_legacy_name}_latest_orders.json")
    if not source_state.exists():
        return {"status": "source_missing", "source": str(source_state)}

    target_dir = Path(scoped_state_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    key = ledger_key(strategy_name)
    target_state = _contained_path(target_dir, f"{key}.json")
    target_orders = _contained_path(target_dir, f"{key}_latest_orders.json")
    marker = _contained_path(target_dir, ".migrations", f"{key}.json")
    lock_path = _contained_path(target_dir, ".locks", f"migration-{key}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with FileLock(str(lock_path), timeout=30):
        if marker.exists():
            return {**dict(read_json(marker) or {}), "status": "already_migrated"}
        if target_state.exists():
            return {"status": "target_exists", "target": str(target_state)}

        source_bytes = source_state.read_bytes()
        orders_bytes = source_orders.read_bytes() if source_orders.exists() else b""
        checksum = sha256(source_bytes + b"\0" + orders_bytes).hexdigest()
        payload = json.loads(source_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Legacy ledger must contain a JSON object")
        legacy_strategy = str(payload.get("strategy_name") or strategy_name)
        if legacy_strategy != strategy_name:
            raise ValueError("Legacy ledger strategy name does not match the requested migration")
        orders = json.loads(orders_bytes.decode("utf-8")) if orders_bytes else []
        if not isinstance(orders, list):
            raise ValueError("Legacy latest orders must contain a JSON array")

        payload.update(
            {
                "schema_version": PAPER_LEDGER_SCHEMA_VERSION,
                "organization_id": scope.organization_id,
                "deployment_id": scope.deployment_id,
                "project_id": scope.project_id,
                "ledger_key": key,
                "revision": max(1, int(payload.get("revision", 0) or 0)),
                "latest_orders": orders,
                "applied_execution_keys": list(payload.get("applied_execution_keys", [])),
            }
        )
        atomic_write_json(target_state, payload)
        try:
            atomic_write_json(target_orders, orders)
        except OSError:
            pass
        marker_payload = {
            "status": "migrated",
            "source": str(source_state),
            "source_orders": str(source_orders) if source_orders.exists() else None,
            "target": str(target_state),
            "checksum_sha256": checksum,
            "organization_id": scope.organization_id,
            "deployment_id": scope.deployment_id,
        }
        atomic_write_json(marker, marker_payload)
        return marker_payload
