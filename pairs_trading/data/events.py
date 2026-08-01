from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import time
from threading import Lock
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from typing import Sequence

import numpy as np
import pandas as pd

from .sec import SecCompanyFactsClient


class _RateLimiter:
    """Rate-limits requests and retries with exponential backoff on 429/503."""

    def __init__(self, requests_per_second: float = 8.0, max_retries: int = 3, base_backoff: float = 1.0) -> None:
        self.min_interval = 1.0 / requests_per_second
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self._last_call = 0.0
        self._lock = Lock()

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()

    def fetch_json(self, url: str, user_agent: str, timeout: float) -> dict:
        for attempt in range(self.max_retries):
            self.wait()
            try:
                request = Request(url, headers={"User-Agent": user_agent})
                with urlopen(request, timeout=timeout) as response:
                    payload = response.read().decode("utf-8")
                return json.loads(payload)
            except HTTPError as e:
                if e.code in (429, 503) and attempt < self.max_retries - 1:
                    time.sleep(self.base_backoff * (2 ** attempt))
                    continue
                raise
        raise RuntimeError("Exhausted retries")


EVENT_COLUMNS = [
    "timestamp",
    "ticker",
    "event_score",
    "confidence",
    "event_type",
    "source",
    "form",
]


@dataclass(frozen=True)
class EventRequest:
    tickers: tuple[str, ...]
    start: str
    end: str

    @classmethod
    def from_inputs(
        cls,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> "EventRequest":
        normalized = tuple(dict.fromkeys(str(ticker).upper() for ticker in tickers))
        return cls(
            tickers=normalized,
            start=str(pd.Timestamp(start).strftime("%Y-%m-%d")),
            end=str(pd.Timestamp(end).strftime("%Y-%m-%d")),
        )

    @property
    def cache_key(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True)
        return sha256(payload.encode("utf-8")).hexdigest()[:16]


class EventProvider(ABC):
    @abstractmethod
    def get_events(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        """
        Return rows with:
        - timestamp
        - ticker
        - event_score
        Optional:
        - confidence
        - event_type
        - source
        - form
        """


class CompositeEventProvider(EventProvider):
    """Combine multiple event sources into one standardized event panel."""

    def __init__(self, providers: Sequence[EventProvider]) -> None:
        if not providers:
            raise ValueError("CompositeEventProvider requires at least one provider.")
        self.providers = list(providers)

    def get_events(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for provider in self.providers:
            frame = provider.get_events(tickers=tickers, start=start, end=end)
            if frame.empty:
                continue
            frames.append(frame)
        if not frames:
            return pd.DataFrame(columns=EVENT_COLUMNS)

        combined = pd.concat(frames, axis=0, ignore_index=True, sort=False)
        combined["timestamp"] = pd.to_datetime(combined["timestamp"]).dt.tz_localize(None)
        combined["ticker"] = combined["ticker"].astype(str).str.upper()
        combined["event_score"] = pd.to_numeric(combined["event_score"], errors="coerce").fillna(0.0)
        combined["confidence"] = pd.to_numeric(combined.get("confidence", 1.0), errors="coerce").fillna(1.0).clip(0.0, 1.0)
        for column in ("event_type", "source", "form"):
            if column not in combined.columns:
                combined[column] = ""
            combined[column] = combined[column].fillna("").astype(str)
        dedup_columns = [column for column in ("timestamp", "ticker", "event_type", "form", "source") if column in combined.columns]
        return combined.sort_values(["timestamp", "ticker", "confidence"], ascending=[True, True, False]).drop_duplicates(
            subset=dedup_columns,
            keep="first",
        ).reset_index(drop=True)


class LocalEventFileProvider(EventProvider):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def get_events(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        if self.path.suffix.lower() == ".parquet":
            frame = pd.read_parquet(self.path)
        else:
            frame = pd.read_csv(self.path)

        if frame.empty:
            return frame

        required = {"timestamp", "ticker", "event_score"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"Missing required event columns in {self.path}: {sorted(missing)}")

        filtered = frame.copy()
        filtered["timestamp"] = pd.to_datetime(filtered["timestamp"]).dt.tz_localize(None)
        filtered["ticker"] = filtered["ticker"].astype(str).str.upper()
        filtered["event_score"] = pd.to_numeric(filtered["event_score"], errors="coerce").fillna(0.0)
        if "confidence" not in filtered.columns:
            filtered["confidence"] = 1.0
        filtered["confidence"] = pd.to_numeric(filtered["confidence"], errors="coerce").fillna(1.0).clip(0.0, 1.0)
        if "event_type" not in filtered.columns:
            filtered["event_type"] = "file_event"
        if "source" not in filtered.columns:
            filtered["source"] = "local_file"
        if "form" not in filtered.columns:
            filtered["form"] = ""

        request = EventRequest.from_inputs(tickers=tickers, start=start, end=end)
        start_ts = pd.Timestamp(request.start)
        end_ts = pd.Timestamp(request.end)
        filtered = filtered[filtered["ticker"].isin(request.tickers)]
        filtered = filtered[(filtered["timestamp"] >= start_ts) & (filtered["timestamp"] <= end_ts)]
        return filtered.sort_values(["timestamp", "ticker"]).reset_index(drop=True)


class CachedEventProvider(EventProvider):
    def __init__(self, upstream: EventProvider | None = None, cache_dir: str | Path = "data/event_cache") -> None:
        self.upstream = upstream
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _paths_for(self, request: EventRequest) -> tuple[Path, Path]:
        parquet_path = self.cache_dir / f"{request.cache_key}.parquet"
        meta_path = self.cache_dir / f"{request.cache_key}.json"
        return parquet_path, meta_path

    def get_events(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = EventRequest.from_inputs(tickers=tickers, start=start, end=end)
        parquet_path, meta_path = self._paths_for(request)

        if parquet_path.exists():
            cached = pd.read_parquet(parquet_path)
            if not cached.empty and "timestamp" in cached.columns:
                cached["timestamp"] = pd.to_datetime(cached["timestamp"]).dt.tz_localize(None)
            return cached.sort_values(["timestamp", "ticker"]).reset_index(drop=True)

        if self.upstream is None:
            raise FileNotFoundError(f"Missing event cache entry {parquet_path} and no upstream provider is configured.")

        events = self.upstream.get_events(
            tickers=request.tickers,
            start=request.start,
            end=request.end,
        )
        events.to_parquet(parquet_path)
        meta_path.write_text(json.dumps(asdict(request), indent=2), encoding="utf-8")
        return events


class SecCompanyFactsEventProvider(EventProvider):
    """
    Builds EDGAR event scores from official SEC company facts.

    The provider derives a simple post-filing score from year-over-year changes in
    revenue and net income. It is intentionally conservative: the output is meant
    to be a clean event panel that an event-drift strategy can trade after the
    filing date, not a substitute for analyst-surprise data.
    """

    TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
    COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

    REVENUE_CONCEPTS = (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
    )
    EARNINGS_CONCEPTS = (
        "NetIncomeLoss",
        "ProfitLoss",
    )
    EPS_CONCEPTS = (
        "EarningsPerShareDiluted",
        "EarningsPerShareBasic",
        "IncomeLossFromContinuingOperationsPerDilutedShare",
    )

    def __init__(
        self,
        user_agent: str,
        cache_dir: str | Path = "data/sec_cache",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not user_agent or "@" not in user_agent:
            raise ValueError("SEC requests require a descriptive User-Agent with contact information.")
        self.user_agent = user_agent
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        self._rate_limiter = _RateLimiter()
        self._company_facts_client = SecCompanyFactsClient(
            cache_dir=self.cache_dir,
            fetch_json=lambda url: self._fetch_json(url),
        )

    def _fetch_json(self, url: str) -> dict:
        return self._rate_limiter.fetch_json(url, self.user_agent, self.timeout_seconds)

    def _ticker_map_path(self) -> Path:
        return self.cache_dir / "company_tickers.json"

    def _load_ticker_map(self) -> dict[str, str]:
        return self._company_facts_client.ticker_map()

    def _companyfacts_path(self, cik: str) -> Path:
        return self.cache_dir / "companyfacts" / f"CIK{cik}.json"

    def _load_companyfacts(self, cik: str) -> dict:
        return self._company_facts_client.company_facts(cik)

    def _extract_series(self, payload: dict, concepts: Sequence[str]) -> tuple[pd.DataFrame, str | None]:
        facts = payload.get("facts", {}).get("us-gaap", {})
        for concept in concepts:
            fact = facts.get(concept)
            if not fact:
                continue

            rows: list[dict[str, object]] = []
            for unit_key, entries in fact.get("units", {}).items():
                if not str(unit_key).upper().startswith("USD"):
                    continue
                for entry in entries:
                    form = str(entry.get("form", ""))
                    filed = entry.get("filed")
                    fy = entry.get("fy")
                    fp = entry.get("fp")
                    value = entry.get("val")
                    if filed is None or fy is None or fp is None or value is None:
                        continue
                    if form not in {"10-Q", "10-Q/A", "10-K", "10-K/A"}:
                        continue
                    rows.append(
                        {
                            "filed": pd.Timestamp(filed),
                            "fy": int(fy),
                            "fp": str(fp),
                            "form": form,
                            "val": float(value),
                        }
                    )

            if not rows:
                continue

            frame = pd.DataFrame(rows)
            frame = frame.sort_values(["fy", "fp", "filed"]).drop_duplicates(["fy", "fp"], keep="last")
            return frame.reset_index(drop=True), concept

        return pd.DataFrame(columns=["filed", "fy", "fp", "form", "val"]), None

    @staticmethod
    def _yoy_growth(series: pd.DataFrame, value_column: str) -> pd.Series:
        previous = series.set_index(["fy", "fp"])[value_column].to_dict()
        growth = []
        for row in series.to_dict("records"):
            prior_value = previous.get((int(row["fy"]) - 1, str(row["fp"])))
            if prior_value in (None, 0):
                growth.append(float("nan"))
                continue
            growth.append(float(row[value_column]) / float(prior_value) - 1.0)
        return pd.Series(growth, index=series.index)

    def _build_company_events(self, ticker: str, payload: dict, cik: str | None = None) -> pd.DataFrame:
        revenue, revenue_concept = self._extract_series(payload, self.REVENUE_CONCEPTS)
        earnings, earnings_concept = self._extract_series(payload, self.EARNINGS_CONCEPTS)
        eps, eps_concept = self._extract_series(payload, self.EPS_CONCEPTS)

        if revenue.empty and earnings.empty and eps.empty:
            return pd.DataFrame(
                columns=[
                    "timestamp",
                    "ticker",
                    "cik",
                    "event_score",
                    "confidence",
                    "event_type",
                    "source",
                    "form",
                    "revenue",
                    "earnings",
                    "eps",
                    "revenue_yoy",
                    "earnings_yoy",
                    "eps_yoy",
                ]
            )

        metric_frames: list[pd.DataFrame] = []
        if not revenue.empty:
            revenue = revenue.copy()
            revenue["revenue_yoy"] = self._yoy_growth(revenue, "val")
            metric_frames.append(revenue.rename(columns={"val": "revenue"}))
        if not earnings.empty:
            earnings = earnings.copy()
            earnings["earnings_yoy"] = self._yoy_growth(earnings, "val")
            metric_frames.append(earnings.rename(columns={"val": "earnings"}))
        if not eps.empty:
            eps = eps.copy()
            eps["eps_yoy"] = self._yoy_growth(eps, "val")
            metric_frames.append(eps.rename(columns={"val": "eps"}))

        merged = metric_frames[0]
        for metric_frame in metric_frames[1:]:
            merged = pd.merge(merged, metric_frame, on=["filed", "fy", "fp", "form"], how="outer")
        merged = merged.sort_values("filed")

        for column in ("revenue", "earnings", "eps", "revenue_yoy", "earnings_yoy", "eps_yoy"):
            if column not in merged.columns:
                merged[column] = float("nan")

        score_components = []
        for column in ("revenue_yoy", "earnings_yoy", "eps_yoy"):
            score_components.append(merged[column].clip(-1.5, 1.5))

        stacked = pd.concat(score_components, axis=1)
        merged["event_score"] = stacked.mean(axis=1, skipna=True).map(
            lambda value: 0.0 if pd.isna(value) else float(np.tanh(2.0 * value))
        )
        yoy_completeness = stacked.notna().mean(axis=1).fillna(0.0)
        metric_completeness = merged[["revenue", "earnings", "eps"]].notna().mean(axis=1).fillna(0.0)
        merged["confidence"] = (0.65 * metric_completeness + 0.35 * yoy_completeness).clip(0.0, 1.0)

        merged["timestamp"] = pd.to_datetime(merged["filed"]).dt.tz_localize(None)
        merged["ticker"] = ticker
        merged["cik"] = cik or ""
        merged["event_type"] = "edgar_companyfacts"
        merged["source"] = "sec_companyfacts"
        merged["revenue_concept"] = revenue_concept or ""
        merged["earnings_concept"] = earnings_concept or ""
        merged["eps_concept"] = eps_concept or ""

        result = merged[
            [
                "timestamp",
                "ticker",
                "cik",
                "event_score",
                "confidence",
                "event_type",
                "source",
                "form",
                "fy",
                "fp",
                "revenue",
                "earnings",
                "eps",
                "revenue_yoy",
                "earnings_yoy",
                "eps_yoy",
                "revenue_concept",
                "earnings_concept",
                "eps_concept",
            ]
        ].copy()
        result = result[result["confidence"] > 0.0]
        return result.sort_values("timestamp").reset_index(drop=True)

    def get_events(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = EventRequest.from_inputs(tickers=tickers, start=start, end=end)
        ticker_map = self._load_ticker_map()

        events: list[pd.DataFrame] = []
        for ticker in request.tickers:
            cik = ticker_map.get(ticker)
            if cik is None:
                continue
            try:
                payload = self._load_companyfacts(cik)
            except HTTPError as error:
                if error.code != 404:
                    raise
                continue
            company_events = self._build_company_events(ticker, payload, cik=cik)
            if not company_events.empty:
                events.append(company_events)

        if not events:
            return pd.DataFrame(
                columns=[
                    "timestamp",
                    "ticker",
                    "event_score",
                    "confidence",
                    "event_type",
                    "source",
                    "form",
                ]
            )

        combined = pd.concat(events, axis=0, ignore_index=True)
        start_ts = pd.Timestamp(request.start)
        end_ts = pd.Timestamp(request.end)
        combined = combined[(combined["timestamp"] >= start_ts) & (combined["timestamp"] <= end_ts)]
        return combined.sort_values(["timestamp", "ticker"]).reset_index(drop=True)


class SecCompanyFilingsEventProvider(EventProvider):
    """
    Builds official company event timestamps from SEC submissions history.

    This provider captures events that are official and auditable, especially:
    - 8-K Item 2.02 earnings/results releases,
    - 10-Q quarterly reports,
    - 10-K annual reports.

    The scores are deliberately low-conviction event-drift priors. Directional
    fundamentals still come from company-facts or a separate surprise dataset.
    """

    TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
    SUBMISSIONS_FILE_URL = "https://data.sec.gov/submissions/{name}"
    DEFAULT_FORMS = ("8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A")

    def __init__(
        self,
        user_agent: str,
        cache_dir: str | Path = "data/sec_cache",
        forms: Sequence[str] | None = None,
        timeout_seconds: float = 30.0,
        include_historical_files: bool = True,
    ) -> None:
        if not user_agent or "@" not in user_agent:
            raise ValueError("SEC requests require a descriptive User-Agent with contact information.")
        self.user_agent = user_agent
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.forms = tuple(dict.fromkeys((forms or self.DEFAULT_FORMS)))
        self.timeout_seconds = timeout_seconds
        self.include_historical_files = include_historical_files
        self._rate_limiter = _RateLimiter()

    def _fetch_json(self, url: str) -> dict:
        return self._rate_limiter.fetch_json(url, self.user_agent, self.timeout_seconds)

    def _ticker_map_path(self) -> Path:
        return self.cache_dir / "company_tickers.json"

    def _load_ticker_map(self) -> dict[str, str]:
        path = self._ticker_map_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = self._fetch_json(self.TICKER_MAP_URL)
            path.write_text(json.dumps(data), encoding="utf-8")

        mapping: dict[str, str] = {}
        for record in data.values():
            ticker = str(record.get("ticker", "")).upper()
            cik = str(record.get("cik_str", "")).strip()
            if ticker and cik:
                mapping[ticker] = cik.zfill(10)
        return mapping

    def _submissions_path(self, cik: str) -> Path:
        return self.cache_dir / "submissions" / f"CIK{cik}.json"

    def _submissions_file_path(self, name: str) -> Path:
        return self.cache_dir / "submissions" / name

    def _load_submission_payload(self, cik: str) -> dict:
        path = self._submissions_path(cik)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        payload = self._fetch_json(self.SUBMISSIONS_URL.format(cik=cik))
        path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def _load_submission_file(self, name: str) -> dict:
        path = self._submissions_file_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        payload = self._fetch_json(self.SUBMISSIONS_FILE_URL.format(name=name))
        path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    @staticmethod
    def _columnar_records(filings: dict) -> list[dict[str, object]]:
        if not filings:
            return []
        keys = list(filings.keys())
        row_count = max((len(value) for value in filings.values() if isinstance(value, list)), default=0)
        records: list[dict[str, object]] = []
        for index in range(row_count):
            record: dict[str, object] = {}
            for key in keys:
                values = filings.get(key)
                if isinstance(values, list) and index < len(values):
                    record[key] = values[index]
            records.append(record)
        return records

    @staticmethod
    def _filing_url(cik: str, accession_number: str, primary_document: str) -> str:
        if not accession_number or not primary_document:
            return ""
        accession_path = str(accession_number).replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/{primary_document}"

    @staticmethod
    def _event_from_record(form: str, items: str, description: str) -> tuple[str, float, float] | None:
        normalized_form = form.upper().strip()
        normalized_items = str(items or "").lower()
        normalized_description = str(description or "").lower()
        event_text = f"{normalized_items} {normalized_description}"
        if normalized_form in {"10-Q", "10-Q/A"}:
            return "quarterly_earnings_report", 0.15, 0.40
        if normalized_form in {"10-K", "10-K/A"}:
            return "annual_earnings_report", 0.15, 0.45
        if normalized_form in {"8-K", "8-K/A"}:
            if "dividend" in event_text:
                return "dividend_announcement", 0.10, 0.45
            if any(term in event_text for term in ("share repurchase", "stock repurchase", "buyback")):
                return "buyback_announcement", 0.12, 0.45
            if any(term in event_text for term in ("merger", "acquisition", "acquire", "disposition")) or "2.01" in normalized_items:
                return "merger_acquisition_update", 0.15, 0.45
            if any(term in event_text for term in ("debt", "credit agreement", "senior notes", "notes offering")) or "2.03" in normalized_items:
                return "debt_financing", 0.10, 0.40
            if any(term in event_text for term in ("equity", "common stock", "registered direct", "private placement")) or "3.02" in normalized_items:
                return "equity_financing", 0.10, 0.40
            if any(term in event_text for term in ("guidance", "outlook", "forecast")):
                return "guidance_update", 0.12, 0.40
            if "presentation" in event_text or "7.01" in normalized_items:
                return "investor_presentation", 0.08, 0.35
            is_earnings_release = (
                "2.02" in normalized_items
                or "results of operations" in normalized_description
                or "financial condition" in normalized_description
                or "earnings" in normalized_description
            )
            if is_earnings_release:
                return "earnings_release_8k", 0.20, 0.35
            return "material_8k", 0.10, 0.25
        if normalized_form in {"S-1", "S-1/A", "S-3", "S-3/A", "424B2", "424B5"}:
            if any(term in event_text for term in ("debt", "notes", "bond", "debenture")):
                return "debt_financing", 0.10, 0.35
            return "equity_financing", 0.10, 0.35
        if normalized_form in {"DEF 14A", "DEFA14A"}:
            return "regulatory_filing", 0.05, 0.30
        return None

    @staticmethod
    def _files_overlap(file_record: dict, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> bool:
        filing_from = pd.to_datetime(file_record.get("filingFrom"), errors="coerce")
        filing_to = pd.to_datetime(file_record.get("filingTo"), errors="coerce")
        if pd.isna(filing_from) or pd.isna(filing_to):
            return False
        return filing_from <= end_ts and filing_to >= start_ts

    def _records_for_range(self, payload: dict, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> list[dict[str, object]]:
        filings = payload.get("filings", {})
        records = self._columnar_records(filings.get("recent", {}))
        if not self.include_historical_files:
            return records

        for file_record in filings.get("files", []) or []:
            if not isinstance(file_record, dict) or not self._files_overlap(file_record, start_ts, end_ts):
                continue
            name = str(file_record.get("name", ""))
            if not name:
                continue
            try:
                file_payload = self._load_submission_file(name)
            except Exception:
                continue
            records.extend(self._columnar_records(file_payload))
        return records

    def _build_filing_events(self, ticker: str, cik: str, payload: dict, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> pd.DataFrame:
        allowed_forms = {form.upper() for form in self.forms}
        rows: list[dict[str, object]] = []
        for record in self._records_for_range(payload, start_ts, end_ts):
            form = str(record.get("form", "")).upper().strip()
            base_form = form[:-2] if form.endswith("/A") else form
            if form not in allowed_forms and base_form not in allowed_forms:
                continue
            filing_date = pd.to_datetime(record.get("filingDate"), errors="coerce")
            if pd.isna(filing_date) or filing_date < start_ts or filing_date > end_ts:
                continue
            event = self._event_from_record(
                form=form,
                items=str(record.get("items", "")),
                description=str(record.get("primaryDocDescription", "")),
            )
            if event is None:
                continue
            event_type, event_score, confidence = event
            accession_number = str(record.get("accessionNumber", ""))
            primary_document = str(record.get("primaryDocument", ""))
            rows.append(
                {
                    "timestamp": filing_date.tz_localize(None),
                    "ticker": ticker,
                    "event_score": event_score,
                    "confidence": confidence,
                    "event_type": event_type,
                    "source": "sec_submissions",
                    "form": form,
                    "report_date": record.get("reportDate"),
                    "accession_number": accession_number,
                    "primary_document": primary_document,
                    "items": record.get("items", ""),
                    "description": record.get("primaryDocDescription", ""),
                    "url": self._filing_url(cik, accession_number, primary_document),
                }
            )

        if not rows:
            return pd.DataFrame(columns=EVENT_COLUMNS)
        return pd.DataFrame(rows).sort_values(["timestamp", "ticker", "form"]).reset_index(drop=True)

    def get_events(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = EventRequest.from_inputs(tickers=tickers, start=start, end=end)
        ticker_map = self._load_ticker_map()
        start_ts = pd.Timestamp(request.start)
        end_ts = pd.Timestamp(request.end)

        events: list[pd.DataFrame] = []
        for ticker in request.tickers:
            cik = ticker_map.get(ticker)
            if cik is None:
                continue
            try:
                payload = self._load_submission_payload(cik)
            except HTTPError as error:
                if error.code != 404:
                    raise
                continue
            filing_events = self._build_filing_events(ticker, cik, payload, start_ts, end_ts)
            if not filing_events.empty:
                events.append(filing_events)

        if not events:
            return pd.DataFrame(columns=EVENT_COLUMNS)
        return pd.concat(events, axis=0, ignore_index=True, sort=False).sort_values(["timestamp", "ticker"]).reset_index(drop=True)
