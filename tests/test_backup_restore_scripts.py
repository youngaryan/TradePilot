from __future__ import annotations

import os
from pathlib import Path
import hashlib
import json
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _bash_available() -> bool:
    # The Windows-hosted Bash available to local development may be WSL and
    # cannot execute host paths or host-created mock binaries. CI runs Linux.
    if os.name == "nt":
        return False
    bash = shutil.which("bash")
    if bash is None:
        return False
    result = subprocess.run(
        [bash, "-lc", "command -v python >/dev/null && command -v sha256sum >/dev/null"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


pytestmark = pytest.mark.skipif(not _bash_available(), reason="Bash backup toolchain is unavailable")


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _environment(bin_dir: Path, **values: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment['PATH']}"
    environment.update(values)
    return environment


def test_postgres_backup_normalizes_sqlalchemy_url_and_emits_required_sidecars(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    _write_executable(
        bin_dir / "psql",
        f"printf '%s\\n' \"$*\" >> '{calls.as_posix()}'\n"
        "if [[ \"$*\" == *current_database* ]]; then printf 'isolated_restore_test\\n'; else printf '0008_atomic_quotas_billing\\n'; fi\n",
    )
    _write_executable(
        bin_dir / "pg_dump",
        f"printf '%s\\n' \"$*\" >> '{calls.as_posix()}'\n"
        "while (($#)); do if [[ \"$1\" == --file ]]; then printf 'valid-custom-dump' > \"$2\"; exit; fi; shift; done\nexit 3\n",
    )
    _write_executable(bin_dir / "pg_restore", "[[ \"$1\" == --list && -s \"$2\" ]]\n")

    backup_dir = tmp_path / "backups"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "backup_postgres.sh")],
        cwd=ROOT,
        env=_environment(
            bin_dir,
            DATABASE_URL="postgresql+psycopg://test-user:do-not-print@db.invalid:5432/isolated_restore_test",
            BACKUP_DIR=str(backup_dir),
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    archives = list(backup_dir.glob("*.dump"))
    assert len(archives) == 1
    archive = archives[0]
    assert archive.with_suffix(archive.suffix + ".sha256").is_file()
    assert archive.with_suffix(archive.suffix + ".manifest.json").is_file()
    assert "postgresql://test-user@" in calls.read_text(encoding="utf-8")
    assert "postgresql+psycopg://" not in calls.read_text(encoding="utf-8")
    assert "do-not-print" not in calls.read_text(encoding="utf-8")
    assert "do-not-print" not in result.stdout
    assert "do-not-print" not in result.stderr


def test_postgres_restore_rejects_tampering_before_destructive_command(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    destructive_marker = tmp_path / "restore-called"
    _write_executable(bin_dir / "pg_dump", "exit 99\n")
    _write_executable(bin_dir / "psql", "printf 'isolated_restore_test\\n'\n")
    _write_executable(
        bin_dir / "pg_restore",
        f"if [[ \"${{1:-}}\" != --list ]]; then touch '{destructive_marker.as_posix()}'; fi\nexit 0\n",
    )
    archive = tmp_path / "backup.dump"
    archive.write_bytes(b"original")
    manifest = archive.with_suffix(".dump.manifest.json")
    manifest.write_text(
        '{"archive":"backup.dump","schema_revision":"0008_atomic_quotas_billing"}\n',
        encoding="utf-8",
    )
    archive.with_suffix(".dump.sha256").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
        f"{hashlib.sha256(manifest.read_bytes()).hexdigest()}  {manifest.name}\n",
        encoding="utf-8",
    )
    archive.write_bytes(b"tampered")

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "restore_postgres.sh")],
        cwd=ROOT,
        env=_environment(
            bin_dir,
            DATABASE_URL="postgresql+psycopg://test-user:do-not-print@db.invalid:5432/isolated_restore_test",
            BACKUP_FILE=str(archive),
            ALLOW_RESTORE="1",
            RESTORE_TARGET_DATABASE="isolated_restore_test",
            RESTORE_CONFIRMATION="RESTORE:isolated_restore_test",
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "checksum validation failed" in result.stderr.lower()
    assert not destructive_marker.exists()
    assert "do-not-print" not in result.stdout
    assert "do-not-print" not in result.stderr


def test_restore_scripts_require_exact_target_confirmations() -> None:
    postgres = (ROOT / "scripts" / "restore_postgres.sh").read_text(encoding="utf-8")
    objects = (ROOT / "scripts" / "restore_object_storage.sh").read_text(encoding="utf-8")
    assert 'RESTORE:${RESTORE_TARGET_DATABASE}' in postgres
    assert 'RESTORE-BUCKET:${S3_BUCKET}' in objects
    assert 'DELETE-EXTRA:${S3_BUCKET}' in objects
    assert "alembic upgrade" not in postgres


def test_object_restore_rejects_undeclared_files_before_contacting_target(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    contacted = tmp_path / "target-contacted"
    _write_executable(bin_dir / "mc", f"touch '{contacted.as_posix()}'\n")

    backup = tmp_path / "object-backup"
    objects = backup / "objects"
    objects.mkdir(parents=True)
    declared = objects / "declared.json"
    declared.write_text('{"safe":true}\n', encoding="utf-8")
    (objects / "undeclared.txt").write_text("must not upload\n", encoding="utf-8")
    inventory = {
        "algorithm": "sha256",
        "objects": [
            {
                "path": declared.name,
                "sha256": hashlib.sha256(declared.read_bytes()).hexdigest(),
                "size": declared.stat().st_size,
            }
        ],
    }
    inventory_path = backup / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    manifest_path = backup / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "bucket": "tradepilot-artifacts",
                "inventory_sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
                "object_count": 1,
            }
        ),
        encoding="utf-8",
    )
    (backup / "checksums.sha256").write_text(
        f"{hashlib.sha256(inventory_path.read_bytes()).hexdigest()}  inventory.json\n"
        f"{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}  manifest.json\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "restore_object_storage.sh")],
        cwd=ROOT,
        env=_environment(
            bin_dir,
            OBJECT_BACKUP_DIR=str(backup),
            S3_BUCKET="tradepilot-artifacts",
            S3_ENDPOINT_URL="https://storage.invalid",
            S3_ACCESS_KEY_ID="temporary-access",
            S3_SECRET_ACCESS_KEY="temporary-secret",
            ALLOW_OBJECT_RESTORE="1",
            RESTORE_CONFIRMATION="RESTORE-BUCKET:tradepilot-artifacts",
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "not declared" in result.stderr.lower()
    assert not contacted.exists()
    assert "temporary-secret" not in result.stdout
    assert "temporary-secret" not in result.stderr
