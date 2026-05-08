from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..authz import require_admin_context, require_csrf
from ..config import BackendSettings
from ..saas import AdminService, RequestContext
from ..schemas import AdminStrategyStatusUpdateRequest, AdminUserUpdateRequest
from ..strategy_builder import StrategyBuilderService


def build_admin_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])
    admin_service = AdminService(settings)
    strategy_service = StrategyBuilderService(settings)
    admin_context = require_admin_context(settings)
    csrf_guard = require_csrf(settings)

    @router.get("/overview")
    def overview(_: RequestContext = Depends(admin_context)) -> dict[str, Any]:
        return admin_service.overview()

    @router.get("/users")
    def users(
        search: str | None = Query(default=None, max_length=120),
        role: str | None = Query(default=None, pattern="^(admin|user)$"),
        status: str | None = Query(default=None, pattern="^(active|inactive)$"),
        sort_by: str = Query(default="created_at_utc", max_length=60),
        sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
        limit: int = Query(default=200, ge=1, le=1000),
        _: RequestContext = Depends(admin_context),
    ) -> list[dict[str, Any]]:
        return admin_service.list_users(
            search=search,
            role=role,
            status=status,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=limit,
        )

    @router.get("/audit-log")
    def audit_log(
        limit: int = Query(default=100, ge=1, le=500),
        _: RequestContext = Depends(admin_context),
    ) -> list[dict[str, Any]]:
        return admin_service.audit_log(limit=limit)

    @router.get("/user-strategies")
    def user_strategies(
        organization_id: str | None = Query(default=None, max_length=120),
        user_id: str | None = Query(default=None, max_length=120),
        status: str | None = Query(default=None, pattern="^(active|disabled)$"),
        risk_level: str | None = Query(default=None, pattern="^(low|medium|high)$"),
        limit: int = Query(default=200, ge=1, le=1000),
        _: RequestContext = Depends(admin_context),
    ) -> list[dict[str, Any]]:
        return strategy_service.admin_list(
            organization_id=organization_id,
            user_id=user_id,
            status=status,
            risk_level=risk_level,
            limit=limit,
        )

    @router.patch("/user-strategies/{strategy_id}")
    def update_user_strategy(
        strategy_id: str,
        request: AdminStrategyStatusUpdateRequest,
        ctx: RequestContext = Depends(admin_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        try:
            return strategy_service.admin_update_status(
                strategy_id=strategy_id,
                status=request.status,
                actor_user_id=str(ctx.user["id"]),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/user-strategies/{strategy_id}")
    def delete_user_strategy(
        strategy_id: str,
        ctx: RequestContext = Depends(admin_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        try:
            return strategy_service.admin_delete(strategy_id=strategy_id, actor_user_id=str(ctx.user["id"]))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/system-health")
    def system_health(_: RequestContext = Depends(admin_context)) -> dict[str, Any]:
        return admin_service.system_health()

    @router.get("/quotas")
    def quotas(_: RequestContext = Depends(admin_context)) -> dict[str, Any]:
        return admin_service.quotas()

    @router.patch("/quotas/{organization_id}")
    def update_quotas(
        organization_id: str,
        payload: dict[str, Any],
        ctx: RequestContext = Depends(admin_context),
        __: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        return admin_service.update_quotas(organization_id=organization_id, payload=payload, actor_user_id=str(ctx.user["id"]))

    @router.patch("/users/{user_id}")
    def update_user(
        user_id: str,
        request: AdminUserUpdateRequest,
        ctx: RequestContext = Depends(admin_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        try:
            return admin_service.update_user(
                user_id=user_id,
                role=request.role,
                status=request.status,
                actor_user_id=str(ctx.user["id"]),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
