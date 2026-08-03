from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from .framework import StrategyOutput


class PortfolioManager:
    """
    Converts standardized strategy outputs into a portfolio-level result.

    The allocator expects a per-unit return series (`unit_return`) from each strategy and uses:
    - rolling unit-return volatility for risk targeting,
    - forecast magnitude for conviction,
    - estimated trading cost as a penalty term,
    - a portfolio-level leverage cap.
    """

    def __init__(
        self,
        max_leverage: float = 1.5,
        risk_per_trade: float = 0.10,
        volatility_window: int = 20,
        forecast_floor: float = 0.15,
        max_strategy_weight: float | None = None,
        max_pair_weight: float | None = None,
        allocation_method: str = "risk_weighted",
        max_active_positions: int | None = None,
    ) -> None:
        self.max_leverage = max_leverage
        self.risk_per_trade = risk_per_trade
        self.volatility_window = volatility_window
        self.forecast_floor = forecast_floor
        self.max_strategy_weight = max_strategy_weight if max_strategy_weight is not None else max_pair_weight
        if allocation_method not in {"risk_weighted", "equal_weight"}:
            raise ValueError("allocation_method must be risk_weighted or equal_weight")
        self.allocation_method = allocation_method
        if max_active_positions is not None and int(max_active_positions) < 1:
            raise ValueError("max_active_positions must be at least 1 when provided")
        self.max_active_positions = int(max_active_positions) if max_active_positions is not None else None

    def allocate_capital(
        self,
        strategy_outputs: Mapping[str, StrategyOutput],
        portfolio_name: str = "portfolio",
    ) -> StrategyOutput:
        if not strategy_outputs:
            raise ValueError("PortfolioManager needs at least one strategy output.")

        ordered_outputs = dict(strategy_outputs)
        for name, output in ordered_outputs.items():
            if output.frame.empty:
                raise ValueError(f"Strategy output '{name}' is empty.")

        portfolio_index = ordered_outputs[next(iter(ordered_outputs))].frame.index
        for output in ordered_outputs.values():
            portfolio_index = portfolio_index.union(output.frame.index)
        portfolio_index = portfolio_index.sort_values()

        weights = pd.DataFrame(index=portfolio_index)
        strategy_returns = pd.DataFrame(index=portfolio_index)
        strategy_costs = pd.DataFrame(index=portfolio_index)
        strategy_short_exposure = pd.DataFrame(index=portfolio_index)
        strategy_gross_exposure = pd.DataFrame(index=portfolio_index)
        strategy_forecasts = pd.DataFrame(index=portfolio_index)
        strategy_sentiment_strength = pd.DataFrame(index=portfolio_index)
        strategy_sentiment_confidence = pd.DataFrame(index=portfolio_index)
        strategy_symbol_targets: dict[str, pd.DataFrame] = {}

        strategy_summaries: list[dict[str, float | str]] = []

        for strategy_name, output in ordered_outputs.items():
            frame = output.frame.reindex(portfolio_index).copy()
            frame["unit_return"] = frame.get("unit_return", frame.get("spread_return", frame.get("instrument_return", 0.0)))
            for column in ("signal", "forecast", "position", "cost_estimate", "unit_return"):
                frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
            frame["short_exposure_per_unit"] = pd.to_numeric(
                frame.get("short_exposure_per_unit", pd.Series(0.5, index=frame.index)),
                errors="coerce",
            ).fillna(0.5)
            frame["gross_exposure_per_unit"] = pd.to_numeric(
                frame.get("gross_exposure_per_unit", pd.Series(1.0, index=frame.index)),
                errors="coerce",
            ).fillna(1.0)
            frame["sentiment_strength"] = pd.to_numeric(
                frame.get("sentiment_strength", pd.Series(0.0, index=frame.index)),
                errors="coerce",
            ).fillna(0.0)
            frame["sentiment_confidence"] = pd.to_numeric(
                frame.get("sentiment_confidence", pd.Series(0.0, index=frame.index)),
                errors="coerce",
            ).fillna(0.0)

            rolling_vol = frame["unit_return"].rolling(self.volatility_window, min_periods=2).std()
            expanding_vol = frame["unit_return"].expanding(min_periods=2).std()
            rolling_vol = rolling_vol.replace(0.0, np.nan).fillna(expanding_vol.replace(0.0, np.nan)).fillna(0.01)
            fallback_vol = float(rolling_vol.iloc[-1]) if not rolling_vol.empty else 0.01

            conviction = frame["forecast"].abs().clip(lower=self.forecast_floor, upper=2.0)
            cost_penalty = (1.0 / (1.0 + frame["cost_estimate"] * 10_000.0)).clip(lower=0.25, upper=1.0)
            if self.allocation_method == "equal_weight":
                gross_weight = pd.Series(
                    float(self.max_strategy_weight if self.max_strategy_weight is not None else 1.0),
                    index=frame.index,
                )
            else:
                gross_weight = (self.risk_per_trade / rolling_vol) * conviction * cost_penalty
            if self.max_strategy_weight is not None:
                gross_weight = gross_weight.clip(upper=self.max_strategy_weight)

            signed_weight = gross_weight * frame["position"]

            weights[strategy_name] = signed_weight.fillna(0.0)
            strategy_returns[strategy_name] = frame["unit_return"]
            strategy_costs[strategy_name] = frame["cost_estimate"]
            strategy_short_exposure[strategy_name] = frame["short_exposure_per_unit"]
            strategy_gross_exposure[strategy_name] = frame["gross_exposure_per_unit"]
            strategy_forecasts[strategy_name] = frame["forecast"]
            strategy_sentiment_strength[strategy_name] = frame["sentiment_strength"]
            strategy_sentiment_confidence[strategy_name] = frame["sentiment_confidence"]
            target_columns = [column for column in frame.columns if str(column).startswith("target_weight_")]
            if target_columns:
                strategy_symbol_targets[strategy_name] = frame[target_columns].apply(pd.to_numeric, errors="coerce").fillna(0.0)

            strategy_summaries.append(
                {
                    "strategy": strategy_name,
                    "mean_abs_forecast": float(frame["forecast"].abs().mean()),
                    "mean_cost_estimate": float(frame["cost_estimate"].mean()),
                    "fallback_vol": fallback_vol,
                    **output.diagnostics,
                }
            )

        if self.max_active_positions is not None and len(weights.columns) > self.max_active_positions:
            # Prefer the strongest active forecasts, with stable column order resolving ties.
            scores = weights.abs() * (1.0 + strategy_forecasts.abs())
            ranks = scores.rank(axis=1, method="first", ascending=False)
            weights = weights.where((weights == 0.0) | (ranks <= self.max_active_positions), 0.0)

        gross_leverage = weights.abs().sum(axis=1)
        scaling = pd.Series(1.0, index=portfolio_index)
        over_limit = gross_leverage > self.max_leverage
        scaling.loc[over_limit] = self.max_leverage / gross_leverage.loc[over_limit]

        scaled_weights = weights.mul(scaling, axis=0)
        lagged_weights = scaled_weights.shift(1).fillna(0.0)

        gross_return = (lagged_weights * strategy_returns).sum(axis=1)
        cost_estimate = (scaled_weights.abs() * strategy_costs).sum(axis=1)
        short_exposure = (lagged_weights.abs() * strategy_short_exposure).sum(axis=1)
        deployed_gross = (lagged_weights.abs() * strategy_gross_exposure).sum(axis=1)
        turnover = scaled_weights.diff().abs().fillna(scaled_weights.abs()).sum(axis=1)

        weight_abs_sum = scaled_weights.abs().sum(axis=1).replace(0.0, np.nan)
        forecast = (scaled_weights.abs() * strategy_forecasts.abs()).sum(axis=1) / weight_abs_sum
        forecast = forecast.fillna(0.0)
        portfolio_sentiment_strength = (scaled_weights.abs() * strategy_sentiment_strength).sum(axis=1) / weight_abs_sum
        portfolio_sentiment_confidence = (scaled_weights.abs() * strategy_sentiment_confidence).sum(axis=1) / weight_abs_sum
        portfolio_sentiment_strength = portfolio_sentiment_strength.fillna(0.0)
        portfolio_sentiment_confidence = portfolio_sentiment_confidence.fillna(0.0)

        portfolio_frame = pd.DataFrame(index=portfolio_index)
        portfolio_frame["signal"] = np.sign(scaled_weights.sum(axis=1)).fillna(0.0)
        portfolio_frame["forecast"] = forecast
        portfolio_frame["position"] = scaled_weights.abs().sum(axis=1)
        portfolio_frame["cost_estimate"] = cost_estimate
        portfolio_frame["gross_return"] = gross_return
        portfolio_frame["turnover"] = turnover
        portfolio_frame["short_exposure"] = short_exposure
        portfolio_frame["gross_exposure"] = deployed_gross
        portfolio_frame["sentiment_strength"] = portfolio_sentiment_strength
        portfolio_frame["sentiment_confidence"] = portfolio_sentiment_confidence

        for strategy_name in scaled_weights.columns:
            portfolio_frame[f"weight_{strategy_name}"] = scaled_weights[strategy_name]

        symbol_targets: dict[str, pd.Series] = {}
        for strategy_name, target_frame in strategy_symbol_targets.items():
            if strategy_name not in scaled_weights.columns:
                continue
            strategy_allocation = scaled_weights[strategy_name]
            for column in target_frame.columns:
                symbol = str(column).removeprefix("target_weight_")
                contribution = strategy_allocation * target_frame[column]
                symbol_targets[symbol] = symbol_targets.get(symbol, pd.Series(0.0, index=portfolio_index)) + contribution
        for symbol, series in sorted(symbol_targets.items()):
            portfolio_frame[f"target_weight_{symbol}"] = series.fillna(0.0)

        diagnostics = {
            "portfolio_name": portfolio_name,
            "strategy_count": len(strategy_outputs),
            "strategy_summaries": strategy_summaries,
        }

        return StrategyOutput(
            name=portfolio_name,
            frame=portfolio_frame,
            diagnostics=diagnostics,
        ).validate(extra_columns=("gross_return",))
