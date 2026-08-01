from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from ..authz import require_admin_context, require_auth_context, require_csrf
from ..config import BackendSettings
from ..marketplace import MarketplaceService, MarketplaceUnavailableError
from ..saas import RequestContext
from ..schemas import (
    MarketplaceListingCreateRequest,
    MarketplaceListingUpdateRequest,
    MarketplaceModerationRequest,
    MarketplaceMutationRequest,
    MarketplaceUpgradeRequest,
    MarketplaceVersionCreateRequest,
)


def _call(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except MarketplaceUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def build_marketplace_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/marketplace", tags=["marketplace"])
    service = MarketplaceService(settings)
    auth_context = require_auth_context(settings)
    admin_context = require_admin_context(settings)
    csrf_guard = require_csrf(settings)

    @router.get("/listings")
    def search_listings(
        search: str | None = Query(default=None, max_length=120),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100_000),
    ) -> list[dict[str, Any]]:
        return service.search(search=search, limit=limit, offset=offset)

    @router.get("/listings/{identifier}")
    def listing_detail(identifier: str) -> dict[str, Any]:
        return _call(lambda: service.detail(identifier))

    @router.get("/me/publications")
    def my_publications(ctx: RequestContext = Depends(auth_context)) -> list[dict[str, Any]]:
        return _call(lambda: service.my_publications(ctx.organization_id))

    @router.get("/me/subscriptions")
    def my_subscriptions(ctx: RequestContext = Depends(auth_context)) -> list[dict[str, Any]]:
        return _call(lambda: service.my_subscriptions(ctx.organization_id))

    @router.post("/listings")
    def create_listing(
        request: MarketplaceListingCreateRequest,
        ctx: RequestContext = Depends(auth_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        return _call(lambda: service.create(
            organization_id=ctx.organization_id,
            user_id=str(ctx.user["id"]),
            **request.model_dump(),
        ))

    @router.patch("/listings/{listing_id}")
    def update_listing(
        listing_id: str,
        request: MarketplaceListingUpdateRequest,
        ctx: RequestContext = Depends(auth_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        updates = request.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=422, detail="Provide at least one listing field.")
        return _call(lambda: service.update_draft(
            listing_id=listing_id,
            organization_id=ctx.organization_id,
            user_id=str(ctx.user["id"]),
            updates=updates,
        ))

    @router.post("/listings/{listing_id}/versions")
    def create_version(
        listing_id: str,
        request: MarketplaceVersionCreateRequest,
        ctx: RequestContext = Depends(auth_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        return _call(lambda: service.create_version(
            listing_id=listing_id,
            organization_id=ctx.organization_id,
            user_id=str(ctx.user["id"]),
            source_strategy_id=request.source_strategy_id,
        ))

    @router.post("/listings/{listing_id}/publish")
    def publish_listing(
        listing_id: str,
        ctx: RequestContext = Depends(auth_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        return _call(lambda: service.publish(
            listing_id=listing_id, organization_id=ctx.organization_id, user_id=str(ctx.user["id"])
        ))

    @router.post("/listings/{listing_id}/archive")
    def archive_listing(
        listing_id: str,
        ctx: RequestContext = Depends(auth_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        return _call(lambda: service.archive(
            listing_id=listing_id, organization_id=ctx.organization_id, user_id=str(ctx.user["id"])
        ))

    @router.post("/listings/{listing_id}/subscribe")
    def subscribe(
        listing_id: str,
        request: MarketplaceMutationRequest,
        ctx: RequestContext = Depends(auth_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        return _call(lambda: service.subscribe(
            listing_id=listing_id,
            organization_id=ctx.organization_id,
            user_id=str(ctx.user["id"]),
            idempotency_key=request.idempotency_key,
        ))

    @router.post("/listings/{listing_id}/upgrade")
    def upgrade(
        listing_id: str,
        request: MarketplaceUpgradeRequest,
        ctx: RequestContext = Depends(auth_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        return _call(lambda: service.subscribe(
            listing_id=listing_id,
            organization_id=ctx.organization_id,
            user_id=str(ctx.user["id"]),
            idempotency_key=request.idempotency_key,
            version_id=request.version_id,
        ))

    @router.post("/listings/{listing_id}/unsubscribe")
    def unsubscribe(
        listing_id: str,
        request: MarketplaceMutationRequest,
        ctx: RequestContext = Depends(auth_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        return _call(lambda: service.unsubscribe(
            listing_id=listing_id,
            organization_id=ctx.organization_id,
            user_id=str(ctx.user["id"]),
            idempotency_key=request.idempotency_key,
        ))

    @router.patch("/admin/listings/{listing_id}")
    def moderate_listing(
        listing_id: str,
        request: MarketplaceModerationRequest,
        ctx: RequestContext = Depends(admin_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        return _call(lambda: service.moderate(
            listing_id=listing_id, status=request.status, actor_user_id=str(ctx.user["id"])
        ))

    return router
