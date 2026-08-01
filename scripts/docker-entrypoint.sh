#!/bin/sh
set -eu

if [ "${RUN_DB_MIGRATIONS:-true}" = "true" ]; then
  alembic upgrade head
fi

if [ -n "${PROMETHEUS_MULTIPROC_DIR:-}" ]; then
  mkdir -p "${PROMETHEUS_MULTIPROC_DIR}"
  find "${PROMETHEUS_MULTIPROC_DIR}" -type f -maxdepth 1 -delete
fi

exec gunicorn pairs_trading.backend.app:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --config /app/scripts/gunicorn_conf.py \
  --workers "${WEB_CONCURRENCY:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-180}" \
  --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}"
