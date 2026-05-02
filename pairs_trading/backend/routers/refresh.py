from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import BackendSettings
from ..saas import AuthService
from ..schemas import DataRefreshRequest, DataRefreshTickRequest
from ..telemetry import DailyRefreshService


bearer = HTTPBearer(auto_error=False)


def build_refresh_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/refresh", tags=["refresh"])
    auth_service = AuthService(settings)
    refresh_service = DailyRefreshService(settings)

    def context(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    ) -> dict[str, str]:
        if credentials is None:
            raise HTTPException(status_code=401, detail="Login required.")
        try:
            auth_context = auth_service.authenticate(token=credentials.credentials, organization_id=organization_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return {"user_id": str(auth_context.user["id"]), "organization_id": auth_context.organization_id}

    @router.get("/status")
    def status(ctx: dict[str, str] = Depends(context)) -> dict[str, Any]:
        return refresh_service.status_payload(organization_id=ctx["organization_id"])

    @router.post("/run", status_code=202)
    def run_refresh(request: DataRefreshRequest, ctx: dict[str, str] = Depends(context)) -> dict[str, Any]:
        user_id = request.user_id or ctx["user_id"]
        return refresh_service.run_for_user(user_id=user_id, organization_id=ctx["organization_id"], force=request.force)

    @router.post("/tick", status_code=202)
    def tick(request: DataRefreshTickRequest, ctx: dict[str, str] = Depends(context)) -> dict[str, Any]:
        return refresh_service.run_due_users(limit=request.limit, force=request.force)

    return router
