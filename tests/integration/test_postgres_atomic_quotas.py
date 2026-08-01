from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any
import threading

import pytest

from pairs_trading.platform.persistence import PostgresMetadataStore, QuotaReservationExceededError


pytestmark = pytest.mark.integration


def test_real_postgres_atomic_quota_reservation_under_concurrency(postgres_context: Any) -> None:
    context = postgres_context
    workspace = context.store.ensure_demo_workspace(
        email=f"{context.prefix}@example.test",
        organization_name="Atomic quota integration",
        organization_slug=f"{context.prefix}-quota",
        role="user",
        plan="free",
        subscription_status="active",
    )
    organization_id = workspace["organization_id"]
    stores = [
        PostgresMetadataStore(context.url, enable_demo_accounts=False, initialize=False)
        for _ in range(6)
    ]
    barrier = threading.Barrier(len(stores))

    def reserve(store: PostgresMetadataStore) -> bool:
        barrier.wait(timeout=10)
        try:
            store.reserve_usage_events(
                organization_id=organization_id,
                reservations=[{"feature": "backtest_job", "quantity": 1, "limit": 2}],
                window_start_utc="2026-08-01T00:00:00Z",
                window_end_utc="2026-08-02T00:00:00Z",
                occurred_at_utc="2026-08-01T12:00:00Z",
            )
            return True
        except QuotaReservationExceededError:
            return False

    try:
        with ThreadPoolExecutor(max_workers=len(stores)) as executor:
            outcomes = list(executor.map(reserve, stores))
        assert outcomes.count(True) == 2
        assert outcomes.count(False) == 4
        assert context.store.usage_count_window(
            organization_id=organization_id,
            feature="backtest_job",
            window_start_utc="2026-08-01T00:00:00Z",
            window_end_utc="2026-08-02T00:00:00Z",
        ) == 2
    finally:
        with context.store._connect() as connection:
            connection.execute("DELETE FROM organizations WHERE id = ?", (organization_id,))
            connection.execute("DELETE FROM users WHERE id = ?", (workspace["user_id"],))
