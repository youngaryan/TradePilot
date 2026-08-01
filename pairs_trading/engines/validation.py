from __future__ import annotations

from dataclasses import dataclass
import itertools
import math
from typing import Callable

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, norm, skew


MetricFunction = Callable[[pd.Series, int], float]


@dataclass(frozen=True)
class ValidationConfig:
    purge_bars: int = 0
    embargo_bars: int = 0
    pbo_partitions: int = 8


@dataclass(frozen=True)
class FoldBoundary:
    fold: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def annualized_sharpe(returns: pd.Series, bars_per_year: int = 252) -> float:
    clean = pd.Series(returns, copy=False).dropna()
    if clean.empty:
        return 0.0
    volatility = float(clean.std(ddof=0))
    if volatility <= 0:
        return 0.0
    return float(clean.mean() / volatility * math.sqrt(bars_per_year))


def probabilistic_sharpe_ratio(
    observed_sharpe: float,
    benchmark_sharpe: float,
    sample_size: int,
    skewness: float = 0.0,
    kurtosis_value: float = 3.0,
) -> float:
    if sample_size <= 1:
        return 0.5

    denominator = math.sqrt(
        max(
            1e-12,
            (1.0 - skewness * observed_sharpe + ((kurtosis_value - 1.0) / 4.0) * observed_sharpe**2)
            / max(sample_size - 1, 1),
        )
    )
    z_score = (observed_sharpe - benchmark_sharpe) / denominator
    return float(norm.cdf(z_score))


def expected_maximum_sharpe(trial_sharpes: pd.Series) -> float:
    clean = pd.Series(trial_sharpes, copy=False).dropna()
    if clean.empty:
        return 0.0
    if len(clean) == 1:
        return float(clean.iloc[0])

    gamma = 0.5772156649
    mean_value = float(clean.mean())
    std_value = float(clean.std(ddof=0))
    if std_value <= 0:
        return mean_value

    trials = len(clean)
    quantile_a = float(norm.ppf(1.0 - 1.0 / trials))
    quantile_b = float(norm.ppf(1.0 - 1.0 / (trials * math.e)))
    return float(mean_value + std_value * ((1.0 - gamma) * quantile_a + gamma * quantile_b))


def deflated_sharpe_ratio(
    returns: pd.Series,
    *,
    bars_per_year: int = 252,
    trial_sharpes: pd.Series | None = None,
) -> dict[str, float]:
    clean = pd.Series(returns, copy=False).dropna()
    if clean.empty:
        return {
            "sharpe": 0.0,
            "psr": 0.5,
            "dsr": 0.5,
            "benchmark_sharpe": 0.0,
            "sample_size": 0.0,
            "skew": 0.0,
            "kurtosis": 3.0,
            "trial_count": 0.0,
        }

    sample_size = len(clean)
    observed_sharpe = annualized_sharpe(clean, bars_per_year=bars_per_year)
    skewness = float(skew(clean, bias=False, nan_policy="omit")) if sample_size > 2 else 0.0
    kurtosis_value = float(kurtosis(clean, fisher=False, bias=False, nan_policy="omit")) if sample_size > 3 else 3.0
    if not np.isfinite(skewness):
        skewness = 0.0
    if not np.isfinite(kurtosis_value):
        kurtosis_value = 3.0

    psr = probabilistic_sharpe_ratio(
        observed_sharpe=observed_sharpe,
        benchmark_sharpe=0.0,
        sample_size=sample_size,
        skewness=skewness,
        kurtosis_value=kurtosis_value,
    )

    trial_series = None if trial_sharpes is None else pd.Series(trial_sharpes, copy=False).dropna()
    benchmark_sharpe = expected_maximum_sharpe(trial_series) if trial_series is not None and not trial_series.empty else 0.0
    dsr = probabilistic_sharpe_ratio(
        observed_sharpe=observed_sharpe,
        benchmark_sharpe=benchmark_sharpe,
        sample_size=sample_size,
        skewness=skewness,
        kurtosis_value=kurtosis_value,
    )

    return {
        "sharpe": float(observed_sharpe),
        "psr": float(psr),
        "dsr": float(dsr),
        "benchmark_sharpe": float(benchmark_sharpe),
        "sample_size": float(sample_size),
        "skew": float(skewness),
        "kurtosis": float(kurtosis_value),
        "trial_count": float(0 if trial_series is None else len(trial_series)),
    }


