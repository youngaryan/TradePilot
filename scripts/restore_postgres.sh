#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?Set DATABASE_URL to the target Postgres database URL.}"
: "${BACKUP_FILE:?Set BACKUP_FILE to a pg_dump custom-format .dump file.}"
: "${RESTORE_TARGET_DATABASE:?Set RESTORE_TARGET_DATABASE to the exact target database name.}"

for command_name in pg_dump pg_restore psql sha256sum python; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required restore command is unavailable: ${command_name}" >&2
    exit 2
  fi
done

if [[ "${ALLOW_RESTORE:-}" != "1" ]]; then
  echo "Refusing to restore without ALLOW_RESTORE=1." >&2
  exit 2
fi
if [[ "${RESTORE_CONFIRMATION:-}" != "RESTORE:${RESTORE_TARGET_DATABASE}" ]]; then
  echo "Set RESTORE_CONFIRMATION=RESTORE:${RESTORE_TARGET_DATABASE} to confirm the exact target." >&2
  exit 2
fi
if [[ ! -f "${BACKUP_FILE}" || ! -f "${BACKUP_FILE}.sha256" || ! -f "${BACKUP_FILE}.manifest.json" ]]; then
  echo "The backup archive and mandatory .sha256/.manifest.json sidecars are required." >&2
  exit 2
fi

BACKUP_DIR_ABS="$(cd "$(dirname "${BACKUP_FILE}")" && pwd)"
BACKUP_BASENAME="$(basename "${BACKUP_FILE}")"
python - "${BACKUP_FILE}" "${BACKUP_FILE}.sha256" "${BACKUP_FILE}.manifest.json" <<'PY' || {
import hashlib
import sys
from pathlib import Path

archive, sidecar, manifest = map(Path, sys.argv[1:])
expected_names = {archive.name: archive, manifest.name: manifest}
found = {}
for raw_line in sidecar.read_text(encoding="utf-8").splitlines():
    parts = raw_line.split(maxsplit=1)
    if len(parts) != 2:
        raise SystemExit("invalid checksum sidecar")
    digest, name = parts
    name = name.removeprefix("*")
    if len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
        raise SystemExit("invalid checksum digest")
    if name not in expected_names or name in found:
        raise SystemExit("checksum sidecar references an unexpected file")
    found[name] = digest.lower()
if set(found) != set(expected_names):
    raise SystemExit("checksum sidecar is incomplete")
for name, path in expected_names.items():
    if hashlib.sha256(path.read_bytes()).hexdigest() != found[name]:
        raise SystemExit(f"checksum mismatch: {name}")
PY
  echo "Backup checksum validation failed." >&2
  exit 2
}
pg_restore --list "${BACKUP_FILE}" >/dev/null || {
  echo "Backup is not a readable PostgreSQL custom archive." >&2
  exit 2
}

read_manifest_field() {
  python - "${BACKUP_FILE}.manifest.json" "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle).get(sys.argv[2], "")
if not isinstance(value, str):
    raise SystemExit("invalid manifest field")
print(value)
PY
}

MANIFEST_ARCHIVE="$(read_manifest_field archive)"
EXPECTED_REVISION="$(read_manifest_field schema_revision)"
if [[ "${MANIFEST_ARCHIVE}" != "${BACKUP_BASENAME}" || -z "${EXPECTED_REVISION}" ]]; then
  echo "Backup manifest does not match the selected archive or lacks a schema revision." >&2
  exit 2
fi

REPORT_DIR="${RESTORE_REPORT_DIR:-${BACKUP_DIR_ABS}/restore-reports}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p -- "${REPORT_DIR}"
TEMP_DIR="$(mktemp -d "${REPORT_DIR}/.postgres-restore.XXXXXX")"
PG_CREDENTIAL_DIR=""
cleanup() {
  rm -rf -- "${TEMP_DIR}"
  [[ -z "${PG_CREDENTIAL_DIR}" || ! -d "${PG_CREDENTIAL_DIR}" ]] || rm -rf -- "${PG_CREDENTIAL_DIR}"
}
trap cleanup EXIT INT TERM
PG_CREDENTIAL_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tradepilot-pg-restore.XXXXXX")"

