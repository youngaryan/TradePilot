from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

import pandas as pd


class TradingMode(StrEnum):
    DAILY = "daily"
    SHORT_TERM = "short_term"


DAILY_INTERVALS = {"1d", "1wk", "1mo"}
INTRADAY_INTERVALS = {"1h", "4h"}
SUPPORTED_MARKET_INTERVALS = DAILY_INTERVALS | INTRADAY_INTERVALS

_INTERVAL_ALIASES = {
    "daily": "1d",
    "day": "1d",
    "1day": "1d",
    "1d": "1d",
    "week": "1wk",
    "weekly": "1wk",
    "1w": "1wk",
    "1wk": "1wk",
    "month": "1mo",
    "monthly": "1mo",
    "1mth": "1mo",
    "1mo": "1mo",
    "hour": "1h",
    "hourly": "1h",
    "60m": "1h",
    "1hour": "1h",
    "1h": "1h",
    "4hour": "4h",
    "4hours": "4h",
    "4-hour": "4h",
    "4h": "4h",
}

_RESAMPLE_RULES = {
    "4h": "4h",
    "1wk": "W-FRI",
    "1mo": "ME",
}


@dataclass(frozen=True)
class TimeframeSpec:
    mode: TradingMode
    execution_interval: str
    signal_intervals: tuple[str, ...]
    primary_signal_interval: str
    bars_per_year: int
    default_train_bars: int
    default_test_bars: int
    default_step_bars: int
    decision_horizons: tuple[str, ...]
    description: str

    def to_metadata(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["mode"] = str(self.mode)
        return payload


DAILY_TIMEFRAME = TimeframeSpec(
    mode=TradingMode.DAILY,
    execution_interval="1d",
    signal_intervals=("1d",),
    primary_signal_interval="1d",
    bars_per_year=252,
    default_train_bars=300,
    default_test_bars=63,
    default_step_bars=63,
    decision_horizons=("swing", "long-term"),
    description="Daily bars for longer-term signals, execution, backtests, and agent decisions.",
)

SHORT_TERM_TIMEFRAME = TimeframeSpec(
    mode=TradingMode.SHORT_TERM,
    execution_interval="1h",
    signal_intervals=("1h", "4h"),
    primary_signal_interval="4h",
    bars_per_year=1638,
    default_train_bars=390,
    default_test_bars=78,
    default_step_bars=78,
    decision_horizons=("intraday",),
    description="Hourly execution bars with 4-hour signal confirmation for short-term opportunities.",
)


def normalize_market_interval(interval: str | None) -> str:
    raw = str(interval or "1d").strip().lower().replace("_", "-").replace(" ", "")
    normalized = _INTERVAL_ALIASES.get(raw, raw)
    if normalized not in SUPPORTED_MARKET_INTERVALS:
        raise ValueError(f"Unsupported market data interval: {interval!r}.")
    return normalized


def base_interval_for(interval: str | None) -> str:
    normalized = normalize_market_interval(interval)
    return "1h" if normalized == "4h" else normalized


def is_derived_interval(interval: str | None) -> bool:
    normalized = normalize_market_interval(interval)
    return base_interval_for(normalized) != normalized


def trading_mode_from_interval(interval: str | None) -> TradingMode:
    normalized = normalize_market_interval(interval)
    return TradingMode.SHORT_TERM if normalized in INTRADAY_INTERVALS else TradingMode.DAILY


def normalize_trading_mode(mode: str | TradingMode | None, *, interval: str | None = None) -> TradingMode:
    if mode is None or str(mode).strip() == "":
        return trading_mode_from_interval(interval or "1d")
    raw = str(mode).strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"daily", "day", "1d", "long_term", "longer_term", "swing_daily"}:
        return TradingMode.DAILY
    if raw in {"short_term", "short", "intraday", "hourly", "1h", "4h"}:
        return TradingMode.SHORT_TERM
    raise ValueError(f"Unsupported trading mode: {mode!r}.")


def timeframe_spec_for(mode: str | TradingMode) -> TimeframeSpec:
    normalized = normalize_trading_mode(mode)
    return SHORT_TERM_TIMEFRAME if normalized == TradingMode.SHORT_TERM else DAILY_TIMEFRAME


def resolve_timeframe_spec(*, trading_mode: str | TradingMode | None = None, interval: str | None = None) -> TimeframeSpec:
    mode = normalize_trading_mode(trading_mode, interval=interval)
    spec = timeframe_spec_for(mode)
    if trading_mode is None and interval:
        normalized_interval = normalize_market_interval(interval)
        if normalized_interval == "4h":
            return TimeframeSpec(
                mode=TradingMode.SHORT_TERM,
                execution_interval="4h",
                signal_intervals=("1h", "4h"),
                primary_signal_interval="4h",
                bars_per_year=504,
                default_train_bars=252,
                default_test_bars=42,
                default_step_bars=42,
                decision_horizons=("intraday",),
                description="4-hour execution bars derived from hourly data for short-term opportunities.",
            )
        if normalized_interval != spec.execution_interval and mode == TradingMode.DAILY:
            return TimeframeSpec(
                mode=TradingMode.DAILY,
                execution_interval=normalized_interval,
                signal_intervals=(normalized_interval,),
                primary_signal_interval=normalized_interval,
                bars_per_year=52 if normalized_interval == "1wk" else 12 if normalized_interval == "1mo" else 252,
                default_train_bars=spec.default_train_bars,
                default_test_bars=spec.default_test_bars,
                default_step_bars=spec.default_step_bars,
                decision_horizons=spec.decision_horizons,
                description=f"{normalized_interval} bars using the daily-mode backtesting contract.",
            )
    return spec


def resample_close_prices(prices: pd.DataFrame, interval: str) -> pd.DataFrame:
    normalized = normalize_market_interval(interval)
    frame = prices.copy().sort_index()
    frame.index = pd.DatetimeIndex(frame.index).tz_localize(None)
    if normalized not in _RESAMPLE_RULES:
        return frame
    resampled = frame.resample(_RESAMPLE_RULES[normalized], label="right", closed="right").last()
    return resampled.dropna(how="all").sort_index()
