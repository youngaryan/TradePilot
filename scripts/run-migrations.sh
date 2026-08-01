#!/bin/sh
set -eu

if [ -n "${DATABASE_URL:-}" ] && [ -n "${DATABASE_URL_FILE:-}" ]; then
  echo "Configure only one of DATABASE_URL or DATABASE_URL_FILE." >&2
  exit 1
fi

if [ -n "${DATABASE_URL_FILE:-}" ]; then
  if [ ! -r "${DATABASE_URL_FILE}" ]; then
    echo "Unable to load DATABASE_URL from its configured secret file." >&2
    exit 1
  fi
  DATABASE_URL="$(cat -- "${DATABASE_URL_FILE}")"
  export DATABASE_URL
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is required for migrations." >&2
  exit 1
fi

exec alembic upgrade head
