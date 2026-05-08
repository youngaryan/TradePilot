from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from ..core.framework import StrategyOutput, WalkForwardStrategy


def _extract_price_series(train_data: pd.DataFrame, test_data: pd.DataFrame, symbol: str | None = None) -> tuple[str, pd.Series, pd.Index]:
    train_frame = train_data.copy()
    test_frame = test_data.copy()
    if symbol is None:
        if len(train_frame.columns) != 1 or len(test_frame.columns) != 1:
            raise ValueError("Single-asset strategies require exactly one column unless a symbol is provided.")
        symbol = str(train_frame.columns[0])

    if symbol not in train_frame.columns or symbol not in test_frame.columns:
        raise KeyError(f"Symbol '{symbol}' must exist in both train and test data.")

    combined = pd.concat([train_frame[[symbol]], test_frame[[symbol]]], axis=0)
    combined = combined[~combined.index.duplicated(keep="last")]
    return symbol, combined[symbol].astype(float), test_frame.index


def _build_standard_output(
    *,
    name: str,
    analysis: pd.DataFrame,
    test_index: pd.Index,
    transaction_cost_bps: float,
    diagnostics: dict[str, object],
) -> StrategyOutput:
    full = analysis.copy()
    full["turnover"] = full["position"].diff().abs().fillna(full["position"].abs())
    full["cost_estimate"] = full["turnover"] * (transaction_cost_bps / 10_000.0)
    full["gross_return"] = full["position"].shift(1).fillna(0.0) * full["unit_return"].fillna(0.0)
    full["signal"] = np.sign(full["position"]).replace({-0.0: 0.0}).fillna(0.0)
    frame = full.reindex(test_index).copy()
    frame["short_exposure_per_unit"] = (frame["position"] < 0.0).astype(float)
    frame["gross_exposure_per_unit"] = 1.0
    frame["instrument_return"] = frame["unit_return"].fillna(0.0)
    symbol = str(diagnostics.get("symbol") or "")
    if symbol:
        frame[f"target_weight_{symbol}"] = np.sign(frame["position"]).replace({-0.0: 0.0}).fillna(0.0) * frame["position"].abs()

    for column in (
        "signal",
        "forecast",
        "position",
        "cost_estimate",
        "unit_return",
        "gross_return",
        "turnover",
        "short_exposure_per_unit",
        "gross_exposure_per_unit",
        "instrument_return",
    ):
        frame[column] = frame[column].fillna(0.0)

    return StrategyOutput(name=name, frame=frame, diagnostics=diagnostics).validate(extra_columns=("unit_return", "gross_return"))


def _price_analysis_frame(prices: pd.Series) -> pd.DataFrame:
    analysis = pd.DataFrame(index=prices.index)
    analysis["price"] = prices
    analysis["unit_return"] = prices.pct_change().fillna(0.0)
    return analysis


def _rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window).mean()
    std = series.rolling(window).std(ddof=0).replace(0.0, np.nan)
    return ((series - mean) / std).replace([np.inf, -np.inf], 0.0).fillna(0.0)


def _stateful_mean_reversion_positions(zscore: pd.Series, entry_z: float, exit_z: float) -> np.ndarray:
    positions = np.zeros(len(zscore))
    current_pos = 0.0
    for index, value in enumerate(zscore.fillna(0.0).to_numpy(dtype=float)):
        if current_pos == 0.0:
            if value <= -entry_z:
                current_pos = 1.0
            elif value >= entry_z:
                current_pos = -1.0
        elif current_pos > 0.0 and value >= -exit_z:
            current_pos = 0.0
        elif current_pos < 0.0 and value <= exit_z:
            current_pos = 0.0
        positions[index] = current_pos
    return positions


def _compute_rsi(prices: pd.Series, window: int) -> pd.Series:
    delta = prices.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    avg_gain = gains.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = losses.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    relative_strength = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + relative_strength))
    return rsi.fillna(50.0)


def _approximate_true_range(prices: pd.Series) -> pd.Series:
    return prices.diff().abs().fillna(0.0)


class BuyAndHoldStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        transaction_cost_bps: float = 0.25,
    ) -> None:
        self.symbol = symbol
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["forecast"] = 1.0
        analysis["position"] = prices.notna().astype(float)

        return _build_standard_output(
            name=f"{symbol}_buy_and_hold",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "buy_and_hold",
                "explanation": "Long-only benchmark that stays invested whenever a valid price is available.",
            },
        )


class MovingAverageCrossStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        fast_window: int = 20,
        slow_window: int = 80,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        if fast_window >= slow_window:
            raise ValueError("fast_window must be smaller than slow_window.")
        self.symbol = symbol
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["fast_ma"] = prices.rolling(self.fast_window).mean()
        analysis["slow_ma"] = prices.rolling(self.slow_window).mean()
        spread = analysis["fast_ma"] / analysis["slow_ma"] - 1.0
        analysis["forecast"] = (spread * 100.0).clip(-2.0, 2.0).fillna(0.0)
        analysis["position"] = np.sign(spread).replace({-0.0: 0.0}).fillna(0.0)
        analysis.loc[analysis["slow_ma"].isna(), "position"] = 0.0

        return _build_standard_output(
            name=f"{symbol}_ma_cross",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "moving_average_cross",
                "fast_window": float(self.fast_window),
                "slow_window": float(self.slow_window),
            },
        )


class EMACrossStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        fast_window: int = 12,
        slow_window: int = 48,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        if fast_window >= slow_window:
            raise ValueError("fast_window must be smaller than slow_window.")
        self.symbol = symbol
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["fast_ema"] = prices.ewm(span=self.fast_window, adjust=False, min_periods=self.fast_window).mean()
        analysis["slow_ema"] = prices.ewm(span=self.slow_window, adjust=False, min_periods=self.slow_window).mean()
        spread = analysis["fast_ema"] / analysis["slow_ema"] - 1.0
        analysis["forecast"] = (spread * 120.0).clip(-2.0, 2.0).fillna(0.0)
        analysis["position"] = np.sign(spread).replace({-0.0: 0.0}).fillna(0.0)
        analysis.loc[analysis["slow_ema"].isna(), "position"] = 0.0

        return _build_standard_output(
            name=f"{symbol}_ema_cross",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "ema_cross",
                "fast_window": float(self.fast_window),
                "slow_window": float(self.slow_window),
                "explanation": "Faster exponential trend detector than a simple moving-average crossover.",
            },
        )


class PriceSMADeviationStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        window: int = 40,
        entry_z: float = 1.25,
        exit_z: float = 0.25,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        self.symbol = symbol
        self.window = window
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["sma"] = prices.rolling(self.window).mean()
        analysis["deviation"] = prices - analysis["sma"]
        analysis["zscore"] = _rolling_zscore(analysis["deviation"], self.window)
        analysis["forecast"] = (-analysis["zscore"]).clip(-2.0, 2.0)
        analysis["position"] = _stateful_mean_reversion_positions(analysis["zscore"], self.entry_z, self.exit_z)
        analysis.loc[analysis["sma"].isna(), "position"] = 0.0

        return _build_standard_output(
            name=f"{symbol}_sma_deviation",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "sma_deviation",
                "window": float(self.window),
                "entry_z": float(self.entry_z),
                "exit_z": float(self.exit_z),
                "explanation": "Mean-reversion model that fades stretched deviations from a moving average.",
            },
        )


class RSIMeanReversionStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        rsi_window: int = 14,
        lower_entry: float = 30.0,
        upper_entry: float = 70.0,
        exit_level: float = 50.0,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        self.symbol = symbol
        self.rsi_window = rsi_window
        self.lower_entry = lower_entry
        self.upper_entry = upper_entry
        self.exit_level = exit_level
        self.transaction_cost_bps = transaction_cost_bps

    def _compute_rsi(self, prices: pd.Series) -> pd.Series:
        return _compute_rsi(prices, self.rsi_window)

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["rsi"] = self._compute_rsi(prices)
        analysis["forecast"] = ((50.0 - analysis["rsi"]) / 20.0).clip(-2.0, 2.0).fillna(0.0)

        positions = np.zeros(len(analysis))
        current_pos = 0.0
        for index in range(len(analysis)):
            rsi = float(analysis["rsi"].iloc[index])
            if np.isnan(rsi):
                positions[index] = 0.0
                continue
            if current_pos == 0.0:
                if rsi <= self.lower_entry:
                    current_pos = 1.0
                elif rsi >= self.upper_entry:
                    current_pos = -1.0
            elif current_pos > 0.0 and rsi >= self.exit_level:
                current_pos = 0.0
            elif current_pos < 0.0 and rsi <= self.exit_level:
                current_pos = 0.0
            positions[index] = current_pos

        analysis["position"] = positions

        return _build_standard_output(
            name=f"{symbol}_rsi_mean_reversion",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "rsi_mean_reversion",
                "rsi_window": float(self.rsi_window),
                "lower_entry": float(self.lower_entry),
                "upper_entry": float(self.upper_entry),
            },
        )


class StochasticOscillatorStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        window: int = 14,
        smooth_window: int = 3,
        lower_entry: float = 20.0,
        upper_entry: float = 80.0,
        exit_level: float = 50.0,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        self.symbol = symbol
        self.window = window
        self.smooth_window = smooth_window
        self.lower_entry = lower_entry
        self.upper_entry = upper_entry
        self.exit_level = exit_level
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        low = prices.rolling(self.window).min()
        high = prices.rolling(self.window).max()
        denominator = (high - low).replace(0.0, np.nan)
        analysis["stochastic_k"] = (100.0 * (prices - low) / denominator).fillna(50.0)
        analysis["stochastic_d"] = analysis["stochastic_k"].rolling(self.smooth_window).mean().fillna(50.0)
        analysis["forecast"] = ((50.0 - analysis["stochastic_d"]) / 25.0).clip(-2.0, 2.0)

        positions = np.zeros(len(analysis))
        current_pos = 0.0
        for index, value in enumerate(analysis["stochastic_d"].to_numpy(dtype=float)):
            if current_pos == 0.0:
                if value <= self.lower_entry:
                    current_pos = 1.0
                elif value >= self.upper_entry:
                    current_pos = -1.0
            elif current_pos > 0.0 and value >= self.exit_level:
                current_pos = 0.0
            elif current_pos < 0.0 and value <= self.exit_level:
                current_pos = 0.0
            positions[index] = current_pos
        analysis["position"] = positions
        analysis.loc[low.isna() | high.isna(), "position"] = 0.0

        return _build_standard_output(
            name=f"{symbol}_stochastic_oscillator",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "stochastic_oscillator",
                "window": float(self.window),
                "lower_entry": float(self.lower_entry),
                "upper_entry": float(self.upper_entry),
                "explanation": "Range oscillator that buys lower-channel exhaustion and sells upper-channel exhaustion.",
            },
        )


class BollingerBandMeanReversionStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        window: int = 20,
        num_std: float = 2.0,
        exit_z: float = 0.25,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        self.symbol = symbol
        self.window = window
        self.num_std = num_std
        self.exit_z = exit_z
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["middle_band"] = prices.rolling(self.window).mean()
        analysis["rolling_std"] = prices.rolling(self.window).std(ddof=0)
        analysis["upper_band"] = analysis["middle_band"] + self.num_std * analysis["rolling_std"]
        analysis["lower_band"] = analysis["middle_band"] - self.num_std * analysis["rolling_std"]
        analysis["zscore"] = ((prices - analysis["middle_band"]) / analysis["rolling_std"].replace(0.0, np.nan)).fillna(0.0)
        analysis["forecast"] = (-analysis["zscore"]).clip(-2.0, 2.0)
        analysis["position"] = _stateful_mean_reversion_positions(analysis["zscore"], self.num_std, self.exit_z)
        analysis.loc[analysis["middle_band"].isna(), "position"] = 0.0

        return _build_standard_output(
            name=f"{symbol}_bollinger_mean_reversion",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "bollinger_mean_reversion",
                "window": float(self.window),
                "num_std": float(self.num_std),
                "explanation": "Classic Bollinger-band fade strategy with explicit z-score entries and exits.",
            },
        )


class MACDTrendStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        fast_window: int = 12,
        slow_window: int = 26,
        signal_window: int = 9,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        if fast_window >= slow_window:
            raise ValueError("fast_window must be smaller than slow_window.")
        self.symbol = symbol
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.signal_window = signal_window
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        fast_ema = prices.ewm(span=self.fast_window, adjust=False, min_periods=self.fast_window).mean()
        slow_ema = prices.ewm(span=self.slow_window, adjust=False, min_periods=self.slow_window).mean()
        analysis["macd"] = fast_ema - slow_ema
        analysis["macd_signal"] = analysis["macd"].ewm(span=self.signal_window, adjust=False, min_periods=self.signal_window).mean()
        analysis["macd_histogram"] = analysis["macd"] - analysis["macd_signal"]
        histogram_scale = analysis["macd_histogram"].rolling(self.slow_window).std(ddof=0).replace(0.0, np.nan)
        analysis["forecast"] = (analysis["macd_histogram"] / histogram_scale).clip(-2.0, 2.0).fillna(0.0)
        analysis["position"] = np.sign(analysis["macd_histogram"]).replace({-0.0: 0.0}).fillna(0.0)
        analysis.loc[analysis["macd_signal"].isna(), "position"] = 0.0

        return _build_standard_output(
            name=f"{symbol}_macd_trend",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "macd_trend",
                "fast_window": float(self.fast_window),
                "slow_window": float(self.slow_window),
                "signal_window": float(self.signal_window),
                "explanation": "Momentum model that trades MACD histogram direction after signal-line confirmation.",
            },
        )


class DonchianBreakoutStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        breakout_window: int = 55,
        exit_window: int = 20,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        if exit_window >= breakout_window:
            raise ValueError("exit_window must be smaller than breakout_window.")
        self.symbol = symbol
        self.breakout_window = breakout_window
        self.exit_window = exit_window
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["upper_band"] = prices.shift(1).rolling(self.breakout_window).max()
        analysis["lower_band"] = prices.shift(1).rolling(self.breakout_window).min()
        analysis["exit_high"] = prices.shift(1).rolling(self.exit_window).max()
        analysis["exit_low"] = prices.shift(1).rolling(self.exit_window).min()

        positions = np.zeros(len(analysis))
        forecasts = np.zeros(len(analysis))
        current_pos = 0.0
        for index in range(len(analysis)):
            price = float(analysis["price"].iloc[index])
            upper = analysis["upper_band"].iloc[index]
            lower = analysis["lower_band"].iloc[index]
            exit_high = analysis["exit_high"].iloc[index]
            exit_low = analysis["exit_low"].iloc[index]

            if pd.isna(upper) or pd.isna(lower):
                positions[index] = 0.0
                continue

            if current_pos == 0.0:
                if price > float(upper):
                    current_pos = 1.0
                elif price < float(lower):
                    current_pos = -1.0
            elif current_pos > 0.0 and price < float(exit_low):
                current_pos = 0.0
            elif current_pos < 0.0 and price > float(exit_high):
                current_pos = 0.0

            positions[index] = current_pos
            channel_width = max(float(upper) - float(lower), 1e-8)
            forecasts[index] = float(np.clip((price - (float(upper) + float(lower)) / 2.0) / channel_width * 4.0, -2.0, 2.0))

        analysis["position"] = positions
        analysis["forecast"] = forecasts

        return _build_standard_output(
            name=f"{symbol}_donchian_breakout",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "donchian_breakout",
                "breakout_window": float(self.breakout_window),
                "exit_window": float(self.exit_window),
            },
        )


class KeltnerChannelBreakoutStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        window: int = 40,
        atr_multiplier: float = 1.5,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        self.symbol = symbol
        self.window = window
        self.atr_multiplier = atr_multiplier
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["ema"] = prices.ewm(span=self.window, adjust=False, min_periods=self.window).mean()
        analysis["atr_proxy"] = _approximate_true_range(prices).ewm(span=self.window, adjust=False, min_periods=self.window).mean()
        analysis["upper_channel"] = analysis["ema"] + self.atr_multiplier * analysis["atr_proxy"]
        analysis["lower_channel"] = analysis["ema"] - self.atr_multiplier * analysis["atr_proxy"]

        positions = np.zeros(len(analysis))
        forecasts = np.zeros(len(analysis))
        current_pos = 0.0
        for index in range(len(analysis)):
            price = float(analysis["price"].iloc[index])
            ema = analysis["ema"].iloc[index]
            upper = analysis["upper_channel"].iloc[index]
            lower = analysis["lower_channel"].iloc[index]
            width = max(float(upper - lower), 1e-8) if pd.notna(upper) and pd.notna(lower) else np.nan

            if pd.isna(ema) or pd.isna(upper) or pd.isna(lower):
                positions[index] = 0.0
                continue

            if current_pos == 0.0:
                if price > float(upper):
                    current_pos = 1.0
                elif price < float(lower):
                    current_pos = -1.0
            elif current_pos > 0.0 and price < float(ema):
                current_pos = 0.0
            elif current_pos < 0.0 and price > float(ema):
                current_pos = 0.0

            positions[index] = current_pos
            forecasts[index] = float(np.clip((price - float(ema)) / width * 2.0, -2.0, 2.0))

        analysis["position"] = positions
        analysis["forecast"] = forecasts

        return _build_standard_output(
            name=f"{symbol}_keltner_breakout",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "keltner_breakout",
                "window": float(self.window),
                "atr_multiplier": float(self.atr_multiplier),
                "explanation": "Volatility-adjusted channel breakout using an ATR proxy from close-to-close movement.",
            },
        )


class VolatilityTargetTrendStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        trend_window: int = 120,
        volatility_window: int = 20,
        target_volatility: float = 0.15,
        max_position: float = 1.5,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        self.symbol = symbol
        self.trend_window = trend_window
        self.volatility_window = volatility_window
        self.target_volatility = target_volatility
        self.max_position = max_position
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["trend_ma"] = prices.rolling(self.trend_window).mean()
        analysis["trend_score"] = prices / analysis["trend_ma"] - 1.0
        analysis["realized_volatility"] = analysis["unit_return"].rolling(self.volatility_window).std(ddof=0) * np.sqrt(252.0)
        inverse_vol_scale = (self.target_volatility / analysis["realized_volatility"].replace(0.0, np.nan)).clip(0.0, self.max_position)
        trend_direction = np.sign(analysis["trend_score"]).replace({-0.0: 0.0}).fillna(0.0)
        analysis["position"] = (trend_direction * inverse_vol_scale).fillna(0.0)
        analysis.loc[analysis["trend_ma"].isna(), "position"] = 0.0
        analysis["forecast"] = (analysis["trend_score"] * 50.0).clip(-2.0, 2.0).fillna(0.0)

        return _build_standard_output(
            name=f"{symbol}_volatility_target_trend",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "volatility_target_trend",
                "trend_window": float(self.trend_window),
                "volatility_window": float(self.volatility_window),
                "target_volatility": float(self.target_volatility),
                "max_position": float(self.max_position),
                "explanation": "Trend-following signal scaled down in volatile regimes and up in quieter regimes.",
            },
        )


class TimeSeriesMomentumStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        lookbacks: Sequence[int] = (21, 63, 126, 252),
        min_agreement: float = 0.25,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        if not lookbacks:
            raise ValueError("lookbacks must contain at least one horizon.")
        self.symbol = symbol
        self.lookbacks = tuple(int(value) for value in lookbacks)
        self.min_agreement = min_agreement
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        horizon_signals = []
        for lookback in self.lookbacks:
            momentum = prices / prices.shift(lookback) - 1.0
            column = f"momentum_{lookback}"
            analysis[column] = momentum
            horizon_signals.append(np.sign(momentum).replace({-0.0: 0.0}).fillna(0.0))
        signal_frame = pd.concat(horizon_signals, axis=1)
        analysis["forecast"] = signal_frame.mean(axis=1).clip(-2.0, 2.0).fillna(0.0)
        analysis["position"] = np.where(analysis["forecast"].abs() >= self.min_agreement, np.sign(analysis["forecast"]), 0.0)
        analysis.loc[prices.shift(max(self.lookbacks)).isna(), "position"] = 0.0

        return _build_standard_output(
            name=f"{symbol}_time_series_momentum",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "time_series_momentum",
                "lookbacks": ",".join(str(value) for value in self.lookbacks),
                "min_agreement": float(self.min_agreement),
                "explanation": "Multi-horizon momentum vote that requires enough horizons to agree before taking risk.",
            },
        )


class AdaptiveRegimeStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        fast_window: int = 30,
        slow_window: int = 120,
        mean_reversion_window: int = 40,
        volatility_window: int = 30,
        volatility_quantile: float = 0.70,
        entry_z: float = 1.25,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        if fast_window >= slow_window:
            raise ValueError("fast_window must be smaller than slow_window.")
        self.symbol = symbol
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.mean_reversion_window = mean_reversion_window
        self.volatility_window = volatility_window
        self.volatility_quantile = volatility_quantile
        self.entry_z = entry_z
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["fast_ma"] = prices.rolling(self.fast_window).mean()
        analysis["slow_ma"] = prices.rolling(self.slow_window).mean()
        analysis["trend_score"] = analysis["fast_ma"] / analysis["slow_ma"] - 1.0
        analysis["deviation_z"] = _rolling_zscore(prices - prices.rolling(self.mean_reversion_window).mean(), self.mean_reversion_window)
        analysis["realized_volatility"] = analysis["unit_return"].rolling(self.volatility_window).std(ddof=0) * np.sqrt(252.0)

        train_volatility = analysis.loc[train_data.index, "realized_volatility"].dropna()
        volatility_threshold = (
            float(train_volatility.quantile(self.volatility_quantile))
            if not train_volatility.empty
            else float(analysis["realized_volatility"].dropna().median() or 0.0)
        )
        trend_strength = analysis["trend_score"].abs()
        trend_threshold = float(trend_strength.loc[train_data.index].dropna().median() or 0.0)
        use_trend = (analysis["realized_volatility"] >= volatility_threshold) | (trend_strength >= trend_threshold)

        trend_position = np.sign(analysis["trend_score"]).replace({-0.0: 0.0}).fillna(0.0)
        mean_reversion_position = np.where(
            analysis["deviation_z"].abs() >= self.entry_z,
            -np.sign(analysis["deviation_z"]),
            0.0,
        )
        analysis["position"] = np.where(use_trend.fillna(False), trend_position, mean_reversion_position)
        analysis.loc[analysis["slow_ma"].isna(), "position"] = 0.0
        trend_forecast = (analysis["trend_score"] * 80.0).clip(-2.0, 2.0).fillna(0.0)
        mean_reversion_forecast = (-analysis["deviation_z"]).clip(-2.0, 2.0).fillna(0.0)
        analysis["forecast"] = np.where(use_trend.fillna(False), trend_forecast, mean_reversion_forecast)

        return _build_standard_output(
            name=f"{symbol}_adaptive_regime",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "adaptive_regime",
                "fast_window": float(self.fast_window),
                "slow_window": float(self.slow_window),
                "mean_reversion_window": float(self.mean_reversion_window),
                "volatility_window": float(self.volatility_window),
                "volatility_threshold": volatility_threshold,
                "explanation": "Regime switcher that follows trends in stronger or higher-volatility regimes and fades deviations otherwise.",
            },
        )
