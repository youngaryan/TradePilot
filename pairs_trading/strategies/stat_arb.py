from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm

from ..core.framework import StrategyOutput, WalkForwardStrategy


class SectorResidualMeanReversionStrategy(WalkForwardStrategy):
    def __init__(
        self,
        symbol: str,
        sector_symbols: list[str],
        lookback: int = 60,
        entry_z: float = 1.5,
        exit_z: float = 0.35,
        transaction_cost_bps: float = 2.0,
    ) -> None:
        if len(sector_symbols) < 2:
            raise ValueError("Sector residual strategy needs at least two symbols in the sector universe.")
        self.symbol = symbol
        self.sector_symbols = list(dict.fromkeys(sector_symbols))
        self.lookback = lookback
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.transaction_cost_bps = transaction_cost_bps

    def _flat_output(self, index: pd.Index, reason: str) -> StrategyOutput:
        frame = pd.DataFrame(index=index)
        for column in (
            "signal",
            "forecast",
            "position",
            "cost_estimate",
            "unit_return",
            "gross_return",
            "turnover",
            "gross_exposure_per_unit",
            "short_exposure_per_unit",
            "residual_zscore",
        ):
            frame[column] = 0.0
        return StrategyOutput(
            name=f"{self.symbol}_residual_stat_arb",
            frame=frame,
            diagnostics={"symbol": self.symbol, "status": reason, "strategy_type": "sector_residual_mean_reversion"},
        ).validate(extra_columns=("unit_return", "gross_return"))

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        available_symbols = [symbol for symbol in self.sector_symbols if symbol in train_data.columns and symbol in test_data.columns]
        if self.symbol not in available_symbols or len(available_symbols) < 2:
            return self._flat_output(test_data.index, reason="missing_sector_columns")

        train_sector = train_data[available_symbols].dropna()
        test_sector = test_data[available_symbols].dropna()
        if len(train_sector) < max(self.lookback, 40) or test_sector.empty:
            return self._flat_output(test_data.index, reason="insufficient_history")

        combined = pd.concat([train_sector, test_sector], axis=0)
        combined = combined[~combined.index.duplicated(keep="last")]
        returns = combined.pct_change().fillna(0.0)

        benchmark_symbols = [symbol for symbol in available_symbols if symbol != self.symbol]
        benchmark_return = returns[benchmark_symbols].mean(axis=1)
        regression_frame = pd.DataFrame(
            {
                "asset": returns.loc[train_sector.index, self.symbol],
                "benchmark": benchmark_return.loc[train_sector.index],
            }
        ).dropna()
        if len(regression_frame) < 30 or regression_frame["benchmark"].abs().sum() == 0.0:
            return self._flat_output(test_data.index, reason="degenerate_regression")

        model = sm.OLS(regression_frame["asset"], sm.add_constant(regression_frame["benchmark"])).fit()
        alpha = float(model.params.iloc[0])
        beta = float(model.params.iloc[1])

        analysis = pd.DataFrame(index=combined.index)
        analysis["unit_asset_return"] = returns[self.symbol].fillna(0.0)
        analysis["benchmark_return"] = benchmark_return.fillna(0.0)
        analysis["residual_return"] = analysis["unit_asset_return"] - alpha - beta * analysis["benchmark_return"]
        analysis["residual_index"] = analysis["residual_return"].cumsum()
        rolling_mean = analysis["residual_index"].rolling(self.lookback).mean()
        rolling_std = analysis["residual_index"].rolling(self.lookback).std().replace(0.0, np.nan)
        analysis["residual_zscore"] = ((analysis["residual_index"] - rolling_mean) / rolling_std).fillna(0.0)
        analysis["forecast"] = (-analysis["residual_zscore"] / max(self.entry_z, 1e-6)).clip(-2.0, 2.0)

        positions = np.zeros(len(analysis))
        current_position = 0.0
        for index in range(len(analysis)):
            zscore = float(analysis["residual_zscore"].iloc[index])
            if index < len(train_sector.index) or np.isnan(zscore):
                positions[index] = 0.0
                continue

            if current_position == 0.0:
                if zscore >= self.entry_z:
                    current_position = -1.0
                elif zscore <= -self.entry_z:
                    current_position = 1.0
            elif current_position > 0.0 and zscore >= -self.exit_z:
                current_position = 0.0
            elif current_position < 0.0 and zscore <= self.exit_z:
                current_position = 0.0
            positions[index] = current_position

        analysis["position"] = positions
        gross_exposure_per_unit = 1.0 + abs(beta)
        analysis["gross_exposure_per_unit"] = gross_exposure_per_unit
        analysis["unit_return"] = analysis["residual_return"] / max(gross_exposure_per_unit, 1e-6)
        analysis["gross_return"] = analysis["position"].shift(1).fillna(0.0) * analysis["unit_return"]
        analysis[f"target_weight_{self.symbol}"] = analysis["position"] / max(gross_exposure_per_unit, 1e-6)
        hedge_weight = -analysis["position"] * beta / max(gross_exposure_per_unit, 1e-6)
        for benchmark_symbol in benchmark_symbols:
            analysis[f"target_weight_{benchmark_symbol}"] = hedge_weight / max(len(benchmark_symbols), 1)
        analysis["short_exposure_per_unit"] = np.where(
            analysis["position"] >= 0.0,
            abs(beta) / max(gross_exposure_per_unit, 1e-6),
            1.0 / max(gross_exposure_per_unit, 1e-6),
        )
        analysis["turnover"] = analysis["position"].diff().abs().fillna(analysis["position"].abs())
        analysis["cost_estimate"] = (
            analysis["turnover"] * gross_exposure_per_unit * (self.transaction_cost_bps / 10_000.0)
        )
        analysis["signal"] = np.sign(analysis["position"]).replace({-0.0: 0.0}).fillna(0.0)

        test_frame = analysis.reindex(test_data.index).copy()
        for column in (
            "signal",
            "forecast",
            "position",
            "cost_estimate",
            "unit_return",
            "gross_return",
            "turnover",
            "gross_exposure_per_unit",
            "short_exposure_per_unit",
            "residual_zscore",
            f"target_weight_{self.symbol}",
            *(f"target_weight_{benchmark_symbol}" for benchmark_symbol in benchmark_symbols),
        ):
            test_frame[column] = test_frame[column].fillna(0.0)

        return StrategyOutput(
            name=f"{self.symbol}_residual_stat_arb",
            frame=test_frame,
            diagnostics={
                "symbol": self.symbol,
                "strategy_type": "sector_residual_mean_reversion",
                "alpha": alpha,
                "beta": beta,
                "lookback": float(self.lookback),
                "entry_z": float(self.entry_z),
                "exit_z": float(self.exit_z),
            },
        ).validate(extra_columns=("unit_return", "gross_return"))
