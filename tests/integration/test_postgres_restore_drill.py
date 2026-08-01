from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

import pytest


pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def _configured_url() -> str:
    value = str(os.getenv("TEST_POSTGRES_URL") or "").strip()
    if value:
        return value
    if str(os.getenv("REQUIRE_REAL_INTEGRATION") or "").lower() in {"1", "true", "yes", "on"}:
        pytest.fail("TEST_POSTGRES_URL is required for the restore drill")
    pytest.skip("TEST_POSTGRES_URL is not configured")


def test_backup_restore_round_trip_uses_a_new_isolated_database(tmp_path: Path) -> None:
    if shutil.which("bash") is None or any(shutil.which(command) is None for command in ("pg_dump", "pg_restore", "psql")):
        if str(os.getenv("REQUIRE_REAL_INTEGRATION") or "").lower() in {"1", "true", "yes", "on"}:
            pytest.fail("PostgreSQL client tools and Bash are required for the restore drill")
        pytest.skip("PostgreSQL client tools or Bash are unavailable")

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    supplied_url = _configured_url().replace("postgresql+psycopg://", "postgresql://", 1)
    connection_parameters = conninfo_to_dict(supplied_url)
    source_database = connection_parameters.get("dbname")
    database_name = f"tradepilot_restore_{uuid4().hex[:16]}"
    assert database_name != source_database
    admin_url = make_conninfo(**{**connection_parameters, "dbname": "postgres"})
    isolated_url = make_conninfo(**{**connection_parameters, "dbname": database_name})
    sqlalchemy_isolated_url = isolated_url.replace("postgresql://", "postgresql+psycopg://", 1)

    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))

    environment = os.environ.copy()
    environment.update({"DATABASE_URL": sqlalchemy_isolated_url, "APP_ENV": "development"})
    try:
        subprocess.run(["alembic", "upgrade", "head"], cwd=ROOT, env=environment, check=True, capture_output=True, text=True)
        with psycopg.connect(isolated_url) as connection:
            connection.execute("CREATE TABLE restore_drill_sentinel (value text PRIMARY KEY)")
            connection.execute("INSERT INTO restore_drill_sentinel (value) VALUES ('before-backup')")

        backup_dir = tmp_path / "postgres"
        backup_result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "backup_postgres.sh")],
            cwd=ROOT,
            env={**environment, "BACKUP_DIR": str(backup_dir)},
            check=False,
            capture_output=True,
            text=True,
        )
        assert backup_result.returncode == 0, backup_result.stderr
        backup_file = next(backup_dir.glob("*.dump"))

        with psycopg.connect(isolated_url) as connection:
            connection.execute("DELETE FROM restore_drill_sentinel")

        restore_result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "restore_postgres.sh")],
            cwd=ROOT,
            env={
                **environment,
                "BACKUP_FILE": str(backup_file),
                "ALLOW_RESTORE": "1",
                "RESTORE_TARGET_DATABASE": database_name,
                "RESTORE_CONFIRMATION": f"RESTORE:{database_name}",
                "RESTORE_REPORT_DIR": str(tmp_path / "restore-reports"),
            },
            check=False,
            capture_output=True,
            text=True,
        )
        assert restore_result.returncode == 0, restore_result.stderr
        with psycopg.connect(isolated_url) as connection:
            value = connection.execute("SELECT value FROM restore_drill_sentinel").fetchone()
            assert value == ("before-backup",)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database_name)))
