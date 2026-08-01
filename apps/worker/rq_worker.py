from __future__ import annotations

import json
import sys

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.job_queue import QUEUE_NAME
from pairs_trading.backend.readiness import RoleHeartbeat, check_role_from_settings
from pairs_trading.backend.observability import configure_role_observability, start_metrics_server


def healthcheck(settings: BackendSettings | None = None) -> bool:
    result = check_role_from_settings(settings or BackendSettings.from_env(), role="worker")
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return bool(result["healthy"])


def main(argv: list[str] | None = None) -> None:
    args = list(argv or [])
    settings = BackendSettings.from_env()
    if args == ["--healthcheck"]:
        if not healthcheck(settings):
            raise SystemExit(1)
        return
    if args:
        raise SystemExit("Usage: python -m apps.worker.rq_worker [--healthcheck]")
    settings.validate_external_job_runtime(role="worker")
    configure_role_observability(settings, role="worker")
    metrics_server = start_metrics_server(settings)
    try:
        from redis import Redis
        from rq import Worker
        from rq.serializers import JSONSerializer
    except ImportError as exc:  # pragma: no cover - optional deployment dependency
        raise RuntimeError("Install worker dependencies with `pip install -e .[backend]`.") from exc

    redis = Redis.from_url(settings.redis_url)
    heartbeat = RoleHeartbeat(redis, settings, role="worker")
    heartbeat.start()
    try:
        Worker([QUEUE_NAME], connection=redis, serializer=JSONSerializer).work(with_scheduler=True)
    finally:
        heartbeat.stop()
        if metrics_server is not None:
            metrics_server.stop()


if __name__ == "__main__":
    main(sys.argv[1:])
