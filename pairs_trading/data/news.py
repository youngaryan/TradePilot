from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
import html
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen
from typing import Sequence
import xml.etree.ElementTree as ET

import pandas as pd

from ..features.sentiment import BaseSentimentModel, NewsSentimentAggregator
from ..features.ticker_extractor import TickerExtractor


FX_CURRENCY_CODES = {
    "AUD",
    "CAD",
    "CHF",
    "CNH",
    "CNY",
    "EUR",
    "GBP",
    "HKD",
    "JPY",
    "MXN",
    "NOK",
    "NZD",
    "SEK",
    "SGD",
    "USD",
    "ZAR",
}

CURRENCY_NAMES = {
    "AUD": "Australian dollar",
    "CAD": "Canadian dollar",
    "CHF": "Swiss franc",
    "CNH": "offshore yuan",
    "CNY": "yuan",
    "EUR": "euro",
    "GBP": "pound",
    "HKD": "Hong Kong dollar",
    "JPY": "yen",
    "MXN": "Mexican peso",
    "NOK": "Norwegian krone",
    "NZD": "New Zealand dollar",
    "SEK": "Swedish krona",
    "SGD": "Singapore dollar",
    "USD": "dollar",
    "ZAR": "rand",
}

TOPIC_ALIASES = {
    "DIA": ("dow jones", "industrial average", "blue-chip stocks"),
    "DXY": ("dollar index", "us dollar", "greenback"),
    "GLD": ("gold", "bullion", "spot gold", "gold etf", "real yields"),
    "HYG": ("high yield bonds", "junk bonds", "credit spreads"),
    "IWM": ("russell 2000", "small caps", "small-cap stocks"),
    "LQD": ("investment grade bonds", "corporate bonds", "credit spreads"),
    "QQQ": ("nasdaq", "nasdaq 100", "technology stocks", "megacap tech"),
    "SLV": ("silver", "spot silver", "silver etf"),
    "SPY": ("s&p 500", "sp 500", "stock market", "equities"),
    "TLT": ("treasury bonds", "long bonds", "treasury yields", "bond yields", "rates"),
    "USO": ("oil", "crude oil", "wti", "brent"),
    "VIX": ("volatility index", "market volatility", "fear gauge"),
}

SOURCE_GROUP_LABELS = {
    "proper_news": "External market news",
    "generic_web": "Generic web",
    "social": "Social",
    "local_files": "Local files",
    "unknown": "Unknown",
}

SOURCE_GROUP_DESCRIPTIONS = {
    "proper_news": "Editorial or market-data news providers and known financial publishers.",
    "generic_web": "Broad RSS, GDELT, local web crawl, and direct web discovery sources.",
    "social": "Crowd-sourced message boards and social feeds.",
    "local_files": "Uploaded CSV/parquet files, sample data, and manually curated local rows.",
    "unknown": "Rows without enough provider metadata to classify.",
}

_SOURCE_GROUP_PRIORITY = {
    "proper_news": 0,
    "social": 1,
    "generic_web": 2,
    "local_files": 3,
    "unknown": 4,
}

_PROPER_NEWS_PROVIDERS = {
    "alphavantagenewsprovider",
    "benzinganewsprovider",
    "newsapiheadlineprovider",
}

_LOCAL_FILE_PROVIDERS = {
    "localnewsfileprovider",
}

_GENERIC_WEB_PROVIDERS = {
    "rssheadlineprovider",
    "localwebsearchheadlineprovider",
    "webresearchheadlineprovider",
}

_SOCIAL_PROVIDERS = {
    "stocktwitsheadlineprovider",
}

_PROPER_NEWS_DOMAINS = {
    "apnews.com",
    "barrons.com",
    "benzinga.com",
    "bloomberg.com",
    "businesswire.com",
    "cnbc.com",
    "finance.yahoo.com",
    "financialpost.com",
    "ft.com",
    "globenewswire.com",
    "investors.com",
    "marketwatch.com",
    "morningstar.com",
    "nasdaq.com",
    "prnewswire.com",
    "reuters.com",
    "seekingalpha.com",
    "thefly.com",
    "wsj.com",
    "zacks.com",
}

_SOCIAL_DOMAINS = {
    "reddit.com",
    "stocktwits.com",
    "x.com",
    "twitter.com",
}


def source_group_label(source_group: str) -> str:
    return SOURCE_GROUP_LABELS.get(str(source_group).strip().lower(), SOURCE_GROUP_LABELS["unknown"])


def source_group_description(source_group: str) -> str:
    return SOURCE_GROUP_DESCRIPTIONS.get(str(source_group).strip().lower(), SOURCE_GROUP_DESCRIPTIONS["unknown"])


def preferred_source_group(groups: Sequence[str]) -> str:
    normalized = [str(group).strip().lower() for group in groups if str(group).strip()]
    if not normalized:
        return "unknown"
    return min(normalized, key=lambda group: _SOURCE_GROUP_PRIORITY.get(group, _SOURCE_GROUP_PRIORITY["unknown"]))


def _host_from_value(value: str) -> str:
    raw = str(value).strip().lower()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        raw = parsed.netloc or parsed.path
    raw = raw.removeprefix("www.")
    return raw.split("/")[0].split(":")[0]


def _source_host(source: str, url: str) -> str:
    raw_source = str(source).strip().lower()
    for prefix in ("web:", "local-web:"):
        if raw_source.startswith(prefix):
            return _host_from_value(raw_source.removeprefix(prefix))
    if raw_source.startswith("reddit:"):
        return "reddit.com"
    source_host = _host_from_value(raw_source)
    if "." in source_host and " " not in source_host:
        return source_host
    return _host_from_value(str(url))


def _host_matches(host: str, domains: set[str]) -> bool:
    if not host:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def classify_source_group(source: str = "", provider_name: str = "", url: str = "") -> str:
    provider = str(provider_name).strip().lower()
    source_text = str(source).strip().lower()
    host = _source_host(source_text, str(url))

    if provider in _SOCIAL_PROVIDERS or source_text.startswith("reddit:") or _host_matches(host, _SOCIAL_DOMAINS):
        return "social"
    if provider in _LOCAL_FILE_PROVIDERS:
        return "local_files"
    if _host_matches(host, _PROPER_NEWS_DOMAINS) or provider in _PROPER_NEWS_PROVIDERS:
        return "proper_news"
    if provider in _GENERIC_WEB_PROVIDERS or source_text.startswith(("web:", "local-web:")):
        return "generic_web"
    if not source_text and not provider and not host:
        return "unknown"
    return "generic_web"


def _string_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if column in frame.columns:
        return frame[column].fillna("").astype(str)
    return pd.Series([""] * len(frame), index=frame.index, dtype="object")


def annotate_source_groups(frame: pd.DataFrame) -> pd.DataFrame:
    annotated = frame.copy()
    if annotated.empty:
        if "source_group" not in annotated.columns:
            annotated["source_group"] = pd.Series(dtype="object")
        if "source_group_label" not in annotated.columns:
            annotated["source_group_label"] = pd.Series(dtype="object")
        return annotated

    sources = _string_column(annotated, "source")
    providers = _string_column(annotated, "provider_name")
    urls = _string_column(annotated, "url")
    existing = _string_column(annotated, "source_group")
    groups: list[str] = []
    for source, provider, url, current_group in zip(sources, providers, urls, existing):
        normalized = current_group.strip().lower()
        groups.append(normalized if normalized in SOURCE_GROUP_LABELS else classify_source_group(source, provider, url))
    annotated["source_group"] = groups
    annotated["source_group_label"] = [source_group_label(group) for group in groups]
    return annotated


def _compact_fx_pair(value: str) -> str | None:
    compact = str(value).upper().strip().removesuffix("=X").replace("/", "").replace("-", "").replace("_", "")
    if len(compact) == 6 and compact[:3] in FX_CURRENCY_CODES and compact[3:] in FX_CURRENCY_CODES:
        return compact
    return None


def _newsapi_query_for_ticker(ticker: str) -> str:
    fx_pair = _compact_fx_pair(ticker)
    if fx_pair:
        base, quote = fx_pair[:3], fx_pair[3:]
        named_pair = f'"{CURRENCY_NAMES.get(base, base)} {CURRENCY_NAMES.get(quote, quote)}"'
        return f'"{fx_pair}" OR "{base}/{quote}" OR "{base} {quote}" OR "{fx_pair}=X" OR {named_pair}'
    return f'"{ticker}" OR ${ticker}'


def _rss_query_ticker(ticker: str) -> str:
    fx_pair = _compact_fx_pair(ticker)
    if fx_pair:
        return f"{fx_pair}=X"
    return ticker


def _alphavantage_query_tickers(tickers: Sequence[str]) -> tuple[list[str], dict[str, str]]:
    query_tickers: list[str] = []
    alias_to_requested: dict[str, str] = {}
    for ticker in tickers:
        requested = str(ticker).upper()
        fx_pair = _compact_fx_pair(requested)
        if fx_pair:
            for alias in (fx_pair, f"FOREX:{fx_pair[:3]}", f"FOREX:{fx_pair[3:]}"):
                query_tickers.append(alias)
                alias_to_requested[alias] = fx_pair
            continue
        query_tickers.append(requested)
        alias_to_requested[requested] = requested
    return list(dict.fromkeys(query_tickers)), alias_to_requested


