from __future__ import annotations

from pathlib import Path
import shutil

import numpy as np
import pandas as pd


def synthetic_prices_and_sector_map() -> tuple[pd.DataFrame, dict[str, str]]:
    rng = np.random.default_rng(7)
    index = pd.date_range("2020-01-01", periods=900, freq="B")

    base_a = 100 + np.cumsum(rng.normal(0, 0.4, len(index)))
    base_b = 80 + np.cumsum(rng.normal(0, 0.35, len(index)))
    base_c = 60 + np.cumsum(rng.normal(0, 0.3, len(index)))

    prices = pd.DataFrame(
        {
            "A1": base_a + rng.normal(0, 0.4, len(index)),
            "A2": 5 + 0.95 * base_a + rng.normal(0, 0.5, len(index)),
            "A3": 10 + 1.05 * base_a + rng.normal(0, 0.7, len(index)),
            "B1": base_b + rng.normal(0, 0.3, len(index)),
            "B2": 3 + 1.02 * base_b + rng.normal(0, 0.45, len(index)),
            "C1": base_c + rng.normal(0, 1.8, len(index)),
        },
        index=index,
    ).abs() + 5

    sector_map = {
        "A1": "SectorA",
        "A2": "SectorA",
        "A3": "SectorA",
        "B1": "SectorB",
        "B2": "SectorB",
        "C1": "SectorC",
    }
    return prices, sector_map


def synthetic_daily_sentiment(index: pd.Index) -> pd.DataFrame:
    dates = pd.DatetimeIndex(index).normalize()
    rows: list[dict[str, float | str | pd.Timestamp]] = []
    for i, date in enumerate(dates):
        phase = np.sin(i / 12.0)
        rows.extend(
            [
                {
                    "date": date,
                    "ticker": "A1",
                    "sentiment_score": float(0.55 + 0.20 * phase),
                    "sentiment_abs": float(0.55 + 0.20 * abs(phase)),
                    "confidence": 0.90,
                    "article_count": 3,
                    "positive_prob": 0.80,
                    "negative_prob": 0.05,
                    "neutral_prob": 0.15,
                },
                {
                    "date": date,
                    "ticker": "A2",
                    "sentiment_score": float(-0.35 - 0.10 * phase),
                    "sentiment_abs": float(0.35 + 0.10 * abs(phase)),
                    "confidence": 0.88,
                    "article_count": 3,
                    "positive_prob": 0.10,
                    "negative_prob": 0.72,
                    "neutral_prob": 0.18,
                },
                {
                    "date": date,
                    "ticker": "B1",
                    "sentiment_score": float(-0.50 - 0.15 * phase),
                    "sentiment_abs": float(0.50 + 0.15 * abs(phase)),
                    "confidence": 0.87,
                    "article_count": 2,
                    "positive_prob": 0.08,
                    "negative_prob": 0.77,
                    "neutral_prob": 0.15,
                },
                {
                    "date": date,
                    "ticker": "B2",
                    "sentiment_score": float(0.45 + 0.12 * phase),
                    "sentiment_abs": float(0.45 + 0.12 * abs(phase)),
                    "confidence": 0.86,
                    "article_count": 2,
                    "positive_prob": 0.73,
                    "negative_prob": 0.09,
                    "neutral_prob": 0.18,
                },
            ]
        )

    return pd.DataFrame(rows)


def synthetic_directional_prices() -> pd.DataFrame:
    rng = np.random.default_rng(19)
    index = pd.date_range("2020-01-01", periods=700, freq="B")
    phase = np.arange(len(index))

    trend = 80 + np.cumsum(rng.normal(0.12, 0.45, len(index)))
    mean_revert = 100 + 4.0 * np.sin(phase / 6.0) + rng.normal(0.0, 0.7, len(index))
    breakout_base = 70 + np.cumsum(rng.normal(0.01, 0.25, len(index)))
    breakout = breakout_base.copy()
    breakout[380:] += np.cumsum(rng.normal(0.18, 0.35, len(index) - 380))
    defensive = 90 + np.cumsum(rng.normal(0.03, 0.20, len(index)))

    return pd.DataFrame(
        {
            "TREND": pd.Series(trend, index=index).abs() + 10,
            "MEAN": pd.Series(mean_revert, index=index).abs() + 10,
            "BREAK": pd.Series(breakout, index=index).abs() + 10,
            "DEF": pd.Series(defensive, index=index).abs() + 10,
        },
        index=index,
    )


def synthetic_etf_prices() -> pd.DataFrame:
    rng = np.random.default_rng(29)
    index = pd.date_range("2016-01-01", periods=900, freq="B")
    phase = np.arange(len(index))

    spy = 200 + np.cumsum(rng.normal(0.08, 0.55, len(index)))
    qqq = 150 + np.cumsum(rng.normal(0.11, 0.70, len(index)))
    tlt = 120 + np.cumsum(rng.normal(0.01, 0.35, len(index)))
    gld = 110 + np.cumsum(rng.normal(0.03, 0.30, len(index)))
    xle = 70 + np.cumsum(rng.normal(0.02 + 0.02 * np.sin(phase / 80.0), 0.80, len(index)))
    xlf = 60 + np.cumsum(rng.normal(0.05, 0.50, len(index)))

    return pd.DataFrame(
        {
            "SPY": pd.Series(spy, index=index).abs() + 20,
            "QQQ": pd.Series(qqq, index=index).abs() + 20,
            "TLT": pd.Series(tlt, index=index).abs() + 20,
            "GLD": pd.Series(gld, index=index).abs() + 20,
            "XLE": pd.Series(xle, index=index).abs() + 20,
            "XLF": pd.Series(xlf, index=index).abs() + 20,
        },
        index=index,
    )


def synthetic_event_panel(index: pd.Index, symbols: list[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    timestamps = pd.DatetimeIndex(index)
    for symbol_index, symbol in enumerate(symbols):
        for offset in range(40, len(timestamps), 90):
            score = 0.45 if (offset // 90 + symbol_index) % 2 == 0 else -0.35
            rows.append(
                {
                    "timestamp": timestamps[offset],
                    "ticker": symbol,
                    "event_score": score,
                    "confidence": 0.8,
                    "event_type": "synthetic_filing",
                    "source": "unit_test",
                    "form": "10-Q",
                }
            )
    return pd.DataFrame(rows)


def fresh_test_dir(relative_path: str) -> Path:
    path = Path(relative_path)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path
