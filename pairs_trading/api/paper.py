from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from ..engines.backtesting import json_ready


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_float(value: Any, default: float = 0.0) -> float:
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return default
    return float(numeric)


def _latest_history_row(state: dict[str, Any]) -> dict[str, Any]:
    history = state.get("history", [])
    if not history:
        return {}
    return dict(history[-1])


def build_paper_dashboard_payload(
    *,
    state_dir: str | Path = "artifacts/paper/state",
    batch_summary_path: str | Path | None = None,
    allow_legacy: bool = False,
) -> dict[str, Any]:
    """Build a frontend-friendly read model from saved shadow paper ledgers."""

    state_path = Path(state_dir)
    batch_summary = _read_json(Path(batch_summary_path)) if batch_summary_path is not None else None

    strategies: list[dict[str, Any]] = []
    for ledger_path in sorted(state_path.glob("*.json")):
        if ledger_path.name.endswith("_latest_orders.json"):
            continue

        state = _read_json(ledger_path) or {}
        if not isinstance(state, dict):
            continue
        is_canonical = int(state.get("schema_version", 0) or 0) >= 2
        if not is_canonical and not allow_legacy:
            continue
        if is_canonical and str(state.get("ledger_key") or "") != ledger_path.stem:
            continue
        latest = _latest_history_row(state)
        orders = state.get("latest_orders") if isinstance(state.get("latest_orders"), list) else None
        if orders is None and allow_legacy:
            orders = _read_json(state_path / f"{ledger_path.stem}_latest_orders.json") or []
        orders = orders or []
        metadata = latest.get("metadata", {})

        strategies.append(
            {
                "name": state.get("strategy_name", ledger_path.stem),
                "pipeline": metadata.get("pipeline", "unknown"),
                "mode": latest.get("mode", state.get("mode", "unknown")),
                "equity": _safe_float(latest.get("equity_after"), _safe_float(state.get("initial_cash"))),
                "daily_pnl": _safe_float(latest.get("daily_pnl")),
                "rebalance_cost_pnl": _safe_float(latest.get("rebalance_cost_pnl")),
                "return_since_inception": _safe_float(latest.get("net_return_since_inception")),
                "cash": _safe_float(latest.get("cash_after"), _safe_float(state.get("cash"))),
                "gross_exposure": _safe_float(latest.get("gross_exposure_notional")),
                "gross_exposure_ratio": _safe_float(latest.get("gross_exposure_ratio")),
                "position_count": int(_safe_float(latest.get("position_count"))),
                "trade_count": int(_safe_float(latest.get("trade_count"))),
                "turnover": _safe_float(latest.get("turnover_notional")),
                "positions": latest.get("positions", state.get("positions", {})),
                "target_weights": latest.get("target_weights", {}),
                "latest_orders": orders,
                "diagnostics": latest.get("diagnostics", {}),
                "history": state.get("history", []),
            }
        )

    strategies.sort(key=lambda item: item["return_since_inception"], reverse=True)

    totals = {
        "equity": sum(item["equity"] for item in strategies),
        "daily_pnl": sum(item["daily_pnl"] for item in strategies),
        "rebalance_cost_pnl": sum(item["rebalance_cost_pnl"] for item in strategies),
        "cash": sum(item["cash"] for item in strategies),
        "gross_exposure": sum(item["gross_exposure"] for item in strategies),
        "position_count": sum(item["position_count"] for item in strategies),
        "trade_count": sum(item["trade_count"] for item in strategies),
        "turnover": sum(item["turnover"] for item in strategies),
    }
    totals["gross_exposure_ratio"] = totals["gross_exposure"] / totals["equity"] if abs(totals["equity"]) > 1e-9 else 0.0

    payload = {
        "asof_date": batch_summary.get("asof_date") if batch_summary else None,
        "run_timestamp_utc": batch_summary.get("run_timestamp_utc") if batch_summary else None,
        "totals": totals,
        "leaderboard": [
            {
                "strategy": item["name"],
                "pipeline": item["pipeline"],
                "mode": item["mode"],
                "equity": item["equity"],
                "return_since_inception": item["return_since_inception"],
                "daily_pnl": item["daily_pnl"],
                "trade_count": item["trade_count"],
                "gross_exposure_ratio": item["gross_exposure_ratio"],
            }
            for item in strategies
        ],
        "strategies": strategies,
        "visuals": batch_summary.get("visuals", {}) if batch_summary else {},
    }
    return json_ready(payload)
