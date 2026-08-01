from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import pandas as pd

from ..data.events import EventProvider, SecCompanyFactsEventProvider, SecCompanyFilingsEventProvider
from ..data.market import CachedParquetProvider, MarketDataProvider, YahooFinanceProvider
from ..engines.backtesting import json_ready
from .config import BackendSettings


FINANCIAL_EVENT_FORMS = (
    "8-K",
    "8-K/A",
    "10-Q",
    "10-Q/A",
    "10-K",
    "10-K/A",
    "S-1",
    "S-1/A",
    "S-3",
    "S-3/A",
    "424B2",
    "424B5",
    "DEF 14A",
    "DEFA14A",
)

EVENT_LABELS = {
    "annual_earnings_report": "Annual earnings report",
    "quarterly_earnings_report": "Quarterly earnings report",
    "earnings_release_8k": "Earnings release",
    "edgar_companyfacts": "Reported financial results",
    "dividend_announcement": "Dividend announcement",
    "buyback_announcement": "Buyback announcement",
    "guidance_update": "Guidance update",
    "merger_acquisition_update": "Merger/acquisition update",
    "debt_financing": "Debt financing",
    "equity_financing": "Equity financing",
    "investor_presentation": "Investor presentation",
    "regulatory_filing": "Regulatory filing",
    "material_8k": "Material 8-K",
}


@dataclass(frozen=True)
class FinancialEventRequest:
    symbols: tuple[str, ...]
    start: str
    end: str

    @classmethod
    def from_inputs(cls, symbols: Sequence[str], start: str, end: str) -> "FinancialEventRequest":
        normalized = tuple(dict.fromkeys(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()))
        if not normalized:
            raise ValueError("Choose at least one symbol for financial events.")
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        if pd.isna(start_ts) or pd.isna(end_ts):
            raise ValueError("Financial event dates must be valid YYYY-MM-DD values.")
        if end_ts < start_ts:
            raise ValueError("Financial event end date must be on or after the start date.")
        return cls(
            symbols=normalized,
            start=start_ts.strftime("%Y-%m-%d"),
            end=end_ts.strftime("%Y-%m-%d"),
        )


