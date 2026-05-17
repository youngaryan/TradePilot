from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_TICKER_REGEX = re.compile(r"(?<!\w)\$?[A-Z]{2,5}(?:\.[A-Z])?(?!\w)|\$[A-Z](?!\w)")
_TOKEN_REGEX = re.compile(r"\b\w+\b")

_SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
_KB_CACHE_DIR = Path("data/ticker_kb")
_KB_CACHE_FILE = _KB_CACHE_DIR / "company_name_to_tickers.json"

_MANUAL_ALIASES: dict[str, list[str]] = {
    "berkshire hathaway": ["BRK.A", "BRK.B"],
    "berkshire hathaway inc": ["BRK.A", "BRK.B"],
    "alphabet": ["GOOGL", "GOOG"],
    "alphabet inc": ["GOOGL", "GOOG"],
    "meta": ["META"],
    "meta platforms": ["META"],
    "meta platforms inc": ["META"],
    "s&p 500": ["SPY"],
    "sp 500": ["SPY"],
    "nasdaq": ["QQQ"],
    "nasdaq 100": ["QQQ"],
    "dow jones": ["DIA"],
    "industrial average": ["DIA"],
    "gold": ["GLD"],
    "silver": ["SLV"],
    "oil": ["USO"],
    "crude oil": ["USO"],
    "treasury bonds": ["TLT"],
    "dollar index": ["DXY"],
}

_COMMON_WORDS: set[str] = {
    "this", "that", "the", "and", "for", "are", "all", "can", "has", "had",
    "but", "not", "you", "its", "new", "old", "big", "top", "may", "now",
    "get", "see", "use", "say", "says", "said", "made", "make", "take",
    "will", "with", "from", "each", "some", "more", "than", "very",
    "just", "also", "over", "into", "only", "other", "such", "about",
    "inc", "corp", "ltd", "llc", "plc", "co", "mvp", "ceo", "cfo",
    "my", "is", "it", "at", "by", "to", "of", "in", "on", "up", "if",
    "be", "an", "or", "as", "so", "do", "no", "go", "he", "she", "we",
    "us", "me", "am", "text", "data", "code", "info", "work", "time",
    "day", "year", "rate", "price", "best", "set", "put", "add", "end",
}

_STOCK_EXCHANGE_SUFFIXES = [
    " inc", " ltd", " limited", " corp", " corporation", " co", " company",
    " llc", " plc", " ag", " nv", " sa", " gmbh", " lp",
    " holdings", " holding", " group", " group inc",
    " technologies", " technology",
    " bancorp", " financial", " finance",
    " common stock", " com", " ordinary shares",
]


def _normalize_company_name(name: str) -> str:
    name = re.sub(r"[^a-z0-9\s]", " ", name.lower())
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _strip_corporate_suffix(name: str) -> str:
    for suffix in _STOCK_EXCHANGE_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    return name


def _download_sec_ticker_map() -> dict[str, Any]:
    logger.info("Downloading SEC company_tickers.json from %s", _SEC_TICKER_URL)
    try:
        req = Request(
            _SEC_TICKER_URL,
            headers={"User-Agent": "PairsTradingResearch/1.0 (contact@example.com)"},
        )
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("Failed to download SEC ticker map: %s", exc)
        return {}


def _build_name_to_tickers_kb() -> dict[str, list[str]]:
    raw = _download_sec_ticker_map()
    kb: dict[str, list[str]] = {}
    for entry in raw.values():
        ticker = str(entry.get("ticker", "")).upper().strip()
        title = str(entry.get("title", "")).strip()
        if not ticker or not title:
            continue
        normalized = _normalize_company_name(title)
        if normalized:
            kb.setdefault(normalized, []).append(ticker)
        stripped = _strip_corporate_suffix(normalized)
        if stripped and stripped != normalized:
            kb.setdefault(stripped, []).append(ticker)
    for alias_name, alias_tickers in _MANUAL_ALIASES.items():
        existing = set(kb.get(alias_name, []))
        merged = existing.union(alias_tickers)
        kb[alias_name] = list(merged)
    return kb


