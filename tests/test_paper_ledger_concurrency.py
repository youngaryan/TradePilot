from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd
import pytest

from pairs_trading.operations import paper_state
from pairs_trading.operations.paper_state import PaperStateScope, ledger_key, resolve_scoped_state_dir
from pairs_trading.operations.paper_trading import (
    PaperExecutionSettings,
    PaperLedger,
    PaperSignalSnapshot,
)


STRATEGY = "Concurrent Strategy"


def _snapshot(index: int, *, timestamp: str | None = None) -> PaperSignalSnapshot:
    return PaperSignalSnapshot(
        strategy_name=STRATEGY,
        timestamp=pd.Timestamp(timestamp or f"2026-08-{index + 1:02d}"),
        mode="asset",
        target_weights={"SPY": 0.15 + 0.05 * (index % 8)},
        instrument_prices={"SPY": 100.0 + index},
        diagnostics={"index": index},
        metadata={"pipeline": "buy_and_hold", "index": index},
    )


def _ledger(state_dir: Path, scope: PaperStateScope) -> PaperLedger:
    return PaperLedger(
        strategy_name=STRATEGY,
        mode="asset",
        settings=PaperExecutionSettings(
            initial_cash=100_000.0,
            commission_bps=0.5,
            slippage_bps=1.0,
            min_trade_notional=1.0,
            weight_tolerance=0.0,
        ),
        state_dir=state_dir,
        scope=scope,
    )


def test_concurrent_ledgers_do_not_lose_updates_and_keep_valid_json() -> None:
    with TemporaryDirectory(prefix="tradepilot-paper-concurrent-") as temp_dir:
        scope = PaperStateScope("organization-concurrent", "deployment-concurrent")
        state_dir = resolve_scoped_state_dir(Path(temp_dir) / "state", scope)

        def apply(index: int) -> dict[str, object]:
            return _ledger(state_dir, scope).apply_snapshot(
                _snapshot(index),
                idempotency_key=f"execution-{index}",
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            summaries = list(executor.map(apply, range(16)))

        assert len(summaries) == 16
        state_path = state_dir / f"{ledger_key(STRATEGY)}.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["revision"] == 16
        assert len(state["history"]) == 16
        assert len(state["applied_execution_keys"]) == 16
        assert set(state["applied_execution_keys"]) == {f"execution-{index}" for index in range(16)}
        assert isinstance(state["latest_orders"], list)
        assert state["organization_id"] == scope.organization_id
        assert state["deployment_id"] == scope.deployment_id


def test_repeated_execution_key_is_idempotent() -> None:
    with TemporaryDirectory(prefix="tradepilot-paper-idempotent-") as temp_dir:
        scope = PaperStateScope("organization-idempotent", "deployment-idempotent")
        state_dir = resolve_scoped_state_dir(Path(temp_dir) / "state", scope)
        ledger = _ledger(state_dir, scope)
        first = ledger.apply_snapshot(_snapshot(0), idempotency_key="same-execution")
        state_path = state_dir / f"{ledger_key(STRATEGY)}.json"
        before = state_path.read_bytes()
        second = _ledger(state_dir, scope).apply_snapshot(
            _snapshot(7, timestamp="2026-08-01"),
            idempotency_key="same-execution",
        )
        after = state_path.read_bytes()

        assert second == first
        assert after == before
        state = json.loads(after.decode("utf-8"))
        assert state["revision"] == 1
        assert len(state["history"]) == 1


def test_replace_failure_preserves_previous_canonical_state() -> None:
    with TemporaryDirectory(prefix="tradepilot-paper-replace-failure-") as temp_dir:
        scope = PaperStateScope("organization-atomic", "deployment-atomic")
        state_dir = resolve_scoped_state_dir(Path(temp_dir) / "state", scope)
        ledger = _ledger(state_dir, scope)
        ledger.apply_snapshot(_snapshot(0), idempotency_key="first")
        state_path = state_dir / f"{ledger_key(STRATEGY)}.json"
        before = state_path.read_bytes()

        with patch.object(paper_state.os, "replace", side_effect=OSError("simulated replace failure")):
            with pytest.raises(OSError, match="simulated replace failure"):
                ledger.apply_snapshot(_snapshot(1), idempotency_key="second")

        assert state_path.read_bytes() == before
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["revision"] == 1
        assert state["applied_execution_keys"] == ["first"]
        assert not list(state_dir.glob(".*.tmp"))


def test_orders_projection_failure_leaves_canonical_revision_valid() -> None:
    with TemporaryDirectory(prefix="tradepilot-paper-orders-projection-") as temp_dir:
        scope = PaperStateScope("organization-orders", "deployment-orders")
        state_dir = resolve_scoped_state_dir(Path(temp_dir) / "state", scope)
        ledger = _ledger(state_dir, scope)
        original_atomic_write = paper_state.atomic_write_json

        def fail_projection(path: str | Path, payload: object) -> None:
            if str(path).endswith("_latest_orders.json"):
                raise OSError("projection unavailable")
            original_atomic_write(path, payload)

        with patch("pairs_trading.operations.paper_trading.atomic_write_json", side_effect=fail_projection):
            summary = ledger.apply_snapshot(_snapshot(0), idempotency_key="projection-failure")

        assert summary["trade_count"] > 0
        state_path = state_dir / f"{ledger_key(STRATEGY)}.json"
        projection_path = state_dir / f"{ledger_key(STRATEGY)}_latest_orders.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["revision"] == 1
        assert state["latest_orders"]
        assert not projection_path.exists()
