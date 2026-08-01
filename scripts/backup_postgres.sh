#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?Set DATABASE_URL to the Postgres database URL before backup.}"

for command_name in pg_dump pg_restore psql sha256sum python; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required backup command is unavailable: ${command_name}" >&2
    exit 2
  fi
done

BACKUP_DIR="${BACKUP_DIR:-backups/postgres}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT="${BACKUP_DIR}/quantops_${TIMESTAMP}.dump"
TEMP_DIR=""
PG_CREDENTIAL_DIR=""

cleanup() {
  if [[ -n "${TEMP_DIR}" && -d "${TEMP_DIR}" ]]; then
    rm -rf -- "${TEMP_DIR}"
  fi
  if [[ -n "${PG_CREDENTIAL_DIR}" && -d "${PG_CREDENTIAL_DIR}" ]]; then
    rm -rf -- "${PG_CREDENTIAL_DIR}"
  fi
}
trap cleanup EXIT INT TERM

mkdir -p -- "${BACKUP_DIR}"
TEMP_DIR="$(mktemp -d "${BACKUP_DIR}/.postgres-backup.XXXXXX")"
TEMP_DUMP="${TEMP_DIR}/$(basename "${OUTPUT}")"
PG_CREDENTIAL_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tradepilot-pg-backup.XXXXXX")"

# Keep the password out of process arguments and pg-tool output.  The temporary
# pgpass file is removed by the trap above and never published with the backup.
mapfile -t PG_CONNECTION < <(PGPASSFILE_PATH="${PG_CREDENTIAL_DIR}/.pgpass" python <<'PY'
import os
import sys
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
if hostname:
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    authority = f"{quote(username, safe='')}@{rendered_host}"
    if port is not None:
        authority += f":{port}"
else:
    authority = ""
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

DATABASE_NAME="$(psql "${PG_URL}" --no-psqlrc --tuples-only --no-align --quiet --command 'SELECT current_database()')"
SCHEMA_REVISION="$(psql "${PG_URL}" --no-psqlrc --tuples-only --no-align --quiet --command 'SELECT version_num FROM alembic_version LIMIT 1')"
if [[ -z "${DATABASE_NAME}" || -z "${SCHEMA_REVISION}" ]]; then
  echo "Could not establish the database identity and Alembic revision." >&2
  exit 2
fi

pg_dump "${PG_URL}" \
  --format=custom \
  --no-owner \
  --no-acl \
  --file "${TEMP_DUMP}"

# A successful dump process is insufficient: prove that pg_restore can parse it.
pg_restore --list "${TEMP_DUMP}" >/dev/null

python - "${TEMP_DUMP}.manifest.json" "$(basename "${OUTPUT}")" "${DATABASE_NAME}" "${SCHEMA_REVISION}" "${TIMESTAMP}" <<'PY'
import json
import sys
from pathlib import Path

manifest_path, archive, database, revision, timestamp = sys.argv[1:]
Path(manifest_path).write_text(
    json.dumps(
        {
            "format": "postgresql-custom",
            "archive": archive,
            "database": database,
            "schema_revision": revision,
            "created_at_utc": timestamp,
            "checksum_algorithm": "sha256",
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY

(
  cd "${TEMP_DIR}"
  sha256sum "$(basename "${TEMP_DUMP}")" "$(basename "${TEMP_DUMP}").manifest.json" > "$(basename "${TEMP_DUMP}").sha256"
)

# Publish the archive last so a discovered .dump always has both sidecars.
mv -- "${TEMP_DUMP}.sha256" "${OUTPUT}.sha256"
mv -- "${TEMP_DUMP}.manifest.json" "${OUTPUT}.manifest.json"
mv -- "${TEMP_DUMP}" "${OUTPUT}"

echo "Postgres backup validated and written to ${OUTPUT}"
