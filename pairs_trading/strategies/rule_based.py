from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..core.framework import StrategyOutput, WalkForwardStrategy
from .directional import _build_standard_output, _compute_rsi, _extract_price_series, _price_analysis_frame


@dataclass(frozen=True)
class RuleBasedStrategyConfig:
    name: str
    entry_rules: tuple[dict[str, Any], ...]
    exit_rules: tuple[dict[str, Any], ...]
    side: str = "long_only"
    max_position: float = 1.0
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    transaction_cost_bps: float = 2.0
    entry_logic: str = "all"
    exit_logic: str = "any"


def _rule_parameters(rule: dict[str, Any]) -> dict[str, Any]:
    params = rule.get("parameters")
    return params if isinstance(params, dict) else {}


def _as_int(params: dict[str, Any], key: str, default: int) -> int:
    value = params.get(key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(parsed, 1)


def _as_float(params: dict[str, Any], key: str, default: float) -> float:
    value = params.get(key, default)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return parsed


def _indicator_column(analysis: pd.DataFrame, name: str, series: pd.Series) -> pd.Series:
    if name not in analysis:
        analysis[name] = series
    return analysis[name]


def _condition_signal(analysis: pd.DataFrame, prices: pd.Series, rule: dict[str, Any]) -> pd.Series:
    kind = str(rule.get("kind") or "").lower()
    params = _rule_parameters(rule)
    if kind in {"price_above_sma", "price_below_sma"}:
        window = _as_int(params, "window", 50)
        sma = _indicator_column(analysis, f"sma_{window}", prices.rolling(window).mean())
        return (prices > sma).fillna(False) if kind.endswith("above_sma") else (prices < sma).fillna(False)

    if kind in {"price_above_ema", "price_below_ema"}:
        window = _as_int(params, "window", 50)
        ema = _indicator_column(analysis, f"ema_{window}", prices.ewm(span=window, adjust=False, min_periods=window).mean())
        return (prices > ema).fillna(False) if kind.endswith("above_ema") else (prices < ema).fillna(False)

    if kind in {"sma_cross_above", "sma_cross_below", "ema_cross_above", "ema_cross_below"}:
        fast_window = _as_int(params, "fast_window", 50)
        slow_window = _as_int(params, "slow_window", 200)
        if fast_window >= slow_window:
            return pd.Series(False, index=prices.index)
        if kind.startswith("ema"):
            fast = _indicator_column(analysis, f"ema_{fast_window}", prices.ewm(span=fast_window, adjust=False, min_periods=fast_window).mean())
            slow = _indicator_column(analysis, f"ema_{slow_window}", prices.ewm(span=slow_window, adjust=False, min_periods=slow_window).mean())
        else:
            fast = _indicator_column(analysis, f"sma_{fast_window}", prices.rolling(fast_window).mean())
            slow = _indicator_column(analysis, f"sma_{slow_window}", prices.rolling(slow_window).mean())
        current = fast > slow
        prior = fast.shift(1) <= slow.shift(1)
        crossed_above = (current & prior).fillna(False)
        crossed_below = ((fast < slow) & (fast.shift(1) >= slow.shift(1))).fillna(False)
        return crossed_above if kind.endswith("above") else crossed_below

    if kind in {"rsi_below", "rsi_above"}:
        window = _as_int(params, "window", 14)
        threshold = _as_float(params, "threshold", 30.0 if kind.endswith("below") else 70.0)
        rsi = _indicator_column(analysis, f"rsi_{window}", _compute_rsi(prices, window))
        return (rsi <= threshold).fillna(False) if kind.endswith("below") else (rsi >= threshold).fillna(False)

    if kind in {"macd_above_signal", "macd_below_signal"}:
        fast_window = _as_int(params, "fast_window", 12)
        slow_window = _as_int(params, "slow_window", 26)
        signal_window = _as_int(params, "signal_window", 9)
        if fast_window >= slow_window:
            return pd.Series(False, index=prices.index)
        fast = prices.ewm(span=fast_window, adjust=False, min_periods=fast_window).mean()
        slow = prices.ewm(span=slow_window, adjust=False, min_periods=slow_window).mean()
        macd = _indicator_column(analysis, "macd", fast - slow)
        signal = _indicator_column(analysis, "macd_signal", macd.ewm(span=signal_window, adjust=False, min_periods=signal_window).mean())
        return (macd > signal).fillna(False) if kind.endswith("above_signal") else (macd < signal).fillna(False)

    return pd.Series(False, index=prices.index)


def _combine_rules(analysis: pd.DataFrame, prices: pd.Series, rules: tuple[dict[str, Any], ...], *, mode: str) -> pd.Series:
    if not rules:
        return pd.Series(False, index=prices.index)
    signals = [_condition_signal(analysis, prices, rule) for rule in rules]
    frame = pd.concat(signals, axis=1).fillna(False).astype(bool)
    return frame.any(axis=1) if mode == "any" else frame.all(axis=1)


class RuleBasedDirectionalStrategy(WalkForwardStrategy):
    """Safe strategy interpreter for user-approved rule specs.

    It accepts only validated condition dictionaries and emits the same
    StrategyOutput shape as the built-in directional strategies.
    """

    def __init__(self, symbol: str, config: RuleBasedStrategyConfig) -> None:
        if config.max_position <= 0:
            raise ValueError("max_position must be positive.")
        if config.side not in {"long_only", "long_short", "short_only"}:
            raise ValueError("side must be long_only, long_short, or short_only.")
        self.symbol = symbol
        self.config = config

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        entry = _combine_rules(analysis, prices, self.config.entry_rules, mode=self.config.entry_logic)
        exit_ = _combine_rules(analysis, prices, self.config.exit_rules, mode=self.config.exit_logic)

        positions = np.zeros(len(analysis), dtype=float)
        current = 0.0
        entry_price: float | None = None
        max_position = float(self.config.max_position)
        closes = prices.to_numpy(dtype=float)
        risk_exits = np.zeros(len(analysis), dtype=bool)
        for index, (should_enter, should_exit) in enumerate(zip(entry.to_numpy(dtype=bool), exit_.to_numpy(dtype=bool), strict=True)):
            current_price = closes[index]
            if current != 0.0 and np.isfinite(current_price):
                # Targets execute at the following close. Anchor risk controls to
                # that delayed fill bar, not to the already-observed signal close.
                if entry_price is None:
                    entry_price = current_price
                else:
                    position_return = (current_price / entry_price - 1.0) * np.sign(current)
                    if self.config.stop_loss_pct is not None and position_return <= -float(self.config.stop_loss_pct):
                        should_exit = True
                        risk_exits[index] = True
                    if self.config.take_profit_pct is not None and position_return >= float(self.config.take_profit_pct):
                        should_exit = True
                        risk_exits[index] = True
            if current == 0.0 and should_enter:
                current = -max_position if self.config.side == "short_only" else max_position
                entry_price = None
            elif current != 0.0 and should_exit:
                current = 0.0
                entry_price = None
            positions[index] = current

        analysis["position"] = positions
        analysis["forecast"] = np.where(max_position > 0, analysis["position"] / max_position, 0.0)
        analysis["entry_signal"] = entry.astype(float)
        analysis["exit_signal"] = exit_.astype(float)
        analysis["risk_exit_signal"] = risk_exits.astype(float)

        return _build_standard_output(
            name=f"{symbol}_{self.config.name}",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.config.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "rule_based_user_strategy",
                "ruleset": self.config.name,
                "side": self.config.side,
                "entry_rule_count": len(self.config.entry_rules),
                "exit_rule_count": len(self.config.exit_rules),
                "explanation": "Validated user strategy spec interpreted through approved rule blocks.",
            },
        )


