from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RiskConfig:
    max_gross_leverage: float = 1.5
    max_net_leverage: float = 1.0
    max_turnover: float | None = None


class RiskManager:
    def __init__(self, config: RiskConfig = RiskConfig()) -> None:
        self.config = config

    def apply(self, frame: pd.DataFrame) -> pd.DataFrame:
        adjusted = frame.copy()

        adjusted["target_position_raw"] = pd.to_numeric(
            adjusted.get("position", pd.Series(0.0, index=adjusted.index)),
            errors="coerce",
        ).fillna(0.0)
        adjusted["target_gross_return_raw"] = pd.to_numeric(
            adjusted.get("gross_return", pd.Series(0.0, index=adjusted.index)),
            errors="coerce",
        ).fillna(0.0)

        weight_columns = [column for column in adjusted.columns if column.startswith("weight_")]
        target_weight_columns = [column for column in adjusted.columns if column.startswith("target_weight_")]
        exposure_columns = target_weight_columns if target_weight_columns else weight_columns
        if exposure_columns:
            gross_proxy = adjusted[exposure_columns].abs().sum(axis=1)
            net_proxy = adjusted[exposure_columns].sum(axis=1).abs()
        else:
            signed_proxy = adjusted["target_position_raw"]
            if "signal" in adjusted.columns:
                signal = pd.to_numeric(adjusted["signal"], errors="coerce").fillna(0.0)
                unsigned_position = adjusted["target_position_raw"].abs()
                signed_proxy = np.where(signal.abs() > 0.0, signal * unsigned_position, adjusted["target_position_raw"])
                signed_proxy = pd.Series(signed_proxy, index=adjusted.index)
            gross_proxy = signed_proxy.abs()
            net_proxy = signed_proxy.abs()

        scale = pd.Series(1.0, index=adjusted.index, dtype=float)

        with np.errstate(divide="ignore", invalid="ignore"):
            gross_scale = self.config.max_gross_leverage / gross_proxy.replace(0.0, np.nan)
            net_scale = self.config.max_net_leverage / net_proxy.replace(0.0, np.nan)

        gross_scale = gross_scale.replace([np.inf, -np.inf], np.nan).fillna(1.0)
        net_scale = net_scale.replace([np.inf, -np.inf], np.nan).fillna(1.0)
        scale = np.minimum(scale, gross_scale)
        scale = np.minimum(scale, net_scale)

        if self.config.max_turnover is not None:
            turnover = pd.to_numeric(
                adjusted.get("turnover", pd.Series(0.0, index=adjusted.index)),
                errors="coerce",
            ).fillna(0.0)
            turnover_scale = self.config.max_turnover / turnover.replace(0.0, np.nan)
            turnover_scale = turnover_scale.replace([np.inf, -np.inf], np.nan).fillna(1.0)
            scale = np.minimum(scale, turnover_scale)

        scale = pd.Series(np.clip(scale, 0.0, 1.0), index=adjusted.index)
        lagged_scale = scale.shift(1).fillna(scale)

        adjusted["risk_scale"] = scale
        adjusted["risk_gross_exposure"] = gross_proxy * scale
        adjusted["risk_net_exposure"] = net_proxy * scale

        if "position" in adjusted.columns:
            adjusted["position"] = pd.to_numeric(adjusted["position"], errors="coerce").fillna(0.0) * scale
        if "gross_exposure" in adjusted.columns:
            adjusted["gross_exposure"] = pd.to_numeric(adjusted["gross_exposure"], errors="coerce").fillna(0.0) * lagged_scale
        if "short_exposure" in adjusted.columns:
            adjusted["short_exposure"] = pd.to_numeric(adjusted["short_exposure"], errors="coerce").fillna(0.0) * lagged_scale
        if "turnover" in adjusted.columns:
            adjusted["turnover"] = pd.to_numeric(adjusted["turnover"], errors="coerce").fillna(0.0) * scale
        if "cost_estimate" in adjusted.columns:
            adjusted["cost_estimate"] = pd.to_numeric(adjusted["cost_estimate"], errors="coerce").fillna(0.0) * scale
        if "gross_return" in adjusted.columns:
            adjusted["gross_return"] = pd.to_numeric(adjusted["gross_return"], errors="coerce").fillna(0.0) * lagged_scale
        for column in [*weight_columns, *target_weight_columns]:
            adjusted[column] = pd.to_numeric(adjusted[column], errors="coerce").fillna(0.0) * scale

        adjusted["risk_flag"] = (scale < 0.999).astype(int)
        return adjusted
