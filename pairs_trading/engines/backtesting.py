from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from ..core.framework import StrategyOutput, WalkForwardStrategy
from ..core.portfolio import PortfolioManager
from ..data.market import CachedParquetProvider, YahooFinanceProvider
from ..pipelines import SectorStatArbPipeline, StatArbConfig
from ..research import PairScreenConfig
from .broker import BrokerAdapter, BrokerConfig, SimulatedBroker
from .execution import ExecutionConfig
from .risk import RiskConfig
from .validation import ValidationConfig, build_validation_report, build_walk_forward_boundaries


@dataclass(frozen=True)
class CostModel:
    commission_bps: float = 1.0
    spread_bps: float = 1.0
    slippage_bps: float = 1.0
    market_impact_bps: float = 0.5
    borrow_bps_annual: float = 50.0
    funding_bps_annual: float = 0.0
    delay_bars: int = 0


@dataclass(frozen=True)
class WalkForwardConfig:
    train_bars: int
    test_bars: int
    step_bars: int | None = None
    bars_per_year: int = 252
    purge_bars: int = 0
    embargo_bars: int = 0


@dataclass
class ExperimentResult:
    experiment_id: str
    summary: dict[str, Any]
    fold_metrics: pd.DataFrame
    equity_curve: pd.DataFrame
    diagnostics: list[dict[str, Any]]
    artifact_dir: Path
    validation: dict[str, Any] = field(default_factory=dict)


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [json_ready(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    if isinstance(value, pd.Series):
        return value.to_dict()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    return value


class WalkForwardBacktester:
    def __init__(
        self,
        strategy: WalkForwardStrategy,
        prices: pd.DataFrame,
        config: WalkForwardConfig,
        cost_model: CostModel,
        broker: BrokerAdapter | None = None,
        validation_config: ValidationConfig = ValidationConfig(),
        experiment_root: str | Path = "artifacts/experiments",
    ) -> None:
        self.strategy = strategy
        self.prices = prices.sort_index()
        self.config = config
        self.cost_model = cost_model
        self.broker = broker or SimulatedBroker(
            BrokerConfig(
                risk=RiskConfig(max_gross_leverage=10.0, max_net_leverage=10.0),
                execution=ExecutionConfig(
                    commission_bps=cost_model.commission_bps,
                    spread_bps=cost_model.spread_bps,
                    slippage_bps=cost_model.slippage_bps,
                    market_impact_bps=cost_model.market_impact_bps,
                    borrow_bps_annual=cost_model.borrow_bps_annual,
                    funding_bps_annual=cost_model.funding_bps_annual,
                    delay_bars=cost_model.delay_bars,
                ),
            )
        )
        self.validation_config = validation_config
        self.experiment_root = Path(experiment_root)
        self.experiment_root.mkdir(parents=True, exist_ok=True)

    def _fold_boundaries(self):
        return build_walk_forward_boundaries(
            total_bars=len(self.prices),
            train_bars=self.config.train_bars,
            test_bars=self.config.test_bars,
            step_bars=self.config.step_bars,
            purge_bars=self.config.purge_bars,
            embargo_bars=self.config.embargo_bars,
        )

    def _compute_metrics(self, frame: pd.DataFrame) -> dict[str, float]:
        if frame.empty:
            return {
                "bars": 0,
                "total_return": 0.0,
                "annualized_return": 0.0,
                "annualized_vol": 0.0,
                "sharpe": 0.0,
                "max_drawdown": 0.0,
                "avg_turnover": 0.0,
                "hit_rate": 0.0,
            }

        net_returns = frame["net_return"].fillna(0.0)
        equity_curve = (1.0 + net_returns).cumprod()
        total_return = float(equity_curve.iloc[-1] - 1.0)
        bars = len(frame)

        mean_return = float(net_returns.mean())
        vol = float(net_returns.std(ddof=0))
        annualized_return = float((1.0 + total_return) ** (self.config.bars_per_year / max(bars, 1)) - 1.0)
        annualized_vol = float(vol * np.sqrt(self.config.bars_per_year))
        sharpe = float(mean_return / vol * np.sqrt(self.config.bars_per_year)) if vol > 0 else 0.0

        rolling_max = equity_curve.cummax()
        drawdown = equity_curve / rolling_max - 1.0
        max_drawdown = float(drawdown.min())

        return {
            "bars": float(bars),
            "total_return": total_return,
            "annualized_return": annualized_return,
            "annualized_vol": annualized_vol,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "avg_turnover": float(frame["turnover"].mean()),
            "hit_rate": float((net_returns > 0).mean()),
        }

    def _save_artifacts(
        self,
        experiment_id: str,
        summary: dict[str, Any],
        fold_metrics: pd.DataFrame,
        equity_curve: pd.DataFrame,
        diagnostics: list[dict[str, Any]],
        validation: dict[str, Any],
    ) -> Path:
        artifact_dir = self.experiment_root / experiment_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        with (artifact_dir / "summary.json").open("w", encoding="utf-8") as handle:
            json.dump(json_ready(summary), handle, indent=2)
        with (artifact_dir / "diagnostics.json").open("w", encoding="utf-8") as handle:
            json.dump(json_ready(diagnostics), handle, indent=2)
        with (artifact_dir / "validation.json").open("w", encoding="utf-8") as handle:
            json.dump(json_ready(validation), handle, indent=2)

        fold_metrics.to_parquet(artifact_dir / "fold_metrics.parquet")
        equity_curve.to_parquet(artifact_dir / "equity_curve.parquet")
        return artifact_dir

    def run(
        self,
        experiment_name: str | None = None,
        validation_trial_returns: pd.DataFrame | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> ExperimentResult:
        fold_boundaries = self._fold_boundaries()
        if not fold_boundaries:
            raise ValueError("Not enough data to generate any walk-forward folds.")

        experiment_label = experiment_name or getattr(self.strategy, "name", "walk_forward")
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        experiment_id = f"{timestamp}_{experiment_label}"

        fold_frames: list[pd.DataFrame] = []
        fold_metrics: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []

        for boundary in fold_boundaries:
            train_data = self.prices.iloc[boundary.train_start : boundary.train_end].copy()
            test_data = self.prices.iloc[boundary.test_start : boundary.test_end].copy()

            output = self.strategy.run_fold(train_data=train_data, test_data=test_data)
            evaluated_output = self.broker.process(output, bars_per_year=self.config.bars_per_year)
            evaluated = evaluated_output.frame.copy()
            evaluated["fold"] = boundary.fold
            fold_frames.append(evaluated)

            metrics = self._compute_metrics(evaluated)
            metrics.update(
                {
                    "fold": boundary.fold,
                    "train_start": train_data.index[0],
                    "train_end": train_data.index[-1],
                    "test_start": test_data.index[0],
                    "test_end": test_data.index[-1],
                }
            )
            fold_metrics.append(metrics)
            diagnostics.append({"fold": boundary.fold, "diagnostics": evaluated_output.diagnostics})
            if progress_callback is not None:
                progress_callback(
                    {
                        "completed_folds": len(fold_frames),
                        "total_folds": len(fold_boundaries),
                        "latest_fold": boundary.fold,
                        "equity_curve": pd.concat(fold_frames, axis=0).sort_index(),
                        "fold_metrics": pd.DataFrame(fold_metrics),
                        "diagnostics": list(diagnostics),
                    }
                )

        combined = pd.concat(fold_frames, axis=0)
        overall_summary = self._compute_metrics(combined)
        validation_summary = build_validation_report(
            returns=combined["net_return"],
            bars_per_year=self.config.bars_per_year,
            trial_returns=validation_trial_returns,
            pbo_partitions=self.validation_config.pbo_partitions,
        )
        overall_summary.update(
            {
                "experiment_id": experiment_id,
                "folds": len(fold_frames),
                "strategy": experiment_label,
                "cost_model": asdict(self.cost_model),
                "walk_forward_config": asdict(self.config),
                "psr": validation_summary["psr"],
                "dsr": validation_summary["dsr"],
                "pbo": validation_summary["pbo"],
            }
        )

        fold_metrics_frame = pd.DataFrame(fold_metrics)
        artifact_dir = self._save_artifacts(
            experiment_id=experiment_id,
            summary=overall_summary,
            fold_metrics=fold_metrics_frame,
            equity_curve=combined,
            diagnostics=diagnostics,
            validation=validation_summary,
        )

        return ExperimentResult(
            experiment_id=experiment_id,
            summary=overall_summary,
            fold_metrics=fold_metrics_frame,
            equity_curve=combined,
            diagnostics=diagnostics,
            validation=validation_summary,
            artifact_dir=artifact_dir,
        )


def run_trial_grid(
    *,
    prices: pd.DataFrame,
    strategy_factories: Mapping[str, WalkForwardStrategy],
    config: WalkForwardConfig,
    cost_model: CostModel,
    broker: BrokerAdapter | None = None,
    experiment_root: str | Path = "artifacts/experiments/trials",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    trial_returns: dict[str, pd.Series] = {}
    trial_metrics: list[dict[str, Any]] = []

    for trial_name, strategy in strategy_factories.items():
        result = WalkForwardBacktester(
            strategy=strategy,
            prices=prices,
            config=config,
            cost_model=cost_model,
            broker=broker,
            experiment_root=Path(experiment_root),
        ).run(experiment_name=trial_name)
        trial_returns[trial_name] = result.equity_curve["net_return"].rename(trial_name)
        metrics = dict(result.summary)
        metrics["trial_name"] = trial_name
        trial_metrics.append(metrics)

    return pd.DataFrame(trial_returns).sort_index(), pd.DataFrame(trial_metrics)


def main() -> None:
    sector_map = {
        "KO": "Beverages",
        "PEP": "Beverages",
        "KDP": "Beverages",
        "XOM": "Energy",
        "CVX": "Energy",
        "COP": "Energy",
        "JPM": "Banks",
        "BAC": "Banks",
        "WFC": "Banks",
        "C": "Banks",
    }

    provider = CachedParquetProvider(
        upstream=YahooFinanceProvider(),
        cache_dir="data/cache",
    )
    prices = provider.get_close_prices(
        symbols=list(sector_map.keys()),
        start="2013-01-01",
        end="2026-04-15",
        interval="1d",
    )

    pipeline = SectorStatArbPipeline(
        sector_map=sector_map,
        portfolio_manager=PortfolioManager(
            max_leverage=1.5,
            risk_per_trade=0.08,
            volatility_window=20,
            max_strategy_weight=0.60,
        ),
        screen_config=PairScreenConfig(
            min_history=252,
            correlation_floor=0.60,
            coint_pvalue_threshold=0.10,
            min_half_life=2.0,
            max_half_life=60.0,
            target_half_life=15.0,
        ),
        stat_arb_config=StatArbConfig(
            top_n_pairs=3,
            entry_z=2.0,
            exit_z=0.35,
            break_window=80,
            break_pvalue=0.20,
            transaction_cost_bps=4.0,
        ),
        name="serious_stat_arb",
    )

    backtester = WalkForwardBacktester(
        strategy=pipeline,
        prices=prices,
        config=WalkForwardConfig(
            train_bars=504,
            test_bars=63,
            step_bars=63,
            bars_per_year=252,
        ),
        cost_model=CostModel(
            commission_bps=0.5,
            spread_bps=1.0,
            slippage_bps=0.5,
            borrow_bps_annual=40.0,
        ),
    )

    result = backtester.run(experiment_name="serious_stat_arb")
    print(json.dumps(json_ready(result.summary), indent=2))
    print(f"Artifacts saved to: {result.artifact_dir}")


if __name__ == "__main__":
    main()
