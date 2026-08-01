from __future__ import annotations

import json
from pathlib import Path
import unittest

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.schemas import BacktestRunRequest
from pairs_trading.backend.services import BacktestService, PaperService
from pairs_trading.platform import SQLiteMetadataStore
from tests.common import fresh_test_dir


class BackendServiceTests(unittest.TestCase):
    def test_backtest_validation_rejects_directional_train_bars_below_warmup(self) -> None:
        workspace = fresh_test_dir("artifacts/test_backend_backtest_validation")
        service = BacktestService(
            BackendSettings(
                backtest_artifact_root=workspace / "backtests",
                metadata_db_path=workspace / "metadata.sqlite3",
                default_paper_config=workspace / "missing.json",
            )
        )
        request = BacktestRunRequest(
            pipeline="time_series_momentum",
            symbols=["SPY"],
            train_bars=252,
        )

        with self.assertRaisesRegex(ValueError, "requires at least 300 train_bars"):
            service.validate_request(request)

    def test_paper_service_builds_frontend_payload_and_latest_summary(self) -> None:
        workspace = fresh_test_dir("artifacts/test_backend_services")
        state_dir = workspace / "state"
        run_dir = workspace / "runs" / "20260424T220000Z_paper_batch"
        state_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)

        state_dir.joinpath("trend.json").write_text(
            json.dumps(
                {
                    "strategy_name": "trend",
                    "mode": "asset",
                    "initial_cash": 100000.0,
                    "cash": 15000.0,
                    "positions": {"SPY": 100.0},
                    "history": [
                        {
                            "timestamp": "2026-04-24T00:00:00",
                            "mode": "asset",
                            "equity_after": 102000.0,
                            "daily_pnl": 500.0,
                            "rebalance_cost_pnl": -4.0,
                            "net_return_since_inception": 0.02,
                            "cash_after": 15000.0,
                            "gross_exposure_notional": 87000.0,
                            "gross_exposure_ratio": 0.852941,
                            "position_count": 1,
                            "trade_count": 1,
                            "turnover_notional": 5000.0,
                            "positions": {"SPY": 100.0},
                            "target_weights": {"SPY": 0.85},
                            "metadata": {"pipeline": "etf_trend"},
                            "diagnostics": {"selected_symbols": ["SPY"]},
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        state_dir.joinpath("trend_latest_orders.json").write_text(
            json.dumps([{"instrument": "SPY", "side": "buy", "notional": 5000.0}], indent=2),
            encoding="utf-8",
        )
        summary_path = run_dir / "paper_batch_summary.json"
        summary_path.write_text(
            json.dumps({"asof_date": "2026-04-24", "run_timestamp_utc": "20260424T220000Z"}),
            encoding="utf-8",
        )

        settings = BackendSettings(
                paper_state_dir=state_dir,
                paper_artifact_root=workspace / "runs",
                metadata_db_path=workspace / "metadata.sqlite3",
                default_paper_config=workspace / "missing.json",
            )
        service = PaperService(settings)
        store = SQLiteMetadataStore(settings.metadata_db_path)
        user = store.get_user_by_email("demo@quantops.local")
        assert user is not None
        organization_id = str(store.get_default_organization_id(user_id=str(user["id"])))
        payload = service.build_dashboard_payload(organization_id=organization_id, allow_legacy=True)

        self.assertEqual(service.latest_batch_summary_path(), summary_path)
        self.assertEqual(payload["totals"]["equity"], 102000.0)
        self.assertEqual(payload["leaderboard"][0]["strategy"], "trend")
        self.assertEqual(service.get_strategy(organization_id=organization_id, strategy_name="trend", allow_legacy=True)["pipeline"], "etf_trend")
        self.assertIsNone(service.get_strategy(organization_id=organization_id, strategy_name="missing", allow_legacy=True))


if __name__ == "__main__":
    unittest.main()
