from __future__ import annotations

import os
from pathlib import Path

from .config import BackendSettings


def _dotenv_value(name: str) -> str | None:
    dotenv_path = Path(os.getenv("PAIRS_TRADING_DOTENV_PATH", ".env"))
    if not dotenv_path.exists() or not dotenv_path.is_file():
        return None
    try:
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value or None
    return None


class SecretProvider:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings

    def resolve(self, secret_ref: str) -> str | None:
        if not secret_ref:
            return None
        if self.settings.is_production and not secret_ref.startswith(("env:", "secret-manager:")):
            raise ValueError("Production secret references must use env: or secret-manager: references.")
        if secret_ref.startswith("env:"):
            name = secret_ref.removeprefix("env:")
            return os.getenv(name) or _dotenv_value(name)
        if secret_ref.startswith("secret-manager:"):
            raise NotImplementedError("Deploy a concrete secret-manager adapter before using this reference.")
        return os.getenv(secret_ref) or _dotenv_value(secret_ref)
