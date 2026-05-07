#!/usr/bin/env bash
set -euo pipefail

: "${S3_BUCKET:?Set S3_BUCKET before artifact backup.}"
: "${S3_ENDPOINT_URL:?Set S3_ENDPOINT_URL before artifact backup.}"
: "${S3_ACCESS_KEY_ID:?Set S3_ACCESS_KEY_ID before artifact backup.}"
: "${S3_SECRET_ACCESS_KEY:?Set S3_SECRET_ACCESS_KEY before artifact backup.}"

BACKUP_DIR="${BACKUP_DIR:-backups/object-storage}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT="${BACKUP_DIR}/${S3_BUCKET}_${TIMESTAMP}"

mkdir -p "${OUTPUT}"

if command -v mc >/dev/null 2>&1; then
  mc alias set quantops-backup "${S3_ENDPOINT_URL}" "${S3_ACCESS_KEY_ID}" "${S3_SECRET_ACCESS_KEY}" >/dev/null
  mc mirror "quantops-backup/${S3_BUCKET}" "${OUTPUT}"
elif command -v aws >/dev/null 2>&1; then
  AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${S3_SECRET_ACCESS_KEY}" \
    aws --endpoint-url "${S3_ENDPOINT_URL}" s3 sync "s3://${S3_BUCKET}" "${OUTPUT}"
else
  echo "Install MinIO mc or AWS CLI before backing up object storage." >&2
  exit 2
fi

echo "Object storage backup written to ${OUTPUT}"
