from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


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

_REDACTED = "[REDACTED]"


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.strip().lower()
    return normalized in _SENSITIVE_EXACT_KEYS or normalized.endswith(_SENSITIVE_KEY_SUFFIXES)


def sanitize_job_data(value: Any) -> Any:
    """Return a secret-free copy of a JSON-like job payload.

    Sensitive mapping entries are omitted rather than masked so a reconstructed
    execution request cannot accidentally treat a mask as a real credential.
    Container inputs are never modified. Tuple shape is retained for callers
    that use the helper before JSON serialization.
    """

    if isinstance(value, Mapping):
        return {
            key: sanitize_job_data(item)
            for key, item in value.items()
            if not _is_sensitive_key(key)
        }
    if isinstance(value, list):
        return [sanitize_job_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_job_data(item) for item in value)
    return value


def _collect_scalar_values(value: Any, secrets: set[str]) -> None:
    if isinstance(value, Mapping):
        for item in value.values():
            _collect_scalar_values(item, secrets)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _collect_scalar_values(item, secrets)
        return
    if isinstance(value, bytes):
        try:
            decoded = value.decode("utf-8")
        except UnicodeDecodeError:
            return
        if decoded:
            secrets.add(decoded)
        return
    if isinstance(value, str):
        if value:
            secrets.add(value)
        return
    if value is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
        secrets.add(str(value))


def collect_secret_values(value: Any) -> set[str]:
    """Collect scalar values stored beneath recognized sensitive keys."""

    secrets: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if _is_sensitive_key(key):
                    _collect_scalar_values(child, secrets)
                else:
                    visit(child)
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return secrets


def redact_secret_values(text: str, secrets: Iterable[str]) -> str:
    """Replace exact secret values in text, preferring longer values first."""

    redacted = text
    normalized = {str(secret) for secret in secrets if secret is not None and str(secret)}
    for secret in sorted(normalized, key=len, reverse=True):
        redacted = redacted.replace(secret, _REDACTED)
    return redacted


__all__ = ["collect_secret_values", "redact_secret_values", "sanitize_job_data"]
