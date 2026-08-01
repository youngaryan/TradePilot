from __future__ import annotations

from datetime import date
from typing import Any, Protocol, Sequence

from pydantic import BaseModel, Field

from .sec import SecCompanyFactsClient


class FundamentalFact(BaseModel):
    value: float
    unit: str
    concept: str
    form: str
    filing_date: str
    report_start_date: str | None = None
    report_end_date: str | None = None
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    accession_number: str | None = None
    source_url: str
    selection_rationale: str


class FundamentalsSnapshot(BaseModel):
    ticker: str
    as_of_date: str
    provider: str = "sec_companyfacts"
    currency: str | None = "USD"
    revenue: FundamentalFact | None = None
    net_income: FundamentalFact | None = None
    diluted_eps: FundamentalFact | None = None
    cash: FundamentalFact | None = None
    total_assets: FundamentalFact | None = None
    total_liabilities: FundamentalFact | None = None
    debt: FundamentalFact | None = None
    shares_outstanding: FundamentalFact | None = None
    filing_dates: list[str] = Field(default_factory=list)
    freshness_days: int | None = None
    missing_fields: list[str] = Field(default_factory=list)
    guidance_available: bool = False
    forward_estimates_available: bool = False


class FundamentalsProvider(Protocol):
    def get_snapshot(self, ticker: str, as_of_date: date) -> FundamentalsSnapshot:
        ...


class SecCompanyFactsFundamentalsProvider:
    CONCEPTS: dict[str, tuple[str, ...]] = {
        "revenue": (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "Revenues",
        ),
        "net_income": ("NetIncomeLoss", "ProfitLoss"),
        "diluted_eps": (
            "EarningsPerShareDiluted",
            "IncomeLossFromContinuingOperationsPerDilutedShare",
            "EarningsPerShareBasic",
        ),
        "cash": ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
        "total_assets": ("Assets",),
        "total_liabilities": ("Liabilities",),
        "debt": ("LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent", "LongTermDebtNoncurrent"),
        "shares_outstanding": ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"),
    }
    ALLOWED_FORMS = frozenset({"10-Q", "10-Q/A", "10-K", "10-K/A"})

    def __init__(self, client: SecCompanyFactsClient) -> None:
        self.client = client

    @staticmethod
    def _source_url(cik: str, entry: dict[str, Any]) -> str:
        accession = str(entry.get("accn") or "")
        if accession:
            return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
        return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

    def _select_fact(
        self,
        payload: dict[str, Any],
        cik: str,
        concepts: Sequence[str],
        as_of_date: date,
    ) -> FundamentalFact | None:
        facts = payload.get("facts", {}).get("us-gaap", {})
        candidates: list[tuple[date, int, int, dict[str, Any]]] = []
        for concept_priority, concept in enumerate(concepts):
            fact = facts.get(concept)
            if not isinstance(fact, dict):
                continue
            for unit, entries in fact.get("units", {}).items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict) or entry.get("val") is None:
                        continue
                    form = str(entry.get("form") or "")
                    try:
                        filed = date.fromisoformat(str(entry.get("filed") or ""))
                        float(entry["val"])
                    except (TypeError, ValueError):
                        continue
                    if form not in self.ALLOWED_FORMS or filed > as_of_date:
                        continue
                    # Sort by filing availability first; prefer non-amended rows
                    # and higher-priority concepts for equal filing dates.
                    candidates.append(
                        (
                            filed,
                            -int(form.endswith("/A")),
                            -concept_priority,
                            {**entry, "_concept": concept, "_unit": str(unit)},
                        )
                    )
        if not candidates:
            return None
        _, _, _, selected = max(candidates, key=lambda item: item[:3])
        filed = str(selected["filed"])
        return FundamentalFact(
            value=float(selected["val"]),
            unit=str(selected["_unit"]),
            concept=str(selected["_concept"]),
            form=str(selected.get("form") or ""),
            filing_date=filed,
            report_start_date=str(selected.get("start")) if selected.get("start") else None,
            report_end_date=str(selected.get("end")) if selected.get("end") else None,
            fiscal_year=int(selected["fy"]) if selected.get("fy") is not None else None,
            fiscal_period=str(selected.get("fp")) if selected.get("fp") else None,
            accession_number=str(selected.get("accn")) if selected.get("accn") else None,
            source_url=self._source_url(cik, selected),
            selection_rationale="Latest SEC filing available on or before the requested as-of date; concept priority applied conservatively.",
        )

    def get_snapshot(self, ticker: str, as_of_date: date) -> FundamentalsSnapshot:
        normalized = str(ticker).upper()
        resolved = self.client.company_facts_for_ticker(normalized)
        if resolved is None:
            return FundamentalsSnapshot(
                ticker=normalized,
                as_of_date=as_of_date.isoformat(),
                currency=None,
                missing_fields=list(self.CONCEPTS),
            )
        cik, payload = resolved
        selected = {
            field: self._select_fact(payload, cik, concepts, as_of_date)
            for field, concepts in self.CONCEPTS.items()
        }
        filing_dates = sorted({fact.filing_date for fact in selected.values() if fact is not None})
        latest = date.fromisoformat(filing_dates[-1]) if filing_dates else None
        return FundamentalsSnapshot(
            ticker=normalized,
            as_of_date=as_of_date.isoformat(),
            **selected,
            filing_dates=filing_dates,
            freshness_days=(as_of_date - latest).days if latest else None,
            missing_fields=[field for field, fact in selected.items() if fact is None],
        )


__all__ = [
    "FundamentalFact",
    "FundamentalsProvider",
    "FundamentalsSnapshot",
    "SecCompanyFactsFundamentalsProvider",
]
