from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ExecutionConfig:
    commission_bps: float = 1.0
    spread_bps: float = 1.0
    slippage_bps: float = 1.0
    market_impact_bps: float = 0.5
    borrow_bps_annual: float = 50.0
    funding_bps_annual: float = 0.0
    delay_bars: int = 0
    max_participation_rate: float = 0.25
    liquidity_lookback: int = 20
    latency_slippage_factor: float = 0.35


class ExecutionEngine:
    def __init__(self, config: ExecutionConfig = ExecutionConfig()) -> None:
        self.config = config

    def apply(self, frame: pd.DataFrame, *, bars_per_year: int) -> pd.DataFrame:
        executed = frame.copy()

        gross_return = pd.to_numeric(
            executed.get("gross_return", pd.Series(0.0, index=executed.index)),
            errors="coerce",
        ).fillna(0.0)
        turnover = pd.to_numeric(
            executed.get("turnover", pd.Series(0.0, index=executed.index)),
            errors="coerce",
        ).fillna(0.0)
        short_exposure = pd.to_numeric(
            executed.get("short_exposure", pd.Series(0.0, index=executed.index)),
            errors="coerce",
        ).fillna(0.0)
        gross_exposure = pd.to_numeric(
            executed.get("gross_exposure", pd.Series(0.0, index=executed.index)),
            errors="coerce",
        ).fillna(
            pd.to_numeric(
                executed.get("position", pd.Series(0.0, index=executed.index)),
                errors="coerce",
            ).abs().fillna(0.0)
        )
        strategy_cost = pd.to_numeric(
            executed.get("cost_estimate", pd.Series(0.0, index=executed.index)),
            errors="coerce",
        ).fillna(0.0)

        if "unit_return" in executed.columns:
            unit_return = pd.to_numeric(executed["unit_return"], errors="coerce").fillna(0.0)
        else:
            position_proxy = pd.to_numeric(
                executed.get("position", pd.Series(0.0, index=executed.index)),
                errors="coerce",
            ).abs().replace(0.0, np.nan)
            unit_return = (gross_return / position_proxy).replace([np.inf, -np.inf], np.nan).fillna(0.0)

        avg_abs_move_bps = (unit_return.abs().rolling(self.config.liquidity_lookback).mean() * 10_000.0).fillna(
            unit_return.abs().mean() * 10_000.0
        )
        avg_abs_move_bps = avg_abs_move_bps.replace(0.0, np.nan).fillna(5.0)

        liquidity_proxy = turnover.rolling(self.config.liquidity_lookback).mean().replace(0.0, np.nan)
        liquidity_proxy = liquidity_proxy.fillna(max(float(turnover.mean()), 0.05))
        participation = (turnover / liquidity_proxy).clip(lower=0.0, upper=max(self.config.max_participation_rate, 1e-6))

        transaction_cost = turnover * (
            self.config.commission_bps + self.config.spread_bps + self.config.slippage_bps
        ) / 10_000.0
        impact_cost = turnover * self.config.market_impact_bps / 10_000.0 * np.sqrt(participation.clip(lower=0.0))
        latency_cost = (
            turnover
            * self.config.delay_bars
            * self.config.latency_slippage_factor
            * avg_abs_move_bps
            / 10_000.0
        )
        borrow_cost = short_exposure * (self.config.borrow_bps_annual / 10_000.0 / bars_per_year)
        funding_cost = gross_exposure * (self.config.funding_bps_annual / 10_000.0 / bars_per_year)

        broker_cost = transaction_cost + impact_cost + latency_cost + borrow_cost + funding_cost
        total_cost = broker_cost + strategy_cost

        executed["execution_cost"] = transaction_cost
        executed["impact_cost"] = impact_cost
        executed["latency_cost"] = latency_cost
        executed["borrow_cost"] = borrow_cost
        executed["funding_cost"] = funding_cost
        executed["broker_cost"] = broker_cost
        executed["strategy_cost"] = strategy_cost
        executed["total_cost"] = total_cost
        executed["net_return"] = gross_return - total_cost
        executed["equity_curve"] = (1.0 + executed["net_return"].fillna(0.0)).cumprod()
        executed["participation_rate"] = participation
        return executed
