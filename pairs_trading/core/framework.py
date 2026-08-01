from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller


@dataclass
class StrategyOutput:
    name: str
    frame: pd.DataFrame
    diagnostics: dict[str, Any] = field(default_factory=dict)

    REQUIRED_COLUMNS = ("signal", "forecast", "position", "cost_estimate")

    def validate(self, extra_columns: Iterable[str] = ()) -> "StrategyOutput":
        missing = [column for column in (*self.REQUIRED_COLUMNS, *extra_columns) if column not in self.frame.columns]
        if missing:
            raise ValueError(f"{self.name} is missing required output columns: {missing}")
        self.frame = self.frame.sort_index()
        return self


class WalkForwardStrategy(ABC):
    @abstractmethod
    def run_fold(self, train_data: pd.DataFrame, test_data: pd.DataFrame) -> StrategyOutput:
        """Fit on the train window and emit a standardized result for the test window."""


def estimate_half_life(spread: pd.Series) -> float:
    clean = spread.dropna()
    if len(clean) < 20:
        return float("inf")

    lagged = clean.shift(1)
    delta = clean.diff()
    regression_frame = pd.concat([lagged, delta], axis=1).dropna()
    if regression_frame.empty:
        return float("inf")

    x = regression_frame.iloc[:, 0].to_numpy()
    y = regression_frame.iloc[:, 1].to_numpy()
    beta = np.polyfit(x, y, 1)[0]
    if beta >= 0:
        return float("inf")

    return float(max(1.0, -np.log(2.0) / beta))


def rolling_adf_pvalue(series: pd.Series, window: int) -> pd.Series:
    values: list[float] = []
    for index in range(len(series)):
        if index + 1 < window:
            values.append(np.nan)
            continue

        sample = series.iloc[index + 1 - window : index + 1].dropna()
        if sample.nunique() < 5:
            values.append(np.nan)
            continue

        try:
            values.append(float(adfuller(sample, maxlag=1, regression="c", autolag=None)[1]))
        except Exception:
            values.append(np.nan)

    return pd.Series(values, index=series.index)
