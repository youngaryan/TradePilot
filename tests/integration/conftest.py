from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Iterator
from uuid import uuid4

import pytest


def _required_test_url(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        if str(os.getenv("REQUIRE_REAL_INTEGRATION") or "").lower() in {"1", "true", "yes", "on"}:
            pytest.fail(f"{name} is required for the real-infrastructure integration gate")
        pytest.skip(f"{name} is not configured; real infrastructure integration test skipped")
    return value


@dataclass
class PostgresTestContext:
    url: str
    store: Any
    prefix: str

    def job_id(self, label: str) -> str:
        return f"{self.prefix}-{label}"

    def organization_id(self, label: str) -> str:
        return f"{self.prefix}-org-{label}"


@pytest.fixture
def postgres_context() -> Iterator[PostgresTestContext]:
    url = _required_test_url("TEST_POSTGRES_URL")
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - CI installs backend extras
        pytest.fail(f"TEST_POSTGRES_URL is configured but psycopg is unavailable: {exc}")

    from pairs_trading.platform.persistence import PostgresMetadataStore

    connect_url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        with psycopg.connect(connect_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version_num FROM alembic_version")
                revisions = {str(row[0]) for row in cursor.fetchall()}
                cursor.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'jobs'
                    """
                )
                columns = {str(row[0]) for row in cursor.fetchall()}
    except Exception as exc:
        pytest.fail(f"Configured Postgres integration service is unavailable or unmigrated: {exc}")
    from pairs_trading.backend.readiness import _expected_alembic_heads

    assert revisions == _expected_alembic_heads(), "Database must be exactly at the application Alembic head"
    assert {"version", "attempt", "worker_id", "lease_expires_at_utc"}.issubset(columns)

    prefix = f"it-{uuid4().hex}"
    context = PostgresTestContext(
        url=url,
        store=PostgresMetadataStore(url, enable_demo_accounts=False, initialize=False),
        prefix=prefix,
    )
    try:
        yield context
    finally:
        with psycopg.connect(connect_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM jobs WHERE id LIKE %s", (f"{prefix}%",))


@dataclass
class RedisTestContext:
    url: str
    connection: Any
    prefix: str
    queue_jobs: list[tuple[str, str]] = field(default_factory=list)
    probe_keys: set[str] = field(default_factory=set)

    def track_job(self, queue_name: str, job_id: str) -> None:
        self.queue_jobs.append((queue_name, job_id))

    def probe_key(self, label: str) -> str:
        key = f"{self.prefix}:probe:{label}"
        self.probe_keys.add(key)
        return key


@pytest.fixture
def redis_context() -> Iterator[RedisTestContext]:
    url = _required_test_url("TEST_REDIS_URL")
    try:
        from redis import Redis
        from rq import Queue
        from rq.serializers import JSONSerializer
    except ImportError as exc:  # pragma: no cover - CI installs backend extras
        pytest.fail(f"TEST_REDIS_URL is configured but Redis/RQ dependencies are unavailable: {exc}")

    connection = Redis.from_url(url)
    try:
        connection.ping()
    except Exception as exc:
        pytest.fail(f"Configured Redis integration service is unavailable: {exc}")
    context = RedisTestContext(url=url, connection=connection, prefix=f"it:{uuid4().hex}")
    try:
        yield context
    finally:
        for queue_name, job_id in reversed(context.queue_jobs):
            queue = Queue(queue_name, connection=connection, serializer=JSONSerializer)
            try:
                job = queue.fetch_job(job_id)
                if job is not None:
                    job.delete()
                else:
                    queue.remove(job_id)
            except Exception:
                # Preserve the original test result; unique prefixes prevent a
                # failed best-effort cleanup from colliding with another test.
                pass
        if context.probe_keys:
            connection.delete(*sorted(context.probe_keys))
