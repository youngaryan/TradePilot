from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..platform import build_metadata_store
from .config import BackendSettings


DEFAULT_QUOTAS: dict[str, float] = {
    "backtest_job": 20,
    "sentiment_job": 20,
    "paper_job": 20,
    "news_pages": 500,
    "artifact_storage_mb": 1024,
}


@dataclass(frozen=True)
class QuotaExceeded(Exception):
    feature: str
    limit: float
    used: float

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": "quota_exceeded",
            "feature": self.feature,
            "limit": self.limit,
            "used": self.used,
            "message": f"Daily quota exceeded for {self.feature}.",
        }


class QuotaService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.store = build_metadata_store(settings)

    @staticmethod
    def current_window_start() -> str:
        now = datetime.now(UTC)
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")

    def quotas_for_org(self, *, organization_id: str) -> dict[str, float]:
        stored = self.store.get_organization_quotas(organization_id=organization_id) or {}
        quotas = dict(DEFAULT_QUOTAS)
        for key, value in stored.items():
            try:
                quotas[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        return quotas

    def check(self, *, organization_id: str, feature: str, quantity: float = 1.0) -> dict[str, Any]:
        quotas = self.quotas_for_org(organization_id=organization_id)
        limit = float(quotas.get(feature, DEFAULT_QUOTAS.get(feature, 0)))
        used = self.store.usage_count_since(
            organization_id=organization_id,
            feature=feature,
            since_utc=self.current_window_start(),
        )
        if limit >= 0 and used + quantity > limit:
            raise QuotaExceeded(feature=feature, limit=limit, used=used)
        return {"feature": feature, "limit": limit, "used": used, "remaining": max(limit - used, 0.0)}

    def record(self, *, organization_id: str, feature: str, quantity: float = 1.0, user_id: str | None = None, properties: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.store.record_usage_event(
            organization_id=organization_id,
            user_id=user_id,
            feature=feature,
            quantity=quantity,
            properties=properties,
        )

    def check_and_record(self, *, organization_id: str, feature: str, quantity: float = 1.0, user_id: str | None = None, properties: dict[str, Any] | None = None) -> dict[str, Any]:
        allowance = self.check(organization_id=organization_id, feature=feature, quantity=quantity)
        usage = self.record(
            organization_id=organization_id,
            feature=feature,
            quantity=quantity,
            user_id=user_id,
            properties=properties,
        )
        return {"allowance": allowance, "usage": usage}
