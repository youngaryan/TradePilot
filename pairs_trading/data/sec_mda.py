from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Sequence
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from ..features.lm_dict import LoughranMcDonaldScorer
from .events import EVENT_COLUMNS, EventProvider, EventRequest


SEC_URL = "https://data.sec.gov"
SUBMISSIONS_URL = SEC_URL + "/submissions/CIK{cik}.json"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"

MDA_SECTION_PATTERNS = [
    re.compile(
        r'item\s*7\.?\s*[—\-–]?\s*management[’\']?s?\s*discussion'
        r'(?:\s+and\s+analysis)?',
        re.IGNORECASE,
    ),
    re.compile(
        r'item\s*2\.?\s*[—\-–]?\s*management[’\']?s?\s*discussion'
        r'(?:\s+and\s+analysis)?',
        re.IGNORECASE,
    ),
    re.compile(
        r'management[’\']?s?\s*discussion\s+and\s+analysis'
        r'(?:\s+of\s+financial\s+condition\s+and\s+results\s+of\s+operations)?',
        re.IGNORECASE,
    ),
    re.compile(r'\bmd\s*&?\s*a\b', re.IGNORECASE),
]

NEXT_SECTION_PATTERNS = [
    re.compile(
        r'item\s*(?:7[aA]|8|7[ABab]?)\.?\s*[—\-–]?\s*(?:quantitative|financial'
        r'\s+statements|consolidated|controls|other\s+information)',
        re.IGNORECASE,
    ),
    re.compile(r'signatures?\s*$', re.IGNORECASE),
    re.compile(r'item\s+9[abAB]?', re.IGNORECASE),
    re.compile(r'item\s+7t\b', re.IGNORECASE),
]


def _fetch_json(url: str, user_agent: str, timeout: float = 30.0) -> dict:
    req = Request(url, headers={"User-Agent": user_agent})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_text(url: str, user_agent: str, timeout: float = 30.0) -> str:
    req = Request(url, headers={"User-Agent": user_agent})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _load_ticker_map(cache_dir: Path, user_agent: str, timeout: float) -> dict[str, str]:
    path = cache_dir / "company_tickers.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = _fetch_json(TICKER_MAP_URL, user_agent, timeout)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    mapping: dict[str, str] = {}
    for record in data.values():
        ticker = str(record.get("ticker", "")).upper()
        cik = str(record.get("cik_str", "")).strip()
        if ticker and cik:
            mapping[ticker] = cik.zfill(10)
    return mapping


def _load_submissions(cik: str, cache_dir: Path, user_agent: str, timeout: float) -> dict:
    path = cache_dir / "submissions" / f"CIK{cik}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    payload = _fetch_json(SUBMISSIONS_URL.format(cik=cik), user_agent, timeout)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _recent_filings(payload: dict, forms: set[str], max_count: int) -> list[dict]:
    filings = payload.get("filings", {}).get("recent", {})
    if not filings:
        return []
    keys = list(filings.keys())
    n = max(len(v) for v in filings.values() if isinstance(v, list))
    results: list[dict] = []
    for i in range(n):
        form = str(filings.get("form", [])[i]) if i < len(filings.get("form", [])) else ""
        if form.upper() in forms:
            results.append({
                k: (filings[k][i] if i < len(filings.get(k, [])) else "")
                for k in keys
            })
            if len(results) >= max_count:
                break
    return results


def _filing_url(cik: str, accession: str, doc: str) -> str:
    acc = accession.replace("-", "")
    return ARCHIVE_URL.format(cik=cik, accession=acc, doc=doc)


