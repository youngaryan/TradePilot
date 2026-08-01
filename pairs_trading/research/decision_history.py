from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..platform.persistence import build_metadata_store, MetadataStore
from ..backend.config import BackendSettings


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class CommitteeDecision(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    ticker: str
    pair_ticker: str | None = None
    timestamp: str = Field(default_factory=_utc_now_iso)
    analysis_date: str = ""
    horizon: str = "swing"
    decision: str = ""
    confidence: int = 0
    reasoning: str = ""
    signals_summary: dict[str, Any] = Field(default_factory=dict)
    market_metrics: dict[str, Any] = Field(default_factory=dict)
    data_quality: dict[str, Any] = Field(default_factory=dict)
    evaluation: dict[str, Any] = Field(default_factory=dict)
    recommendation: str = ""
    llm_provider: str = ""
    llm_model: str = ""
    organization_id: str | None = None
    user_id: str | None = None
    job_id: str | None = None
    report_id: str | None = None


class DecisionHistoryStore:
    def __init__(self, settings: BackendSettings | None = None) -> None:
        self._settings = settings
        self._store: MetadataStore | None = None
        self._db_path: Path | None = None

    def _metadata_store(self) -> MetadataStore:
        if self._store is None and self._settings is not None:
            self._store = build_metadata_store(self._settings)
        if self._store is not None:
            return self._store
        if self._db_path is None:
            self._db_path = Path("data/decision_history.db")
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        from ..platform.persistence import SQLiteMetadataStore
        self._store = SQLiteMetadataStore(self._db_path)
        return self._store

    def add(self, decision: CommitteeDecision) -> None:
        store = self._metadata_store()
        store.upsert_job(
            kind="committee_decision",
            payload={
                "id": decision.id,
                "organization_id": decision.organization_id,
                **decision.model_dump(mode="json"),
            },
        )

    def list(
        self,
        *,
        ticker: str | None = None,
        organization_id: str | None = None,
        limit: int = 50,
    ) -> list[CommitteeDecision]:
        store = self._metadata_store()
        raw = store.list_jobs(kind="committee_decision", organization_id=organization_id)
        results = []
        for item in raw:
            try:
                d = CommitteeDecision(**item)
                if ticker and d.ticker.upper() != ticker.upper() and (not d.pair_ticker or d.pair_ticker.upper() != ticker.upper()):
                    continue
                results.append(d)
            except Exception:
                continue
        results.sort(key=lambda d: d.timestamp, reverse=True)
        return results[:limit]

    def get(self, decision_id: str) -> CommitteeDecision | None:
        store = self._metadata_store()
        raw = store.list_jobs(kind="committee_decision")
        for item in raw:
            if item.get("id") == decision_id:
                try:
                    return CommitteeDecision(**item)
                except Exception:
                    return None
        return None

    def delete(self, decision_id: str) -> None:
        store = self._metadata_store()
        store.delete_job(kind="committee_decision", job_id=decision_id)

    def decisions_by_ticker(self, ticker: str, *, limit: int = 20) -> list[CommitteeDecision]:
        return self.list(ticker=ticker, limit=limit)

    def summary(self, *, organization_id: str | None = None) -> dict[str, Any]:
        all_decisions = self.list(organization_id=organization_id, limit=1000)
        ticker_count = len({d.ticker for d in all_decisions})
        decision_counts: dict[str, int] = {}
        for d in all_decisions:
            decision_counts[d.decision] = decision_counts.get(d.decision, 0) + 1
        avg_confidence = sum(d.confidence for d in all_decisions) / max(len(all_decisions), 1)
        return {
            "total_decisions": len(all_decisions),
            "unique_tickers": ticker_count,
            "decision_breakdown": dict(sorted(decision_counts.items(), key=lambda x: -x[1])),
            "average_confidence": round(avg_confidence, 1),
        }
