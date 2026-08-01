from __future__ import annotations

import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


_DIGEST = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_REQUIRED_VALUES = (
    "API_IMAGE_REPOSITORY",
    "WEB_IMAGE_REPOSITORY",
    "APP_BASE_URL",
    "CORS_ORIGINS",
    "TRUSTED_HOSTS",
    "API_HEALTH_HOST",
    "S3_ENDPOINT_URL",
    "S3_BUCKET",
    "SMTP_HOST",
    "EMAIL_FROM",
    "STRIPE_PRICE_PRO_MONTHLY",
    "STRIPE_SUCCESS_URL",
    "STRIPE_CANCEL_URL",
    "PAIRS_TRADING_MARKET_RESEARCH_LLM_PROVIDER",
    "PAIRS_TRADING_MARKET_RESEARCH_LLM_MODEL",
    "WEB_PORT",
    "APP_RELEASE",
)
_SECRET_FILES = (
    "DATABASE_URL_SECRET_FILE",
    "REDIS_URL_SECRET_FILE",
    "SESSION_SECRET_FILE",
    "CSRF_SECRET_FILE",
    "MFA_ENCRYPTION_KEY_FILE",
    "S3_ACCESS_KEY_ID_FILE",
    "S3_SECRET_ACCESS_KEY_FILE",
    "SMTP_USERNAME_FILE",
    "SMTP_PASSWORD_FILE",
    "STRIPE_SECRET_KEY_FILE",
    "STRIPE_WEBHOOK_SECRET_FILE",
    "MARKET_RESEARCH_API_KEY_FILE",
    "SENTRY_DSN_SECRET_FILE",
    "OBSERVABILITY_METRICS_TOKEN_SECRET_FILE",
)


def _https_origin(name: str, value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def validate() -> list[str]:
    errors: list[str] = []
    for name in _REQUIRED_VALUES:
        if not str(os.getenv(name) or "").strip():
            errors.append(f"{name} is required")
    for name in ("API_IMAGE_REPOSITORY", "WEB_IMAGE_REPOSITORY"):
        repository = str(os.getenv(name) or "").strip()
        if repository and ("@" in repository or repository.endswith(":latest") or any(char.isspace() for char in repository)):
            errors.append(f"{name} must be an untagged registry repository without a digest")
    for name in ("API_IMAGE_DIGEST", "WEB_IMAGE_DIGEST"):
        if not _DIGEST.fullmatch(str(os.getenv(name) or "")):
            errors.append(f"{name} must use sha256 followed by exactly 64 hexadecimal characters")
    for name in _SECRET_FILES:
        value = str(os.getenv(name) or "").strip()
        try:
            path = Path(value)
            valid = bool(value) and path.is_file() and 0 < path.stat().st_size <= 1_048_576
        except OSError:
            valid = False
        if not valid:
            errors.append(f"{name} must identify a readable, non-empty secret file")
    for name in ("APP_BASE_URL", "STRIPE_SUCCESS_URL", "STRIPE_CANCEL_URL"):
        value = str(os.getenv(name) or "").strip()
        if value and not _https_origin(name, value):
            errors.append(f"{name} must be an HTTPS URL without embedded credentials")
    trusted_hosts = tuple(item.strip() for item in str(os.getenv("TRUSTED_HOSTS") or "").split(",") if item.strip())
    health_host = str(os.getenv("API_HEALTH_HOST") or "").strip()
    if not trusted_hosts or "*" in trusted_hosts or any("://" in item or "/" in item for item in trusted_hosts):
        errors.append("TRUSTED_HOSTS must contain explicit hostnames without a universal wildcard")
    if health_host and health_host not in trusted_hosts:
        errors.append("API_HEALTH_HOST must be included exactly in TRUSTED_HOSTS")
    try:
        if not 1 <= int(str(os.getenv("WEB_PORT") or "")) <= 65_535:
            raise ValueError
    except ValueError:
        errors.append("WEB_PORT must be an integer between 1 and 65535")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"production deployment configuration error: {error}", file=sys.stderr)
        return 1
    print("Production deployment configuration validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
