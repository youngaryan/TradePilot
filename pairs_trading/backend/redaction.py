from __future__ import annotations

from typing import Any


PATH_KEYS = {
    "artifact_dir",
    "artifact_files",
    "deployment_config_path",
    "output_dir",
    "path",
    "raw_headlines_path",
    "scored_headlines_path",
    "daily_sentiment_path",
    "metadata_path",
}


def redact_paths(value: Any) -> Any:
    if isinstance(value, list):
        return [redact_paths(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key in PATH_KEYS or key.endswith("_path") or key.endswith("_dir"):
                redacted[key] = None
            else:
                redacted[key] = redact_paths(item)
        return redacted
    return value
