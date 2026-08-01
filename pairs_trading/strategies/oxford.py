from __future__ import annotations

import numpy as np
import pandas as pd

from ..core.framework import StrategyOutput, WalkForwardStrategy
from .directional import _build_standard_output, _compute_rsi, _extract_price_series, _price_analysis_frame


def _finite(value: object) -> bool:
    try:
        return bool(pd.notna(value) and np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _linear_regression_slope(prices: pd.Series, window: int) -> pd.Series:
    x = np.arange(window, dtype=float)
    x_centered = x - x.mean()
    denominator = float(np.dot(x_centered, x_centered))

    def slope(values: np.ndarray) -> float:
        if np.isnan(values).any():
            return np.nan
        y_centered = values - values.mean()
        return float(np.dot(x_centered, y_centered) / denominator)

    return prices.rolling(window).apply(slope, raw=True)


def _source_diagnostics(source_url: str, assumption: str, **values: object) -> dict[str, object]:
    return {
        "source_url": source_url,
        "implementation_assumption": assumption,
        **values,
    }


class OxfordCombinedDonchianStrategy(WalkForwardStrategy):
    """Oxford combined Donchian channel breakout with a shorter trailing channel exit."""

    source_url = "https://oxfordstrat.com/trading-strategies/combined-donchian-channels/"

    def __init__(
        self,
        symbol: str,
        entry_window: int = 100,
        exit_window: int = 50,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        if exit_window >= entry_window:
            raise ValueError("exit_window must be smaller than entry_window.")
        self.symbol = symbol
        self.entry_window = entry_window
        self.exit_window = exit_window
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["entry_high"] = prices.shift(1).rolling(self.entry_window).max()
        analysis["entry_low"] = prices.shift(1).rolling(self.entry_window).min()
        analysis["exit_high"] = prices.shift(1).rolling(self.exit_window).max()
        analysis["exit_low"] = prices.shift(1).rolling(self.exit_window).min()

        positions = np.zeros(len(analysis), dtype=float)
        forecasts = np.zeros(len(analysis), dtype=float)
        current = 0.0
        for index, row in enumerate(analysis[["price", "entry_high", "entry_low", "exit_high", "exit_low"]].itertuples(index=False)):
            price, entry_high, entry_low, exit_high, exit_low = row
            if not all(_finite(value) for value in (price, entry_high, entry_low)):
                current = 0.0
                positions[index] = current
                continue
            if current == 0.0:
                if float(price) > float(entry_high):
                    current = 1.0
                elif float(price) < float(entry_low):
                    current = -1.0
            elif current > 0.0 and _finite(exit_low) and float(price) < float(exit_low):
                current = 0.0
            elif current < 0.0 and _finite(exit_high) and float(price) > float(exit_high):
                current = 0.0

            channel_width = max(float(entry_high) - float(entry_low), 1e-8)
            channel_mid = (float(entry_high) + float(entry_low)) / 2.0
            positions[index] = current
            forecasts[index] = float(np.clip((float(price) - channel_mid) / channel_width * 4.0, -2.0, 2.0))

        analysis["position"] = positions
        analysis["forecast"] = forecasts
        return _build_standard_output(
            name=f"{symbol}_oxford_combined_donchian",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "oxford_combined_donchian",
                **_source_diagnostics(
                    self.source_url,
                    "Oxford uses stop orders on high/low Donchian channels; this implementation uses close-only channel breaks.",
                    entry_window=float(self.entry_window),
                    exit_window=float(self.exit_window),
                ),
            },
        )


class OxfordPriceMomentumStrategy(WalkForwardStrategy):
    """Kaufman/Oxford fast and slow price momentum model."""

    source_url = "https://oxfordstrat.com/trading-strategies/price-momentum-model/"

    def __init__(
        self,
        symbol: str,
        slow_lookback: int = 120,
        fast_lookback_index: float = 0.75,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        if slow_lookback < 2:
            raise ValueError("slow_lookback must be at least 2.")
        if fast_lookback_index <= 0:
            raise ValueError("fast_lookback_index must be positive.")
        self.symbol = symbol
        self.slow_lookback = slow_lookback
        self.fast_lookback_index = fast_lookback_index
        self.fast_lookback = max(1, int(round(slow_lookback * fast_lookback_index)))
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["fast_momentum"] = prices - prices.shift(self.fast_lookback)
        analysis["slow_momentum"] = prices - prices.shift(self.slow_lookback)

        positions = np.zeros(len(analysis), dtype=float)
        current = 0.0
        for index, row in enumerate(analysis[["fast_momentum", "slow_momentum"]].itertuples(index=False)):
            fast, slow = row
            if not all(_finite(value) for value in (fast, slow)):
                current = 0.0
            elif current == 0.0:
                if float(fast) > 0.0 and float(slow) > 0.0:
                    current = 1.0
                elif float(fast) < 0.0 and float(slow) < 0.0:
                    current = -1.0
            elif current > 0.0 and float(fast) < 0.0:
                current = 0.0
            elif current < 0.0 and float(fast) > 0.0:
                current = 0.0
            positions[index] = current

        fast_return = prices / prices.shift(self.fast_lookback) - 1.0
        slow_return = prices / prices.shift(self.slow_lookback) - 1.0
        analysis["forecast"] = ((fast_return + slow_return) * 4.0).clip(-2.0, 2.0).fillna(0.0)
        analysis["position"] = positions
        return _build_standard_output(
            name=f"{symbol}_oxford_price_momentum",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "oxford_price_momentum",
                **_source_diagnostics(
                    self.source_url,
                    "Oxford enters at next open after prior-bar momentum; close signals are shifted by the backtester before returns are earned.",
                    slow_lookback=float(self.slow_lookback),
                    fast_lookback=float(self.fast_lookback),
                    fast_lookback_index=float(self.fast_lookback_index),
                ),
            },
        )


class OxfordDualMomentumROCStrategy(WalkForwardStrategy):
    """Dual rate-of-change momentum with slow filter, fast setup, and a time exit."""

    source_url = "https://oxfordstrat.com/trading-strategies/dual-momentum-rate-of-change/"

    def __init__(
        self,
        symbol: str,
        slow_lookback: int = 120,
        fast_lookback: int = 60,
        time_exit_bars: int = 60,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        if fast_lookback >= slow_lookback:
            raise ValueError("fast_lookback must be smaller than slow_lookback.")
        self.symbol = symbol
        self.slow_lookback = slow_lookback
        self.fast_lookback = fast_lookback
        self.time_exit_bars = time_exit_bars
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["slow_roc"] = prices / prices.shift(self.slow_lookback) - 1.0
        analysis["fast_roc"] = prices / prices.shift(self.fast_lookback) - 1.0

        positions = np.zeros(len(analysis), dtype=float)
        current = 0.0
        bars_held = 0
        for index, row in enumerate(analysis[["fast_roc", "slow_roc"]].itertuples(index=False)):
            fast, slow = row
            if not all(_finite(value) for value in (fast, slow)):
                current = 0.0
                bars_held = 0
            else:
                direction = 1.0 if float(fast) > 0.0 and float(slow) > 0.0 else -1.0 if float(fast) < 0.0 and float(slow) < 0.0 else 0.0
                if current == 0.0 and direction != 0.0:
                    current = direction
                    bars_held = 0
                elif current != 0.0:
                    bars_held += 1
                    if direction == -current or bars_held >= self.time_exit_bars:
                        current = 0.0
                        bars_held = 0
            positions[index] = current

        analysis["forecast"] = ((analysis["fast_roc"] + analysis["slow_roc"]) * 3.0).clip(-2.0, 2.0).fillna(0.0)
        analysis["position"] = positions
        return _build_standard_output(
            name=f"{symbol}_oxford_dual_momentum_roc",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "oxford_dual_momentum_roc",
                **_source_diagnostics(
                    self.source_url,
                    "Oxford reports longer holding periods were preferred; this implementation uses a configurable time exit.",
                    slow_lookback=float(self.slow_lookback),
                    fast_lookback=float(self.fast_lookback),
                    time_exit_bars=float(self.time_exit_bars),
                ),
            },
        )


class OxfordBollingerMomentumStrategy(WalkForwardStrategy):
    """Bollinger Bands momentum model with long, short, and neutral phases."""

    source_url = "https://oxfordstrat.com/trading-strategies/bollinger-band/"

    def __init__(
        self,
        symbol: str,
        window: int = 80,
        num_std: float = 2.0,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        self.symbol = symbol
        self.window = window
        self.num_std = num_std
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["middle_band"] = prices.rolling(self.window).mean()
        analysis["rolling_std"] = prices.rolling(self.window).std(ddof=0)
        analysis["upper_band"] = analysis["middle_band"] + self.num_std * analysis["rolling_std"]
        analysis["lower_band"] = analysis["middle_band"] - self.num_std * analysis["rolling_std"]

        positions = np.zeros(len(analysis), dtype=float)
        current = 0.0
        for index, row in enumerate(analysis[["price", "middle_band", "upper_band", "lower_band"]].itertuples(index=False)):
            price, middle, upper, lower = row
            if not all(_finite(value) for value in (price, middle, upper, lower)):
                current = 0.0
            elif current == 0.0:
                if float(price) > float(upper):
                    current = 1.0
                elif float(price) < float(lower):
                    current = -1.0
            elif current > 0.0 and float(price) < float(middle):
                current = 0.0
            elif current < 0.0 and float(price) > float(middle):
                current = 0.0
            positions[index] = current

        zscore = ((prices - analysis["middle_band"]) / analysis["rolling_std"].replace(0.0, np.nan)).fillna(0.0)
        analysis["forecast"] = zscore.clip(-2.0, 2.0)
        analysis["position"] = positions
        return _build_standard_output(
            name=f"{symbol}_oxford_bollinger_momentum",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "oxford_bollinger_momentum",
                **_source_diagnostics(
                    self.source_url,
                    "Oxford enters at next open; close-only signals are used and returns are earned after the signal bar.",
                    window=float(self.window),
                    num_std=float(self.num_std),
                ),
            },
        )


class OxfordKeltnerThreePhaseStrategy(WalkForwardStrategy):
    """Oxford Keltner channel 3-phase trend model using a close-to-close ATR proxy."""

    source_url = "https://oxfordstrat.com/trading-strategies/keltner-channels-2/"

    def __init__(
        self,
        symbol: str,
        window: int = 60,
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
        analysis["middle_line"] = prices.ewm(span=self.window, adjust=False, min_periods=self.window).mean()
        analysis["atr_proxy"] = prices.diff().abs().ewm(span=self.window, adjust=False, min_periods=self.window).mean()
        analysis["buy_line"] = analysis["middle_line"] + self.atr_multiplier * analysis["atr_proxy"]
        analysis["sell_line"] = analysis["middle_line"] - self.atr_multiplier * analysis["atr_proxy"]

        positions = np.zeros(len(analysis), dtype=float)
        current = 0.0
        for index, row in enumerate(analysis[["price", "middle_line", "buy_line", "sell_line"]].itertuples(index=False)):
            price, middle, buy_line, sell_line = row
            if not all(_finite(value) for value in (price, middle, buy_line, sell_line)):
                current = 0.0
            elif current == 0.0:
                if float(price) > float(buy_line):
                    current = 1.0
                elif float(price) < float(sell_line):
                    current = -1.0
            elif current > 0.0 and float(price) < float(middle):
                current = 0.0
            elif current < 0.0 and float(price) > float(middle):
                current = 0.0
            positions[index] = current

        width = (analysis["buy_line"] - analysis["sell_line"]).replace(0.0, np.nan)
        analysis["forecast"] = ((prices - analysis["middle_line"]) / width * 2.0).clip(-2.0, 2.0).fillna(0.0)
        analysis["position"] = positions
        return _build_standard_output(
            name=f"{symbol}_oxford_keltner_three_phase",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "oxford_keltner_three_phase",
                **_source_diagnostics(
                    self.source_url,
                    "Oxford uses true range from OHLC data; this implementation uses close-to-close absolute movement as the ATR proxy.",
                    window=float(self.window),
                    atr_multiplier=float(self.atr_multiplier),
                ),
            },
        )


class OxfordNormalizedRegressionSlopeStrategy(WalkForwardStrategy):
    """Normalized linear regression slope trend model."""

    source_url = "https://oxfordstrat.com/trading-strategies/normalized-linear-regression/"

    def __init__(
        self,
        symbol: str,
        window: int = 120,
        growth_threshold: float = 0.02,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        self.symbol = symbol
        self.window = window
        self.growth_threshold = growth_threshold
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["regression_slope"] = _linear_regression_slope(prices, self.window)
        analysis["normalized_slope"] = (analysis["regression_slope"] * self.window / prices.replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan)

        positions = np.zeros(len(analysis), dtype=float)
        current = 0.0
        for index, value in enumerate(analysis["normalized_slope"].to_numpy(dtype=float)):
            if not np.isfinite(value):
                current = 0.0
            elif current == 0.0:
                if value > self.growth_threshold:
                    current = 1.0
                elif value < -self.growth_threshold:
                    current = -1.0
            elif current > 0.0 and value < 0.0:
                current = 0.0
            elif current < 0.0 and value > 0.0:
                current = 0.0
            positions[index] = current

        analysis["forecast"] = (analysis["normalized_slope"] / max(self.growth_threshold, 1e-8)).clip(-2.0, 2.0).fillna(0.0)
        analysis["position"] = positions
        return _build_standard_output(
            name=f"{symbol}_oxford_normalized_regression_slope",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "oxford_normalized_regression_slope",
                **_source_diagnostics(
                    self.source_url,
                    "Normalized slope is approximated as fitted slope times lookback divided by price.",
                    window=float(self.window),
                    growth_threshold=float(self.growth_threshold),
                ),
            },
        )


class OxfordBollingerPercentBReversalStrategy(WalkForwardStrategy):
    """Trend-filtered Bollinger %b pullback/reversal model."""

    source_url = "https://oxfordstrat.com/trading-strategies/bollinger-bands-reversal/"

    def __init__(
        self,
        symbol: str,
        band_window: int = 20,
        trend_window: int = 200,
        num_std: float = 2.0,
        lower_percent_b: float = 0.2,
        upper_percent_b: float = 0.8,
        confirmation_bars: int = 3,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        self.symbol = symbol
        self.band_window = band_window
        self.trend_window = trend_window
        self.num_std = num_std
        self.lower_percent_b = lower_percent_b
        self.upper_percent_b = upper_percent_b
        self.confirmation_bars = confirmation_bars
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["trend_ma"] = prices.rolling(self.trend_window).mean()
        analysis["band_mid"] = prices.rolling(self.band_window).mean()
        analysis["band_std"] = prices.rolling(self.band_window).std(ddof=0)
        analysis["upper_band"] = analysis["band_mid"] + self.num_std * analysis["band_std"]
        analysis["lower_band"] = analysis["band_mid"] - self.num_std * analysis["band_std"]
        band_width = (analysis["upper_band"] - analysis["lower_band"]).replace(0.0, np.nan)
        analysis["percent_b"] = ((prices - analysis["lower_band"]) / band_width).clip(-1.0, 2.0)
        oversold = analysis["percent_b"] < self.lower_percent_b
        overbought = analysis["percent_b"] > self.upper_percent_b
        analysis["oversold_count"] = oversold.rolling(self.confirmation_bars).sum()
        analysis["overbought_count"] = overbought.rolling(self.confirmation_bars).sum()

        positions = np.zeros(len(analysis), dtype=float)
        current = 0.0
        for index, row in enumerate(analysis[["price", "trend_ma", "percent_b", "oversold_count", "overbought_count"]].itertuples(index=False)):
            price, trend_ma, percent_b, oversold_count, overbought_count = row
            if not all(_finite(value) for value in (price, trend_ma, percent_b)):
                current = 0.0
            elif current == 0.0:
                if float(price) > float(trend_ma) and _finite(oversold_count) and float(oversold_count) >= self.confirmation_bars:
                    current = 1.0
                elif float(price) < float(trend_ma) and _finite(overbought_count) and float(overbought_count) >= self.confirmation_bars:
                    current = -1.0
            elif current > 0.0 and float(percent_b) > self.upper_percent_b:
                current = 0.0
            elif current < 0.0 and float(percent_b) < self.lower_percent_b:
                current = 0.0
            positions[index] = current

        analysis["forecast"] = (0.5 - analysis["percent_b"]).clip(-2.0, 2.0).fillna(0.0)
        analysis["position"] = positions
        return _build_standard_output(
            name=f"{symbol}_oxford_bollinger_percent_b_reversal",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "oxford_bollinger_percent_b_reversal",
                **_source_diagnostics(
                    self.source_url,
                    "Oxford uses next-open execution after three %b observations; this close-only version earns returns after the signal bar.",
                    band_window=float(self.band_window),
                    trend_window=float(self.trend_window),
                    confirmation_bars=float(self.confirmation_bars),
                ),
            },
        )


class OxfordRSI2PullbackStrategy(WalkForwardStrategy):
    """Oxford 2-period RSI long-only pullback model for equity-index style markets."""

    source_url = "https://oxfordstrat.com/trading-strategies/relative-strength-index-1/"

    def __init__(
        self,
        symbol: str,
        setup_window: int = 200,
        rsi_window: int = 2,
        rsi_entry: float = 10.0,
        exit_window: int = 10,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        self.symbol = symbol
        self.setup_window = setup_window
        self.rsi_window = rsi_window
        self.rsi_entry = rsi_entry
        self.exit_window = exit_window
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["setup_ma"] = prices.rolling(self.setup_window).mean()
        analysis["exit_ma"] = prices.rolling(self.exit_window).mean()
        analysis["rsi"] = _compute_rsi(prices, self.rsi_window)

        positions = np.zeros(len(analysis), dtype=float)
        current = 0.0
        for index, row in enumerate(analysis[["price", "setup_ma", "exit_ma", "rsi"]].itertuples(index=False)):
            price, setup_ma, exit_ma, rsi = row
            if not all(_finite(value) for value in (price, setup_ma, exit_ma, rsi)):
                current = 0.0
            elif current == 0.0 and float(price) > float(setup_ma) and float(rsi) <= self.rsi_entry:
                current = 1.0
            elif current > 0.0 and float(price) > float(exit_ma):
                current = 0.0
            positions[index] = current

        analysis["forecast"] = ((50.0 - analysis["rsi"]) / 25.0).clip(-2.0, 2.0).fillna(0.0)
        analysis["position"] = positions
        return _build_standard_output(
            name=f"{symbol}_oxford_rsi2_pullback",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "oxford_rsi2_pullback",
                **_source_diagnostics(
                    self.source_url,
                    "Oxford tests five equity futures and a long-only bull-market pullback; this implementation does not short downtrends.",
                    setup_window=float(self.setup_window),
                    rsi_window=float(self.rsi_window),
                    rsi_entry=float(self.rsi_entry),
                    exit_window=float(self.exit_window),
                ),
            },
        )


class OxfordWyckoffRangeReversionStrategy(WalkForwardStrategy):
    """Close-only approximation of Wyckoff bear-trap/bull-trap range reversion."""

    source_url = "https://oxfordstrat.com/trading-strategies/richard-wyckoff-mean-reversion-3/"

    def __init__(
        self,
        symbol: str,
        range_window: int = 40,
        exit_window: int = 10,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        self.symbol = symbol
        self.range_window = range_window
        self.exit_window = exit_window
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["range_high"] = prices.shift(1).rolling(self.range_window).max()
        analysis["range_low"] = prices.shift(1).rolling(self.range_window).min()
        analysis["range_mid"] = (analysis["range_high"] + analysis["range_low"]) / 2.0

        positions = np.zeros(len(analysis), dtype=float)
        current = 0.0
        bars_held = 0
        previous_price = np.nan
        for index, row in enumerate(analysis[["price", "range_high", "range_low", "range_mid"]].itertuples(index=False)):
            price, range_high, range_low, range_mid = row
            if not all(_finite(value) for value in (price, range_high, range_low, range_mid)):
                current = 0.0
                bars_held = 0
            elif current == 0.0:
                if _finite(previous_price) and float(previous_price) < float(range_low) <= float(price):
                    current = 1.0
                    bars_held = 0
                elif _finite(previous_price) and float(previous_price) > float(range_high) >= float(price):
                    current = -1.0
                    bars_held = 0
            else:
                bars_held += 1
                if current > 0.0 and (float(price) >= float(range_mid) or bars_held >= self.exit_window):
                    current = 0.0
                    bars_held = 0
                elif current < 0.0 and (float(price) <= float(range_mid) or bars_held >= self.exit_window):
                    current = 0.0
                    bars_held = 0
            positions[index] = current
            previous_price = price

        width = (analysis["range_high"] - analysis["range_low"]).replace(0.0, np.nan)
        analysis["forecast"] = (-(prices - analysis["range_mid"]) / width * 2.0).clip(-2.0, 2.0).fillna(0.0)
        analysis["position"] = positions
        return _build_standard_output(
            name=f"{symbol}_oxford_wyckoff_range_reversion",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "oxford_wyckoff_range_reversion",
                **_source_diagnostics(
                    self.source_url,
                    "Oxford defines traps with swing points and stop levels; this approximation uses closes moving outside and back inside a rolling range.",
                    range_window=float(self.range_window),
                    exit_window=float(self.exit_window),
                ),
            },
        )


class OxfordVolatilityClusteringStrategy(WalkForwardStrategy):
    """DELTA20-adjacent public volatility clustering continuation model."""

    source_url = "https://oxfordstrat.com/data/volatility-clustering-1/"

    def __init__(
        self,
        symbol: str,
        range_window: int = 20,
        move_window: int = 2,
        time_exit_bars: int = 3,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        if move_window < 1:
            raise ValueError("move_window must be positive.")
        self.symbol = symbol
        self.range_window = range_window
        self.move_window = move_window
        self.time_exit_bars = time_exit_bars
        self.transaction_cost_bps = transaction_cost_bps

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        symbol, prices, test_index = _extract_price_series(train_data, test_data, self.symbol)
        analysis = _price_analysis_frame(prices)
        analysis["move"] = prices - prices.shift(self.move_window)
        analysis["abs_move"] = analysis["move"].abs()
        analysis["prior_max_abs_move"] = analysis["abs_move"].shift(1).rolling(self.range_window).max()
        analysis["wide_move"] = analysis["abs_move"] >= analysis["prior_max_abs_move"]

        positions = np.zeros(len(analysis), dtype=float)
        current = 0.0
        bars_held = 0
        for index, row in enumerate(analysis[["move", "prior_max_abs_move", "wide_move"]].itertuples(index=False)):
            move, prior_max_abs_move, wide_move = row
            if not all(_finite(value) for value in (move, prior_max_abs_move)):
                current = 0.0
                bars_held = 0
            elif current == 0.0 and bool(wide_move):
                current = float(np.sign(move))
                bars_held = 0
            elif current != 0.0:
                bars_held += 1
                if bars_held >= self.time_exit_bars:
                    current = 0.0
                    bars_held = 0
            positions[index] = current

        analysis["forecast"] = (analysis["move"] / analysis["prior_max_abs_move"].replace(0.0, np.nan)).clip(-2.0, 2.0).fillna(0.0)
        analysis["position"] = positions
        return _build_standard_output(
            name=f"{symbol}_oxford_volatility_clustering",
            analysis=analysis,
            test_index=test_index,
            transaction_cost_bps=self.transaction_cost_bps,
            diagnostics={
                "symbol": symbol,
                "strategy_type": "oxford_volatility_clustering",
                **_source_diagnostics(
                    self.source_url,
                    "Oxford uses true high/low wide-range patterns and next-open entries; this close-only version uses large close-to-close moves.",
                    range_window=float(self.range_window),
                    move_window=float(self.move_window),
                    time_exit_bars=float(self.time_exit_bars),
                ),
            },
        )
