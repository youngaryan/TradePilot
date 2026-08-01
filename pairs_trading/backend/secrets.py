from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import os
from threading import RLock
import time
from typing import Any, Callable, Protocol

from .config import BackendSettings, secret_env_value
from .dotenv import dotenv_value


class SecretResolutionError(RuntimeError):
    """Safe secret lookup failure that never includes a secret id or value."""


class SecretResolver(Protocol):
    def resolve(self, secret_ref: str) -> str | None:
        ...


class EnvironmentSecretResolver:
    """Resolve explicit ``env:NAME`` references using direct or mounted secrets."""

    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings

    def resolve(self, secret_ref: str) -> str | None:
        if not secret_ref.startswith("env:"):
            raise SecretResolutionError("Environment secret references must use env:NAME.")
        name = secret_ref.removeprefix("env:").strip()
        if not name or not name.replace("_", "A").isalnum() or not name[0].isalpha():
            raise SecretResolutionError("The env: secret reference is invalid.")
        try:
            return secret_env_value(name, allow_dotenv=not self.settings.is_production)
        except RuntimeError as exc:
            raise SecretResolutionError(f"Unable to resolve env:{name}.") from exc


@dataclass(frozen=True)
class _CacheEntry:
    value: str
    expires_at: float


class AwsSecretsManagerResolver:
    """Resolve AWS Secrets Manager references with a bounded in-process cache."""

    prefix = "secret-manager:aws:"

    def __init__(
        self,
        *,
        client: Any | None = None,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must not be negative")
        self._client = client
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = RLock()

    def _aws_client(self) -> Any:
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config

                self._client = boto3.client(
                    "secretsmanager",
                    config=Config(
                        connect_timeout=3,
                        read_timeout=5,
                        retries={"max_attempts": 2, "mode": "standard"},
                    ),
                )
            except Exception as exc:
                raise SecretResolutionError(
                    "Unable to initialize the secret-manager:aws resolver."
                ) from exc
        return self._client

    @staticmethod
    def _decode_payload(response: dict[str, Any]) -> str:
        if isinstance(response.get("SecretString"), str):
            return str(response["SecretString"])
        binary = response.get("SecretBinary")
        try:
            if isinstance(binary, str):
                raw = base64.b64decode(binary, validate=True)
            elif isinstance(binary, (bytes, bytearray)):
                raw = bytes(binary)
                try:
                    raw.decode("utf-8")
                except UnicodeDecodeError:
                    raw = base64.b64decode(raw, validate=True)
            else:
                raise ValueError("missing secret payload")
            return raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
            raise SecretResolutionError(
                "The secret-manager:aws response did not contain a supported secret payload."
            ) from exc

    @staticmethod
    def _select_json_key(value: str, json_key: str | None) -> str:
        if json_key is None:
            return value
        try:
            payload = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise SecretResolutionError(
                "The secret-manager:aws JSON-key reference did not contain a JSON object."
            ) from exc
        if not isinstance(payload, dict):
            raise SecretResolutionError(
                "The secret-manager:aws JSON-key reference did not contain a JSON object."
            )
        selected = payload.get(json_key)
        if json_key not in payload or selected is None or isinstance(selected, (dict, list)):
            raise SecretResolutionError(
                "The secret-manager:aws JSON-key reference is missing a scalar value."
            )
        if isinstance(selected, bool):
            return "true" if selected else "false"
        return str(selected)

    def resolve(self, secret_ref: str) -> str | None:
        if not secret_ref.startswith(self.prefix):
            raise SecretResolutionError(
                "AWS secret references must use secret-manager:aws:<secret-id>."
            )
        reference = secret_ref.removeprefix(self.prefix)
        secret_id, separator, json_key = reference.partition("#")
        if not secret_id.strip() or (separator and not json_key.strip()):
            raise SecretResolutionError("The secret-manager:aws reference is invalid.")
        now = self._clock()
        with self._lock:
            cached = self._cache.get(secret_ref)
            if cached is not None and cached.expires_at > now:
                return cached.value
        try:
            response = self._aws_client().get_secret_value(SecretId=secret_id)
            value = self._select_json_key(
                self._decode_payload(response), json_key if separator else None
            )
        except SecretResolutionError:
            raise
        except Exception as exc:
            raise SecretResolutionError(
                "Unable to resolve secret-manager:aws reference."
            ) from exc
        with self._lock:
            self._cache[secret_ref] = _CacheEntry(
                value=value, expires_at=now + self._ttl_seconds
            )
        return value

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()


class CompositeSecretResolver:
    def __init__(
        self,
        settings: BackendSettings,
        *,
        environment: SecretResolver | None = None,
        aws: SecretResolver | None = None,
    ) -> None:
        self.settings = settings
        self.environment = environment or EnvironmentSecretResolver(settings)
        self.aws = aws or AwsSecretsManagerResolver()

    def resolve(self, secret_ref: str) -> str | None:
        reference = str(secret_ref or "").strip()
        if not reference:
            return None
        if reference.startswith("env:"):
            return self.environment.resolve(reference)
        if reference.startswith("secret-manager:aws:"):
            return self.aws.resolve(reference)
        if reference.startswith("secret-manager:"):
            raise SecretResolutionError(
                "Secret-manager references must name a provider, for example "
                "secret-manager:aws:<secret-id>."
            )
        if self.settings.is_production:
            raise SecretResolutionError(
                "Production secret references must use env: or an explicit secret-manager provider."
            )
        # Backwards-compatible development-only lookup for legacy bare names.
        return os.getenv(reference) or dotenv_value(reference)

    def clear_cache(self) -> None:
        clear = getattr(self.aws, "clear_cache", None)
        if callable(clear):
            clear()


class SecretProvider:
    """Backwards-compatible facade over the consolidated resolver registry."""

    def __init__(self, settings: BackendSettings, *, resolver: SecretResolver | None = None) -> None:
        self.settings = settings
        self.resolver = resolver or CompositeSecretResolver(settings)

    def resolve(self, secret_ref: str) -> str | None:
        return self.resolver.resolve(secret_ref)

    def clear_cache(self) -> None:
        clear = getattr(self.resolver, "clear_cache", None)
        if callable(clear):
            clear()


__all__ = [
    "AwsSecretsManagerResolver",
    "CompositeSecretResolver",
    "EnvironmentSecretResolver",
    "SecretProvider",
    "SecretResolutionError",
    "SecretResolver",
]
