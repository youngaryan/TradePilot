from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence
from urllib.error import HTTPError

import numpy as np
import pandas as pd

from ..features.lm_dict import LoughranMcDonaldScorer
from .events import EVENT_COLUMNS, EventProvider, EventRequest

TRANSCRIPT_EXTRA_COLUMNS = [
    "transcript_year",
    "transcript_quarter",
    "transcript_word_count",
    "positive_word_count",
    "negative_word_count",
]

EMPTY_COLUMNS = list(EVENT_COLUMNS) + TRANSCRIPT_EXTRA_COLUMNS


class AlphaVantageTranscriptProvider(EventProvider):
    """
    Fetches earnings call transcripts via Alpha Vantage EARNINGS_CALL_TRANSCRIPT.

    Requires a premium Alpha Vantage API key (the free tier does not include
    this endpoint).

    API: GET https://www.alphavantage.co/query?function=EARNINGS_CALL_TRANSCRIPT&symbol={ticker}&apikey={key}
    """

    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(
        self,
        api_key: str,
        scorer: LoughranMcDonaldScorer | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("Alpha Vantage API key is required for transcript fetching.")
        self.api_key = api_key
        self.scorer = scorer or LoughranMcDonaldScorer()
        self.timeout_seconds = timeout_seconds

    def _fetch_json(self, params: dict[str, str]) -> dict[str, Any]:
        from urllib.request import Request, urlopen
        from urllib.parse import urlencode

        url = f"{self.BASE_URL}?{urlencode(params)}"
        request = Request(url)
        with urlopen(request, timeout=self.timeout_seconds) as resp:
            return __import__("json").loads(resp.read().decode("utf-8"))

    def get_events(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = EventRequest.from_inputs(tickers=tickers, start=start, end=end)
        start_ts = pd.Timestamp(request.start)
        end_ts = pd.Timestamp(request.end)
        all_events: list[dict[str, Any]] = []

        for ticker in request.tickers:
            params = {
                "function": "EARNINGS_CALL_TRANSCRIPT",
                "symbol": ticker,
                "apikey": self.api_key,
            }
            try:
                payload = self._fetch_json(params)
            except HTTPError:
                continue

            if not isinstance(payload, dict):
                continue

            if "Information" in payload:
                raise RuntimeError(
                    f"Alpha Vantage EARNINGS_CALL_TRANSCRIPT is a premium endpoint. "
                    f"Your API key does not have access. Message: {payload['Information']}"
                )
            if "Error Message" in payload:
                raise RuntimeError(f"Alpha Vantage error: {payload['Error Message']}")

            transcript_text = payload.get("transcript", "").strip()
            if not transcript_text:
                continue

            fiscal_year = payload.get("fiscal_year", payload.get("year", ""))
            fiscal_quarter = payload.get("fiscal_quarter", payload.get("quarter", ""))

            try:
                yr = int(fiscal_year)
                qtr = int(fiscal_quarter) if fiscal_quarter else 0
            except (ValueError, TypeError):
                yr = 0
                qtr = 0

            if yr > 0:
                if qtr in (1, 2, 3):
                    timestamp = pd.Timestamp(f"{yr}-{qtr * 3 + 1:02d}-01")
                elif qtr == 4:
                    timestamp = pd.Timestamp(f"{yr + 1}-01-01")
                else:
                    timestamp = pd.Timestamp(f"{yr}-07-01")
            else:
                timestamp_str = payload.get("fiscal_date_ending", "")
                timestamp = pd.Timestamp(timestamp_str) if timestamp_str else pd.NaT

            if pd.isna(timestamp):
                continue
            if timestamp < start_ts or timestamp > end_ts:
                continue

            scores = self.scorer.score_texts([transcript_text])
            row = scores.iloc[0]
            word_count = len(transcript_text.split())

            all_events.append({
                "timestamp": timestamp,
                "ticker": ticker,
                "event_score": float(row["score"]),
                "confidence": float(row["confidence"]),
                "event_type": "earnings_call_transcript",
                "source": "alphavantage",
                "form": "",
                "transcript_year": yr,
                "transcript_quarter": qtr,
                "transcript_word_count": word_count,
                "positive_word_count": int(row.get("positive_count", 0)),
                "negative_word_count": int(row.get("negative_count", 0)),
            })

        if not all_events:
            return pd.DataFrame(columns=EMPTY_COLUMNS)

        combined = pd.DataFrame(all_events)
        combined["confidence"] = pd.to_numeric(combined["confidence"], errors="coerce").fillna(0.5).clip(0.0, 1.0)
        return combined.sort_values(["timestamp", "ticker"]).reset_index(drop=True)


class LocalTranscriptFileProvider(EventProvider):
    """
    Reads pre-downloaded earnings call transcripts from a local parquet or CSV file.

    Required columns: timestamp, ticker, transcript_text
    Optional columns: year, quarter

    Each row is one earnings call. The transcript_text is scored with
    LoughranMcDonaldScorer to produce event_score and confidence.
    """

    REQUIRED_COLUMNS = {"timestamp", "ticker", "transcript_text"}

    def __init__(
        self,
        path: str | Path,
        scorer: LoughranMcDonaldScorer | None = None,
    ) -> None:
        self.path = Path(path)
        self.scorer = scorer or LoughranMcDonaldScorer()
        self._validate_and_load()

    def _validate_and_load(self) -> pd.DataFrame:
        if self.path.suffix.lower() == ".parquet":
            frame = pd.read_parquet(self.path)
        else:
            frame = pd.read_csv(self.path)

        if not frame.empty:
            missing = self.REQUIRED_COLUMNS - set(frame.columns)
            if missing:
                raise ValueError(
                    f"Transcript file {self.path} missing required columns: {sorted(missing)}. "
                    f"Need: {sorted(self.REQUIRED_COLUMNS)}"
                )
        self._data = frame
        return frame

    def get_events(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = EventRequest.from_inputs(tickers=tickers, start=start, end=end)
        start_ts = pd.Timestamp(request.start)
        end_ts = pd.Timestamp(request.end)

        frame = self._data.copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"]).dt.tz_localize(None)
        frame["ticker"] = frame["ticker"].astype(str).str.upper()
        frame = frame[frame["ticker"].isin(request.tickers)]
        frame = frame[(frame["timestamp"] >= start_ts) & (frame["timestamp"] <= end_ts)]

        if frame.empty:
            return pd.DataFrame(columns=EMPTY_COLUMNS)

        all_texts = frame["transcript_text"].fillna("").tolist()
        score_results = self.scorer.score_texts(all_texts)

        rows: list[dict[str, Any]] = []
        for idx, (_, row) in enumerate(frame.iterrows()):
            scores = score_results.iloc[idx] if idx < len(score_results) else None
            if scores is None:
                continue

            text = str(row.get("transcript_text", ""))
            word_count = len(text.split())
            yr = int(row.get("year", 0)) if "year" in frame.columns else 0
            qtr = int(row.get("quarter", 0)) if "quarter" in frame.columns else 0

            rows.append({
                "timestamp": row["timestamp"],
                "ticker": row["ticker"],
                "event_score": float(scores["score"]),
                "confidence": float(scores["confidence"]),
                "event_type": "earnings_call_transcript",
                "source": "local_file",
                "form": "",
                "transcript_year": yr,
                "transcript_quarter": qtr,
                "transcript_word_count": word_count,
                "positive_word_count": int(scores.get("positive_count", 0)),
                "negative_word_count": int(scores.get("negative_count", 0)),
            })

        if not rows:
            return pd.DataFrame(columns=EMPTY_COLUMNS)

        combined = pd.DataFrame(rows)
        combined["confidence"] = pd.to_numeric(combined["confidence"], errors="coerce").fillna(0.5).clip(0.0, 1.0)
        return combined.sort_values(["timestamp", "ticker"]).reset_index(drop=True)
