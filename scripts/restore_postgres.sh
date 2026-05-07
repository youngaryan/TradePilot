#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?Set DATABASE_URL to the target Postgres database URL before restore.}"
: "${BACKUP_FILE:?Set BACKUP_FILE to a pg_dump custom-format .dump file.}"

if [[ "${ALLOW_RESTORE:-}" != "1" ]]; then
  echo "Refusing to restore without ALLOW_RESTORE=1. Restores overwrite database objects." >&2
  exit 2
fi

if [[ ! -f "${BACKUP_FILE}" ]]; then
  echo "Backup file not found: ${BACKUP_FILE}" >&2
  exit 2
fi

pg_restore --clean --if-exists --no-owner --no-acl --dbname "${DATABASE_URL}" "${BACKUP_FILE}"
alembic upgrade head

echo "Postgres restore completed from ${BACKUP_FILE}"
