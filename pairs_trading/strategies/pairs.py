from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.stattools import coint

from ..core.framework import StrategyOutput, WalkForwardStrategy, estimate_half_life, rolling_adf_pvalue


class KalmanPairsStrategy(WalkForwardStrategy):
    """
    Pair strategy with a dynamic hedge ratio and break detection.
    """

    def __init__(
        self,
        ticker1: str,
        ticker2: str,
        entry_z: float = 2.0,
        exit_z: float = 0.35,
        break_window: int = 80,
        break_pvalue: float = 0.20,
        transition_delta: float = 1e-4,
        observation_var: float = 1e-3,
        warmup_bars: int = 80,
        transaction_cost_bps: float = 4.0,
        pair_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.ticker1 = ticker1
        self.ticker2 = ticker2
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.break_window = break_window
        self.break_pvalue = break_pvalue
        self.transition_delta = transition_delta
        self.observation_var = observation_var
        self.warmup_bars = warmup_bars
        self.transaction_cost_bps = transaction_cost_bps
        self.pair_metadata = pair_metadata or {}

    def _run_kalman_filter(self, data: pd.DataFrame) -> pd.DataFrame:
        y = data[self.ticker1].to_numpy()
        x = data[self.ticker2].to_numpy()

        state_mean = np.zeros(2)
        state_cov = np.eye(2)
        transition_cov = self.transition_delta / max(1e-9, 1.0 - self.transition_delta) * np.eye(2)

        intercepts = np.zeros(len(data))
        hedge_ratios = np.zeros(len(data))
        zscores = np.zeros(len(data))
        residual_vars = np.zeros(len(data))

        for index in range(len(data)):
            design = np.array([1.0, x[index]])
            state_cov = state_cov + transition_cov

            y_pred = float(np.dot(design, state_mean))
            error = float(y[index] - y_pred)
            residual_var = float(np.dot(np.dot(design, state_cov), design.T) + self.observation_var)
            residual_var = max(residual_var, 1e-8)

            kalman_gain = np.dot(state_cov, design.T) / residual_var
            state_mean = state_mean + kalman_gain * error
            state_cov = state_cov - np.outer(kalman_gain, np.dot(design, state_cov))

            intercepts[index] = state_mean[0]
            hedge_ratios[index] = state_mean[1]
            zscores[index] = error / np.sqrt(residual_var)
            residual_vars[index] = residual_var

        return pd.DataFrame(
            {
                "intercept": intercepts,
                "hedge_ratio": hedge_ratios,
                "zscore": zscores,
                "residual_var": residual_vars,
            },
            index=data.index,
        )

    def _train_summary(self, train_data: pd.DataFrame) -> dict[str, float]:
        pair = train_data[[self.ticker1, self.ticker2]].dropna()
        if len(pair) < 30:
            return {
                "train_corr": np.nan,
                "train_coint_pvalue": np.nan,
                "train_half_life": np.inf,
                "ols_hedge_ratio": np.nan,
            }

        returns_corr = pair[self.ticker1].pct_change().corr(pair[self.ticker2].pct_change())
        try:
            coint_pvalue = float(coint(pair[self.ticker1], pair[self.ticker2])[1])
        except Exception:
            coint_pvalue = np.nan

        ols_model = sm.OLS(pair[self.ticker1], sm.add_constant(pair[self.ticker2])).fit()
        hedge_ratio = float(ols_model.params.iloc[1])
        spread = pair[self.ticker1] - hedge_ratio * pair[self.ticker2]

        return {
            "train_corr": float(returns_corr) if pd.notna(returns_corr) else np.nan,
            "train_coint_pvalue": coint_pvalue,
            "train_half_life": float(estimate_half_life(spread)),
            "ols_hedge_ratio": hedge_ratio,
        }

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        train_pair = train_data[[self.ticker1, self.ticker2]].dropna()
        test_pair = test_data[[self.ticker1, self.ticker2]].dropna()
        train_summary = self._train_summary(train_pair)
        if train_pair.empty or test_pair.empty:
            empty = pd.DataFrame(index=test_data.index)
            for column in ("signal", "forecast", "position", "cost_estimate", "unit_return", "gross_return"):
                empty[column] = 0.0
            empty["spread_return"] = 0.0
            empty["break_flag"] = True
            return StrategyOutput(
                name=f"{self.ticker1}_{self.ticker2}_kalman",
                frame=empty,
                diagnostics={"pair": f"{self.ticker1}/{self.ticker2}", "status": "insufficient_data"},
            ).validate(extra_columns=("unit_return", "gross_return"))

        warmup_history = train_pair.tail(self.warmup_bars)
        combined = pd.concat([warmup_history, test_pair], axis=0)
        combined = combined[~combined.index.duplicated(keep="last")]

        kalman_frame = self._run_kalman_filter(combined)
        analysis = combined.join(kalman_frame)
        analysis["spread"] = analysis[self.ticker1] - (
            analysis["intercept"] + analysis["hedge_ratio"] * analysis[self.ticker2]
        )
        analysis["adf_pvalue"] = rolling_adf_pvalue(analysis["spread"], window=self.break_window)
        analysis["hedge_instability"] = analysis["hedge_ratio"].diff().abs().rolling(self.break_window).mean()
        analysis["break_flag"] = (
            (analysis["adf_pvalue"] > self.break_pvalue)
            | (analysis["hedge_instability"] > 0.25)
            | (analysis["zscore"].abs() > 6.0)
        ).fillna(False)

        analysis["signal"] = 0.0
        analysis.loc[analysis["zscore"] >= self.entry_z, "signal"] = -1.0
        analysis.loc[analysis["zscore"] <= -self.entry_z, "signal"] = 1.0
        analysis.loc[analysis["break_flag"], "signal"] = 0.0

        positions = np.zeros(len(analysis))
        current_pos = 0.0
        for index in range(len(analysis)):
            zscore = analysis["zscore"].iloc[index]
            broken = bool(analysis["break_flag"].iloc[index])
            if index < self.warmup_bars or pd.isna(zscore):
                positions[index] = 0.0
                continue

            if broken:
                current_pos = 0.0
            elif current_pos == 0.0:
                if zscore >= self.entry_z:
                    current_pos = -1.0
                elif zscore <= -self.entry_z:
                    current_pos = 1.0
            elif current_pos == 1.0 and zscore >= -self.exit_z:
                current_pos = 0.0
            elif current_pos == -1.0 and zscore <= self.exit_z:
                current_pos = 0.0

            positions[index] = current_pos

        analysis["position"] = positions
        analysis["forecast"] = (-analysis["zscore"] / max(self.entry_z, 1e-6)).clip(-2.0, 2.0).fillna(0.0)

        prev_beta = analysis["hedge_ratio"].shift(1).ffill().fillna(train_summary["ols_hedge_ratio"])
        analysis["gross_exposure_per_unit"] = 1.0 + prev_beta.abs()
        analysis["spread_return"] = (
            analysis[self.ticker1].pct_change().fillna(0.0)
            - prev_beta * analysis[self.ticker2].pct_change().fillna(0.0)
        ) / analysis["gross_exposure_per_unit"].replace(0.0, np.nan)
        analysis["spread_return"] = analysis["spread_return"].fillna(0.0)
        analysis["unit_return"] = analysis["spread_return"]
        analysis["gross_return"] = analysis["position"].shift(1).fillna(0.0) * analysis["unit_return"]
        analysis[f"target_weight_{self.ticker1}"] = (
            analysis["position"] / analysis["gross_exposure_per_unit"].replace(0.0, np.nan)
        ).fillna(0.0)
        analysis[f"target_weight_{self.ticker2}"] = (
            -analysis["position"] * prev_beta / analysis["gross_exposure_per_unit"].replace(0.0, np.nan)
        ).fillna(0.0)
        analysis["short_exposure_per_unit"] = np.where(
            analysis["position"] >= 0.0,
            prev_beta.abs(),
            1.0,
        ) / analysis["gross_exposure_per_unit"].replace(0.0, np.nan)
        analysis["short_exposure_per_unit"] = analysis["short_exposure_per_unit"].fillna(0.0)

        entry_turnover = analysis["position"].diff().abs().fillna(analysis["position"].abs())
        hedge_turnover = analysis["hedge_ratio"].diff().abs().fillna(0.0)
        analysis["cost_estimate"] = (
            (entry_turnover * analysis["gross_exposure_per_unit"])
            + hedge_turnover * analysis["position"].shift(1).abs().fillna(0.0)
        ) * (self.transaction_cost_bps / 10_000.0)

        test_frame = analysis.reindex(test_data.index).copy()
        for column in (
            "signal",
            "forecast",
            "position",
            "cost_estimate",
            "unit_return",
            "spread_return",
            "gross_return",
            "gross_exposure_per_unit",
            "short_exposure_per_unit",
            "hedge_ratio",
            "zscore",
            "adf_pvalue",
            "hedge_instability",
            f"target_weight_{self.ticker1}",
            f"target_weight_{self.ticker2}",
        ):
            test_frame[column] = test_frame[column].fillna(0.0)
        test_frame["break_flag"] = test_frame["break_flag"].fillna(True)

        diagnostics = train_summary
        diagnostics.update(
            {
                "pair": f"{self.ticker1}/{self.ticker2}",
                "ticker1": self.ticker1,
                "ticker2": self.ticker2,
                "entry_z": self.entry_z,
                "exit_z": self.exit_z,
                "break_window": self.break_window,
                "break_pvalue": self.break_pvalue,
                **self.pair_metadata,
            }
        )

        return StrategyOutput(
            name=f"{self.ticker1}_{self.ticker2}_kalman",
            frame=test_frame,
            diagnostics=diagnostics,
        ).validate(extra_columns=("unit_return", "gross_return"))
