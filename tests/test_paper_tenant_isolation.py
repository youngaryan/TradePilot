from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pytest

from pairs_trading.api.paper import build_paper_dashboard_payload
from pairs_trading.operations.paper_state import (
    PaperStateScope,
    ledger_key,
    migrate_legacy_ledger,
    resolve_scoped_artifact_root,
    resolve_scoped_state_dir,
    stable_deployment_id,
)
from pairs_trading.operations.paper_trading import (
    PaperExecutionSettings,
    PaperLedger,
    PaperSignalSnapshot,
)


def _snapshot(*, strategy: str, timestamp: str, price: float, weight: float = 0.5) -> PaperSignalSnapshot:
    return PaperSignalSnapshot(
        strategy_name=strategy,
        timestamp=pd.Timestamp(timestamp),
        mode="asset",
        target_weights={"SPY": weight},
        instrument_prices={"SPY": price},
        diagnostics={"source": "tenant-test"},
        metadata={"pipeline": "buy_and_hold"},
    )


def test_organization_and_deployment_ledgers_are_isolated() -> None:
    with TemporaryDirectory(prefix="tradepilot-paper-scope-") as temp_dir:
        root = Path(temp_dir)
        state_root = root / "state"
        artifact_root = root / "runs"
        org_a = PaperStateScope("organization-a", "deployment-one", "project-main")
        org_b = PaperStateScope("organization-b", "deployment-one", "project-main")
        dep_b = PaperStateScope("organization-a", "deployment-two", "project-main")
        scopes = [org_a, org_b, dep_b]
        prices = [100.0, 200.0, 300.0]
        weights = [0.2, 0.5, 0.8]

        for scope, price, weight in zip(scopes, prices, weights, strict=True):
            scoped_dir = resolve_scoped_state_dir(state_root, scope)
            ledger = PaperLedger(
                strategy_name="Shared Display Strategy",
                mode="asset",
                settings=PaperExecutionSettings(initial_cash=100_000),
                state_dir=scoped_dir,
                scope=scope,
            )
            ledger.apply_snapshot(
                _snapshot(
                    strategy="Shared Display Strategy",
                    timestamp="2026-08-01",
                    price=price,
                    weight=weight,
                )
            )

        state_dirs = [resolve_scoped_state_dir(state_root, scope) for scope in scopes]
        artifact_dirs = [resolve_scoped_artifact_root(artifact_root, scope) for scope in scopes]
        assert len({str(path) for path in state_dirs}) == 3
        assert len({str(path) for path in artifact_dirs}) == 3
        key = ledger_key("Shared Display Strategy")
        payloads = [json.loads((path / f"{key}.json").read_text(encoding="utf-8")) for path in state_dirs]
        assert [payload["organization_id"] for payload in payloads] == [
            "organization-a",
            "organization-b",
            "organization-a",
        ]
        assert [payload["deployment_id"] for payload in payloads] == [
            "deployment-one",
            "deployment-one",
            "deployment-two",
        ]
        assert [payload["instrument_prices"]["SPY"] for payload in payloads] == prices

        dashboard_a = build_paper_dashboard_payload(state_dir=state_dirs[0])
        dashboard_b = build_paper_dashboard_payload(state_dir=state_dirs[1])
        assert len(dashboard_a["strategies"]) == 1
        assert len(dashboard_b["strategies"]) == 1
        assert dashboard_a["strategies"][0]["gross_exposure"] != dashboard_b["strategies"][0]["gross_exposure"]


@pytest.mark.parametrize("unsafe", ["../escape", "folder/name", "folder\\name", ".", "..", "CON", "LPT1.txt"])
def test_scope_and_strategy_path_inputs_reject_traversal_and_reserved_names(unsafe: str) -> None:
    with pytest.raises(ValueError):
        PaperStateScope(unsafe, "deployment")
    with pytest.raises(ValueError):
        PaperStateScope("organization", unsafe)
    with pytest.raises(ValueError):
        ledger_key(unsafe)


