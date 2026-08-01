from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen


class SecCompanyFactsClient:
    """Shared SEC ticker-map/company-facts fetch and cache foundation."""

    TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
    COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

    def __init__(
        self,
        *,
        cache_dir: str | Path,
        fetch_json: Callable[[str], dict[str, Any]] | None = None,
        user_agent: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if fetch_json is None and (not user_agent or "@" not in user_agent):
            raise ValueError("SEC requests require a descriptive User-Agent with contact information.")
        self.user_agent = user_agent
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        self.fetch_json = fetch_json or self._fetch_remote

    def _fetch_remote(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={"User-Agent": str(self.user_agent), "Accept": "application/json"},
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - fixed SEC endpoints only
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("SEC returned an invalid JSON object.")
        return payload

    def _cached_json(self, path: Path, url: str) -> dict[str, Any]:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            payload = self.fetch_json(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
        if not isinstance(payload, dict):
            raise ValueError("SEC returned an invalid JSON object.")
        return payload

    def ticker_map(self) -> dict[str, str]:
        data = self._cached_json(self.cache_dir / "company_tickers.json", self.TICKER_MAP_URL)
        mapping: dict[str, str] = {}
        for record in data.values():
            if not isinstance(record, dict):
                continue
            ticker = str(record.get("ticker", "")).upper()
            cik = str(record.get("cik_str", "")).strip()
            if ticker and cik:
                mapping[ticker] = cik.zfill(10)
        return mapping

    def company_facts(self, cik: str) -> dict[str, Any]:
        normalized = str(cik).zfill(10)
        return self._cached_json(
            self.cache_dir / "companyfacts" / f"CIK{normalized}.json",
            self.COMPANY_FACTS_URL.format(cik=normalized),
        )

    def company_facts_for_ticker(self, ticker: str) -> tuple[str, dict[str, Any]] | None:
        cik = self.ticker_map().get(str(ticker).upper())
        if cik is None:
            return None
        return cik, self.company_facts(cik)


__all__ = ["SecCompanyFactsClient"]
