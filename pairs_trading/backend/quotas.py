from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
from typing import Any

from ..platform import build_metadata_store
from ..platform.persistence import QuotaReservationExceededError
from .config import BackendSettings


DEFAULT_QUOTAS: dict[str, float] = {
    "backtest_job": 20,
    "market_research_job": 20,
    "sentiment_job": 20,
    "paper_job": 20,
    "news_pages": 500,
    "artifact_storage_mb": 1024,
}


def _is_admin_role(role: object) -> bool:
    return str(role or "user").lower() == "admin"


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
    def current_window(now_utc: str | None = None) -> tuple[str, str, str]:
        if now_utc is None:
            now = datetime.now(UTC)
        else:
            try:
                now = datetime.fromisoformat(str(now_utc).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("Quota occurrence time must be ISO-8601") from exc
            if now.tzinfo is None:
                now = now.replace(tzinfo=UTC)
            now = now.astimezone(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return (
            start.isoformat().replace("+00:00", "Z"),
            end.isoformat().replace("+00:00", "Z"),
            now.isoformat().replace("+00:00", "Z"),
        )

    @classmethod
    def current_window_start(cls) -> str:
        return cls.current_window()[0]

    @staticmethod
    def _positive_number(value: object, *, field_name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field_name} must be a finite positive number")
        normalized = float(value)
        if not math.isfinite(normalized) or normalized <= 0:
            raise ValueError(f"{field_name} must be a finite positive number")
        return normalized

    @staticmethod
    def _quota_limit(value: object, *, field_name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field_name} must be a finite non-negative number")
        normalized = float(value)
        if not math.isfinite(normalized) or normalized < 0:
            raise ValueError(f"{field_name} must be a finite non-negative number")
        return normalized

    def quotas_for_org(self, *, organization_id: str) -> dict[str, float]:
        stored = self.store.get_organization_quotas(organization_id=organization_id) or {}
        quotas = dict(DEFAULT_QUOTAS)
        for key, value in stored.items():
            quotas[str(key)] = self._quota_limit(value, field_name=f"Quota limit for {key}")
        return quotas

    def check(self, *, organization_id: str, feature: str, quantity: float = 1.0, role: object = "user") -> dict[str, Any]:
        normalized_quantity = self._positive_number(quantity, field_name="Quota quantity")
        if feature not in self.quotas_for_org(organization_id=organization_id):
            raise ValueError(f"Unknown quota feature: {feature}")
        if _is_admin_role(role):
            return {"feature": feature, "limit": None, "used": 0.0, "remaining": None, "bypassed": True}
        quotas = self.quotas_for_org(organization_id=organization_id)
        limit = self._quota_limit(quotas[feature], field_name=f"Quota limit for {feature}")
        window_start, window_end, _ = self.current_window()
        used = self.store.usage_count_window(
            organization_id=organization_id,
            feature=feature,
            window_start_utc=window_start,
            window_end_utc=window_end,
        )
        if used + normalized_quantity > limit:
            raise QuotaExceeded(feature=feature, limit=limit, used=used)
        return {
            "feature": feature,
            "limit": limit,
            "used": used,
            "remaining": max(limit - used, 0.0),
            "bypassed": False,
            "window_start_utc": window_start,
            "window_end_utc": window_end,
        }

    def record(self, *, organization_id: str, feature: str, quantity: float = 1.0, user_id: str | None = None, properties: dict[str, Any] | None = None) -> dict[str, Any]:
        self._positive_number(quantity, field_name="Quota quantity")
        return self.store.record_usage_event(
            organization_id=organization_id,
            user_id=user_id,
            feature=feature,
            quantity=quantity,
            properties=properties,
        )

    def check_and_record_many(
        self,
        *,
        organization_id: str,
        reservations: list[dict[str, Any]],
        user_id: str | None = None,
        role: object = "user",
        occurred_at_utc: str | None = None,
    ) -> list[dict[str, Any]]:
        if not reservations:
            raise ValueError("At least one quota reservation is required")
        quotas = self.quotas_for_org(organization_id=organization_id)
        normalized: list[dict[str, Any]] = []
        for reservation in reservations:
            feature = str(reservation.get("feature") or "").strip()
            if feature not in quotas:
                raise ValueError(f"Unknown quota feature: {feature}")
            quantity = self._positive_number(reservation.get("quantity", 1.0), field_name="Quota quantity")
            normalized.append(
                {
                    "feature": feature,
                    "quantity": quantity,
                    "limit": self._quota_limit(quotas[feature], field_name=f"Quota limit for {feature}"),
                    "properties": reservation.get("properties") or {},
                }
            )
        if _is_admin_role(role):
            return [
                {
                    "allowance": {
                        "feature": reservation["feature"],
                        "limit": None,
                        "used": 0.0,
                        "remaining": None,
                        "bypassed": True,
                    },
                    "usage": None,
                }
                for reservation in normalized
            ]
        window_start, window_end, occurred = self.current_window(occurred_at_utc)
        try:
            return self.store.reserve_usage_events(
                organization_id=organization_id,
                user_id=user_id,
                reservations=normalized,
                window_start_utc=window_start,
                window_end_utc=window_end,
                occurred_at_utc=occurred,
            )
        except QuotaReservationExceededError as exc:
            raise QuotaExceeded(feature=exc.feature, limit=exc.limit, used=exc.used) from None

    def check_and_record(self, *, organization_id: str, feature: str, quantity: float = 1.0, user_id: str | None = None, properties: dict[str, Any] | None = None, role: object = "user", occurred_at_utc: str | None = None) -> dict[str, Any]:
        return self.check_and_record_many(
            organization_id=organization_id,
            user_id=user_id,
            role=role,
            occurred_at_utc=occurred_at_utc,
            reservations=[{"feature": feature, "quantity": quantity, "properties": properties or {}}],
        )[0]
