from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..authz import require_admin_context
from ..config import BackendSettings
from ..saas import AdminService, RequestContext
from ..schemas import AdminUserUpdateRequest


def build_admin_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])
    admin_service = AdminService(settings)
    admin_context = require_admin_context(settings)

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

    @router.patch("/users/{user_id}")
    def update_user(
        user_id: str,
        request: AdminUserUpdateRequest,
        ctx: RequestContext = Depends(admin_context),
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
