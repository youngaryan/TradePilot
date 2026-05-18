from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Sequence
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from .events import EVENT_COLUMNS, EventProvider, EventRequest

logger = logging.getLogger(__name__)

FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"
SERIES_MAP: dict[str, dict[str, str]] = {
    "GDP": {"id": "GDPC1", "name": "Real Gross Domestic Product"},
    "UNEMPLOYMENT": {"id": "UNRATE", "name": "Unemployment Rate"},
    "FEDFUNDS": {"id": "FEDFUNDS", "name": "Federal Funds Effective Rate"},
    "CPI": {"id": "CPIAUCSL", "name": "Consumer Price Index for All Urban Consumers"},
    "RECESSION_PROB": {"id": "RECPROUSM156N", "name": "Recession Probability"},
    "YIELD_CURVE": {"id": "T10Y2Y", "name": "10-Year Treasury minus 2-Year Treasury"},
    "PMI": {"id": "NAPM", "name": "ISM Manufacturing Purchasing Managers Index"},
    "INDUSTRIAL_PROD": {"id": "INDPRO", "name": "Industrial Production Index"},
    "CONSUMER_SENT": {"id": "UMCSENT", "name": "University of Michigan Consumer Sentiment"},
}

SERIES_SIGN: dict[str, int] = {
    "GDP": 1,
    "UNEMPLOYMENT": -1,
    "FEDFUNDS": 0,
    "CPI": -1,
    "RECESSION_PROB": -1,
    "YIELD_CURVE": 1,
    "PMI": 1,
    "INDUSTRIAL_PROD": 1,
    "CONSUMER_SENT": 1,
}


def _fetch_fred_observations(
    series_id: str,
    api_key: str,
    start: str,
    end: str,
    timeout: float = 30.0,
) -> pd.Series:
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start,
        "observation_end": end,
        "sort_order": "asc",
    }
    url = f"{FRED_API_BASE}?{urlencode(params)}"
    req = Request(url)
    with urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    observations = data.get("observations", [])
    dates: list[pd.Timestamp] = []
    values: list[float] = []
    for obs in observations:
        d = pd.Timestamp(obs.get("date", ""))
        v = obs.get("value", "")
        if v == ".":
            continue
        try:
            values.append(float(v))
            dates.append(d)
        except (ValueError, TypeError):
            continue

    return pd.Series(values, index=pd.DatetimeIndex(dates), name=series_id)


def _zscore_normalize(series: pd.Series) -> pd.Series:
    mean = series.mean()
    std = series.std(ddof=0)
    if std == 0 or pd.isna(std):
        return pd.Series(0.0, index=series.index)
    return (series - mean) / std


def _series_to_events(
    series_name: str,
    series_data: pd.Series,
    sign: int,
) -> pd.DataFrame:
    if series_data.empty:
        return pd.DataFrame(columns=list(EVENT_COLUMNS) + ["series_name", "raw_value"])

    z = _zscore_normalize(series_data)
    raw = series_data.values
    rows: list[dict[str, Any]] = []
    for i in range(len(series_data)):
        raw_val = float(raw[i])
        z_val = float(z.iloc[i])
        score = float(np.tanh(z_val * sign)) if sign != 0 else float(np.tanh(z_val))
        rows.append({
            "timestamp": series_data.index[i],
            "ticker": "MACRO",
            "event_score": round(score, 4),
            "confidence": round(min(abs(z_val) / 3.0, 1.0), 4),
            "event_type": f"fred_{series_name.lower()}",
            "source": "fred",
            "form": "",
            "series_name": series_name,
            "raw_value": round(raw_val, 4),
        })

    return pd.DataFrame(rows)


class FredEventProvider(EventProvider):
    def __init__(
        self,
        api_key: str,
        series: list[str] | None = None,
        cache_dir: str | Path = "data/fred_cache",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("FRED API key is required.")
        self.api_key = api_key
        self.series = list(dict.fromkeys(series or list(SERIES_MAP.keys())))
        unknown = [s for s in self.series if s not in SERIES_MAP]
        if unknown:
            raise ValueError(f"Unknown FRED series: {unknown}. Available: {sorted(SERIES_MAP)}")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds

    def _fetch_and_cache(self, series_key: str, start: str, end: str) -> pd.Series:
        info = SERIES_MAP[series_key]
        series_id = info["id"]
        cache_path = self.cache_dir / f"{series_id}_{start}_{end}.csv"
        if cache_path.exists():
            cached = pd.read_csv(cache_path, index_col=0, parse_dates=True).iloc[:, 0]
            return cached

        try:
            result = _fetch_fred_observations(
                series_id=series_id,
                api_key=self.api_key,
                start=start,
                end=end,
                timeout=self.timeout_seconds,
            )
        except HTTPError as e:
            logger.warning("FRED API error for %s: %s", series_key, e)
            return pd.Series(dtype=float, name=series_id)

        if not result.empty:
            result.to_frame().to_csv(cache_path)
        return result

    def get_events(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = EventRequest.from_inputs(tickers=tickers, start=start, end=end)
        all_frames: list[pd.DataFrame] = []
        for series_key in self.series:
            info = SERIES_MAP[series_key]
            data = self._fetch_and_cache(series_key, request.start, request.end)
            sign = SERIES_SIGN.get(series_key, 1)
            events = _series_to_events(series_key, data, sign)
            if not events.empty:
                all_frames.append(events)

        if not all_frames:
            return pd.DataFrame(columns=list(EVENT_COLUMNS) + ["series_name", "raw_value"])

        combined = pd.concat(all_frames, axis=0, ignore_index=True)
        combined["timestamp"] = pd.to_datetime(combined["timestamp"]).dt.tz_localize(None)
        combined["confidence"] = pd.to_numeric(combined["confidence"], errors="coerce").fillna(0.5).clip(0.0, 1.0)
        return combined.sort_values(["timestamp", "event_type"]).reset_index(drop=True)
