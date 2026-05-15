from __future__ import annotations

import os
from pathlib import Path


def _dotenv_paths() -> tuple[Path, ...]:
    configured = os.getenv("PAIRS_TRADING_DOTENV_PATH")
    if configured:
        return (Path(configured),)
    return (Path(".env.local"), Path(".env"))


def dotenv_value(name: str) -> str | None:
    for dotenv_path in _dotenv_paths():
        if not dotenv_path.is_file():
            continue
        try:
            lines = dotenv_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
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
