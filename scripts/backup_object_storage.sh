#!/usr/bin/env bash
set -euo pipefail

: "${S3_BUCKET:?Set S3_BUCKET before artifact backup.}"
: "${S3_ENDPOINT_URL:?Set S3_ENDPOINT_URL before artifact backup.}"
: "${S3_ACCESS_KEY_ID:?Set S3_ACCESS_KEY_ID before artifact backup.}"
: "${S3_SECRET_ACCESS_KEY:?Set S3_SECRET_ACCESS_KEY before artifact backup.}"

for command_name in mc python sha256sum; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required object-backup command is unavailable: ${command_name}" >&2
    exit 2
  fi
done

python <<'PY'
import os
import re
from urllib.parse import urlsplit

bucket = os.environ["S3_BUCKET"]
endpoint = urlsplit(os.environ["S3_ENDPOINT_URL"])
if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket) or ".." in bucket:
    raise SystemExit("S3_BUCKET must be a DNS-compatible bucket name")
if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.fragment:
    raise SystemExit("S3_ENDPOINT_URL must be an HTTP(S) endpoint without embedded credentials or a fragment")
PY

BACKUP_DIR="${BACKUP_DIR:-backups/object-storage}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT="${BACKUP_DIR}/${S3_BUCKET}_${TIMESTAMP}"
TEMP_ROOT=""
MC_CONFIG_DIR=""

cleanup() {
  [[ -z "${TEMP_ROOT}" || ! -d "${TEMP_ROOT}" ]] || rm -rf -- "${TEMP_ROOT}"
  [[ -z "${MC_CONFIG_DIR}" || ! -d "${MC_CONFIG_DIR}" ]] || rm -rf -- "${MC_CONFIG_DIR}"
}
trap cleanup EXIT INT TERM

mkdir -p -- "${BACKUP_DIR}"
if [[ -e "${OUTPUT}" ]]; then
  echo "Refusing to overwrite an existing object backup: ${OUTPUT}" >&2
  exit 2
fi
TEMP_ROOT="$(mktemp -d "${BACKUP_DIR}/.object-backup.XXXXXX")"
MC_CONFIG_DIR="$(mktemp -d "${BACKUP_DIR}/.mc-config.XXXXXX")"
mkdir -p -- "${TEMP_ROOT}/objects"

mc_cmd() {
  if [[ "${S3_INSECURE:-false}" == "true" ]]; then
    mc --config-dir "${MC_CONFIG_DIR}" --insecure "$@"
  else
    mc --config-dir "${MC_CONFIG_DIR}" "$@"
  fi
}

mc_cmd alias set backup-target "${S3_ENDPOINT_URL}" "${S3_ACCESS_KEY_ID}" "${S3_SECRET_ACCESS_KEY}" >/dev/null
# Backup never deletes local data; it only overwrites objects present in the source.
mc_cmd mirror --overwrite "backup-target/${S3_BUCKET}" "${TEMP_ROOT}/objects"

python - "${TEMP_ROOT}/objects" "${TEMP_ROOT}/inventory.json" "${TEMP_ROOT}/manifest.json" "${S3_BUCKET}" "${S3_ENDPOINT_URL}" "${TIMESTAMP}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

objects_dir, inventory_path, manifest_path, bucket, endpoint, timestamp = sys.argv[1:]
root = Path(objects_dir)
entries = []
for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    entries.append({"path": path.relative_to(root).as_posix(), "sha256": digest.hexdigest(), "size": path.stat().st_size})
Path(inventory_path).write_text(json.dumps({"algorithm": "sha256", "objects": entries}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
inventory_digest = hashlib.sha256(Path(inventory_path).read_bytes()).hexdigest()
Path(manifest_path).write_text(
    json.dumps(
        {
            "bucket": bucket,
            "endpoint": endpoint,
            "created_at_utc": timestamp,
            "object_count": len(entries),
            "inventory": "inventory.json",
            "inventory_sha256": inventory_digest,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY

(
  cd "${TEMP_ROOT}"
  sha256sum inventory.json manifest.json > checksums.sha256
)

mv -- "${TEMP_ROOT}" "${OUTPUT}"
TEMP_ROOT=""
echo "Object storage backup validated and written to ${OUTPUT}"
