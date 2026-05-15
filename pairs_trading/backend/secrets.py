from __future__ import annotations

import os

from .config import BackendSettings
from .dotenv import dotenv_value


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
            return os.getenv(name) or dotenv_value(name)
        if secret_ref.startswith("secret-manager:"):
            raise NotImplementedError("Deploy a concrete secret-manager adapter before using this reference.")
        return os.getenv(secret_ref) or dotenv_value(secret_ref)
