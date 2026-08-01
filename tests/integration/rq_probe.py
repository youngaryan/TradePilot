from __future__ import annotations

from typing import Any


def increment_probe(redis_url: str, key: str, token: str) -> dict[str, Any]:
    """Small importable JSON-only RQ task used by the real Redis gate."""

    from redis import Redis

    count = int(Redis.from_url(redis_url).incr(key))
    return {"token": token, "count": count}
