from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import threading
from typing import Any

import pytest

from pairs_trading.platform.persistence import PostgresMetadataStore


pytestmark = pytest.mark.integration


def _iso_after(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _queued_job(job_id: str, organization_id: str, *, max_attempts: int = 3) -> dict[str, Any]:
    now = _iso_after(0)
    return {
        "id": job_id,
        "organization_id": organization_id,
        "status": "queued",
        "stage": "queued",
        "progress": 0.0,
        "request": {"pipeline": "buy_and_hold"},
        "created_at_utc": now,
        "updated_at_utc": now,
        "result": None,
        "error": None,
        "max_attempts": max_attempts,
    }


def test_real_postgres_concurrent_claim_owner_transitions_and_tenant_reads(postgres_context: Any) -> None:
    context = postgres_context
    job_id = context.job_id("claim-race")
    other_job_id = context.job_id("other-tenant")
    organization_a = context.organization_id("a")
    organization_b = context.organization_id("b")
    context.store.upsert_job(kind="backtest", payload=_queued_job(job_id, organization_a))
    context.store.upsert_job(kind="backtest", payload=_queued_job(other_job_id, organization_b))

    assert context.store.get_job(kind="backtest", job_id=job_id, organization_id=organization_a) is not None
    assert context.store.get_job(kind="backtest", job_id=job_id, organization_id=organization_b) is None
    assert {job["id"] for job in context.store.list_jobs(kind="backtest", organization_id=organization_a)} == {job_id}
    assert {job["id"] for job in context.store.list_jobs(kind="backtest", organization_id=organization_b)} == {other_job_id}

    first = PostgresMetadataStore(context.url, enable_demo_accounts=False, initialize=False)
    second = PostgresMetadataStore(context.url, enable_demo_accounts=False, initialize=False)
    barrier = threading.Barrier(2)

    def claim(store: PostgresMetadataStore, worker_id: str) -> dict[str, Any] | None:
        barrier.wait(timeout=10)
        return store.claim_job(
            kind="backtest",
            job_id=job_id,
            worker_id=worker_id,
            lease_expires_at_utc=_iso_after(120),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim, first, "worker-a"), executor.submit(claim, second, "worker-b")]
        results = [future.result(timeout=15) for future in futures]

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    winner = winners[0]
    owner = str(winner["worker_id"])
    assert winner["attempt"] == 1
    assert winner["version"] == 1

    heartbeat = first.heartbeat_job(
        kind="backtest",
        job_id=job_id,
        worker_id=owner,
        heartbeat_at_utc=_iso_after(0),
        lease_expires_at_utc=_iso_after(180),
    )
    assert heartbeat is not None
    updated = second.update_claimed_job(
        kind="backtest",
        job_id=job_id,
        worker_id=owner,
        updates={"stage": "integration", "progress": 0.5},
    )
    assert updated is not None
    released = first.release_job_claim(
        kind="backtest",
        job_id=job_id,
        worker_id=owner,
        status="completed",
        updates={"stage": "completed", "progress": 1.0, "result": {"integration": True}},
    )
    assert released is not None
    assert released["status"] == "completed"
    assert released["result"] == {"integration": True}
    assert released["worker_id"] is None


def test_real_postgres_expiry_recovery_reclaim_and_stale_owner_fencing(postgres_context: Any) -> None:
    context = postgres_context
    job_id = context.job_id("expiry")
    context.store.upsert_job(
        kind="paper",
        payload=_queued_job(job_id, context.organization_id("expiry"), max_attempts=3),
    )
    claimed = context.store.claim_job(
        kind="paper",
        job_id=job_id,
        worker_id="stale-worker",
        lease_expires_at_utc=_iso_after(120),
    )
    assert claimed is not None

    # Expire the durable lease directly so this gate remains deterministic and
    # never waits for wall-clock lease time to pass.
    expired_at = _iso_after(-60)
    with context.store._connect() as connection:
        connection.execute(
            "UPDATE jobs SET lease_expires_at_utc = ? WHERE id = ?",
            (expired_at, job_id),
        )

    assert context.store.heartbeat_job(
        kind="paper", job_id=job_id, worker_id="stale-worker", lease_expires_at_utc=_iso_after(120)
    ) is None
    assert context.store.update_claimed_job(
        kind="paper", job_id=job_id, worker_id="stale-worker", updates={"progress": 0.9}
    ) is None
    assert context.store.release_job_claim(
        kind="paper", job_id=job_id, worker_id="stale-worker", status="completed"
    ) is None

    recovered = context.store.recover_expired_jobs(now_utc=_iso_after(0), limit=10)
    assert [job["id"] for job in recovered] == [job_id]
    assert recovered[0]["status"] == "queued"

    reclaimed = context.store.claim_job(
        kind="paper",
        job_id=job_id,
        worker_id="replacement-worker",
        lease_expires_at_utc=_iso_after(120),
    )
    assert reclaimed is not None
    assert reclaimed["attempt"] == 2
    assert context.store.update_claimed_job(
        kind="paper", job_id=job_id, worker_id="stale-worker", updates={"progress": 0.95}
    ) is None
    assert context.store.release_job_claim(
        kind="paper", job_id=job_id, worker_id="stale-worker", status="completed"
    ) is None
    finalized = context.store.release_job_claim(
        kind="paper",
        job_id=job_id,
        worker_id="replacement-worker",
        status="completed",
        updates={"progress": 1.0, "stage": "completed"},
    )
    assert finalized is not None
    assert finalized["status"] == "completed"


def test_real_postgres_claim_locked_domain_publication_fences_stale_owner(postgres_context: Any) -> None:
    context = postgres_context
    job_id = context.job_id("domain-publication")
    decision_id = context.job_id("decision-publication")
    organization_id = context.organization_id("publication")
    context.store.upsert_job(kind="market_research", payload=_queued_job(job_id, organization_id))
    assert context.store.claim_job(
        kind="market_research", job_id=job_id, worker_id="old-owner",
        lease_expires_at_utc=_iso_after(120),
    ) is not None
    assert context.store.release_job_claim(
        kind="market_research", job_id=job_id, worker_id="old-owner", status="queued"
    ) is not None
    assert context.store.claim_job(
        kind="market_research", job_id=job_id, worker_id="new-owner",
        lease_expires_at_utc=_iso_after(120),
    ) is not None

    def publish(tx: Any) -> str:
        tx.upsert_committee_decision(
            payload={
                "id": decision_id, "organization_id": organization_id, "job_id": job_id,
                "ticker": "SPY", "decision": "HOLD", "timestamp": _iso_after(0),
            }
        )
        return decision_id

    stale, _ = context.store.publish_claimed_job(
        kind="market_research", job_id=job_id, worker_id="old-owner", publisher=publish
    )
    winner, result = context.store.publish_claimed_job(
        kind="market_research", job_id=job_id, worker_id="new-owner", publisher=publish
    )
    assert stale is False
    assert winner is True and result == decision_id
    decisions = context.store.list_jobs(kind="committee_decision", organization_id=organization_id)
    assert [item["id"] for item in decisions] == [decision_id]