def _clean(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _optional_str(value: Any) -> str:
    cleaned = _clean(value)
    return "" if cleaned is None else str(cleaned)


def _optional_float(value: Any) -> float | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    try:
        number = float(cleaned)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def _format_money(value: float | None) -> str | None:
    if value is None:
        return None
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return f"${value:,.0f}"


def _format_eps(value: float | None) -> str | None:
    if value is None:
        return None
    return f"${value:.2f}"


def _format_percent(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:+.1%}"


def _base_form(value: Any) -> str:
    form = _optional_str(value).upper().strip()
    return form[:-2] if form.endswith("/A") else form


def _event_label(event_type: str) -> str:
    return EVENT_LABELS.get(event_type, event_type.replace("_", " ").title())


def _confidence_bucket(confidence: float, missing_fields: Sequence[str]) -> str:
    if confidence >= 0.72 and len(missing_fields) <= 2:
        return "high"
    if confidence >= 0.42:
        return "medium"
    return "low"


def _direction_from_score(value: float | None) -> str:
    if value is None:
        return "neutral"
    if value > 0.05:
        return "positive"
    if value < -0.05:
        return "negative"
    return "neutral"


class FinancialEventsService:
    def __init__(
        self,
        settings: BackendSettings,
        *,
        filings_provider: EventProvider | None = None,
        companyfacts_provider: EventProvider | None = None,
        market_data_provider: MarketDataProvider | None = None,
    ) -> None:
        self.settings = settings
        self._filings_provider = filings_provider
        self._companyfacts_provider = companyfacts_provider
        self._market_data_provider = market_data_provider

    def _edgar_user_agent(self) -> str:
        configured = self.settings.edgar_user_agent or ""
        if "@" in configured:
            return configured
        if self.settings.email_from and "@" in self.settings.email_from:
            return f"QuantOps financial-events research {self.settings.email_from}"
        return "QuantOps financial-events research no-reply@quantops.local"

    def _filings(self) -> EventProvider:
        if self._filings_provider is None:
            self._filings_provider = SecCompanyFilingsEventProvider(
                user_agent=self._edgar_user_agent(),
                cache_dir=self.settings.event_cache_dir / "sec",
                forms=FINANCIAL_EVENT_FORMS,
                include_historical_files=True,
            )
        return self._filings_provider

    def _companyfacts(self) -> EventProvider:
        if self._companyfacts_provider is None:
            self._companyfacts_provider = SecCompanyFactsEventProvider(
                user_agent=self._edgar_user_agent(),
                cache_dir=self.settings.event_cache_dir / "sec",
            )
        return self._companyfacts_provider

    def _market_data(self) -> MarketDataProvider:
        if self._market_data_provider is None:
            self._market_data_provider = CachedParquetProvider(
                upstream=YahooFinanceProvider(tz_cache_dir=self.settings.price_cache_dir / "yfinance_tz_cache"),
                cache_dir=self.settings.price_cache_dir,
            )
        return self._market_data_provider

    @staticmethod
    def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
        if frame.empty:
            return []
        prepared = frame.copy()
        if "timestamp" in prepared.columns:
            prepared["timestamp"] = pd.to_datetime(prepared["timestamp"], errors="coerce").dt.tz_localize(None)
        return prepared.to_dict("records")

    @staticmethod
    def _companyfacts_url(fact: dict[str, Any]) -> str:
        cik = _optional_str(fact.get("cik"))
        if not cik:
            return ""
        return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json"

    @staticmethod
    def _reported_metrics(fact: dict[str, Any] | None) -> tuple[dict[str, float | None], str | None]:
        if not fact:
            return {"revenue": None, "earnings": None, "eps": None, "revenue_yoy": None, "earnings_yoy": None, "eps_yoy": None}, None
        metrics = {
            "revenue": _optional_float(fact.get("revenue")),
            "earnings": _optional_float(fact.get("earnings")),
            "eps": _optional_float(fact.get("eps")),
            "revenue_yoy": _optional_float(fact.get("revenue_yoy")),
            "earnings_yoy": _optional_float(fact.get("earnings_yoy")),
            "eps_yoy": _optional_float(fact.get("eps_yoy")),
        }
        parts: list[str] = []
        if metrics["revenue"] is not None:
            detail = _format_money(metrics["revenue"])
            yoy = _format_percent(metrics["revenue_yoy"])
            parts.append(f"Revenue {detail}{f' YoY {yoy}' if yoy else ''}")
        if metrics["earnings"] is not None:
            detail = _format_money(metrics["earnings"])
            yoy = _format_percent(metrics["earnings_yoy"])
            parts.append(f"Net income {detail}{f' YoY {yoy}' if yoy else ''}")
        if metrics["eps"] is not None:
            detail = _format_eps(metrics["eps"])
            yoy = _format_percent(metrics["eps_yoy"])
            parts.append(f"EPS {detail}{f' YoY {yoy}' if yoy else ''}")
        return metrics, "; ".join(parts) if parts else None

    @staticmethod
    def _match_fact(filing: dict[str, Any], facts: Sequence[dict[str, Any]], used_indexes: set[int]) -> tuple[int | None, dict[str, Any] | None]:
        ticker = _optional_str(filing.get("ticker")).upper()
        filing_date = pd.Timestamp(filing.get("timestamp")).strftime("%Y-%m-%d")
        filing_form = _base_form(filing.get("form"))
        same_ticker = [
            (index, fact)
            for index, fact in enumerate(facts)
            if index not in used_indexes and _optional_str(fact.get("ticker")).upper() == ticker
        ]
        for index, fact in same_ticker:
            fact_date = pd.Timestamp(fact.get("timestamp")).strftime("%Y-%m-%d")
            if fact_date == filing_date and _base_form(fact.get("form")) == filing_form:
                return index, fact
        for index, fact in same_ticker:
            fact_date = pd.Timestamp(fact.get("timestamp")).strftime("%Y-%m-%d")
            if fact_date == filing_date:
                return index, fact
        return None, None

    def _load_event_frames(self, request: FinancialEventRequest) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
        warnings: list[str] = []
        try:
            filings = self._filings().get_events(request.symbols, request.start, request.end)
        except Exception as exc:
            filings = pd.DataFrame()
            warnings.append(f"SEC submissions events were unavailable: {exc}")
        try:
            facts = self._companyfacts().get_events(request.symbols, request.start, request.end)
        except Exception as exc:
            facts = pd.DataFrame()
            warnings.append(f"SEC company facts were unavailable: {exc}")
        return filings, facts, warnings

    def _build_row(self, event: dict[str, Any], fact: dict[str, Any] | None = None, *, facts_only: bool = False) -> dict[str, Any]:
        event_type = "edgar_companyfacts" if facts_only else _optional_str(event.get("event_type")) or "material_financial_event"
        event_date = pd.Timestamp(event.get("timestamp")).strftime("%Y-%m-%d")
        ticker = _optional_str(event.get("ticker")).upper()
        form = _optional_str(event.get("form"))
        report_date = _optional_str(event.get("report_date"))
        description = _optional_str(event.get("description"))
        items = _optional_str(event.get("items"))
        metrics, reported_result = self._reported_metrics(fact if fact is not None else event if facts_only else None)
        source_url = _optional_str(event.get("url")) or (self._companyfacts_url(event) if facts_only else "")
        source = "SEC company facts" if facts_only else "SEC EDGAR filing"
        confidence_inputs = [_optional_float(event.get("confidence"))]
        if fact is not None:
            confidence_inputs.append(_optional_float(fact.get("confidence")))
        confidence_values = [value for value in confidence_inputs if value is not None]
        confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
        missing_fields = ["expected_result", "beat_miss"]
        if reported_result is None:
            missing_fields.append("reported_result")
        if not source_url:
            missing_fields.append("source_url")
        title_base = _event_label(event_type)
        if description:
            title = f"{title_base}: {description}"
        elif facts_only:
            period = " ".join(part for part in (_optional_str(event.get("fy")), _optional_str(event.get("fp"))) if part)
            title = f"{title_base}{f' for {period}' if period else ''}"
        else:
            title = f"{title_base}{f' ({form})' if form else ''}"
        summary_parts = [f"{source} recorded {title_base.lower()} for {ticker} on {event_date}."]
        if form:
            summary_parts.append(f"Form {form}.")
        if items:
            summary_parts.append(f"Items {items}.")
        if report_date:
            summary_parts.append(f"Report date {report_date}.")
        if reported_result:
            summary_parts.append(f"Reported metrics: {reported_result}.")
        if not reported_result:
            summary_parts.append("No numeric result was available from the retrieved filing metadata.")
        event_score = _optional_float(event.get("event_score"))
        direction_source = event_score
        if metrics["revenue_yoy"] is not None or metrics["earnings_yoy"] is not None or metrics["eps_yoy"] is not None:
            yoy_values = [metrics[key] for key in ("revenue_yoy", "earnings_yoy", "eps_yoy") if metrics[key] is not None]
            direction_source = sum(yoy_values) / len(yoy_values) if yoy_values else event_score
        return {
            "id": f"{ticker}-{event_date}-{event_type}-{_optional_str(event.get('accession_number')) or form or 'facts'}",
            "date": event_date,
            "ticker": ticker,
            "event_type": event_type,
            "event_type_label": title_base,
            "event_title": title,
            "summary": " ".join(summary_parts),
            "reported_result": reported_result,
            "reported_metrics": metrics,
            "expected_result": None,
            "beat_miss": "not_available",
            "market_reaction": None,
            "market_reaction_pct": None,
            "market_reaction_source": None,
            "source": source,
            "source_url": source_url or None,
            "confidence": round(float(max(0.0, min(confidence, 1.0))), 3),
            "data_completeness": _confidence_bucket(confidence, missing_fields),
            "verified_fields": [
                "date",
                "event_type",
                "event_title",
                "source",
                *(["reported_result"] if reported_result else []),
            ],
            "inferred_fields": ["event_direction"],
            "missing_fields": missing_fields,
            "event_direction": _direction_from_score(direction_source),
            "form": form or None,
            "report_date": report_date or None,
            "accession_number": _optional_str(event.get("accession_number")) or None,
        }

    def _build_rows(self, filings: pd.DataFrame, facts: pd.DataFrame) -> list[dict[str, Any]]:
        filing_records = self._records(filings)
        fact_records = self._records(facts)
        used_fact_indexes: set[int] = set()
        rows: list[dict[str, Any]] = []

        for filing in filing_records:
            fact_index, fact = self._match_fact(filing, fact_records, used_fact_indexes)
            if fact_index is not None:
                used_fact_indexes.add(fact_index)
            rows.append(self._build_row(filing, fact))

        for index, fact in enumerate(fact_records):
            if index in used_fact_indexes:
                continue
            rows.append(self._build_row(fact, None, facts_only=True))

        deduped: dict[str, dict[str, Any]] = {}
        for row in rows:
            deduped[row["id"]] = row
        return list(deduped.values())

    def _attach_market_reactions(self, rows: list[dict[str, Any]], request: FinancialEventRequest, warnings: list[str]) -> None:
        if not rows:
            return
        start_ts = pd.Timestamp(request.start) - pd.Timedelta(days=7)
        end_ts = pd.Timestamp(request.end) + pd.Timedelta(days=10)
        try:
            prices = self._market_data().get_close_prices(
                symbols=request.symbols,
                start=start_ts.strftime("%Y-%m-%d"),
                end=end_ts.strftime("%Y-%m-%d"),
            )
        except Exception as exc:
            warnings.append(f"Market reaction prices were unavailable: {exc}")
            return

        for row in rows:
            ticker = str(row["ticker"])
            if ticker not in prices.columns:
                continue
            series = pd.to_numeric(prices[ticker], errors="coerce").dropna().sort_index()
            if series.empty:
                continue
            event_date = pd.Timestamp(row["date"])
            before = series[series.index < event_date]
            after = series[series.index > event_date]
            if before.empty or after.empty:
                continue
            prior_close = float(before.iloc[-1])
            next_close = float(after.iloc[0])
            if prior_close == 0:
                continue
            reaction = next_close / prior_close - 1.0
            row["market_reaction_pct"] = round(reaction, 6)
            row["market_reaction"] = f"{reaction:+.2%} next available close vs prior close"
            row["market_reaction_source"] = "Yahoo Finance close prices"
            if "market_reaction" not in row["verified_fields"]:
                row["verified_fields"].append("market_reaction")

    @staticmethod
    def _analysis(rows: Sequence[dict[str, Any]], warnings: Sequence[str]) -> dict[str, Any]:
        verified: list[str] = []
        inferred: list[str] = []
        risks: list[str] = []
        catalysts: list[str] = []
        missing_data = [
            "Consensus estimates are not available from SEC filings, so expected result and beat/miss status remain unavailable unless a separate estimates provider is added."
        ]
        missing_data.extend(warnings)

        if not rows:
            return {
                "summary": "No verified financial events were found for the selected symbols and date window.",
                "verified": [],
                "inferred": ["No financial-event trend can be inferred without verified event rows."],
                "risks": [],
                "catalysts": [],
                "missing_data": list(dict.fromkeys(missing_data)),
                "source_notes": [
                    "Financial event rows are built from SEC EDGAR submissions and SEC company facts where available.",
                    "Market reaction uses next available close versus prior close when price history is available.",
                ],
            }

        by_type: dict[str, int] = {}
        by_direction = {"positive": 0, "negative": 0, "neutral": 0}
        reaction_values: list[float] = []
        for row in rows:
            by_type[str(row.get("event_type_label") or row.get("event_type"))] = by_type.get(str(row.get("event_type_label") or row.get("event_type")), 0) + 1
            direction = str(row.get("event_direction") or "neutral")
            if direction in by_direction:
                by_direction[direction] += 1
            reaction = _optional_float(row.get("market_reaction_pct"))
            if reaction is not None:
                reaction_values.append(reaction)

        latest = max(rows, key=lambda row: str(row.get("date") or ""))
        verified.append(
            f"Latest verified event: {latest.get('ticker')} {latest.get('event_type_label')} on {latest.get('date')}."
        )
        top_types = sorted(by_type.items(), key=lambda item: item[1], reverse=True)[:3]
        verified.append("Most common verified event types: " + ", ".join(f"{label} ({count})" for label, count in top_types) + ".")
        if reaction_values:
            average_reaction = sum(reaction_values) / len(reaction_values)
            verified.append(f"Average measured next-close reaction across priced events was {average_reaction:+.2%}.")
        else:
            missing_data.append("No market reaction could be calculated for the selected event window.")

        if by_direction["positive"] > by_direction["negative"]:
            inferred.append("Reported financial metrics and event priors skew constructive across the retrieved rows.")
        elif by_direction["negative"] > by_direction["positive"]:
            inferred.append("Reported financial metrics and event priors skew negative across the retrieved rows.")
        else:
            inferred.append("The retrieved financial events do not show a strong positive or negative directional skew.")

        for row in rows:
            event_type = str(row.get("event_type"))
            title = str(row.get("event_title") or row.get("event_type_label"))
            if event_type in {"debt_financing", "equity_financing"}:
                risks.append(f"{row.get('ticker')} financing event may affect dilution, leverage, or refinancing risk: {title}.")
            if event_type in {"guidance_update", "earnings_release_8k", "quarterly_earnings_report", "annual_earnings_report"}:
                catalysts.append(f"{row.get('ticker')} {row.get('event_type_label')} can reset forward expectations around {row.get('date')}.")
            if event_type in {"buyback_announcement", "dividend_announcement", "merger_acquisition_update"}:
                catalysts.append(f"{row.get('ticker')} capital allocation or transaction event may change near-term investor focus: {title}.")

        return {
            "summary": f"Found {len(rows)} verified financial event rows across {len({row.get('ticker') for row in rows})} symbol(s).",
            "verified": list(dict.fromkeys(verified)),
            "inferred": list(dict.fromkeys(inferred)),
            "risks": list(dict.fromkeys(risks))[:5],
            "catalysts": list(dict.fromkeys(catalysts))[:5],
            "missing_data": list(dict.fromkeys(missing_data)),
            "source_notes": [
                "SEC fields are verified from EDGAR submissions and company facts; directional labels are inferred from available reported metrics or conservative event priors.",
                "Expected result and beat/miss require analyst-consensus data and are intentionally left unavailable here.",
                "Market reaction uses next available close versus prior close and should not be read as intraday causal attribution.",
            ],
        }

    def events(self, symbols: Sequence[str], start: str, end: str, *, limit: int = 80) -> dict[str, Any]:
        request = FinancialEventRequest.from_inputs(symbols=symbols, start=start, end=end)
        filings, facts, warnings = self._load_event_frames(request)
        rows = self._build_rows(filings, facts)
        self._attach_market_reactions(rows, request, warnings)
        rows = sorted(rows, key=lambda row: (str(row.get("date") or ""), str(row.get("ticker") or "")), reverse=True)
        if limit > 0:
            rows = rows[:limit]
        payload = {
            "request": {
                "symbols": list(request.symbols),
                "start": request.start,
                "end": request.end,
                "limit": limit,
            },
            "events": rows,
            "summary": {
                "event_count": len(rows),
                "symbols": list(request.symbols),
                "sources": ["SEC EDGAR submissions", "SEC company facts", "Yahoo Finance close prices"],
            },
            "analysis": self._analysis(rows, warnings),
            "warnings": list(dict.fromkeys(warnings)),
        }
        return json_ready(payload)
