from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd

from ..core.framework import StrategyOutput, WalkForwardStrategy


@dataclass(frozen=True)
class ETFMomentumConfig:
    symbols: tuple[str, ...]
    lookbacks: tuple[int, ...] = (21, 63, 126, 252)
    lookback_weights: tuple[float, ...] = (4.0, 3.0, 2.0, 1.0)
    trend_window: int = 200
    volatility_window: int = 20
    top_n: int = 3
    rebalance_bars: int = 21
    transaction_cost_bps: float = 2.0

    @classmethod
    def from_symbols(
        cls,
        symbols: Sequence[str],
        *,
        lookbacks: Sequence[int] = (21, 63, 126, 252),
        lookback_weights: Sequence[float] = (4.0, 3.0, 2.0, 1.0),
        trend_window: int = 200,
        volatility_window: int = 20,
        top_n: int = 3,
        rebalance_bars: int = 21,
        transaction_cost_bps: float = 2.0,
    ) -> "ETFMomentumConfig":
        return cls(
            symbols=tuple(dict.fromkeys(symbols)),
            lookbacks=tuple(lookbacks),
            lookback_weights=tuple(lookback_weights),
            trend_window=trend_window,
            volatility_window=volatility_window,
            top_n=top_n,
            rebalance_bars=rebalance_bars,
            transaction_cost_bps=transaction_cost_bps,
        )


class ETFTrendMomentumPipeline(WalkForwardStrategy):
    def __init__(self, config: ETFMomentumConfig, name: str = "etf_trend_momentum") -> None:
        if len(config.lookbacks) != len(config.lookback_weights):
            raise ValueError("lookbacks and lookback_weights must have the same length.")
        self.config = config
        self.name = name

    def _flat_output(self, index: pd.Index, symbols: Sequence[str], reason: str) -> StrategyOutput:
        frame = pd.DataFrame(index=index)
        for column in ("signal", "forecast", "position", "cost_estimate", "unit_return", "gross_return", "turnover"):
            frame[column] = 0.0
        frame["short_exposure"] = 0.0
        frame["gross_exposure"] = 0.0
        for symbol in symbols:
            frame[f"weight_{symbol}"] = 0.0
        return StrategyOutput(
            name=self.name,
            frame=frame,
            diagnostics={"status": reason, "selected_symbols": []},
        ).validate(extra_columns=("unit_return", "gross_return"))

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbols = [symbol for symbol in self.config.symbols if symbol in train_data.columns and symbol in test_data.columns]
        if not symbols:
            return self._flat_output(test_data.index, self.config.symbols, reason="no_symbols")

        combined = pd.concat([train_data[symbols], test_data[symbols]], axis=0)
        combined = combined[~combined.index.duplicated(keep="last")]
        prices = combined.astype(float)
        returns = prices.pct_change().fillna(0.0)
        volatility = returns.rolling(self.config.volatility_window).std().replace(0.0, np.nan)
        volatility = volatility.fillna(volatility.mean()).replace(0.0, np.nan).fillna(0.01)
        trend_filter = prices > prices.rolling(self.config.trend_window).mean()

        score = pd.DataFrame(0.0, index=prices.index, columns=symbols)
        for lookback, weight in zip(self.config.lookbacks, self.config.lookback_weights, strict=True):
            momentum = prices.pct_change(lookback)
            score = score.add(weight * momentum.div(volatility * np.sqrt(max(lookback, 1))), fill_value=0.0)
        score = score.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        score = score.where(trend_filter.fillna(False), other=0.0)

        weights = pd.DataFrame(0.0, index=prices.index, columns=symbols)
        rebalance_dates = set(test_data.index[:: self.config.rebalance_bars])
        current_weights = pd.Series(0.0, index=symbols)

        for date in prices.index:
            if date in rebalance_dates:
                current_score = score.loc[date].dropna()
                current_score = current_score[current_score > 0.0].sort_values(ascending=False).head(self.config.top_n)
                if current_score.empty:
                    current_weights = pd.Series(0.0, index=symbols)
                else:
                    selected_vol = volatility.loc[date, current_score.index].replace(0.0, np.nan).fillna(volatility.mean().mean())
                    inverse_vol = 1.0 / selected_vol
                    normalized = inverse_vol / inverse_vol.sum()
                    current_weights = pd.Series(0.0, index=symbols)
                    current_weights.loc[current_score.index] = normalized
            weights.loc[date] = current_weights

        test_weights = weights.reindex(test_data.index).copy()
        lagged_weights = test_weights.shift(1).fillna(0.0)
        portfolio_gross_return = (lagged_weights * returns.reindex(test_data.index)).sum(axis=1)
        gross_exposure = lagged_weights.abs().sum(axis=1)
        unit_return = portfolio_gross_return.div(gross_exposure.replace(0.0, np.nan)).fillna(0.0)
        turnover = test_weights.diff().abs().fillna(test_weights.abs()).sum(axis=1)

        frame = pd.DataFrame(index=test_data.index)
        frame["signal"] = np.sign(test_weights.sum(axis=1)).fillna(0.0)
        frame["forecast"] = (
            test_weights.abs() * score.reindex(test_data.index).abs()
        ).sum(axis=1).div(test_weights.abs().sum(axis=1).replace(0.0, np.nan)).fillna(0.0).clip(0.0, 2.0)
        frame["position"] = test_weights.abs().sum(axis=1)
        frame["cost_estimate"] = turnover * (self.config.transaction_cost_bps / 10_000.0)
        frame["unit_return"] = unit_return
        frame["gross_return"] = portfolio_gross_return
        frame["turnover"] = turnover
        frame["short_exposure"] = 0.0
        frame["gross_exposure"] = gross_exposure
        for symbol in symbols:
            frame[f"weight_{symbol}"] = test_weights[symbol]

        latest_selection = [
            symbol for symbol, value in test_weights.iloc[-1].items() if abs(float(value)) > 0.0
        ] if not test_weights.empty else []

        return StrategyOutput(
            name=self.name,
            frame=frame,
            diagnostics={
                "strategy_type": "etf_trend_momentum",
                "selected_symbols": latest_selection,
                "lookbacks": list(self.config.lookbacks),
                "top_n": float(self.config.top_n),
            },
        ).validate(extra_columns=("unit_return", "gross_return"))
