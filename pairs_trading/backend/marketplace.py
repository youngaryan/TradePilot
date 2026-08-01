from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import re
from typing import Any

from ..platform import build_metadata_store
from .config import BackendSettings
from .strategy_builder import StrategyBuilderService, dry_run_strategy_spec, validate_strategy_spec


class MarketplaceUnavailableError(RuntimeError):
    pass


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80]
    return normalized or "strategy"


class MarketplaceService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.store = build_metadata_store(settings)

    def _require_enabled(self) -> None:
        if not self.settings.marketplace_enabled:
            raise MarketplaceUnavailableError("Marketplace is not enabled for this deployment.")

    def _owned_listing(self, listing_id: str, organization_id: str) -> dict[str, Any]:
        listing = self.store.get_strategy_listing(listing_id=listing_id)
        if listing is None:
            raise KeyError("Marketplace listing not found.")
        if listing["publisher_organization_id"] != organization_id:
            raise PermissionError("Marketplace listing is owned by another organization.")
        return listing

    def _safe_listing(self, listing: dict[str, Any], *, include_spec: bool = False) -> dict[str, Any]:
        version = self.store.get_strategy_listing_version(version_id=listing["current_version_id"]) if listing.get("current_version_id") else None
        payload = {
            "id": listing["id"],
            "title": listing["title"],
            "slug": listing["slug"],
            "summary": listing["summary"],
            "visibility": listing["visibility"],
            "status": listing["status"],
            "publisher_organization_id": listing["publisher_organization_id"],
            "current_version_id": listing.get("current_version_id"),
            "published_at_utc": listing.get("published_at_utc"),
            "updated_at_utc": listing["updated_at_utc"],
            "version": version.get("version") if version else None,
            "risk_level": version.get("risk_level") if version else None,
            "catalog": version.get("catalog_snapshot") if version else None,
            "validation_summary": {
                "validated": bool(version),
                "warning_count": len((version or {}).get("validation_snapshot", {}).get("warnings", [])),
                "dry_run_status": (version or {}).get("validation_snapshot", {}).get("dry_run", {}).get("status"),
            },
        }
        if include_spec and version:
            payload["strategy_spec"] = version["strategy_spec"]
        return payload

    def search(self, *, search: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        if not self.settings.marketplace_enabled:
            return []
        rows = self.store.list_strategy_listings(
            statuses=("published",), visibility="public", search=search, limit=limit, offset=offset
        )
        return [self._safe_listing(row) for row in rows]

    def detail(self, identifier: str) -> dict[str, Any]:
        self._require_enabled()
        listing = self.store.get_strategy_listing(listing_id=identifier)
        if listing is None:
            listing = self.store.get_strategy_listing(slug=identifier)
        if listing is None or listing["status"] not in {"published", "archived"}:
            raise KeyError("Marketplace listing not found.")
        return self._safe_listing(listing, include_spec=True)

    def create(self, *, organization_id: str, user_id: str, source_strategy_id: str, title: str, summary: str, visibility: str) -> dict[str, Any]:
        self._require_enabled()
        strategy = self.store.get_user_strategy(
            organization_id=organization_id,
            strategy_id=source_strategy_id,
            owner_user_id=user_id,
            active_only=True,
        )
        if strategy is None:
            raise KeyError("Owned active strategy not found.")
        base_slug = _slug(title)
        slug = base_slug
        suffix = 1
        while self.store.get_strategy_listing(slug=slug) is not None:
            suffix += 1
            slug = f"{base_slug[:72]}-{suffix}"
        listing = self.store.create_strategy_listing(
            publisher_organization_id=organization_id,
            publisher_user_id=user_id,
            source_user_strategy_id=source_strategy_id,
            title=title.strip(),
            slug=slug,
            summary=summary.strip(),
            visibility=visibility,
        )
        self.store.record_audit_log(
            action="marketplace.listing_created", organization_id=organization_id,
            actor_user_id=user_id, target_type="strategy_listing", target_id=listing["id"],
            metadata={"visibility": visibility, "status": "draft"},
        )
        return self._safe_listing(listing)

    def update_draft(self, *, listing_id: str, organization_id: str, user_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        self._require_enabled()
        listing = self._owned_listing(listing_id, organization_id)
        if listing["status"] != "draft":
            raise ValueError("Only draft listings can be edited.")
        updated = self.store.update_strategy_listing(listing_id=listing_id, updates=updates)
        self.store.record_audit_log(
            action="marketplace.listing_updated", organization_id=organization_id,
            actor_user_id=user_id, target_type="strategy_listing", target_id=listing_id,
            metadata={"fields": sorted(updates)},
        )
        return self._safe_listing(updated)

    def create_version(self, *, listing_id: str, organization_id: str, user_id: str, source_strategy_id: str | None = None) -> dict[str, Any]:
        self._require_enabled()
        listing = self._owned_listing(listing_id, organization_id)
        if source_strategy_id:
            replacement = self.store.get_user_strategy(
                organization_id=organization_id,
                strategy_id=source_strategy_id,
                owner_user_id=user_id,
                active_only=True,
            )
            if replacement is None:
                raise KeyError("Owned active source strategy not found.")
            listing = self.store.update_strategy_listing(
                listing_id=listing_id,
                updates={"source_user_strategy_id": source_strategy_id},
            )
        strategy = self.store.get_user_strategy(
            organization_id=organization_id,
            strategy_id=listing["source_user_strategy_id"],
            owner_user_id=listing["publisher_user_id"],
            active_only=True,
        )
        if strategy is None:
            raise ValueError("The source strategy is no longer active.")
        validation = validate_strategy_spec(strategy["spec"])
        if not validation.ok or validation.spec is None:
            raise ValueError("The source strategy no longer passes deterministic validation.")
        dry_run = dry_run_strategy_spec(validation.spec)
        content_hash = hashlib.sha256(
            json.dumps(validation.spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        catalog = StrategyBuilderService.catalog_item(strategy)
        safe_catalog = {
            key: catalog.get(key)
            for key in ("name", "family", "difficulty", "summary", "how_it_works", "best_for", "watch_out", "key_parameters", "risk_level", "generation_mode", "generation_label")
        }
        version = self.store.create_strategy_listing_version(
            listing_id=listing_id,
            strategy_spec=validation.spec,
            catalog_snapshot=safe_catalog,
            validation_snapshot={"warnings": validation.warnings, "dry_run": dry_run},
            risk_level=str(strategy.get("risk_level") or "medium"),
            source_strategy_version=int(strategy.get("version") or 1),
            content_hash=content_hash,
            created_by_user_id=user_id,
        )
        self.store.record_audit_log(
            action="marketplace.version_created", organization_id=organization_id,
            actor_user_id=user_id, target_type="strategy_listing_version", target_id=version["id"],
            metadata={"listing_id": listing_id, "version": version["version"], "content_hash": content_hash},
        )
        return version

    def publish(self, *, listing_id: str, organization_id: str, user_id: str) -> dict[str, Any]:
        self._require_enabled()
        listing = self._owned_listing(listing_id, organization_id)
        if listing["status"] in {"suspended", "archived"}:
            raise ValueError("Suspended or archived listings cannot be published.")
        version = self.create_version(listing_id=listing_id, organization_id=organization_id, user_id=user_id)
        now = _utc_now_iso()
        updated = self.store.update_strategy_listing(
            listing_id=listing_id,
            updates={"status": "published", "current_version_id": version["id"], "published_at_utc": listing.get("published_at_utc") or now},
        )
        self.store.record_audit_log(
            action="marketplace.listing_published", organization_id=organization_id,
            actor_user_id=user_id, target_type="strategy_listing", target_id=listing_id,
            metadata={"version_id": version["id"], "version": version["version"]},
        )
        return self._safe_listing(updated, include_spec=True)

    def archive(self, *, listing_id: str, organization_id: str, user_id: str) -> dict[str, Any]:
        self._require_enabled()
        self._owned_listing(listing_id, organization_id)
        updated = self.store.update_strategy_listing(listing_id=listing_id, updates={"status": "archived", "archived_at_utc": _utc_now_iso()})
        self.store.record_audit_log(action="marketplace.listing_archived", organization_id=organization_id, actor_user_id=user_id, target_type="strategy_listing", target_id=listing_id)
        return self._safe_listing(updated)

    def subscribe(self, *, listing_id: str, organization_id: str, user_id: str, idempotency_key: str, version_id: str | None = None) -> dict[str, Any]:
        self._require_enabled()
        listing = self.store.get_strategy_listing(listing_id=listing_id)
        if listing is None or listing["status"] != "published" or not listing.get("current_version_id"):
            raise KeyError("Published marketplace listing not found.")
        if listing["publisher_organization_id"] == organization_id:
            raise ValueError("A publisher organization cannot subscribe to its own listing.")
        pinned = version_id or listing["current_version_id"]
        version = self.store.get_strategy_listing_version(version_id=pinned)
        if version is None or version["listing_id"] != listing_id:
            raise ValueError("The requested listing version is invalid.")
        subscription = self.store.upsert_marketplace_subscription(
            subscriber_organization_id=organization_id, subscriber_user_id=user_id,
            listing_id=listing_id, pinned_listing_version_id=pinned, status="active",
            idempotency_key=idempotency_key,
        )
        self.store.record_audit_log(action="marketplace.subscribed", organization_id=organization_id, actor_user_id=user_id, target_type="strategy_marketplace_subscription", target_id=subscription["id"], metadata={"listing_id": listing_id, "version_id": pinned})
        return subscription

    def unsubscribe(self, *, listing_id: str, organization_id: str, user_id: str, idempotency_key: str) -> dict[str, Any]:
        subscriptions = self.store.list_marketplace_subscriptions(subscriber_organization_id=organization_id)
        current = next((item for item in subscriptions if item["listing_id"] == listing_id), None)
        if current is None:
            raise KeyError("Marketplace subscription not found.")
        result = self.store.upsert_marketplace_subscription(
            subscriber_organization_id=organization_id, subscriber_user_id=user_id,
            listing_id=listing_id, pinned_listing_version_id=current["pinned_listing_version_id"],
            status="cancelled", idempotency_key=idempotency_key,
        )
        self.store.record_audit_log(action="marketplace.unsubscribed", organization_id=organization_id, actor_user_id=user_id, target_type="strategy_marketplace_subscription", target_id=result["id"], metadata={"listing_id": listing_id})
        return result

    def my_subscriptions(self, organization_id: str) -> list[dict[str, Any]]:
        self._require_enabled()
        results: list[dict[str, Any]] = []
        for subscription in self.store.list_marketplace_subscriptions(subscriber_organization_id=organization_id):
            safe = dict(subscription)
            permitted = subscription["status"] == "active" and subscription["listing_status"] in {"published", "archived"}
            version = self.store.get_strategy_listing_version(version_id=subscription["pinned_listing_version_id"]) if permitted else None
            safe["execution_access"] = bool(version)
            if version:
                safe["version"] = version["version"]
                safe["risk_level"] = version["risk_level"]
                safe["catalog"] = version["catalog_snapshot"]
                safe["strategy_spec"] = version["strategy_spec"]
            results.append(safe)
        return results

    def my_publications(self, organization_id: str) -> list[dict[str, Any]]:
        self._require_enabled()
        results: list[dict[str, Any]] = []
        for item in self.store.list_strategy_listings(publisher_organization_id=organization_id, limit=200):
            safe = self._safe_listing(item)
            safe["source_strategy_id"] = item["source_user_strategy_id"]
            results.append(safe)
        return results

    def moderate(self, *, listing_id: str, status: str, actor_user_id: str) -> dict[str, Any]:
        self._require_enabled()
        listing = self.store.get_strategy_listing(listing_id=listing_id)
        if listing is None:
            raise KeyError("Marketplace listing not found.")
        if status == "published" and not listing.get("current_version_id"):
            raise ValueError("A listing without a reviewed version cannot be reinstated.")
        updated = self.store.update_strategy_listing(listing_id=listing_id, updates={"status": status})
        self.store.record_audit_log(action=f"admin.marketplace_{status}", organization_id=listing["publisher_organization_id"], actor_user_id=actor_user_id, target_type="strategy_listing", target_id=listing_id)
        return self._safe_listing(updated)


__all__ = ["MarketplaceService", "MarketplaceUnavailableError"]
