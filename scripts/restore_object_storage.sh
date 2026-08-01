#!/usr/bin/env bash
set -euo pipefail

: "${OBJECT_BACKUP_DIR:?Set OBJECT_BACKUP_DIR to a completed object backup.}"
: "${S3_BUCKET:?Set S3_BUCKET to the exact target bucket.}"
: "${S3_ENDPOINT_URL:?Set S3_ENDPOINT_URL to the target endpoint.}"
: "${S3_ACCESS_KEY_ID:?Set S3_ACCESS_KEY_ID for the target.}"
: "${S3_SECRET_ACCESS_KEY:?Set S3_SECRET_ACCESS_KEY for the target.}"

for command_name in mc python sha256sum; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required object-restore command is unavailable: ${command_name}" >&2
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
if [[ "${ALLOW_OBJECT_RESTORE:-}" != "1" || "${RESTORE_CONFIRMATION:-}" != "RESTORE-BUCKET:${S3_BUCKET}" ]]; then
  echo "Set ALLOW_OBJECT_RESTORE=1 and RESTORE_CONFIRMATION=RESTORE-BUCKET:${S3_BUCKET}." >&2
  exit 2
fi
if [[ ! -d "${OBJECT_BACKUP_DIR}/objects" || ! -f "${OBJECT_BACKUP_DIR}/inventory.json" || ! -f "${OBJECT_BACKUP_DIR}/manifest.json" || ! -f "${OBJECT_BACKUP_DIR}/checksums.sha256" ]]; then
  echo "Backup objects, inventory.json, manifest.json, and checksums.sha256 are all required." >&2
  exit 2
fi

python - "${OBJECT_BACKUP_DIR}" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected = {name: root / name for name in ("inventory.json", "manifest.json")}
found = {}
for raw_line in (root / "checksums.sha256").read_text(encoding="utf-8").splitlines():
    parts = raw_line.split(maxsplit=1)
    if len(parts) != 2:
        raise SystemExit("invalid object-backup checksum sidecar")
    digest, name = parts
    name = name.removeprefix("*")
    if name not in expected or name in found or len(digest) != 64:
        raise SystemExit("unexpected object-backup checksum entry")
    found[name] = digest.lower()
if set(found) != set(expected):
    raise SystemExit("incomplete object-backup checksum sidecar")
for name, path in expected.items():
    if hashlib.sha256(path.read_bytes()).hexdigest() != found[name]:
        raise SystemExit(f"object-backup checksum mismatch: {name}")
PY

MANIFEST_BUCKET="$(python - "${OBJECT_BACKUP_DIR}/manifest.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle).get("bucket", ""))
PY
)"
if [[ "${MANIFEST_BUCKET}" != "${S3_BUCKET}" ]]; then
  echo "Backup manifest bucket does not match the exact target bucket." >&2
  exit 2
fi

# Validate the inventory checksum and every local object before any upload.
python - "${OBJECT_BACKUP_DIR}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

root = Path(sys.argv[1]).resolve()
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
inventory_path = root / "inventory.json"
if hashlib.sha256(inventory_path.read_bytes()).hexdigest() != manifest.get("inventory_sha256"):
    raise SystemExit("inventory checksum mismatch")
inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
entries = inventory.get("objects")
if not isinstance(entries, list) or manifest.get("object_count") != len(entries):
    raise SystemExit("inventory object count mismatch")
expected_paths = set()
for entry in entries:
    if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
        raise SystemExit("invalid inventory entry")
    relative = PurePosixPath(entry["path"])
    if relative.is_absolute() or ".." in relative.parts or not relative.parts or entry["path"] in expected_paths:
        raise SystemExit("unsafe inventory path")
    path = root / "objects" / Path(*relative.parts)
    if not path.is_file() or not isinstance(entry["size"], int) or entry["size"] < 0:
        raise SystemExit(f"missing or invalid object: {entry['path']}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if len(entry["sha256"]) != 64 or digest != entry["sha256"] or path.stat().st_size != entry["size"]:
        raise SystemExit(f"object validation failed: {entry['path']}")
    expected_paths.add(entry["path"])
actual_paths = {
    path.relative_to(root / "objects").as_posix()
    for path in (root / "objects").rglob("*")
    if path.is_file()
}
if actual_paths != expected_paths:
    raise SystemExit("backup contains files not declared by the inventory")
PY

MC_CONFIG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tradepilot-mc.XXXXXX")"
cleanup() { rm -rf -- "${MC_CONFIG_DIR}"; }
trap cleanup EXIT INT TERM
mc_cmd() {
  if [[ "${S3_INSECURE:-false}" == "true" ]]; then
    mc --config-dir "${MC_CONFIG_DIR}" --insecure "$@"
  else
    mc --config-dir "${MC_CONFIG_DIR}" "$@"
  fi
}
mc_cmd alias set restore-target "${S3_ENDPOINT_URL}" "${S3_ACCESS_KEY_ID}" "${S3_SECRET_ACCESS_KEY}" >/dev/null

MIRROR_ARGS=(--overwrite)
if [[ "${OBJECT_RESTORE_DELETE_EXTRA:-false}" == "true" ]]; then
  if [[ "${DELETE_EXTRA_CONFIRMATION:-}" != "DELETE-EXTRA:${S3_BUCKET}" ]]; then
    echo "Set DELETE_EXTRA_CONFIRMATION=DELETE-EXTRA:${S3_BUCKET} before deleting remote-only objects." >&2
    exit 2
  fi
  MIRROR_ARGS+=(--remove)
fi
mc_cmd mirror "${MIRROR_ARGS[@]}" "${OBJECT_BACKUP_DIR}/objects" "restore-target/${S3_BUCKET}"

# Stream every restored object back through mc and compare its content hash.
MC_CONFIG_DIR="${MC_CONFIG_DIR}" MC_INSECURE="${S3_INSECURE:-false}" python - "${OBJECT_BACKUP_DIR}/inventory.json" "${S3_BUCKET}" <<'PY'
import hashlib
import json
import os
import subprocess
import sys

inventory_path, bucket = sys.argv[1:]
inventory = json.loads(open(inventory_path, encoding="utf-8").read())
for entry in inventory.get("objects", []):
    command = ["mc", "--config-dir", os.environ["MC_CONFIG_DIR"]]
    if os.environ.get("MC_INSECURE") == "true":
        command.append("--insecure")
    command.extend(["cat", f"restore-target/{bucket}/{entry['path']}"])
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    digest = hashlib.sha256()
    assert process.stdout is not None
    for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
        digest.update(chunk)
    if process.wait() != 0 or digest.hexdigest() != entry["sha256"]:
        raise SystemExit(f"remote verification failed: {entry['path']}")
PY

echo "Object storage restore and content verification completed for bucket ${S3_BUCKET}"
