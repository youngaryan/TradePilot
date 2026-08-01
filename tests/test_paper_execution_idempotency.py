from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pandas as pd

from pairs_trading.operations.paper_trading import (
    PaperDeploymentConfig,
    PaperExecutionSettings,
    PaperSignalSnapshot,
    PaperStrategySpec,
    PaperTradingService,
)


def test_durable_execution_key_is_namespaced_and_passed_to_each_ledger() -> None:
    config = PaperDeploymentConfig(
        execution=PaperExecutionSettings(),
        strategies=(PaperStrategySpec(name="trend", pipeline="etf_trend", symbols=("SPY",)),),
    )
    snapshot = PaperSignalSnapshot(
        strategy_name="trend",
        timestamp=pd.Timestamp("2026-07-31"),
        mode="asset",
        target_weights={"SPY": 1.0},
        instrument_prices={"SPY": 100.0},
        diagnostics={},
        metadata={},
    )
    ledger = Mock()
    ledger.ledger_key = "ledger-trend"
    ledger.apply_snapshot.return_value = {
        "equity_after": 100_000.0,
        "net_return_since_inception": 0.0,
        "daily_pnl": 0.0,
        "trade_count": 0,
        "gross_exposure_ratio": 0.0,
    }

    with TemporaryDirectory(prefix="paper-idempotency-") as temp_dir:
        root = Path(temp_dir)
        service = PaperTradingService(
            deployment_config=config,
            state_dir=root / "state",
            artifact_root=root / "runs",
            execution_idempotency_key="paper-run-123",
        )
        with (
            patch.object(service, "build_snapshot", return_value=snapshot),
            patch("pairs_trading.operations.paper_trading.PaperLedger", return_value=ledger),
            patch("pairs_trading.operations.paper_trading.PaperDashboardVisualizer") as visualizer,
        ):
            visualizer.return_value.create_dashboard.return_value = {}
            service.run(asof_date="2026-07-31")

    ledger.apply_snapshot.assert_called_once_with(
        snapshot,
        idempotency_key="paper-run-123:ledger-trend",
    )