def build_walk_forward_boundaries(
    total_bars: int,
    train_bars: int,
    test_bars: int,
    *,
    step_bars: int | None = None,
    purge_bars: int = 0,
    embargo_bars: int = 0,
) -> list[FoldBoundary]:
    if total_bars <= 0 or train_bars <= 0 or test_bars <= 0:
        return []

    step = step_bars or test_bars
    boundaries: list[FoldBoundary] = []
    raw_test_start = train_bars + purge_bars
    fold_number = 1

    while raw_test_start + test_bars <= total_bars:
        train_end = raw_test_start - purge_bars
        train_start = max(0, train_end - train_bars)
        if train_end > train_start:
            boundaries.append(
                FoldBoundary(
                    fold=fold_number,
                    train_start=train_start,
                    train_end=train_end,
                    test_start=raw_test_start,
                    test_end=raw_test_start + test_bars,
                )
            )
            fold_number += 1
        raw_test_start += step + embargo_bars

    return boundaries


def probability_of_backtest_overfitting(
    trial_returns: pd.DataFrame,
    *,
    bars_per_year: int = 252,
    partitions: int = 8,
) -> dict[str, float | list[float] | dict[str, int]] | None:
    matrix = trial_returns.copy()
    matrix = matrix.dropna(axis=1, how="all")
    if matrix.empty or matrix.shape[1] < 2:
        return None
    if partitions < 2 or partitions % 2 != 0 or len(matrix) < partitions * 2:
        return None

    blocks = [pd.Index(block) for block in np.array_split(matrix.index.to_numpy(), partitions) if len(block) > 0]
    if len(blocks) != partitions:
        return None

    half = partitions // 2
    lambdas: list[float] = []
    relative_ranks: list[float] = []
    selected_frequency: dict[str, int] = {}

    for train_blocks in itertools.combinations(range(partitions), half):
        train_index = blocks[train_blocks[0]]
        for block in train_blocks[1:]:
            train_index = train_index.append(blocks[block])

        test_blocks = [block for block in range(partitions) if block not in train_blocks]
        test_index = blocks[test_blocks[0]]
        for block in test_blocks[1:]:
            test_index = test_index.append(blocks[block])

        train_metrics = matrix.loc[train_index].apply(annualized_sharpe, axis=0, bars_per_year=bars_per_year)
        test_metrics = matrix.loc[test_index].apply(annualized_sharpe, axis=0, bars_per_year=bars_per_year)
        if train_metrics.dropna().empty or test_metrics.dropna().empty:
            continue

        selected_trial = str(train_metrics.idxmax())
        selected_frequency[selected_trial] = selected_frequency.get(selected_trial, 0) + 1

        ordered = test_metrics.sort_values()
        ranks = ordered.rank(method="average", pct=True)
        relative_rank = float(ranks.loc[selected_trial]) if selected_trial in ranks.index else 0.0
        relative_rank = float(np.clip(relative_rank, 1e-6, 1.0 - 1e-6))
        relative_ranks.append(relative_rank)
        lambdas.append(float(math.log(relative_rank / (1.0 - relative_rank))))

    if not lambdas:
        return None

    lambda_series = pd.Series(lambdas)
    rank_series = pd.Series(relative_ranks)
    return {
        "pbo": float((lambda_series <= 0.0).mean()),
        "median_lambda": float(lambda_series.median()),
        "mean_relative_rank": float(rank_series.mean()),
        "lambda_logit": [float(value) for value in lambdas],
        "selected_frequency": selected_frequency,
    }


def build_validation_report(
    returns: pd.Series,
    *,
    bars_per_year: int = 252,
    trial_returns: pd.DataFrame | None = None,
    trial_sharpes: pd.Series | None = None,
    pbo_partitions: int = 8,
) -> dict[str, float | list[float] | dict[str, int] | None]:
    if trial_sharpes is None and trial_returns is not None and not trial_returns.empty:
        trial_sharpes = trial_returns.apply(annualized_sharpe, axis=0, bars_per_year=bars_per_year)

    dsr_report = deflated_sharpe_ratio(
        returns=returns,
        bars_per_year=bars_per_year,
        trial_sharpes=trial_sharpes,
    )
    pbo_report = None
    if trial_returns is not None and not trial_returns.empty:
        pbo_report = probability_of_backtest_overfitting(
            trial_returns=trial_returns,
            bars_per_year=bars_per_year,
            partitions=pbo_partitions,
        )

    report: dict[str, float | list[float] | dict[str, int] | None] = {
        "sharpe": dsr_report["sharpe"],
        "psr": dsr_report["psr"],
        "dsr": dsr_report["dsr"],
        "benchmark_sharpe": dsr_report["benchmark_sharpe"],
        "trial_count": dsr_report["trial_count"],
        "sample_size": dsr_report["sample_size"],
        "pbo": None if pbo_report is None else float(pbo_report["pbo"]),
        "pbo_detail": pbo_report,
    }
    return report