mapfile -t PG_CONNECTION < <(PGPASSFILE_PATH="${PG_CREDENTIAL_DIR}/.pgpass" python <<'PY'
import os
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

raw = os.environ["DATABASE_URL"]
parsed = urlsplit(raw)
if parsed.scheme not in {"postgres", "postgresql", "postgresql+psycopg"}:
    raise SystemExit("DATABASE_URL must be a PostgreSQL URL")
if parsed.fragment or not parsed.path.lstrip("/") or any(ord(char) < 32 for char in raw):
    raise SystemExit("DATABASE_URL is malformed")
try:
    port = parsed.port
except ValueError as exc:
    raise SystemExit("DATABASE_URL contains an invalid port") from exc
username = unquote(parsed.username or "")
password = unquote(parsed.password or "")
hostname = parsed.hostname or ""
if not username:
    raise SystemExit("DATABASE_URL must include a database user")
rendered_host = f"[{hostname}]" if ":" in hostname else hostname
authority = f"{quote(username, safe='')}@{rendered_host}" if hostname else ""
if hostname and port is not None:
    authority += f":{port}"
clean_url = urlunsplit(("postgresql", authority, parsed.path, parsed.query, ""))

def pgpass(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")

database = unquote(parsed.path.lstrip("/"))
entry = ":".join(pgpass(value) for value in (hostname or "*", str(port or "*"), database, username, password))
path = Path(os.environ["PGPASSFILE_PATH"])
path.write_text(entry + "\n", encoding="utf-8")
path.chmod(0o600)
print(clean_url)
PY
)
if [[ "${#PG_CONNECTION[@]}" -ne 1 || -z "${PG_CONNECTION[0]}" ]]; then
  echo "Could not prepare a secure PostgreSQL connection." >&2
  exit 2
fi
PG_URL="${PG_CONNECTION[0]}"
export PGPASSFILE="${PG_CREDENTIAL_DIR}/.pgpass"
export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-10}"

ACTUAL_DATABASE="$(psql "${PG_URL}" --no-psqlrc --tuples-only --no-align --quiet --command 'SELECT current_database()')"
if [[ "${ACTUAL_DATABASE}" != "${RESTORE_TARGET_DATABASE}" ]]; then
  echo "Connected database does not match RESTORE_TARGET_DATABASE; refusing destructive restore." >&2
  exit 2
fi

PRE_REVISION="$(psql "${PG_URL}" --no-psqlrc --tuples-only --no-align --quiet --command "SELECT COALESCE((SELECT version_num FROM alembic_version LIMIT 1), 'unversioned')" 2>/dev/null || printf 'unversioned')"
ROLLBACK_FILE="${REPORT_DIR}/rollback_${RESTORE_TARGET_DATABASE}_${TIMESTAMP}.dump"
pg_dump "${PG_URL}" --format=custom --no-owner --no-acl --file "${ROLLBACK_FILE}"
pg_restore --list "${ROLLBACK_FILE}" >/dev/null
(
  cd "${REPORT_DIR}"
  sha256sum "$(basename "${ROLLBACK_FILE}")" > "$(basename "${ROLLBACK_FILE}").sha256"
)

write_report() {
  local report_path="$1"
  local phase="$2"
  local revision="$3"
  python - "${report_path}" "${phase}" "${RESTORE_TARGET_DATABASE}" "${revision}" "${TIMESTAMP}" "${BACKUP_BASENAME}" "$(basename "${ROLLBACK_FILE}")" <<'PY'
import json
import sys
from pathlib import Path

path, phase, database, revision, timestamp, archive, rollback_archive = sys.argv[1:]
Path(path).write_text(
    json.dumps(
        {
            "phase": phase,
            "database": database,
            "schema_revision": revision,
            "recorded_at_utc": timestamp,
            "source_archive": archive,
            "rollback_archive": rollback_archive,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
}

write_report "${REPORT_DIR}/restore_${TIMESTAMP}.pre.json" pre "${PRE_REVISION}"

pg_restore \
  --exit-on-error \
  --single-transaction \
  --clean \
  --if-exists \
  --no-owner \
  --no-acl \
  --dbname "${PG_URL}" \
  "${BACKUP_FILE}"

# Integrity and sentinel checks happen before a restore is declared successful.
# Schema migration is deliberately a separate, reviewed operation.
POST_REVISION="$(psql "${PG_URL}" --no-psqlrc --tuples-only --no-align --quiet --command 'SELECT version_num FROM alembic_version LIMIT 1')"
SENTINELS="$(psql "${PG_URL}" --no-psqlrc --tuples-only --no-align --quiet --command "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('alembic_version', 'users')")"
if [[ "${POST_REVISION}" != "${EXPECTED_REVISION}" || "${SENTINELS}" != "2" ]]; then
  echo "Post-restore integrity checks failed. Rollback archive: ${ROLLBACK_FILE}" >&2
  exit 1
fi

write_report "${REPORT_DIR}/restore_${TIMESTAMP}.post.json" post "${POST_REVISION}"
echo "Postgres restore validated. Rollback archive retained at ${ROLLBACK_FILE}"
