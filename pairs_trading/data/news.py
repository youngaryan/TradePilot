from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
import html
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from typing import Sequence
import xml.etree.ElementTree as ET

import pandas as pd

from ..features.sentiment import BaseSentimentModel, NewsSentimentAggregator


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
            new_row["source_list"] = _as_sorted_csv(sources)
            new_row["provider_list"] = _as_sorted_csv(providers)
            new_row["url_list"] = _as_sorted_csv(urls)
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
            if row.get("source"):
                source_set.add(str(row["source"]))
            if row.get("provider_name"):
                provider_set.add(str(row["provider_name"]))
            if row.get("url"):
                url_set.add(str(row["url"]))

            existing["source_list"] = _as_sorted_csv(source_set)
            existing["provider_list"] = _as_sorted_csv(provider_set)
            existing["url_list"] = _as_sorted_csv(url_set)
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


def _safe_provider_error(provider: HeadlineProvider, exc: Exception) -> str:
    label = _provider_label(provider)
    if isinstance(exc, HTTPError):
        return f"{label} failed with HTTP {exc.code} {exc.reason}."
    if isinstance(exc, URLError):
        return f"{label} network error: {exc.reason}."
    return f"{label} failed: {exc}"


def _strip_markup(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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
        assign_single_ticker_when_unmatched: bool = True,
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
        tokens = {token.upper() for token in re.findall(r"\$?[A-Z]{1,5}(?:\.[A-Z])?", text)}
        tokens |= {token[1:] for token in tokens if token.startswith("$")}
        return sorted(ticker for ticker in requested if ticker in tokens)

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

            if not matched and len(requested) == 1:
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
