from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..core.framework import StrategyOutput


@dataclass(frozen=True)
class RegimeOverlayConfig:
    regime_weight: float = 0.15
    forecast_weight: float = 0.20
    min_regime_confidence: float = 0.30
    recession_position_multiplier: float = 0.50
    expansion_position_multiplier: float = 1.20
    overlay_cost_bps: float = 1.0


def classify_fred_regime(fred_events: pd.DataFrame) -> pd.DataFrame:
    if fred_events.empty or "event_score" not in fred_events.columns:
        return pd.DataFrame(columns=["timestamp", "regime_label", "regime_score", "regime_confidence"])
    pivot = fred_events.pivot_table(
        index="timestamp",
        columns="event_type",
        values="event_score",
        aggfunc="last",
    ).sort_index().ffill().fillna(0.0)

    has_recession = "fred_recession_prob" in pivot.columns
    has_gdp = "fred_gdp" in pivot.columns
    has_unemployment = "fred_unemployment" in pivot.columns
    has_pmi = "fred_pmi" in pivot.columns
    has_yield_curve = "fred_yield_curve" in pivot.columns
    has_cpi = "fred_cpi" in pivot.columns
    has_fedfunds = "fred_fedfunds" in pivot.columns

    rows: list[dict[str, Any]] = []
    for dt in pivot.index:
        row = pivot.loc[dt]
        scores: list[float] = []
        recession = bool(has_recession and row.get("fred_recession_prob", 0) > 0.3)
        gdp_neg = bool(has_gdp and row.get("fred_gdp", 0) < -0.5)
        unemp_high = bool(has_unemployment and row.get("fred_unemployment", 0) < -0.5)
        pmi_low = bool(has_pmi and row.get("fred_pmi", 0) < -0.5)

        if recession or (gdp_neg and unemp_high):
            label = "recession"
            regime_score = -0.8
            confidence = 0.8
        elif pmi_low and unemp_high:
            label = "recession"
            regime_score = -0.6
            confidence = 0.6
        elif has_cpi and has_fedfunds:
            cpi_high = row.get("fred_cpi", 0) > 0.5
            rate_high = row.get("fred_fedfunds", 0) > 0.5
            if cpi_high and rate_high:
                label = "tightening"
                regime_score = -0.3
                confidence = 0.5
            elif cpi_high and not rate_high:
                label = "stagflation"
                regime_score = -0.5
                confidence = 0.4
            elif not cpi_high and not rate_high:
                label = "easing"
                regime_score = 0.4
                confidence = 0.5
            else:
                label = "neutral"
                regime_score = 0.0
                confidence = 0.3
        elif has_yield_curve:
            yc = row.get("fred_yield_curve", 0)
            if yc < -0.5:
                label = "yield_inversion"
                regime_score = -0.4
                confidence = 0.5
            else:
                label = "neutral"
                regime_score = 0.1
                confidence = 0.3
        else:
            combined = [v for v in row.values if isinstance(v, (int, float))]
            avg_score = float(np.mean(combined)) if combined else 0.0
            if avg_score > 0.3:
                label = "expansion"
                regime_score = 0.5
                confidence = 0.5
            elif avg_score < -0.3:
                label = "contraction"
                regime_score = -0.4
                confidence = 0.4
            else:
                label = "neutral"
                regime_score = 0.0
                confidence = 0.3

        scores.append(regime_score)

        rows.append({
            "timestamp": dt,
            "regime_label": label,
            "regime_score": round(float(np.mean(scores)), 4) if scores else 0.0,
            "regime_confidence": round(confidence, 4),
        })

    return pd.DataFrame(rows)


