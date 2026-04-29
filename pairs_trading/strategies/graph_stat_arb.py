from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..core.framework import StrategyOutput, WalkForwardStrategy


@dataclass(frozen=True)
class GraphClusterTradingConfig:
    residual_lookback: int = 60
    entry_z: float = 1.25
    top_n_per_side: int = 2
    transaction_cost_bps: float = 3.0


class GraphClusterResidualStrategy(WalkForwardStrategy):
    """Market-neutral laggard/leader trading inside a correlated graph cluster."""

    def __init__(
        self,
        cluster_id: str,
        symbols: list[str],
        config: GraphClusterTradingConfig = GraphClusterTradingConfig(),
        cluster_metadata: dict[str, object] | None = None,
    ) -> None:
        if len(symbols) < 3:
            raise ValueError("Graph cluster residual strategy needs at least three symbols.")
        self.cluster_id = cluster_id
        self.symbols = list(dict.fromkeys(symbols))
        self.config = config
        self.cluster_metadata = cluster_metadata or {}

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
            "cluster_dispersion",
        ):
            frame[column] = 0.0
        return StrategyOutput(
            name=f"graph_cluster_{self.cluster_id}",
            frame=frame,
            diagnostics={
                "strategy_type": "graph_cluster_residual",
                "cluster_id": self.cluster_id,
                "symbols": self.symbols,
                "status": reason,
            },
        ).validate(extra_columns=("unit_return", "gross_return"))

    def _build_internal_weights(self, zscores: pd.DataFrame) -> pd.DataFrame:
        weights = pd.DataFrame(0.0, index=zscores.index, columns=zscores.columns)
        top_n = max(1, int(self.config.top_n_per_side))

        for timestamp, row in zscores.iterrows():
            clean = row.replace([np.inf, -np.inf], np.nan).dropna()
            if len(clean) < 3:
                continue

            leaders = clean[clean >= self.config.entry_z].sort_values(ascending=False).head(top_n)
            laggards = clean[clean <= -self.config.entry_z].sort_values(ascending=True).head(top_n)
            if leaders.empty or laggards.empty:
                continue

            raw = pd.Series(0.0, index=clean.index)
            raw.loc[laggards.index] = -laggards.abs()
            raw.loc[leaders.index] = -leaders.abs()
            raw.loc[laggards.index] = laggards.abs()

            positive = raw.clip(lower=0.0)
            negative = raw.clip(upper=0.0)
            positive_sum = float(positive.sum())
            negative_sum = float(abs(negative.sum()))
            if positive_sum <= 0.0 or negative_sum <= 0.0:
                continue
            normalized = positive / positive_sum * 0.5 + negative / negative_sum * 0.5
            weights.loc[timestamp, normalized.index] = normalized

        return weights

    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        available_symbols = [symbol for symbol in self.symbols if symbol in train_data.columns and symbol in test_data.columns]
        if len(available_symbols) < 3:
            return self._flat_output(test_data.index, reason="missing_cluster_columns")

        train_cluster = train_data[available_symbols].dropna()
        test_cluster = test_data[available_symbols].dropna()
        min_history = max(self.config.residual_lookback, 40)
        if len(train_cluster) < min_history or test_cluster.empty:
            return self._flat_output(test_data.index, reason="insufficient_history")

        combined = pd.concat([train_cluster, test_cluster], axis=0)
        combined = combined[~combined.index.duplicated(keep="last")].ffill().dropna()
        returns = combined.pct_change().fillna(0.0)
        log_prices = np.log(combined.clip(lower=1e-9))
        cluster_mean = log_prices.mean(axis=1)
        residuals = log_prices.sub(cluster_mean, axis=0)
        rolling_mean = residuals.rolling(self.config.residual_lookback).mean()
        rolling_std = residuals.rolling(self.config.residual_lookback).std(ddof=0).replace(0.0, np.nan)
        zscores = ((residuals - rolling_mean) / rolling_std).replace([np.inf, -np.inf], 0.0).fillna(0.0)

        internal_weights = self._build_internal_weights(zscores)
        internal_weights.loc[train_cluster.index, :] = 0.0
        lagged_weights = internal_weights.shift(1).fillna(0.0)

        analysis = pd.DataFrame(index=combined.index)
        analysis["unit_return"] = (lagged_weights * returns).sum(axis=1)
        analysis["gross_return"] = analysis["unit_return"]
        analysis["turnover"] = internal_weights.diff().abs().fillna(internal_weights.abs()).sum(axis=1)
        analysis["cost_estimate"] = analysis["turnover"] * (self.config.transaction_cost_bps / 10_000.0)
        analysis["position"] = internal_weights.abs().sum(axis=1).clip(0.0, 1.0)
        analysis["forecast"] = zscores.abs().mean(axis=1).clip(0.0, 2.0)
        analysis["signal"] = (analysis["position"] > 0.0).astype(float)
        analysis["short_exposure_per_unit"] = internal_weights.clip(upper=0.0).abs().sum(axis=1)
        analysis["gross_exposure_per_unit"] = internal_weights.abs().sum(axis=1).replace(0.0, 1.0)
        analysis["cluster_dispersion"] = zscores.std(axis=1).fillna(0.0)

        for symbol in available_symbols:
            analysis[f"cluster_weight_{symbol}"] = internal_weights[symbol]
            analysis[f"cluster_zscore_{symbol}"] = zscores[symbol]

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
            "cluster_dispersion",
        ):
            test_frame[column] = test_frame[column].fillna(0.0)

        diagnostics = {
            "strategy_type": "graph_cluster_residual",
            "cluster_id": self.cluster_id,
            "symbols": available_symbols,
            "config": asdict(self.config),
            "active_days": int((test_frame["position"] > 0.0).sum()),
            **self.cluster_metadata,
        }
        return StrategyOutput(
            name=f"graph_cluster_{self.cluster_id}",
            frame=test_frame,
            diagnostics=diagnostics,
        ).validate(extra_columns=("unit_return", "gross_return"))