def _load_kb() -> dict[str, list[str]]:
    _KB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if _KB_CACHE_FILE.exists():
        with open(_KB_CACHE_FILE, encoding="utf-8") as f:
            return dict(json.load(f))
    kb = _build_name_to_tickers_kb()
    with open(_KB_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(kb, f)
    return kb


def _install_spacy_model(model: str) -> None:
    try:
        import spacy  # noqa: F401
        spacy.load(model)
    except (ImportError, OSError):
        logger.info("Downloading spaCy model '%s'...", model)
        subprocess.check_call(
            [sys.executable, "-m", "spacy", "download", model],
        )


class TickerExtractor:
    _instance: TickerExtractor | None = None
    _lock = Lock()

    def __init__(self, model: str = "en_core_web_sm") -> None:
        _install_spacy_model(model)
        import spacy
        self.nlp = spacy.load(model)
        self._kb = _load_kb()
        self._kb_lower: dict[str, list[str]] = {k.lower(): v for k, v in self._kb.items()}
        self._ticker_regions: dict[str, str] = self._build_ticker_regions()

    @classmethod
    def default(cls) -> TickerExtractor:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @staticmethod
    def _build_ticker_regions() -> dict[str, str]:
        return {
            "NYSE": "US",
            "NASDAQ": "US",
            "AMEX": "US",
            "TSX": "CA",
            "TSXV": "CA",
            "LSE": "GB",
            "TSE": "JP",
            "FRA": "DE",
            "XETRA": "DE",
        }

    def _kb_lookup(self, name: str) -> list[str]:
        normalized = _normalize_company_name(name)
        if normalized in self._kb_lower:
            return self._kb_lower[normalized]
        stripped = _strip_corporate_suffix(normalized)
        if stripped and stripped in self._kb_lower:
            return self._kb_lower[stripped]
        if normalized.isalpha() and len(normalized) > 2:
            match = next((v for k, v in self._kb_lower.items() if normalized in k.split()), None)
            if match:
                return match
        return []

    def _regex_tickers(self, text: str, requested: set[str] | None = None) -> set[str]:
        tokens = {t.upper() for t in _TICKER_REGEX.findall(text)}
        tokens |= {t[1:] for t in tokens if t.startswith("$")}
        if requested is not None:
            tokens &= requested
        return tokens

    def extract_tickers(
        self,
        text: str,
        requested: set[str] | None = None,
    ) -> list[str]:
        if not text or not text.strip():
            return []
        found: set[str] = set()
        doc = self.nlp(text.strip()[:10_000])
        for ent in doc.ents:
            if ent.label_ in ("ORG", "PERSON"):
                kb_tickers = self._kb_lookup(ent.text)
                found.update(kb_tickers)
        found.update(self._regex_tickers(text, requested=None))
        uk_orgs = [
            ent.text for ent in doc.ents
            if ent.label_ in ("ORG", "GPE")
            and any(word in ent.text.lower() for word in ("ltd", "limited", "plc", "llp"))
        ]
        for name in uk_orgs:
            found.update(self._kb_lookup(name))
        known_exchanges = {"NYSE", "NASDAQ", "AMEX", "TSX", "LSE", "TSE"}
        for token in doc:
            raw = token.text
            up = raw.upper()
            if up in known_exchanges:
                continue
            if token.like_num:
                continue
            lower = raw.lower()
            if lower in _COMMON_WORDS:
                continue
            if raw == lower:
                continue
            if _TICKER_REGEX.fullmatch(up) and len(up) <= 5:
                kb_tickers = self._kb_lookup(raw)
                if kb_tickers and len(kb_tickers) <= 3:
                    found.update(kb_tickers)
        found.discard("")
        if requested is not None:
            found &= requested
        return sorted(found)

    def score_text_relevance(self, text: str, ticker: str) -> float:
        if not text or not text.strip():
            return 0.0
        text_lower = text.lower()
        tickers_in_text = self.extract_tickers(text, requested=None)
        if ticker in tickers_in_text:
            return 0.85
        doc = self.nlp(text.strip()[:10_000])
        ticker_lower = ticker.lower()
        for ent in doc.ents:
            if ent.label_ == "ORG":
                name_lower = ent.text.lower()
                if ticker_lower in name_lower or name_lower in ticker_lower:
                    return 0.65
        alias_score = 0.0
        for alias in self._ticker_aliases(ticker):
            if self._token_match(text_lower, alias):
                alias_score = max(alias_score, 0.72)
                break
        if alias_score > 0:
            return alias_score
        return 0.0

    def score_row(self, row: pd.Series, ticker: str) -> float:
        row_ticker = str(row.get("ticker", "")).upper()
        if row_ticker and row_ticker != ticker:
            return 0.0
        if row_ticker == ticker:
            return max(float(row.get("relevance", 0.7)), 0.85)
        text = " ".join(str(row.get(column, "")) for column in ("headline", "title", "summary", "source"))
        return self.score_text_relevance(text, ticker)

    @staticmethod
    def _ticker_aliases(ticker: str) -> tuple[str, ...]:
        normalized = str(ticker).upper().strip()
        aliases = {normalized.lower(), f"${normalized}".lower()}
        import string
        for punct in (".", "-", "_"):
            stripped = normalized.replace(punct, "")
            if stripped != normalized and len(stripped) <= 5:
                aliases.add(stripped.lower())
        return tuple(sorted(aliases, key=len, reverse=True))

    @staticmethod
    def _token_match(text: str, alias: str) -> bool:
        alias = alias.lower().strip()
        if not alias:
            return False
        if any(char in alias for char in (" ", "/", "-", "=")):
            return alias in text
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text))