@dataclass(frozen=True)
class NewsRequest:
    tickers: tuple[str, ...]
    start: str
    end: str

    @classmethod
    def from_inputs(
        cls,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> "NewsRequest":
        normalized = tuple(dict.fromkeys(str(ticker).upper() for ticker in tickers))
        return cls(
            tickers=normalized,
            start=str(pd.Timestamp(start).strftime("%Y-%m-%d")),
            end=str(pd.Timestamp(end).strftime("%Y-%m-%d")),
        )

    def cache_key(self, extra: dict[str, str] | None = None) -> str:
        payload = asdict(self)
        if extra:
            payload["extra"] = extra
        encoded = json.dumps(payload, sort_keys=True)
        return sha256(encoded.encode("utf-8")).hexdigest()[:16]


class HeadlineProvider(ABC):
    @abstractmethod
    def get_headlines(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        """
        Return headline rows with at least:
        - timestamp
        - ticker
        - headline
        Optional:
        - relevance
        - source
        - url
        """


class DailySentimentProvider(ABC):
    @abstractmethod
    def get_daily_sentiment(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        """
        Return rows with:
        - date
        - ticker
        - sentiment_score
        - sentiment_abs
        - confidence
        - article_count
        - positive_prob / negative_prob / neutral_prob
        - sample_urls (optional, pipe-separated article URLs for this ticker-day)
        - sample_headlines (optional, pipe-separated headline texts)
        """


@dataclass(frozen=True)
class HeadlineDedupConfig:
    enabled: bool = True
    time_window_minutes: int = 180
    min_text_key_length: int = 24


def _normalize_text_key(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()
    return re.sub(r"\s+", " ", normalized)


def _as_sorted_csv(values: set[str]) -> str:
    return ",".join(sorted(value for value in values if value))


def deduplicate_headlines(headlines: pd.DataFrame, config: HeadlineDedupConfig = HeadlineDedupConfig()) -> pd.DataFrame:
    if headlines.empty or not config.enabled:
        return headlines.sort_values(["timestamp", "ticker"]).reset_index(drop=True)

    frame = headlines.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=False).dt.tz_localize(None)
    frame["ticker"] = frame["ticker"].astype(str).str.upper()
    frame["headline"] = frame["headline"].astype(str)
    if "source" not in frame.columns:
        frame["source"] = ""
    frame["source"] = frame["source"].fillna("").astype(str)
    if "provider_name" not in frame.columns:
        frame["provider_name"] = frame["source"]
    frame["provider_name"] = frame["provider_name"].fillna("").astype(str)
    if "url" not in frame.columns:
        frame["url"] = ""
    frame["url"] = frame["url"].fillna("").astype(str)
    if "relevance" not in frame.columns:
        frame["relevance"] = 1.0
    frame["relevance"] = pd.to_numeric(frame["relevance"], errors="coerce").fillna(1.0)
    if "title" not in frame.columns:
        frame["title"] = frame["headline"]
    frame["title"] = frame["title"].fillna("").astype(str)
    if "summary" not in frame.columns:
        frame["summary"] = ""
    frame["summary"] = frame["summary"].fillna("").astype(str)
    frame = annotate_source_groups(frame)

    frame["_url_key"] = frame["url"].str.strip().str.lower()
    frame["_text_key"] = frame["title"].where(frame["title"].str.len() > 0, frame["headline"]).map(_normalize_text_key)
    frame.loc[frame["_text_key"].str.len() < config.min_text_key_length, "_text_key"] = ""

    frame = frame.sort_values(["ticker", "timestamp", "relevance"], ascending=[True, True, False]).reset_index(drop=True)
    time_window = pd.Timedelta(minutes=config.time_window_minutes)

    merged_rows: list[dict[str, object]] = []
    url_map: dict[tuple[str, str], int] = {}
    text_map: dict[tuple[str, str], dict[str, object]] = {}

    for row in frame.to_dict("records"):
        ticker = str(row["ticker"])
        timestamp = pd.Timestamp(row["timestamp"])
        url_key = str(row["_url_key"])
        text_key = str(row["_text_key"])

        match_idx: int | None = None
        if url_key:
            match_idx = url_map.get((ticker, url_key))

        if match_idx is None and text_key:
            text_state = text_map.get((ticker, text_key))
            if text_state is not None:
                last_timestamp = pd.Timestamp(text_state["last_timestamp"])
                if abs(timestamp - last_timestamp) <= time_window:
                    match_idx = int(text_state["idx"])

        if match_idx is None:
            new_row = {key: value for key, value in row.items() if not key.startswith("_")}
            sources = {str(new_row.get("source", ""))} if new_row.get("source") else set()
            providers = {str(new_row.get("provider_name", ""))} if new_row.get("provider_name") else set()
            urls = {str(new_row.get("url", ""))} if new_row.get("url") else set()
            source_groups = {str(new_row.get("source_group", ""))} if new_row.get("source_group") else {"unknown"}
            source_group = preferred_source_group(source_groups)
            new_row["source_group"] = source_group
            new_row["source_group_label"] = source_group_label(source_group)
            new_row["source_list"] = _as_sorted_csv(sources)
            new_row["provider_list"] = _as_sorted_csv(providers)
            new_row["url_list"] = _as_sorted_csv(urls)
            new_row["source_group_list"] = _as_sorted_csv(source_groups)
            new_row["source_group_count"] = len(source_groups)
            new_row["source_count"] = len(sources) or 1
            new_row["duplicate_count"] = 1
            merged_rows.append(new_row)
            match_idx = len(merged_rows) - 1
        else:
            existing = merged_rows[match_idx]
            if len(str(row.get("headline", ""))) > len(str(existing.get("headline", ""))):
                existing["headline"] = row["headline"]
            if len(str(row.get("title", ""))) > len(str(existing.get("title", ""))):
                existing["title"] = row["title"]
            if len(str(row.get("summary", ""))) > len(str(existing.get("summary", ""))):
                existing["summary"] = row["summary"]
            existing["relevance"] = max(float(existing.get("relevance", 1.0)), float(row.get("relevance", 1.0)))
            existing["timestamp"] = min(pd.Timestamp(existing["timestamp"]), timestamp)

            source_set = set(filter(None, str(existing.get("source_list", "")).split(",")))
            provider_set = set(filter(None, str(existing.get("provider_list", "")).split(",")))
            url_set = set(filter(None, str(existing.get("url_list", "")).split(",")))
            source_group_set = set(filter(None, str(existing.get("source_group_list", "")).split(",")))
            if row.get("source"):
                source_set.add(str(row["source"]))
            if row.get("provider_name"):
                provider_set.add(str(row["provider_name"]))
            if row.get("url"):
                url_set.add(str(row["url"]))
            if row.get("source_group"):
                source_group_set.add(str(row["source_group"]))

            existing["source_list"] = _as_sorted_csv(source_set)
            existing["provider_list"] = _as_sorted_csv(provider_set)
            existing["url_list"] = _as_sorted_csv(url_set)
            existing["source_group_list"] = _as_sorted_csv(source_group_set)
            existing["source_group"] = preferred_source_group(source_group_set)
            existing["source_group_label"] = source_group_label(str(existing["source_group"]))
            existing["source_group_count"] = len(source_group_set) if source_group_set else 1
            existing["source_count"] = len(source_set) if source_set else 1
            existing["duplicate_count"] = int(existing.get("duplicate_count", 1)) + 1
            if not existing.get("source") and row.get("source"):
                existing["source"] = row["source"]
            if not existing.get("provider_name") and row.get("provider_name"):
                existing["provider_name"] = row["provider_name"]
            if not existing.get("url") and row.get("url"):
                existing["url"] = row["url"]

        if url_key:
            url_map[(ticker, url_key)] = match_idx
        if text_key:
            text_map[(ticker, text_key)] = {"idx": match_idx, "last_timestamp": timestamp}

    merged = pd.DataFrame(merged_rows)
    if merged.empty:
        return merged
    merged["timestamp"] = pd.to_datetime(merged["timestamp"], utc=False).dt.tz_localize(None)
    merged["ticker"] = merged["ticker"].astype(str).str.upper()
    return merged.sort_values(["timestamp", "ticker"]).reset_index(drop=True)


class RemoteHeadlineProvider(HeadlineProvider):
    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    def _fetch_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        request = Request(url, headers=headers or {})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            raw = response.read()
            encoding = response.headers.get_content_charset() or "utf-8"
        return raw.decode(encoding, errors="replace")

    def _fetch_json(self, url: str, params: dict[str, str], headers: dict[str, str] | None = None) -> dict | list:
        query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
        full_url = f"{url}?{query}" if query else url
        request = Request(full_url, headers=headers or {})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = response.read().decode("utf-8")
        return json.loads(payload)


def _provider_label(provider: HeadlineProvider) -> str:
    label = provider.__class__.__name__
    return label.removesuffix("Provider").removesuffix("Headline")


def _provider_display_label(provider: HeadlineProvider) -> str:
    label = _provider_label(provider)
    display_names = {
        "AlphaVantageNews": "Alpha Vantage",
        "BenzingaNews": "Benzinga",
        "LocalNewsFile": "Local news file",
        "LocalWebSearch": "Local web search",
        "NewsAPI": "NewsAPI",
        "RSS": "RSS",
        "StockTwits": "StockTwits",
        "WebResearch": "Web research",
    }
    if label.endswith("LocalWebSearch"):
        return "Local web search"
    if label.endswith("WebResearch"):
        return "Web research"
    return display_names.get(label, label)


def _safe_provider_error(provider: HeadlineProvider, exc: Exception) -> str:
    label = _provider_display_label(provider)
    if isinstance(exc, HTTPError):
        reason = getattr(exc, "reason", None) or getattr(exc, "msg", "")
        if exc.code == 401:
            return f"{label} rejected the credentials (HTTP 401). Check the API key or unselect this source; partial results from other sources were saved."
        if exc.code == 429:
            return f"{label} was rate-limited (HTTP 429). Partial results from other selected sources were saved; wait a few minutes or reduce web/API requests before running again."
        return f"{label} failed with HTTP {exc.code} {reason}."
    if isinstance(exc, URLError):
        return f"{label} network error: {exc.reason}."
    return f"{label} failed: {exc}"


def _strip_markup(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class _HTMLArticleExtractor(HTMLParser):
    """Small stdlib-only article extractor for weak hardware environments."""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "form", "nav", "footer"}
    TEXT_TAGS = {"p", "li", "h1", "h2", "h3"}

    def __init__(self, max_text_chars: int = 12_000) -> None:
        super().__init__(convert_charrefs=True)
        self.max_text_chars = max_text_chars
        self.title = ""
        self.meta_description = ""
        self.text_blocks: list[str] = []
        self._skip_depth = 0
        self._capture_tag: str | None = None
        self._capture_parts: list[str] = []

    @staticmethod
    def _attrs_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {str(key).lower(): str(value or "") for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return

        attributes = self._attrs_dict(attrs)
        if tag == "meta":
            name = attributes.get("name", "").lower()
            prop = attributes.get("property", "").lower()
            if name == "description" or prop in {"og:description", "twitter:description"}:
                self.meta_description = _strip_markup(attributes.get("content", ""))
            return

        if self._skip_depth:
            return
        if tag == "title" or tag in self.TEXT_TAGS:
            if self._capture_tag is None:
                self._capture_tag = tag
                self._capture_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._capture_tag is None:
            return
        self._capture_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag != self._capture_tag:
            return
        text = _strip_markup(" ".join(self._capture_parts))
        if text:
            if tag == "title" and not self.title:
                self.title = text
            elif len(text) >= 24:
                self.text_blocks.append(text)
        self._capture_tag = None
        self._capture_parts = []

    @property
    def article_text(self) -> str:
        text = " ".join(self.text_blocks)
        return text[: self.max_text_chars]


class _HTMLLinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attributes = {str(key).lower(): str(value or "") for key, value in attrs}
        href = attributes.get("href", "").strip()
        if href:
            self._href = href
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        self.links.append((self._href, _strip_markup(" ".join(self._text_parts))))
        self._href = None
        self._text_parts = []


class LightweightExtractiveSummarizer:
    """
    CPU-cheap summarizer used before sentiment scoring.

    This is intentionally not a transformer. It gives the pipeline a
    ChatGPT-like "summarize findings" step without downloading a large model.
    """

    STOPWORDS = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    }

    SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
    WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z\-']+")

    def __init__(self, max_sentences: int = 2, max_summary_chars: int = 620) -> None:
        self.max_sentences = max(1, int(max_sentences))
        self.max_summary_chars = max(120, int(max_summary_chars))

    def summarize(self, *, title: str, description: str = "", body: str = "") -> str:
        candidates = [
            _strip_markup(part)
            for part in self.SENTENCE_RE.split(" ".join(part for part in (description, body) if part))
            if len(_strip_markup(part)) >= 30
        ]
        if not candidates:
            return _strip_markup(description or title)[: self.max_summary_chars]

        word_counts: dict[str, int] = {}
        ticker_tokens = set(re.findall(r"\b[A-Z]{2,6}\b", title))
        for sentence in candidates:
            for word in self.WORD_RE.findall(sentence.lower()):
                if word in self.STOPWORDS or len(word) <= 2:
                    continue
                word_counts[word] = word_counts.get(word, 0) + 1

        scored: list[tuple[int, float, str]] = []
        for index, sentence in enumerate(candidates[:24]):
            words = [word.lower() for word in self.WORD_RE.findall(sentence)]
            if not words:
                continue
            score = sum(word_counts.get(word, 0) for word in words) / max(len(words), 1)
            if any(token in sentence for token in ticker_tokens):
                score += 0.25
            if any(term in sentence.lower() for term in ("earnings", "guidance", "revenue", "margin", "rate", "inflation", "dollar", "gold")):
                score += 0.20
            scored.append((index, score, sentence))

        selected = sorted(sorted(scored, key=lambda row: row[1], reverse=True)[: self.max_sentences], key=lambda row: row[0])
        summary = " ".join(sentence for _, _, sentence in selected).strip()
        return summary[: self.max_summary_chars]


def _extract_article_bs4(html_text: str, max_text_chars: int = 12_000) -> dict[str, str]:
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(html_text, "lxml")
    except ValueError:
        soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup.find_all(["script", "style", "noscript", "svg", "canvas", "form", "nav", "footer"]):
        tag.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    description = ""
    for attr_name in ("name", "property"):
        for attr_val in ("description", "og:description", "twitter:description"):
            tag = soup.find("meta", attrs={attr_name: attr_val})
            if tag and tag.get("content"):
                description = _strip_markup(tag["content"])
                break
        if description:
            break

    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)[:max_text_chars]
    return {"title": title, "description": description, "body": text}


def _extract_article(html_text: str, max_text_chars: int = 12_000) -> dict[str, str]:
    try:
        return _extract_article_bs4(html_text, max_text_chars=max_text_chars)
    except Exception:
        pass
    extractor = _HTMLArticleExtractor(max_text_chars=max_text_chars)
    extractor.feed(html_text)
    return {
        "title": extractor.title,
        "description": extractor.meta_description,
        "body": extractor.article_text,
    }


def _extract_links(html_text: str) -> list[tuple[str, str]]:
    extractor = _HTMLLinkExtractor()
    extractor.feed(html_text)
    return extractor.links


def _namespaced_text(element: ET.Element, names: Sequence[str]) -> str:
    for name in names:
        try:
            child = element.find(name)
        except SyntaxError:
            child = None
        if child is not None and child.text:
            return _strip_markup(child.text)
    local_names = {
        name.rsplit("}", 1)[-1].split(":", 1)[-1].lower()
        for name in names
    }
    for child in element:
        local_name = child.tag.rsplit("}", 1)[-1].lower()
        if local_name in local_names and child.text:
            return _strip_markup(child.text)
    return ""


def _namespaced_link(element: ET.Element) -> str:
    link = _namespaced_text(element, ("link",))
    if link:
        return link
    for child in element:
        if child.tag.rsplit("}", 1)[-1].lower() != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return str(href)
    return ""


class RSSHeadlineProvider(RemoteHeadlineProvider):
    """
    Free RSS headline provider.

    Feed URLs can either be normal feed URLs or ticker templates containing
    ``{ticker}``. The default template uses Yahoo Finance's ticker headline RSS
    endpoint, which keeps symbol assignment explicit and avoids broad scraping.
    """

    DEFAULT_FEED_TEMPLATE = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"

    def __init__(
        self,
        feed_urls: Sequence[str] | None = None,
        user_agent: str = "QuantResearchApp/0.1 (+research; contact@example.com)",
        max_items_per_feed: int = 200,
        timeout_seconds: float = 20.0,
        skip_errors: bool = True,
        assign_single_ticker_when_unmatched: bool = False,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.feed_urls = tuple(self._normalize_feed_url(url) for url in (feed_urls or (self.DEFAULT_FEED_TEMPLATE,)))
        self.user_agent = user_agent
        self.max_items_per_feed = max(1, int(max_items_per_feed))
        self.skip_errors = skip_errors
        self.assign_single_ticker_when_unmatched = assign_single_ticker_when_unmatched

    @staticmethod
    def _source_name(url: str) -> str:
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix("www.")
        if host in {"reddit.com", "old.reddit.com"}:
            match = re.match(r"^/r/([^/]+)", parsed.path, flags=re.IGNORECASE)
            if match:
                return f"reddit:r/{match.group(1)}"
        return host or "rss"

    @staticmethod
    def _normalize_feed_url(url: str) -> str:
        stripped = str(url).strip()
        parsed = urlparse(stripped)
        host = parsed.netloc.lower().removeprefix("www.")
        if host in {"reddit.com", "old.reddit.com"} and not parsed.path.endswith(".rss"):
            path = parsed.path.rstrip("/")
            if re.match(r"^/r/[^/]+$", path, flags=re.IGNORECASE):
                path = f"{path}/.rss"
                return parsed._replace(path=path).geturl()
        return stripped

    @staticmethod
    def _parse_feed(xml_text: str, source_url: str) -> list[dict[str, object]]:
        root = ET.fromstring(xml_text)
        items = list(root.findall(".//item"))
        if not items:
            items = [
                child
                for child in root.findall(".//*")
                if child.tag.rsplit("}", 1)[-1].lower() == "entry"
            ]

        rows: list[dict[str, object]] = []
        for item in items:
            title = _namespaced_text(item, ("title",))
            summary = _namespaced_text(item, ("description", "summary", "content", "{http://purl.org/rss/1.0/modules/content/}encoded"))
            link = _namespaced_link(item)
            published = _namespaced_text(item, ("pubDate", "published", "updated", "dc:date"))
            timestamp = pd.to_datetime(published, utc=True, errors="coerce")
            rows.append(
                {
                    "timestamp": timestamp,
                    "title": title,
                    "summary": summary,
                    "headline": " ".join(part for part in (title, summary) if part).strip(),
                    "source": RSSHeadlineProvider._source_name(source_url),
                    "url": link,
                    "relevance": 1.0,
                }
            )
        return rows

    @staticmethod
    def _infer_tickers(text: str, requested: set[str]) -> list[str]:
        if not text or not text.strip():
            return []
        extractor = TickerExtractor.default()
        return extractor.extract_tickers(text, requested=requested)

    def _urls_for_request(self, request: NewsRequest) -> list[tuple[str, str | None]]:
        urls: list[tuple[str, str | None]] = []
        for feed_url in self.feed_urls:
            if "{ticker}" in feed_url:
                urls.extend((feed_url.format(ticker=_rss_query_ticker(ticker)), ticker) for ticker in request.tickers)
            else:
                urls.append((feed_url, None))
        return urls

    def get_headlines(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = NewsRequest.from_inputs(tickers=tickers, start=start, end=end)
        requested = set(request.tickers)
        rows: list[dict[str, object]] = []

        for feed_url, explicit_ticker in self._urls_for_request(request):
            try:
                xml_text = self._fetch_text(feed_url, headers={"User-Agent": self.user_agent})
                feed_rows = self._parse_feed(xml_text, source_url=feed_url)[: self.max_items_per_feed]
            except Exception:
                if self.skip_errors:
                    continue
                raise

            for row in feed_rows:
                text = " ".join(str(row.get(column, "")) for column in ("title", "summary", "headline"))
                matched_tickers = [explicit_ticker] if explicit_ticker else self._infer_tickers(text, requested)
                if not matched_tickers and len(requested) == 1 and explicit_ticker:
                    matched_tickers = [explicit_ticker]
                if not matched_tickers and len(requested) == 1 and self.assign_single_ticker_when_unmatched:
                    matched_tickers = [next(iter(requested))]
                for ticker in matched_tickers:
                    record = dict(row)
                    record["ticker"] = str(ticker).upper()
                    rows.append(record)

        frame = pd.DataFrame(rows)
        if frame.empty:
            return pd.DataFrame(columns=["timestamp", "ticker", "headline", "title", "summary", "relevance", "source", "url"])

        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dt.tz_convert(None)
        frame = frame.dropna(subset=["timestamp"])
        frame["ticker"] = frame["ticker"].astype(str).str.upper()
        start_ts = pd.Timestamp(request.start)
        end_ts = pd.Timestamp(request.end) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        frame = frame[(frame["timestamp"] >= start_ts) & (frame["timestamp"] <= end_ts)]
        return frame.sort_values(["timestamp", "ticker"]).reset_index(drop=True)


class LocalWebSearchHeadlineProvider(RemoteHeadlineProvider):
    """
    Local web-search style provider that avoids hosted search APIs.

    It builds a small local index from RSS/Atom feeds and optional direct URLs,
    caches that index on disk, then searches the cached text for ticker aliases,
    ETF topic terms, FX pair names, and optional user query terms.
    """

    LOCAL_WEB_SEARCH_TOOL = "local_web_index_v2"
    DEFAULT_FEED_URLS = (RSSHeadlineProvider.DEFAULT_FEED_TEMPLATE,)
    SOURCE_DOMAIN_SEEDS = {
        "cnbc.com": (
            "https://www.cnbc.com/?format=rss",
            "https://www.cnbc.com/markets/",
            "https://www.cnbc.com/finance/",
        ),
        "finance.yahoo.com": (
            "https://finance.yahoo.com/news/",
            "https://finance.yahoo.com/topic/stock-market-news/",
        ),
        "marketwatch.com": (
            "https://www.marketwatch.com/rss/topstories",
            "https://www.marketwatch.com/markets",
            "https://www.marketwatch.com/",
        ),
    }

    def __init__(
        self,
        feed_urls: Sequence[str] | None = None,
        source_domains: Sequence[str] | None = None,
        direct_urls: Sequence[str] | None = None,
        query_terms: str = "",
        cache_dir: str | Path = "data/sentiment_cache/local_web_index",
        max_items_per_feed: int = 200,
        max_results_per_ticker: int = 25,
        max_crawl_pages_per_source: int = 30,
        refresh_minutes: int = 60,
        fetch_article_text: bool = True,
        timeout_seconds: float = 15.0,
        max_text_chars: int = 8_000,
        user_agent: str = "QuantResearchApp/0.1 (+research; contact@example.com)",
        skip_errors: bool = True,
        assign_single_ticker_when_unmatched: bool = True,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        selected_feeds = self.DEFAULT_FEED_URLS if feed_urls is None else feed_urls
        self.feed_urls = tuple(RSSHeadlineProvider._normalize_feed_url(url) for url in selected_feeds)
        self.source_domains = tuple(dict.fromkeys(WebResearchHeadlineProvider._normalize_domain(domain) for domain in (source_domains or ()) if str(domain).strip()))
        self.direct_urls = tuple(str(url).strip() for url in (direct_urls or ()) if str(url).strip())
        self.query_terms = _strip_markup(query_terms)
        self.cache_dir = Path(cache_dir)
        self.max_items_per_feed = max(1, int(max_items_per_feed))
        self.max_results_per_ticker = max(1, int(max_results_per_ticker))
        self.max_crawl_pages_per_source = max(1, int(max_crawl_pages_per_source))
        self.refresh_minutes = max(0, int(refresh_minutes))
        self.fetch_article_text = bool(fetch_article_text)
        self.max_text_chars = max(1_000, int(max_text_chars))
        self.user_agent = user_agent
        self.skip_errors = skip_errors
        self.assign_single_ticker_when_unmatched = assign_single_ticker_when_unmatched
        self.summarizer = LightweightExtractiveSummarizer()
        self.last_errors: list[str] = []

    @staticmethod
    def _source_name(url: str) -> str:
        parsed = urlparse(str(url))
        host = parsed.netloc.lower().removeprefix("www.")
        if host in {"reddit.com", "old.reddit.com"}:
            match = re.match(r"^/r/([^/]+)", parsed.path, flags=re.IGNORECASE)
            if match:
                return f"local-web:reddit:r/{match.group(1)}"
        return f"local-web:{host or 'feed'}"

    @staticmethod
    def _ensure_url(value: str) -> str:
        stripped = str(value).strip()
        if not stripped:
            return stripped
        parsed = urlparse(stripped)
        if parsed.scheme:
            return stripped
        return f"https://{stripped.strip('/')}/"

    @classmethod
    def _crawl_error_message(cls, url: str, exc: Exception) -> str:
        source = cls._source_name(url)
        if isinstance(exc, HTTPError):
            reason = str(getattr(exc, "reason", None) or getattr(exc, "msg", "") or "").strip()
            if exc.code in {401, 403}:
                return f"{source} blocks local crawler access (HTTP {exc.code}); use RSS feeds or direct article URLs for this site."
            if exc.code == 404:
                return f"{source} does not expose this crawl seed (HTTP 404)."
            return f"{source} crawl unavailable (HTTP {exc.code}{f': {reason}' if reason else ''})."
        return f"{source} crawl unavailable: {exc}"

    @staticmethod
    def _parse_timestamp(value: object) -> pd.Timestamp:
        timestamp = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.notna(timestamp):
            return timestamp
        return pd.NaT

    @staticmethod
    def _token_match(text: str, alias: str) -> bool:
        alias = alias.lower().strip()
        if not alias:
            return False
        if any(char in alias for char in (" ", "/", "-", "=")):
            return alias in text
        return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None

    @staticmethod
    def _query_tokens(query_terms: str) -> list[str]:
        return [
            token.lower()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9&+\-]{2,}", query_terms)
            if token.upper() not in {"AND", "OR", "NOT"}
        ][:12]

    @staticmethod
    def _ticker_aliases(ticker: str) -> tuple[str, ...]:
        normalized = str(ticker).upper().strip()
        aliases = {normalized, f"${normalized}"}
        fx_pair = _compact_fx_pair(normalized)
        if fx_pair:
            base, quote = fx_pair[:3], fx_pair[3:]
            aliases.update(
                {
                    fx_pair,
                    f"{fx_pair}=X",
                    f"{base}/{quote}",
                    f"{base} {quote}",
                    f"{CURRENCY_NAMES.get(base, base)} {CURRENCY_NAMES.get(quote, quote)}",
                }
            )
        aliases.update(TOPIC_ALIASES.get(normalized, ()))
        return tuple(sorted(aliases, key=len, reverse=True))

    def _cache_path(self) -> Path:
        payload = {
            "feeds": self.feed_urls,
            "source_domains": self.source_domains,
            "direct_urls": self.direct_urls,
            "fetch_article_text": self.fetch_article_text,
            "max_text_chars": self.max_text_chars,
            "max_crawl_pages_per_source": self.max_crawl_pages_per_source,
            "version": self.LOCAL_WEB_SEARCH_TOOL,
        }
        fingerprint = sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{fingerprint}.jsonl"

    def _cache_is_fresh(self, path: Path) -> bool:
        if self.refresh_minutes <= 0 or not path.exists():
            return False
        age_seconds = time.time() - path.stat().st_mtime
        return age_seconds <= self.refresh_minutes * 60

    def _read_cache(self, path: Path) -> pd.DataFrame:
        if not path.exists():
            return self._empty_frame()
        try:
            frame = pd.read_json(path, lines=True)
        except ValueError:
            return self._empty_frame()
        return self._normalize_index_frame(frame)

    def _write_cache(self, path: Path, frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized = self._normalize_index_frame(frame)
        normalized.to_json(path, orient="records", lines=True, date_format="iso")

    @staticmethod
    def _empty_frame() -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "timestamp",
                "ticker",
                "headline",
                "title",
                "summary",
                "source",
                "url",
                "relevance",
                "local_web_search_tool",
                "extraction_status",
                "article_text_chars",
                "is_direct_url",
            ]
        )

    def _normalize_index_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return self._empty_frame()
        normalized = frame.copy()
        for column in self._empty_frame().columns:
            if column not in normalized.columns:
                normalized[column] = "" if column not in {"relevance", "article_text_chars", "is_direct_url"} else 0
        normalized["timestamp"] = pd.to_datetime(normalized["timestamp"], utc=True, errors="coerce").dt.tz_convert(None)
        normalized = normalized.dropna(subset=["timestamp"])
        normalized["ticker"] = normalized["ticker"].fillna("").astype(str).str.upper()
        normalized["headline"] = normalized["headline"].fillna("").astype(str)
        normalized["title"] = normalized["title"].fillna("").astype(str)
        normalized["summary"] = normalized["summary"].fillna("").astype(str)
        normalized["source"] = normalized["source"].fillna("").astype(str)
        normalized["url"] = normalized["url"].fillna("").astype(str)
        normalized["relevance"] = pd.to_numeric(normalized["relevance"], errors="coerce").fillna(0.5)
        normalized["local_web_search_tool"] = self.LOCAL_WEB_SEARCH_TOOL
        normalized["extraction_status"] = normalized["extraction_status"].fillna("feed").astype(str)
        normalized["article_text_chars"] = pd.to_numeric(normalized["article_text_chars"], errors="coerce").fillna(0).astype(int)
        normalized["is_direct_url"] = normalized["is_direct_url"].fillna(False).astype(bool)
        return normalized[list(self._empty_frame().columns)].reset_index(drop=True)

    def _urls_for_request(self, request: NewsRequest) -> list[tuple[str, str | None]]:
        urls: list[tuple[str, str | None]] = []
        for feed_url in self.feed_urls:
            if "{ticker}" in feed_url:
                urls.extend((feed_url.format(ticker=_rss_query_ticker(ticker)), ticker) for ticker in request.tickers)
            else:
                urls.append((feed_url, None))
        return urls

    def _article_details(self, url: str, fallback_title: str = "") -> tuple[str, str, str, str]:
        if not self.fetch_article_text or not url:
            return fallback_title, "", "", "metadata_only"
        try:
            html_text = self._fetch_text(url, headers={"User-Agent": self.user_agent})
            extracted = _extract_article(html_text, max_text_chars=self.max_text_chars)
        except Exception:
            return fallback_title, "", "", "fetch_failed"

        title = extracted["title"] or fallback_title
        summary = self.summarizer.summarize(title=title, description=extracted["description"], body=extracted["body"])
        return title, summary, extracted["body"], "extracted"

    @staticmethod
    def _is_probably_article_url(url: str, anchor: str = "") -> bool:
        parsed = urlparse(url)
        path = parsed.path.lower()
        if not parsed.scheme.startswith("http") or not parsed.netloc:
            return False
        if any(path.endswith(suffix) for suffix in (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".pdf", ".zip", ".mp4", ".mp3")):
            return False
        text = f"{path} {anchor.lower()}"
        if re.search(r"/20\d{2}[/\-]", text) or re.search(r"\b20\d{2}\b", text):
            return True
        article_terms = (
            "article",
            "story",
            "news",
            "markets",
            "market",
            "business",
            "finance",
            "investing",
            "stocks",
            "economy",
            "earnings",
            "analysis",
            "commodities",
            "forex",
        )
        return any(term in text for term in article_terms) and len(path.strip("/").split("/")) >= 2

    @staticmethod
    def _parse_sitemap_urls(xml_text: str) -> list[str]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []
        urls: list[str] = []
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1].lower() == "loc" and element.text:
                urls.append(_strip_markup(element.text))
        return urls

    def _candidate_urls_from_html(self, html_text: str, base_url: str) -> list[str]:
        base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
        candidates: list[str] = []
        seen: set[str] = set()
        for href, anchor in _extract_links(html_text):
            if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            url = urljoin(base_url, href).split("#", 1)[0]
            parsed = urlparse(url)
            host = parsed.netloc.lower().removeprefix("www.")
            if host != base_host or url in seen:
                continue
            if not self._is_probably_article_url(url, anchor):
                continue
            seen.add(url)
            candidates.append(url)
            if len(candidates) >= self.max_crawl_pages_per_source:
                break
        return candidates

    def _rows_from_article_url(
        self,
        url: str,
        explicit_ticker: str | None = None,
        *,
        is_direct_url: bool = False,
    ) -> list[dict[str, object]]:
        title, summary, body, status = self._article_details(url, fallback_title="")
        headline = " ".join(part for part in (title, summary) if part).strip()
        if not headline:
            return []
        return [
            {
                "timestamp": pd.Timestamp.now(tz="UTC").tz_convert(None),
                "ticker": str(explicit_ticker or "").upper(),
                "headline": headline,
                "title": title,
                "summary": summary,
                "source": self._source_name(url),
                "url": url,
                "relevance": 0.85 if status == "extracted" else 0.55,
                "local_web_search_tool": self.LOCAL_WEB_SEARCH_TOOL,
                "extraction_status": status,
                "article_text_chars": len(body),
                "is_direct_url": bool(is_direct_url),
            }
        ]

    def _crawl_seed_url(
        self,
        url: str,
        explicit_ticker: str | None = None,
        *,
        record_errors: bool = True,
        error_collector: list[str] | None = None,
    ) -> list[dict[str, object]]:
        url = self._ensure_url(url)
        try:
            text = self._fetch_text(url, headers={"User-Agent": self.user_agent})
        except Exception as exc:
            if self.skip_errors:
                message = self._crawl_error_message(url, exc)
                if error_collector is not None:
                    error_collector.append(message)
                elif record_errors:
                    self.last_errors.append(message)
                return []
            raise

        sitemap_urls = self._parse_sitemap_urls(text)
        if sitemap_urls:
            article_urls = [candidate for candidate in sitemap_urls if self._is_probably_article_url(candidate)]
        else:
            article_urls = self._candidate_urls_from_html(text, base_url=url)
            if not article_urls:
                return self._rows_from_article_url(url, explicit_ticker=explicit_ticker)

        rows: list[dict[str, object]] = []
        for article_url in article_urls[: self.max_crawl_pages_per_source]:
            rows.extend(self._rows_from_article_url(article_url, explicit_ticker=explicit_ticker))
        return rows

    def _domain_seed_urls(self) -> dict[str, list[str]]:
        urls_by_domain: dict[str, list[str]] = {}
        for domain in self.source_domains:
            candidates = [
                *self.SOURCE_DOMAIN_SEEDS.get(domain, ()),
                f"https://{domain}/sitemap.xml",
                f"https://www.{domain}/sitemap.xml",
                f"https://{domain}/",
                f"https://www.{domain}/",
            ]
            urls_by_domain[domain] = list(dict.fromkeys(candidates))
        return urls_by_domain

    def _fetch_feed_rows(self, request: NewsRequest) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for feed_url, explicit_ticker in self._urls_for_request(request):
            try:
                xml_text = self._fetch_text(feed_url, headers={"User-Agent": self.user_agent})
                feed_rows = RSSHeadlineProvider._parse_feed(xml_text, source_url=feed_url)[: self.max_items_per_feed]
            except Exception as exc:
                if not feed_url.lower().endswith((".xml", ".rss", ".atom")):
                    rows.extend(self._crawl_seed_url(feed_url, explicit_ticker=explicit_ticker))
                    continue
                if self.skip_errors:
                    self.last_errors.append(f"{self._source_name(feed_url)} failed: {exc}")
                    continue
                raise

            for row in feed_rows:
                record = dict(row)
                record["ticker"] = str(explicit_ticker or "").upper()
                record["source"] = self._source_name(feed_url)
                record["local_web_search_tool"] = self.LOCAL_WEB_SEARCH_TOOL
                record["extraction_status"] = "feed"
                record["article_text_chars"] = 0
                record["is_direct_url"] = False
                rows.append(record)
        return rows

    def _fetch_direct_rows(self, request: NewsRequest) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for template in self.direct_urls:
            explicit_pairs = (
                [(template.format(ticker=ticker), ticker) for ticker in request.tickers]
                if "{ticker}" in template
                else [(template, None)]
            )
            for url, explicit_ticker in explicit_pairs:
                rows.extend(self._rows_from_article_url(url, explicit_ticker=explicit_ticker, is_direct_url=True))
        return rows

    def _fetch_index_rows(self, request: NewsRequest) -> pd.DataFrame:
        rows = self._fetch_feed_rows(request)
        for domain, seed_urls in self._domain_seed_urls().items():
            domain_rows: list[dict[str, object]] = []
            seed_errors: list[str] = []
            for seed_url in seed_urls:
                domain_rows.extend(self._crawl_seed_url(seed_url, record_errors=False, error_collector=seed_errors))
                if len(domain_rows) >= self.max_crawl_pages_per_source:
                    break
            rows.extend(domain_rows)
            if not domain_rows and seed_errors:
                self.last_errors.append(
                    f"local-web:{domain} had no accessible crawl seed. Last issue: {seed_errors[-1]}"
                )
        rows.extend(self._fetch_direct_rows(request))
        if not rows:
            return self._empty_frame()
        return self._normalize_index_frame(pd.DataFrame(rows))

    def _row_score(self, row: pd.Series, ticker: str) -> float:
        extractor = TickerExtractor.default()
        score = extractor.score_row(row, ticker)
        if score > 0:
            text = " ".join(str(row.get(column, "")) for column in ("headline", "title", "summary", "source")).lower()
            query_bonus = 0.0
            for token in self._query_tokens(self.query_terms):
                if self._token_match(text, token):
                    query_bonus = min(query_bonus + 0.04, 0.16)
            score = min(score + query_bonus, 1.0)
        if score <= 0.0 and self.assign_single_ticker_when_unmatched and len(self._active_request_tickers) == 1 and bool(row.get("is_direct_url")):
            return 0.55
        return score

    def _search_index(self, frame: pd.DataFrame, request: NewsRequest) -> pd.DataFrame:
        if frame.empty:
            return self._empty_frame()
        start_ts = pd.Timestamp(request.start).normalize()
        end_ts = pd.Timestamp(request.end).normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        indexed = self._normalize_index_frame(frame)
        direct_mask = indexed["is_direct_url"].fillna(False).astype(bool)
        date_mask = (indexed["timestamp"] >= start_ts) & (indexed["timestamp"] <= end_ts)
        # Crawled article pages often do not expose a clean publication date.
        # Keep them searchable after discovery instead of silently dropping useful text.
        undated_crawl_mask = indexed["extraction_status"].eq("extracted") & (indexed["article_text_chars"] > 0)
        indexed = indexed[date_mask | direct_mask | undated_crawl_mask].reset_index(drop=True)

        self._active_request_tickers = tuple(request.tickers)
        rows: list[dict[str, object]] = []
        for ticker in request.tickers:
            scored_rows: list[dict[str, object]] = []
            for _, row in indexed.iterrows():
                score = self._row_score(row, ticker)
                if score <= 0:
                    continue
                record = row.to_dict()
                record["ticker"] = ticker
                record["relevance"] = max(float(record.get("relevance", 0.5)), score)
                scored_rows.append(record)
            scored_rows = sorted(scored_rows, key=lambda item: (pd.Timestamp(item["timestamp"]), float(item.get("relevance", 0))), reverse=True)
            rows.extend(scored_rows[: self.max_results_per_ticker])
        if not rows:
            return self._empty_frame()
        return self._normalize_index_frame(pd.DataFrame(rows)).sort_values(["timestamp", "ticker"]).reset_index(drop=True)

    def get_headlines(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = NewsRequest.from_inputs(tickers=tickers, start=start, end=end)
        cache_path = self._cache_path()
        cached = self._read_cache(cache_path)
        self.last_errors = []
        if not self._cache_is_fresh(cache_path):
            fetched = self._fetch_index_rows(request)
            if not fetched.empty:
                combined = pd.concat([cached, fetched], axis=0, ignore_index=True, sort=False) if not cached.empty else fetched
                cached = deduplicate_headlines(self._normalize_index_frame(combined))
                self._write_cache(cache_path, cached)
        return self._search_index(cached, request)


class WebResearchHeadlineProvider(RemoteHeadlineProvider):
    """
    Free lightweight web-research provider.

    It uses GDELT DOC 2.0 for discovery, optional user-provided URLs for
    source-limited browsing, and a stdlib extractive summarizer before the
    existing sentiment model scores the text.
    """

    GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(
        self,
        domains: Sequence[str] | None = None,
        research_urls: Sequence[str] | None = None,
        query_terms: str = "",
        max_articles_per_ticker: int = 4,
        fetch_article_text: bool = True,
        timeout_seconds: float = 18.0,
        max_text_chars: int = 12_000,
        user_agent: str = "QuantResearchApp/0.1 (+research; contact@example.com)",
        use_gdelt: bool = True,
        max_retry_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
        retry_max_sleep_seconds: float = 8.0,
        request_pause_seconds: float = 0.5,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.domains = tuple(dict.fromkeys(self._normalize_domain(domain) for domain in (domains or ()) if str(domain).strip()))
        self.research_urls = tuple(str(url).strip() for url in (research_urls or ()) if str(url).strip())
        self.query_terms = _strip_markup(query_terms)
        self.max_articles_per_ticker = min(max(int(max_articles_per_ticker), 1), 25)
        self.fetch_article_text = bool(fetch_article_text)
        self.max_text_chars = max(1_000, int(max_text_chars))
        self.user_agent = user_agent
        self.use_gdelt = bool(use_gdelt)
        self.summarizer = LightweightExtractiveSummarizer()
        self.max_retry_attempts = max(1, int(max_retry_attempts))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.retry_max_sleep_seconds = max(0.0, float(retry_max_sleep_seconds))
        self.request_pause_seconds = max(0.0, float(request_pause_seconds))

    @staticmethod
    def _normalize_domain(value: str) -> str:
        parsed = urlparse(str(value).strip())
        host = parsed.netloc or parsed.path
        return host.lower().removeprefix("www.").strip("/")

    @staticmethod
    def _source_name(url: str, fallback_domain: str | None = None) -> str:
        parsed = urlparse(str(url))
        host = (parsed.netloc or fallback_domain or "web").lower().removeprefix("www.")
        return f"web:{host}"

    @staticmethod
    def _parse_timestamp(value: object) -> pd.Timestamp:
        text = str(value or "").strip()
        if not text:
            return pd.NaT
        timestamp = pd.to_datetime(text, utc=True, errors="coerce")
        if pd.notna(timestamp):
            return timestamp
        for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
            timestamp = pd.to_datetime(text, format=fmt, utc=True, errors="coerce")
            if pd.notna(timestamp):
                return timestamp
        return pd.NaT

    def _query_for_ticker(self, ticker: str) -> str:
        base_query = _newsapi_query_for_ticker(ticker)
        parts = [f"({base_query})"]
        if self.query_terms:
            parts.append(self.query_terms)
        if self.domains:
            parts.append("(" + " OR ".join(f"domainis:{domain}" for domain in self.domains) + ")")
        return " ".join(parts)

    def _retry_delay(self, exc: HTTPError, attempt: int) -> float:
        retry_after = ""
        if getattr(exc, "headers", None):
            retry_after = str(exc.headers.get("Retry-After", "")).strip()
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), self.retry_max_sleep_seconds)
            except ValueError:
                timestamp = pd.to_datetime(retry_after, utc=True, errors="coerce")
                if pd.notna(timestamp):
                    delay = timestamp.to_pydatetime().timestamp() - time.time()
                    return min(max(delay, 0.0), self.retry_max_sleep_seconds)
        return min(self.retry_backoff_seconds * (2 ** attempt), self.retry_max_sleep_seconds)

    def _fetch_json_with_retries(
        self,
        url: str,
        params: dict[str, str],
        headers: dict[str, str] | None = None,
    ) -> dict | list:
        last_error: HTTPError | None = None
        for attempt in range(self.max_retry_attempts):
            try:
                return self._fetch_json(url, params, headers=headers)
            except HTTPError as exc:
                last_error = exc
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt >= self.max_retry_attempts - 1:
                    raise
                delay = self._retry_delay(exc, attempt)
                if delay > 0:
                    time.sleep(delay)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Web research request failed before a response was received.")

    def _gdelt_articles(self, ticker: str, request: NewsRequest) -> list[dict[str, object]]:
        if not self.use_gdelt:
            return []
        params = {
            "query": self._query_for_ticker(ticker),
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(self.max_articles_per_ticker),
            "sort": "datedesc",
            "startdatetime": pd.Timestamp(request.start).strftime("%Y%m%d000000"),
            "enddatetime": pd.Timestamp(request.end).strftime("%Y%m%d235959"),
        }
        payload = self._fetch_json_with_retries(self.GDELT_URL, params, headers={"User-Agent": self.user_agent})
        if not isinstance(payload, dict):
            return []
        articles = payload.get("articles") or payload.get("items") or []
        return articles if isinstance(articles, list) else []

    def _article_details(self, url: str, fallback_title: str = "") -> tuple[str, str, str, str]:
        if not self.fetch_article_text or not url:
            return fallback_title, "", "", "metadata_only"
        try:
            html_text = self._fetch_text(url, headers={"User-Agent": self.user_agent})
            extracted = _extract_article(html_text, max_text_chars=self.max_text_chars)
        except Exception:
            return fallback_title, "", "", "fetch_failed"

        title = extracted["title"] or fallback_title
        summary = self.summarizer.summarize(
            title=title,
            description=extracted["description"],
            body=extracted["body"],
        )
        return title, summary, extracted["body"], "extracted"

    def _rows_from_article(self, article: dict[str, object], ticker: str) -> list[dict[str, object]]:
        url = str(article.get("url") or article.get("link") or "").strip()
        title_hint = _strip_markup(article.get("title") or article.get("name") or "")
        domain = str(article.get("domain") or "").strip()
        timestamp = self._parse_timestamp(article.get("seendate") or article.get("date") or article.get("published") or article.get("publishedAt"))

        title, summary, body, status = self._article_details(url, fallback_title=title_hint)
        headline = " ".join(part for part in (title, summary) if part).strip()
        if not headline:
            return []

        return [
            {
                "timestamp": timestamp,
                "ticker": ticker,
                "headline": headline,
                "title": title,
                "summary": summary,
                "source": self._source_name(url, fallback_domain=domain),
                "url": url,
                "relevance": 0.9 if status == "extracted" else 0.65,
                "web_research_model": "lightweight_extractive_v1",
                "extraction_status": status,
                "article_text_chars": len(body),
                "is_direct_url": False,
            }
        ]

    def _direct_url_rows(self, request: NewsRequest) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        if not self.research_urls:
            return rows

        for template in self.research_urls:
            explicit_pairs = (
                [(template.format(ticker=ticker), ticker) for ticker in request.tickers]
                if "{ticker}" in template
                else [(template, None)]
            )
            for url, explicit_ticker in explicit_pairs:
                title, summary, body, status = self._article_details(url, fallback_title="")
                headline = " ".join(part for part in (title, summary) if part).strip()
                if not headline:
                    continue
                matched_tickers = [explicit_ticker] if explicit_ticker else RSSHeadlineProvider._infer_tickers(headline, set(request.tickers))
                if not matched_tickers and len(request.tickers) == 1:
                    matched_tickers = [request.tickers[0]]
                for ticker in matched_tickers:
                    rows.append(
                        {
                            "timestamp": pd.Timestamp.now(tz="UTC").tz_convert(None),
                            "ticker": str(ticker).upper(),
                            "headline": headline,
                            "title": title,
                            "summary": summary,
                            "source": self._source_name(url),
                            "url": url,
                            "relevance": 0.85 if status == "extracted" else 0.55,
                            "web_research_model": "lightweight_extractive_v1",
                            "extraction_status": status,
                            "article_text_chars": len(body),
                            "is_direct_url": True,
                        }
                    )
        return rows

    def get_headlines(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = NewsRequest.from_inputs(tickers=tickers, start=start, end=end)
        rows: list[dict[str, object]] = self._direct_url_rows(request)

        for index, ticker in enumerate(request.tickers):
            if index > 0 and self.request_pause_seconds > 0:
                time.sleep(self.request_pause_seconds)
            for article in self._gdelt_articles(ticker, request):
                rows.extend(self._rows_from_article(article, ticker))

        frame = pd.DataFrame(rows)
        if frame.empty:
            return pd.DataFrame(
                columns=[
                    "timestamp",
                    "ticker",
                    "headline",
                    "title",
                    "summary",
                    "relevance",
                    "source",
                    "url",
                    "web_research_model",
                    "extraction_status",
                    "article_text_chars",
                    "is_direct_url",
                ]
            )

        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dt.tz_convert(None)
        frame = frame.dropna(subset=["timestamp"])
        frame["ticker"] = frame["ticker"].astype(str).str.upper()
        start_ts = pd.Timestamp(request.start).normalize()
        end_ts = pd.Timestamp(request.end).normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        direct_url_mask = frame["is_direct_url"].fillna(False).astype(bool)
        date_mask = (frame["timestamp"] >= start_ts) & (frame["timestamp"] <= end_ts)
        frame = frame[date_mask | direct_url_mask]
        return frame.sort_values(["timestamp", "ticker"]).reset_index(drop=True)


class NewsAPIHeadlineProvider(RemoteHeadlineProvider):
    """
    NewsAPI.org /v2/everything adapter.

    This provider is useful as a limited free-tier supplement to RSS. It queries
    per ticker to keep symbol attribution explicit.
    """

    BASE_URL = "https://newsapi.org/v2/everything"

    def __init__(
        self,
        api_key: str,
        language: str = "en",
        sort_by: str = "publishedAt",
        page_size: int = 100,
        max_pages: int = 1,
        domains: Sequence[str] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.api_key = api_key
        self.language = language
        self.sort_by = sort_by
        self.page_size = min(max(int(page_size), 1), 100)
        self.max_pages = max(int(max_pages), 1)
        self.domains = tuple(domains or ())

    def get_headlines(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = NewsRequest.from_inputs(tickers=tickers, start=start, end=end)
        rows: list[dict[str, object]] = []

        for ticker in request.tickers:
            for page in range(1, self.max_pages + 1):
                params = {
                    "q": _newsapi_query_for_ticker(ticker),
                    "from": request.start,
                    "to": request.end,
                    "language": self.language,
                    "sortBy": self.sort_by,
                    "pageSize": str(self.page_size),
                    "page": str(page),
                    "domains": ",".join(self.domains),
                    "apiKey": self.api_key,
                }
                payload = self._fetch_json(self.BASE_URL, params)
                if not isinstance(payload, dict):
                    raise ValueError("Unexpected NewsAPI payload type.")
                if payload.get("status") == "error":
                    raise RuntimeError(f"NewsAPI error: {payload.get('code', 'unknown')} {payload.get('message', '')}")

                articles = payload.get("articles", [])
                if not articles:
                    break
                for article in articles:
                    title = _strip_markup(article.get("title", ""))
                    summary = _strip_markup(article.get("description", "") or article.get("content", ""))
                    source = article.get("source") or {}
                    rows.append(
                        {
                            "timestamp": pd.to_datetime(article.get("publishedAt"), utc=True, errors="coerce"),
                            "ticker": ticker,
                            "headline": " ".join(part for part in (title, summary) if part).strip(),
                            "title": title,
                            "summary": summary,
                            "source": source.get("name") if isinstance(source, dict) else "NewsAPI",
                            "url": article.get("url"),
                            "relevance": 1.0,
                            "author": article.get("author"),
                        }
                    )
                if len(articles) < self.page_size:
                    break

        frame = pd.DataFrame(rows)
        if frame.empty:
            return pd.DataFrame(columns=["timestamp", "ticker", "headline", "title", "summary", "relevance", "source", "url"])

        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dt.tz_convert(None)
        frame = frame.dropna(subset=["timestamp"])
        frame["ticker"] = frame["ticker"].astype(str).str.upper()
        return frame.sort_values(["timestamp", "ticker"]).reset_index(drop=True)


class AlphaVantageNewsProvider(RemoteHeadlineProvider):
    """
    Official docs:
    https://www.alphavantage.co/documentation/

    Uses the NEWS_SENTIMENT endpoint:
    - function=NEWS_SENTIMENT
    - optional tickers, topics, time_from, time_to, sort, limit
    """

    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(
        self,
        api_key: str,
        topics: Sequence[str] | None = None,
        sort: str = "LATEST",
        limit: int = 200,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.api_key = api_key
        self.topics = list(topics or [])
        self.sort = sort
        self.limit = min(max(int(limit), 1), 1000)

    def get_headlines(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = NewsRequest.from_inputs(tickers=tickers, start=start, end=end)
        query_tickers, alias_to_requested = _alphavantage_query_tickers(request.tickers)
        params = {
            "function": "NEWS_SENTIMENT",
            "tickers": ",".join(query_tickers),
            "topics": ",".join(self.topics),
            "time_from": pd.Timestamp(request.start).strftime("%Y%m%dT0000"),
            "time_to": pd.Timestamp(request.end).strftime("%Y%m%dT2359"),
            "sort": self.sort,
            "limit": str(self.limit),
            "apikey": self.api_key,
        }
        payload = self._fetch_json(self.BASE_URL, params)
        if not isinstance(payload, dict):
            raise ValueError("Unexpected Alpha Vantage payload type.")
        if "Note" in payload:
            raise RuntimeError(f"Alpha Vantage rate limit or usage notice: {payload['Note']}")
        if "Information" in payload:
            raise RuntimeError(f"Alpha Vantage information message: {payload['Information']}")
        if "Error Message" in payload:
            raise RuntimeError(f"Alpha Vantage error: {payload['Error Message']}")

        feed = payload.get("feed", [])
        rows: list[dict[str, object]] = []
        requested = set(alias_to_requested)
        for item in feed:
            timestamp = pd.to_datetime(item.get("time_published"), format="%Y%m%dT%H%M%S", errors="coerce")
            title = str(item.get("title", "")).strip()
            summary = str(item.get("summary", "")).strip()
            text = " ".join(part for part in (title, summary) if part)

            ticker_sentiment = item.get("ticker_sentiment") or []
            matched = False
            for ticker_info in ticker_sentiment:
                ticker = str(ticker_info.get("ticker", "")).upper()
                if ticker not in requested:
                    continue
                requested_ticker = alias_to_requested[ticker]
                rows.append(
                    {
                        "timestamp": timestamp,
                        "ticker": requested_ticker,
                        "headline": text,
                        "title": title,
                        "summary": summary,
                        "source": item.get("source"),
                        "url": item.get("url"),
                        "relevance": float(ticker_info.get("relevance_score", 1.0) or 1.0),
                        "provider_sentiment_score": float(item.get("overall_sentiment_score", 0.0) or 0.0),
                        "provider_sentiment_label": item.get("overall_sentiment_label"),
                    }
                )
                matched = True

            if not matched and len(request.tickers) == 1:
                ticker = next(iter(request.tickers))
                rows.append(
                    {
                        "timestamp": timestamp,
                        "ticker": ticker,
                        "headline": text,
                        "title": title,
                        "summary": summary,
                        "source": item.get("source"),
                        "url": item.get("url"),
                        "relevance": 1.0,
                        "provider_sentiment_score": float(item.get("overall_sentiment_score", 0.0) or 0.0),
                        "provider_sentiment_label": item.get("overall_sentiment_label"),
                    }
                )

        frame = pd.DataFrame(rows)
        if frame.empty:
            return pd.DataFrame(columns=["timestamp", "ticker", "headline", "relevance", "source", "url"])

        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=False).dt.tz_localize(None)
        frame["ticker"] = frame["ticker"].astype(str).str.upper()
        return frame.sort_values(["timestamp", "ticker"]).reset_index(drop=True)


class BenzingaNewsProvider(RemoteHeadlineProvider):
    """
    Official docs:
    https://docs.benzinga.com/api-reference/news-api/get-news-items

    Uses:
    - GET /api/v2/news
    - token query parameter
    - dateFrom / dateTo / tickers / page / pageSize / displayOutput
    """

    BASE_URL = "https://api.benzinga.com/api/v2/news"

    def __init__(
        self,
        api_key: str,
        display_output: str = "abstract",
        page_size: int = 100,
        max_pages: int = 5,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.api_key = api_key
        self.display_output = display_output
        self.page_size = min(max(int(page_size), 1), 100)
        self.max_pages = max(int(max_pages), 1)

    def get_headlines(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = NewsRequest.from_inputs(tickers=tickers, start=start, end=end)
        requested = set(request.tickers)
        rows: list[dict[str, object]] = []

        for page in range(self.max_pages):
            params = {
                "token": self.api_key,
                "tickers": ",".join(request.tickers),
                "dateFrom": request.start,
                "dateTo": request.end,
                "page": str(page),
                "pageSize": str(self.page_size),
                "displayOutput": self.display_output,
            }
            payload = self._fetch_json(self.BASE_URL, params, headers={"accept": "application/json"})
            if not isinstance(payload, list):
                raise ValueError("Unexpected Benzinga payload type.")
            if not payload:
                break

            for item in payload:
                title = str(item.get("title", "")).strip()
                teaser = str(item.get("teaser", "")).strip()
                body = str(item.get("body", "")).strip()
                text = " ".join(part for part in (title, teaser, body) if part)
                timestamp = pd.to_datetime(item.get("created"), errors="coerce")
                stocks = item.get("stocks") or []
                matched_tickers = [
                    str(stock.get("name", "")).upper()
                    for stock in stocks
                    if str(stock.get("name", "")).upper() in requested
                ]

                if not stocks and not matched_tickers and len(requested) == 1:
                    matched_tickers = [next(iter(requested))]

                channels = item.get("channels") or []
                channel_names = [str(channel.get("name", "")).strip() for channel in channels if channel.get("name")]

                for ticker in matched_tickers:
                    rows.append(
                        {
                            "timestamp": timestamp,
                            "ticker": ticker,
                            "headline": text,
                            "title": title,
                            "summary": teaser or body,
                            "source": "Benzinga",
                            "url": item.get("url"),
                            "relevance": 1.0,
                            "channels": ",".join(channel_names),
                            "author": item.get("author"),
                        }
                    )

            if len(payload) < self.page_size:
                break

        frame = pd.DataFrame(rows)
        if frame.empty:
            return pd.DataFrame(columns=["timestamp", "ticker", "headline", "relevance", "source", "url"])

        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=False).dt.tz_localize(None)
        frame["ticker"] = frame["ticker"].astype(str).str.upper()
        return frame.sort_values(["timestamp", "ticker"]).reset_index(drop=True)


class CompositeHeadlineProvider(HeadlineProvider):
    def __init__(
        self,
        providers: Sequence[HeadlineProvider],
        dedup_config: HeadlineDedupConfig = HeadlineDedupConfig(),
        skip_errors: bool = True,
    ) -> None:
        if not providers:
            raise ValueError("CompositeHeadlineProvider requires at least one underlying provider.")
        self.providers = list(providers)
        self.dedup_config = dedup_config
        self.skip_errors = skip_errors
        self.last_errors: list[str] = []

    def get_headlines(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        self.last_errors = []
        for provider in self.providers:
            try:
                frame = provider.get_headlines(tickers=tickers, start=start, end=end).copy()
            except Exception as exc:
                if not self.skip_errors:
                    raise
                self.last_errors.append(_safe_provider_error(provider, exc))
                continue
            nested_errors = getattr(provider, "last_errors", [])
            if isinstance(nested_errors, list):
                self.last_errors.extend(str(error) for error in nested_errors if str(error).strip())
            if frame.empty:
                continue
            frame["provider_name"] = provider.__class__.__name__
            if "source" not in frame.columns:
                frame["source"] = frame["provider_name"]
            frame["source"] = frame["source"].fillna(frame["provider_name"]).astype(str)
            if "relevance" not in frame.columns:
                frame["relevance"] = 1.0
            frames.append(frame)

        if not frames:
            return pd.DataFrame(columns=["timestamp", "ticker", "headline", "relevance", "source", "url"])

        combined = pd.concat(frames, axis=0, ignore_index=True, sort=False)
        return deduplicate_headlines(combined, config=self.dedup_config)


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".json":
        return pd.read_json(path)
    raise ValueError(f"Unsupported table format '{path.suffix}' for {path}")


class LocalNewsFileProvider(HeadlineProvider):
    def __init__(
        self,
        path: str | Path,
        timestamp_col: str = "timestamp",
        ticker_col: str = "ticker",
        headline_col: str = "headline",
    ) -> None:
        self.path = Path(path)
        self.timestamp_col = timestamp_col
        self.ticker_col = ticker_col
        self.headline_col = headline_col

    def get_headlines(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        frame = _read_table(self.path).copy()
        required = {self.timestamp_col, self.ticker_col, self.headline_col}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing headline columns in {self.path}: {sorted(missing)}")

        frame = frame.rename(
            columns={
                self.timestamp_col: "timestamp",
                self.ticker_col: "ticker",
                self.headline_col: "headline",
            }
        )
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=False).dt.tz_localize(None)
        frame["ticker"] = frame["ticker"].astype(str).str.upper()

        normalized_tickers = {str(ticker).upper() for ticker in tickers}
        start_ts = pd.Timestamp(start).normalize()
        end_ts = pd.Timestamp(end).normalize()
        mask = (
            frame["ticker"].isin(normalized_tickers)
            & (frame["timestamp"] >= start_ts)
            & (frame["timestamp"] <= end_ts + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1))
        )
        filtered = frame.loc[mask].sort_values(["timestamp", "ticker"]).reset_index(drop=True)
        if "relevance" not in filtered.columns:
            filtered["relevance"] = 1.0
        if "source" not in filtered.columns:
            filtered["source"] = self.path.stem
        if "provider_name" not in filtered.columns:
            filtered["provider_name"] = self.__class__.__name__
        return filtered


class DailySentimentFileProvider(DailySentimentProvider):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def get_daily_sentiment(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        frame = _read_table(self.path).copy()
        required = {
            "date",
            "ticker",
            "sentiment_score",
            "sentiment_abs",
            "confidence",
            "article_count",
            "positive_prob",
            "negative_prob",
            "neutral_prob",
        }
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"Missing daily sentiment columns in {self.path}: {sorted(missing)}")

        frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None).dt.normalize()
        frame["ticker"] = frame["ticker"].astype(str).str.upper()
        normalized_tickers = {str(ticker).upper() for ticker in tickers}
        start_ts = pd.Timestamp(start).normalize()
        end_ts = pd.Timestamp(end).normalize()
        mask = (
            frame["ticker"].isin(normalized_tickers)
            & (frame["date"] >= start_ts)
            & (frame["date"] <= end_ts)
        )
        return frame.loc[mask].sort_values(["date", "ticker"]).reset_index(drop=True)


class CachedNewsSentimentProvider(DailySentimentProvider):
    """
    Fetches raw headlines from a provider, scores them with a sentiment model, and caches the
    aggregated daily sentiment output to parquet.
    """

    def __init__(
        self,
        headline_provider: HeadlineProvider,
        sentiment_model: BaseSentimentModel,
        cache_dir: str | Path = "data/sentiment_cache",
    ) -> None:
        self.headline_provider = headline_provider
        self.sentiment_model = sentiment_model
        self.aggregator = NewsSentimentAggregator(model=sentiment_model)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _paths_for(self, request: NewsRequest) -> tuple[Path, Path]:
        extra = {
            "headline_provider": self.headline_provider.__class__.__name__,
            "sentiment_model": self.sentiment_model.__class__.__name__,
        }
        key = request.cache_key(extra=extra)
        parquet_path = self.cache_dir / f"{key}.parquet"
        meta_path = self.cache_dir / f"{key}.json"
        return parquet_path, meta_path

    def get_daily_sentiment(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = NewsRequest.from_inputs(tickers=tickers, start=start, end=end)
        parquet_path, meta_path = self._paths_for(request)

        if parquet_path.exists():
            cached = pd.read_parquet(parquet_path)
            cached["date"] = pd.to_datetime(cached["date"]).dt.tz_localize(None).dt.normalize()
            cached["ticker"] = cached["ticker"].astype(str).str.upper()
            return cached.sort_values(["date", "ticker"]).reset_index(drop=True)

        headlines = self.headline_provider.get_headlines(
            tickers=request.tickers,
            start=request.start,
            end=request.end,
        )
        daily_sentiment = self.aggregator.build_daily_sentiment(headlines)
        daily_sentiment.to_parquet(parquet_path)

        metadata = {
            **asdict(request),
            "headline_provider": self.headline_provider.__class__.__name__,
            "sentiment_model": self.sentiment_model.__class__.__name__,
            "headline_count": int(len(headlines)),
        }
        with meta_path.open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2)

        return daily_sentiment