def build_regime_overlay(
    fred_events: pd.DataFrame,
    index: pd.Index,
    config: RegimeOverlayConfig,
) -> pd.DataFrame:
    overlay = pd.DataFrame(index=pd.DatetimeIndex(index))
    if fred_events.empty:
        overlay["regime_label"] = "neutral"
        overlay["regime_score"] = 0.0
        overlay["regime_confidence"] = 0.0
        return overlay

    regime = classify_fred_regime(fred_events)
    if regime.empty:
        overlay["regime_label"] = "neutral"
        overlay["regime_score"] = 0.0
        overlay["regime_confidence"] = 0.0
        return overlay

    regime = regime.set_index("timestamp")
    combined_index = regime.index.union(overlay.index).sort_values()
    regime = regime.reindex(combined_index).ffill().fillna({"regime_label": "neutral", "regime_score": 0.0, "regime_confidence": 0.0})

    overlay["regime_label"] = regime["regime_label"].reindex(overlay.index).fillna("neutral")
    overlay["regime_score"] = regime["regime_score"].reindex(overlay.index).fillna(0.0)
    overlay["regime_confidence"] = regime["regime_confidence"].reindex(overlay.index).fillna(0.0)
    return overlay


def apply_regime_overlay(
    strategy_output: StrategyOutput,
    regime_overlay: pd.DataFrame,
    config: RegimeOverlayConfig,
) -> StrategyOutput:
    frame = strategy_output.frame.copy()
    strategy_output.validate(extra_columns=("unit_return", "gross_return"))

    if regime_overlay.empty or "regime_score" not in regime_overlay.columns:
        regime_overlay = pd.DataFrame({"regime_label": "neutral", "regime_score": 0.0, "regime_confidence": 0.0}, index=frame.index)
    overlay = regime_overlay.reindex(frame.index).fillna({"regime_label": "neutral", "regime_score": 0.0, "regime_confidence": 0.0})
    base_position = frame["position"].fillna(0.0)
    base_forecast = frame["forecast"].fillna(0.0)

    regime_score = overlay["regime_score"].clip(-1.0, 1.0)
    regime_confidence = overlay["regime_confidence"].clip(0.0, 1.0)
    regime_labels = overlay["regime_label"]

    recession_mask = regime_labels.isin(["recession", "stagflation", "contraction"])

    regime_multiplier = pd.Series(1.0, index=frame.index)
    regime_multiplier[recession_mask & (regime_confidence >= config.min_regime_confidence)] = config.recession_position_multiplier
    regime_multiplier[~recession_mask & (regime_confidence >= config.min_regime_confidence)] = config.expansion_position_multiplier

    agreement = np.sign(base_position) == np.sign(regime_score)
    valid_signal = (base_position != 0.0) & (regime_score != 0.0) & (regime_confidence >= config.min_regime_confidence)
    regime_multiplier[valid_signal & agreement] *= (1.0 + abs(regime_score[valid_signal & agreement]) * config.regime_weight)
    regime_multiplier[valid_signal & ~agreement] *= (1.0 - abs(regime_score[valid_signal & ~agreement]) * config.regime_weight)
    regime_multiplier = regime_multiplier.clip(lower=0.0, upper=2.0)

    adjusted_position = base_position * regime_multiplier
    overlay_turnover = (adjusted_position - base_position).abs()
    adjusted_forecast = (base_forecast + config.forecast_weight * regime_score).clip(-2.5, 2.5)
    adjusted_signal = np.sign(adjusted_position).replace({-0.0: 0.0})
    adjusted_gross_return = adjusted_position.shift(1).fillna(0.0) * frame["unit_return"].fillna(0.0)
    adjusted_cost = frame["cost_estimate"].fillna(0.0) + overlay_turnover * (config.overlay_cost_bps / 10_000.0)

    frame["signal"] = adjusted_signal
    frame["forecast"] = adjusted_forecast
    frame["position"] = adjusted_position
    frame["gross_return"] = adjusted_gross_return
    frame["cost_estimate"] = adjusted_cost
    frame["regime_label"] = regime_labels
    frame["regime_score"] = regime_score
    frame["regime_confidence"] = regime_confidence
    frame["regime_position_multiplier"] = regime_multiplier
    frame["regime_overlay_turnover"] = overlay_turnover

    diagnostics = dict(strategy_output.diagnostics)
    diagnostics["regime_overlay"] = {
        "mean_regime_score": float(regime_score.abs().mean()),
        "mean_regime_confidence": float(regime_confidence.mean()),
        "recession_bars": int(recession_mask.sum()),
        "total_bars": int(len(frame)),
    }
    return StrategyOutput(
        name=strategy_output.name,
        frame=frame,
        diagnostics=diagnostics,
    ).validate(extra_columns=("unit_return", "gross_return"))