def test_scoped_paths_are_hashed_and_contained() -> None:
    with TemporaryDirectory(prefix="tradepilot-paper-containment-") as temp_dir:
        root = Path(temp_dir).resolve()
        scope = PaperStateScope("Acme Capital", "Momentum Deployment", "Primary Project")
        state_dir = resolve_scoped_state_dir(root / "state", scope)
        artifact_dir = resolve_scoped_artifact_root(root / "artifacts", scope)
        assert (root / "state").resolve() in state_dir.parents
        assert (root / "artifacts").resolve() in artifact_dir.parents
        assert state_dir.name == "ledgers"
        assert artifact_dir.name == "runs"
        assert "Acme Capital" not in str(state_dir)
        assert ledger_key("My Strategy") != "My Strategy"


def test_deployment_hash_omits_secrets_without_colliding_benign_tokenizer_fields() -> None:
    first = stable_deployment_id({"pipeline": "trend", "api_key": "secret-one", "tokenizer": "alpha"})
    changed_secret = stable_deployment_id({"pipeline": "trend", "api_key": "secret-two", "tokenizer": "alpha"})
    changed_tokenizer = stable_deployment_id({"pipeline": "trend", "api_key": "secret-two", "tokenizer": "beta"})
    assert first == changed_secret
    assert first != changed_tokenizer


def test_legacy_migration_requires_owner_and_is_idempotent_without_removing_source() -> None:
    with TemporaryDirectory(prefix="tradepilot-paper-migration-") as temp_dir:
        root = Path(temp_dir)
        legacy_dir = root / "legacy"
        legacy_dir.mkdir()
        strategy = "legacy_strategy"
        source_state = legacy_dir / f"{strategy}.json"
        source_orders = legacy_dir / f"{strategy}_latest_orders.json"
        source_state.write_text(
            json.dumps(
                {
                    "strategy_name": strategy,
                    "mode": "asset",
                    "initial_cash": 100_000.0,
                    "cash": 80_000.0,
                    "positions": {"SPY": 100.0},
                    "instrument_prices": {"SPY": 200.0},
                    "history": [],
                }
            ),
            encoding="utf-8",
        )
        source_orders.write_text(json.dumps([{"instrument": "SPY", "side": "buy"}]), encoding="utf-8")
        original_state = source_state.read_bytes()
        original_orders = source_orders.read_bytes()
        scope = PaperStateScope("organization-owner", "deployment-main")
        scoped_dir = resolve_scoped_state_dir(root / "state", scope)

        assert migrate_legacy_ledger(
            legacy_state_dir=legacy_dir,
            scoped_state_dir=scoped_dir,
            scope=scope,
            strategy_name=strategy,
            owner_organization_id="organization-owner",
        )["status"] == "disabled"
        assert not scoped_dir.exists()

        with pytest.raises(ValueError, match="explicit owner"):
            migrate_legacy_ledger(
                legacy_state_dir=legacy_dir,
                scoped_state_dir=scoped_dir,
                scope=scope,
                strategy_name=strategy,
                owner_organization_id=None,
                enabled=True,
            )
        with pytest.raises(PermissionError):
            migrate_legacy_ledger(
                legacy_state_dir=legacy_dir,
                scoped_state_dir=scoped_dir,
                scope=scope,
                strategy_name=strategy,
                owner_organization_id="another-organization",
                enabled=True,
            )

        migrated = migrate_legacy_ledger(
            legacy_state_dir=legacy_dir,
            scoped_state_dir=scoped_dir,
            scope=scope,
            strategy_name=strategy,
            owner_organization_id="organization-owner",
            enabled=True,
        )
        repeated = migrate_legacy_ledger(
            legacy_state_dir=legacy_dir,
            scoped_state_dir=scoped_dir,
            scope=scope,
            strategy_name=strategy,
            owner_organization_id="organization-owner",
            enabled=True,
        )
        assert migrated["status"] == "migrated"
        assert repeated["status"] == "already_migrated"
        assert repeated["checksum_sha256"] == migrated["checksum_sha256"]
        assert source_state.read_bytes() == original_state
        assert source_orders.read_bytes() == original_orders

        target = scoped_dir / f"{ledger_key(strategy)}.json"
        canonical = json.loads(target.read_text(encoding="utf-8"))
        assert canonical["organization_id"] == scope.organization_id
        assert canonical["deployment_id"] == scope.deployment_id
        assert canonical["schema_version"] == 2
        assert canonical["latest_orders"] == [{"instrument": "SPY", "side": "buy"}]
        assert Path(migrated["source"]) == source_state.resolve()
