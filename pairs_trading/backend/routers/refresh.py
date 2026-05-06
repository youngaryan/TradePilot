from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..authz import require_admin_context, require_csrf, require_paid_context
from ..config import BackendSettings
from ..saas import AuthService, RequestContext, SESSION_COOKIE_NAME
from ..schemas import DataRefreshRequest, DataRefreshTickRequest
from ..telemetry import DailyRefreshService


bearer = HTTPBearer(auto_error=False)


def build_refresh_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/refresh", tags=["refresh"])
    auth_service = AuthService(settings)
    refresh_service = DailyRefreshService(settings)
    paid_context = require_paid_context(settings, feature="Data refresh")
    admin_context = require_admin_context(settings)
    csrf_guard = require_csrf(settings)

    def context(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        active_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    ) -> dict[str, str]:
        token = credentials.credentials if credentials is not None else request.cookies.get(SESSION_COOKIE_NAME)
        if not token:
            raise HTTPException(status_code=401, detail="Login required.")
        try:
            auth_context = auth_service.authenticate(token=token, organization_id=active_organization_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return {"user_id": str(auth_context.user["id"]), "organization_id": auth_context.organization_id}

    @router.get("/status")
    def status(ctx: dict[str, str] = Depends(context)) -> dict[str, Any]:
        return refresh_service.status_payload(organization_id=ctx["organization_id"])

    @router.post("/run", status_code=202)
    def run_refresh(request: DataRefreshRequest, ctx: RequestContext = Depends(paid_context), _: None = Depends(csrf_guard)) -> dict[str, Any]:
        if request.user_id and str(ctx.user.get("role")) != "admin":
            raise HTTPException(status_code=403, detail="Only admins can refresh another user's data.")
        user_id = request.user_id or str(ctx.user["id"])
        return refresh_service.run_for_user(user_id=user_id, organization_id=ctx.organization_id, force=request.force)

    @router.post("/tick", status_code=202)
    def tick(request: DataRefreshTickRequest, _: RequestContext = Depends(admin_context), __: None = Depends(csrf_guard)) -> dict[str, Any]:
        return refresh_service.run_due_users(limit=request.limit, force=request.force)

    return router