def max_rule_lookback(spec: dict[str, Any]) -> int:
    windows: list[int] = [20]
    for rule in list(spec.get("entry_rules") or []) + list(spec.get("exit_rules") or []):
        if not isinstance(rule, dict):
            continue
        params = _rule_parameters(rule)
        for key in ("window", "fast_window", "slow_window", "signal_window"):
            if key in params:
                windows.append(_as_int(params, key, 20))
    return max(windows)


def build_rule_based_strategy_factory(spec: dict[str, Any]):
    config = RuleBasedStrategyConfig(
        name=str(spec.get("id") or spec.get("name") or "user_rule_strategy").lower().replace(" ", "_"),
        entry_rules=tuple(rule for rule in spec.get("entry_rules") or [] if isinstance(rule, dict)),
        exit_rules=tuple(rule for rule in spec.get("exit_rules") or [] if isinstance(rule, dict)),
        side=str(spec.get("side") or "long_only"),
        # Portfolio sizing is applied once by the custom pipeline allocator.
        max_position=1.0,
        stop_loss_pct=float(spec.get("risk_controls", {}).get("stop_loss_pct")) if isinstance(spec.get("risk_controls"), dict) and spec.get("risk_controls", {}).get("stop_loss_pct") is not None else None,
        take_profit_pct=float(spec.get("risk_controls", {}).get("take_profit_pct")) if isinstance(spec.get("risk_controls"), dict) and spec.get("risk_controls", {}).get("take_profit_pct") is not None else None,
        # The ledger is the authoritative cost model. Keeping strategy-level
        # cost estimates at zero prevents charging the same components twice.
        transaction_cost_bps=0.0,
        entry_logic=str(spec.get("entry_logic") or "all"),
        exit_logic=str(spec.get("exit_logic") or "any"),
    )
    min_history = max(80, max_rule_lookback(spec) * 3)
    return lambda symbol: RuleBasedDirectionalStrategy(symbol=symbol, config=config), min_history
