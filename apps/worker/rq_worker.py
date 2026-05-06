from __future__ import annotations

import logging

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.job_queue import QUEUE_NAME


def main() -> None:
    settings = BackendSettings.from_env()
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is required to start the RQ worker.")
    try:
        from redis import Redis
        from rq import Worker
    except ImportError as exc:  # pragma: no cover - optional deployment dependency
        raise RuntimeError("Install worker dependencies with `pip install -e .[backend]`.") from exc

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    redis = Redis.from_url(settings.redis_url)
    Worker([QUEUE_NAME], connection=redis).work(with_scheduler=True)


if __name__ == "__main__":
    main()
