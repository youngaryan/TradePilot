#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?Set DATABASE_URL to the Postgres database URL before backup.}"

BACKUP_DIR="${BACKUP_DIR:-backups/postgres}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT="${BACKUP_DIR}/quantops_${TIMESTAMP}.dump"

mkdir -p "${BACKUP_DIR}"
pg_dump "${DATABASE_URL}" --format=custom --no-owner --no-acl --file "${OUTPUT}"

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "${OUTPUT}" > "${OUTPUT}.sha256"
fi

echo "Postgres backup written to ${OUTPUT}"