def _extract_mda_html(html_text: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

    soup = BeautifulSoup(html_text, "lxml")
    text = soup.get_text(separator="\n", strip=True)
    return _extract_mda_text(text)


def _extract_mda_text(text: str) -> str:
    mda_start = None
    for pat in MDA_SECTION_PATTERNS:
        m = pat.search(text)
        if m:
            mda_start = m.start()
            break

    if mda_start is None:
        return ""

    section_start = mda_start
    section_end = len(text)
    for pat in NEXT_SECTION_PATTERNS:
        m = pat.search(text, section_start + 1)
        if m:
            section_end = m.start()
            break

    return text[section_start:section_end].strip()


def _filing_text_and_doc(cik: str, filing: dict) -> tuple[str, str]:
    primary = str(filing.get("primaryDocument", ""))
    accession = str(filing.get("accessionNumber", ""))
    if not primary or not accession:
        return "", ""
    url = _filing_url(cik, accession, primary)
    return url, primary


class SecMDAEventProvider(EventProvider):
    def __init__(
        self,
        user_agent: str,
        cache_dir: str | Path = "data/sec_cache",
        timeout_seconds: float = 30.0,
        max_filings_per_ticker: int = 4,
        min_mda_length: int = 200,
        scorer: LoughranMcDonaldScorer | None = None,
    ) -> None:
        if not user_agent or "@" not in user_agent:
            raise ValueError("SEC requests require a descriptive User-Agent with contact information.")
        self.user_agent = user_agent
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        self.max_filings_per_ticker = max_filings_per_ticker
        self.min_mda_length = min_mda_length
        self.scorer = scorer or LoughranMcDonaldScorer()

    def get_events(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = EventRequest.from_inputs(tickers=tickers, start=start, end=end)
        ticker_map = _load_ticker_map(self.cache_dir, self.user_agent, self.timeout_seconds)
        start_ts = pd.Timestamp(request.start)
        end_ts = pd.Timestamp(request.end)
        target_forms = {"10-K", "10-K/A", "10-Q", "10-Q/A"}

        all_events: list[pd.DataFrame] = []
        for ticker in request.tickers:
            cik = ticker_map.get(ticker)
            if cik is None:
                continue
            try:
                payload = _load_submissions(cik, self.cache_dir, self.user_agent, self.timeout_seconds)
            except HTTPError as e:
                if e.code != 404:
                    raise
                continue

            filings = _recent_filings(payload, target_forms, self.max_filings_per_ticker)
            ticker_events: list[dict] = []
            for filing in filings:
                filing_date = pd.Timestamp(filing.get("filingDate", "")).tz_localize(None)
                if pd.isna(filing_date) or filing_date < start_ts or filing_date > end_ts:
                    continue

                report_date = filing.get("reportDate", "")
                form = str(filing.get("form", ""))
                url, doc_name = _filing_text_and_doc(cik, filing)
                if not url:
                    continue

                try:
                    html = _fetch_text(url, self.user_agent, self.timeout_seconds)
                except HTTPError:
                    continue

                if url.endswith(".htm") or url.endswith(".html"):
                    mda_text = _extract_mda_html(html)
                else:
                    mda_text = _extract_mda_text(html)

                if len(mda_text) < self.min_mda_length:
                    continue

                scores = self.scorer.score_texts([mda_text])
                row = scores.iloc[0]

                mda_words = len(mda_text.split())
                ticker_events.append({
                    "timestamp": filing_date,
                    "ticker": ticker,
                    "event_score": float(row["score"]),
                    "confidence": float(row["confidence"]),
                    "event_type": "sec_mda_sentiment",
                    "source": "sec_mda",
                    "form": form,
                    "report_date": str(report_date),
                    "filing_url": url,
                    "mda_word_count": mda_words,
                    "num_positive_words": 0,
                    "num_negative_words": 0,
                })

            if ticker_events:
                all_events.append(pd.DataFrame(ticker_events))

        if not all_events:
            return pd.DataFrame(columns=list(EVENT_COLUMNS) + ["report_date", "filing_url", "mda_word_count", "num_positive_words", "num_negative_words"])

        combined = pd.concat(all_events, axis=0, ignore_index=True)
        combined["confidence"] = pd.to_numeric(combined["confidence"], errors="coerce").fillna(0.5).clip(0.0, 1.0)
        return combined.sort_values(["timestamp", "ticker"]).reset_index(drop=True)
