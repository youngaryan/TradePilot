from __future__ import annotations

from typing import Any, Sequence

import pandas as pd

from .news import NewsRequest, RemoteHeadlineProvider, _strip_markup

HEADLINE_COLUMNS = ("timestamp", "ticker", "headline", "relevance", "source", "url")

STOCKTWITS_API_BASE = "https://api.stocktwits.com/api/3/streams/symbol"


class StockTwitsHeadlineProvider(RemoteHeadlineProvider):
    """
    Fetches crowd-sourced messages from StockTwits for ticker-level sentiment.

    API: GET /api/3/streams/symbol/{ticker}.json
    Auth: Bearer <access_token>
    Pagination: cursor-based, max 30 per page, up to max_pages pages.

    Messages include optional user-voted sentiment (Bullish/Bearish) in
    entities.sentiment.basic, exposed as the "user_sentiment" column.
    """

    def __init__(
        self,
        access_token: str,
        max_pages: int = 5,
        timeout_seconds: float = 15.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        if not access_token:
            raise ValueError("StockTwits access token is required.")
        self.access_token = access_token
        self.max_pages = max(int(max_pages), 1)
        self.last_errors: list[str] = []

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def get_headlines(
        self,
        tickers: Sequence[str],
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
    ) -> pd.DataFrame:
        request = NewsRequest.from_inputs(tickers=tickers, start=start, end=end)
        rows: list[dict[str, Any]] = []
        self.last_errors = []
        start_ts = pd.Timestamp(request.start)
        end_ts = pd.Timestamp(request.end) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)

        for ticker in request.tickers:
            cursor: str | None = None
            for page_number in range(1, self.max_pages + 1):
                params: dict[str, str] = {"max": "30"}
                if cursor:
                    params["cursor"] = cursor
                url = f"{STOCKTWITS_API_BASE}/{ticker}.json"
                payload = self._fetch_json(url, params, headers=self._headers())
                if not isinstance(payload, dict):
                    break

                messages = payload.get("messages", [])
                if not messages:
                    break

                page_timestamps: list[pd.Timestamp] = []
                for msg in messages:
                    created = pd.to_datetime(msg.get("created_at"), errors="coerce")
                    if created is pd.NaT:
                        continue
                    if created.tz is not None:
                        created = created.tz_localize(None)
                    page_timestamps.append(created)
                    if created < start_ts or created > end_ts:
                        continue

                    body = _strip_markup(msg.get("body", ""))
                    if not body:
                        continue

                    sentiment_raw = None
                    entities = msg.get("entities") or {}
                    sentiment_entity = entities.get("sentiment") or {}
                    sentiment_raw = sentiment_entity.get("basic")
                    if sentiment_raw not in ("Bullish", "Bearish"):
                        sentiment_raw = None

                    username = str(msg.get("user", {}).get("username", ""))
                    message_id = str(msg.get("id", ""))
                    rows.append({
                        "timestamp": created,
                        "ticker": ticker,
                        "headline": body,
                        "source": "StockTwits",
                        "relevance": 1.0,
                        "url": f"https://stocktwits.com/{username}/{message_id}" if username and message_id else "",
                        "user_sentiment": sentiment_raw,
                        "user_username": username,
                        "message_id": message_id,
                    })

                cursor_data = payload.get("cursor") or {}
                cursor = cursor_data.get("next")
                has_more = bool(cursor_data.get("more", False))
                if page_timestamps and min(page_timestamps) <= start_ts:
                    break
                if not cursor or not has_more:
                    break
                if page_number == self.max_pages:
                    self.last_errors.append(
                        f"StockTwits coverage for {ticker} is incomplete: the {self.max_pages}-page safety limit "
                        f"was reached before the requested start date {request.start}."
                    )

        if not rows:
            return pd.DataFrame(columns=list(HEADLINE_COLUMNS) + ["user_sentiment", "user_username", "message_id"])

        frame = pd.DataFrame(rows)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"]).dt.tz_localize(None)
        frame["ticker"] = frame["ticker"].astype(str).str.upper()
        return frame.sort_values(["timestamp", "ticker"]).reset_index(drop=True)
