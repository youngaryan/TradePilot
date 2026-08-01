from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class ReconciliationSummary:
    mean_abs_position_delta: float
    mean_risk_scale: float
    total_broker_cost: float
    total_strategy_cost: float
    total_cost: float
    total_turnover: float

    def as_dict(self) -> dict[str, float]:
        return {
            "mean_abs_position_delta": self.mean_abs_position_delta,
            "mean_risk_scale": self.mean_risk_scale,
            "total_broker_cost": self.total_broker_cost,
            "total_strategy_cost": self.total_strategy_cost,
            "total_cost": self.total_cost,
            "total_turnover": self.total_turnover,
        }


class ReconciliationEngine:
    def summarize(self, frame: pd.DataFrame) -> dict[str, Any]:
        target_position = pd.to_numeric(frame.get("target_position_raw", 0.0), errors="coerce").fillna(0.0)
        executed_position = pd.to_numeric(frame.get("position", 0.0), errors="coerce").fillna(0.0)
        risk_scale = pd.to_numeric(frame.get("risk_scale", 1.0), errors="coerce").fillna(1.0)
        broker_cost = pd.to_numeric(frame.get("broker_cost", 0.0), errors="coerce").fillna(0.0)
        strategy_cost = pd.to_numeric(frame.get("strategy_cost", 0.0), errors="coerce").fillna(0.0)
        total_cost = pd.to_numeric(frame.get("total_cost", 0.0), errors="coerce").fillna(0.0)
        turnover = pd.to_numeric(frame.get("turnover", 0.0), errors="coerce").fillna(0.0)

        summary = ReconciliationSummary(
            mean_abs_position_delta=float((target_position - executed_position).abs().mean()),
            mean_risk_scale=float(risk_scale.mean()),
            total_broker_cost=float(broker_cost.sum()),
            total_strategy_cost=float(strategy_cost.sum()),
            total_cost=float(total_cost.sum()),
            total_turnover=float(turnover.sum()),
        )
        return summary.as_dict()
