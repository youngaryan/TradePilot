from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from ..core.framework import StrategyOutput, WalkForwardStrategy
from ..core.portfolio import PortfolioManager
from ..core.timeframes import resample_close_prices


StrategyFactory = Callable[[str], WalkForwardStrategy]


@dataclass(frozen=True)
class DirectionalPipelineConfig:
    min_history: int = 120
    symbols: tuple[str, ...] | None = None

    @classmethod
    def from_symbols(cls, symbols: Sequence[str] | None, min_history: int = 120) -> "DirectionalPipelineConfig":
        return cls(
            min_history=min_history,
            symbols=None if symbols is None else tuple(dict.fromkeys(symbols)),
        )


@dataclass(frozen=True)
class MultiTimeframeSignalConfig:
    execution_interval: str = "1h"
    confirmation_interval: str = "4h"
    fast_window: int = 6
    slow_window: int = 24
    require_confirmation: bool = True


class DirectionalStrategyPipeline(WalkForwardStrategy):
    def __init__(
        self,
        strategy_factory: StrategyFactory,
        portfolio_manager: PortfolioManager | None = None,
        config: DirectionalPipelineConfig = DirectionalPipelineConfig(),
        name: str = "directional_pipeline",
        multi_timeframe: MultiTimeframeSignalConfig | None = None,
        timeframe_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.strategy_factory = strategy_factory
        self.portfolio_manager = portfolio_manager or PortfolioManager()
        self.config = config
        self.name = name
        self.multi_timeframe = multi_timeframe
        self.timeframe_metadata = timeframe_metadata or {}

    def _flat_output(self, index: pd.Index, reason: str) -> StrategyOutput:
        frame = pd.DataFrame(index=index)
        frame["signal"] = 0.0
        frame["forecast"] = 0.0
        frame["position"] = 0.0
        frame["cost_estimate"] = 0.0
        frame["gross_return"] = 0.0
        frame["unit_return"] = 0.0
        frame["turnover"] = 0.0
        frame["short_exposure"] = 0.0
        frame["gross_exposure"] = 0.0
        return StrategyOutput(
            name=self.name,
            frame=frame,
            diagnostics={"status": reason, "selected_symbols": [], **self.timeframe_metadata},
        ).validate(extra_columns=("unit_return", "gross_return"))

    def _apply_multi_timeframe_confirmation(
        self,
        *,
        symbol: str,
        train_data: pd.DataFrame,
        test_data: pd.DataFrame,
        output: StrategyOutput,
    ) -> StrategyOutput:
        config = self.multi_timeframe
        if config is None:
            return output
        if config.fast_window >= config.slow_window:
            raise ValueError("multi-timeframe fast_window must be smaller than slow_window.")

        combined = pd.concat([train_data[[symbol]], test_data[[symbol]]], axis=0)
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        higher = resample_close_prices(combined, config.confirmation_interval)
        if higher.empty or symbol not in higher.columns:
            adjusted = output.frame.copy()
            adjusted["mtf_confirmation"] = 0.0
            adjusted["mtf_aligned"] = 0.0
            return StrategyOutput(
                name=output.name,
                frame=adjusted,
                diagnostics={
                    **output.diagnostics,
                    "multi_timeframe": {
                        "status": "missing_confirmation_data",
                        "execution_interval": config.execution_interval,
                        "confirmation_interval": config.confirmation_interval,
                    },
                },
            ).validate(extra_columns=("unit_return", "gross_return"))

        higher_prices = pd.to_numeric(higher[symbol], errors="coerce").dropna()
        fast = higher_prices.ewm(span=config.fast_window, adjust=False, min_periods=config.fast_window).mean()
        slow = higher_prices.ewm(span=config.slow_window, adjust=False, min_periods=config.slow_window).mean()
        confirmation = np.sign(fast - slow).replace({-0.0: 0.0}).fillna(0.0)
        aligned_confirmation = confirmation.reindex(output.frame.index, method="ffill").fillna(0.0)

        frame = output.frame.copy()
        original_position = pd.to_numeric(frame["position"], errors="coerce").fillna(0.0)
        position_direction = np.sign(original_position).replace({-0.0: 0.0}).fillna(0.0)
        if config.require_confirmation:
            aligned = (position_direction == aligned_confirmation) | (position_direction == 0.0)
        else:
            aligned = (position_direction == aligned_confirmation) | (aligned_confirmation == 0.0) | (position_direction == 0.0)
        adjusted_position = original_position.where(aligned, 0.0)

        unit_return = pd.to_numeric(frame.get("unit_return", 0.0), errors="coerce").fillna(0.0)
        turnover = adjusted_position.diff().abs().fillna(adjusted_position.abs())
        original_turnover = pd.to_numeric(frame.get("turnover", turnover), errors="coerce").replace(0.0, np.nan)
        original_cost = pd.to_numeric(frame.get("cost_estimate", 0.0), errors="coerce").fillna(0.0)
        inferred_cost_rate = (original_cost / original_turnover).replace([np.inf, -np.inf], np.nan).median()
        if pd.isna(inferred_cost_rate):
            inferred_cost_rate = 2.0 / 10_000.0

        frame["position"] = adjusted_position
        frame["signal"] = np.sign(adjusted_position).replace({-0.0: 0.0}).fillna(0.0)
        frame["forecast"] = pd.to_numeric(frame["forecast"], errors="coerce").fillna(0.0).where(aligned, 0.0)
        frame["turnover"] = turnover
        frame["cost_estimate"] = turnover * float(inferred_cost_rate)
        frame["gross_return"] = adjusted_position.shift(1).fillna(0.0) * unit_return
        frame["short_exposure_per_unit"] = (adjusted_position < 0.0).astype(float)
        frame["gross_exposure_per_unit"] = 1.0
        frame["mtf_confirmation"] = aligned_confirmation
        frame["mtf_aligned"] = aligned.astype(float)
        target_column = f"target_weight_{symbol}"
        if target_column in frame.columns:
            frame[target_column] = adjusted_position

        diagnostics = {
            **output.diagnostics,
            "multi_timeframe": {
                "status": "applied",
                "execution_interval": config.execution_interval,
                "confirmation_interval": config.confirmation_interval,
                "fast_window": config.fast_window,
                "slow_window": config.slow_window,
                "alignment_rate": float(aligned.mean()) if len(aligned) else 0.0,
            },
        }
        return StrategyOutput(name=output.name, frame=frame, diagnostics=diagnostics).validate(extra_columns=("unit_return", "gross_return"))

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        candidate_symbols = list(self.config.symbols or tuple(train_data.columns))
        available_symbols = [symbol for symbol in candidate_symbols if symbol in train_data.columns and symbol in test_data.columns]
        available_symbols = [
            symbol
            for symbol in available_symbols
            if train_data[symbol].dropna().shape[0] >= self.config.min_history and test_data[symbol].dropna().shape[0] > 0
        ]

        if not available_symbols:
            return self._flat_output(index=test_data.index, reason="no_available_symbols")

        outputs: dict[str, StrategyOutput] = {}
        for symbol in available_symbols:
            strategy = self.strategy_factory(symbol)
            output = strategy.run_fold(
                train_data=train_data[[symbol]],
                test_data=test_data[[symbol]],
            )
            outputs[symbol] = self._apply_multi_timeframe_confirmation(
                symbol=symbol,
                train_data=train_data[[symbol]],
                test_data=test_data[[symbol]],
                output=output,
            )

        portfolio_output = self.portfolio_manager.allocate_capital(
            strategy_outputs=outputs,
            portfolio_name=self.name,
        )
        portfolio_output.diagnostics.update(
            {
                "selected_symbols": available_symbols,
                "strategy_count": len(outputs),
                "pipeline_type": "directional",
                **self.timeframe_metadata,
            }
        )
        return portfolio_output
