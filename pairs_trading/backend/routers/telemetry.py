from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import BackendSettings
from ..saas import AuthService
from ..schemas import TelemetryBatchRequest, TelemetryEventRequest
from ..telemetry import TelemetryContext, TelemetryService


bearer = HTTPBearer(auto_error=False)


def build_telemetry_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/telemetry", tags=["telemetry"])
    auth_service = AuthService(settings)
    telemetry = TelemetryService(settings)

    def optional_context(
        credentials: HTTPAuthorizationCredentials | None,
        organization_id: str | None,
        request: Request,
    ) -> TelemetryContext:
        country = visitor_country(request)
        if credentials is None:
            return TelemetryContext(country=country)
        try:
            context = auth_service.authenticate(token=credentials.credentials, organization_id=organization_id)
            return TelemetryContext(user_id=str(context.user["id"]), organization_id=context.organization_id, country=country)
        except Exception:
            return TelemetryContext(country=country)

    def required_context(
        credentials: HTTPAuthorizationCredentials | None,
        organization_id: str | None,
    ) -> TelemetryContext:
        if credentials is None:
            raise HTTPException(status_code=401, detail="Login required.")
        try:
            context = auth_service.authenticate(token=credentials.credentials, organization_id=organization_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return TelemetryContext(user_id=str(context.user["id"]), organization_id=context.organization_id)

    @router.post("/events")
    def record_event(
        request: TelemetryEventRequest,
        http_request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    ) -> dict[str, Any]:
        return telemetry.track(request, context=optional_context(credentials, organization_id, http_request))

    @router.post("/events/batch")
    def record_batch(
        request: TelemetryBatchRequest,
        http_request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    ) -> dict[str, Any]:
        context = optional_context(credentials, organization_id, http_request)
        results = [telemetry.track(event, context=context) for event in request.events]
        return {"stored_count": sum(1 for result in results if result.get("stored")), "results": results}

    @router.get("/events")
    def list_events(
        limit: int = Query(default=100, ge=1, le=500),
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    ) -> list[dict[str, Any]]:
        context = required_context(credentials, organization_id)
        return telemetry.list_events(organization_id=context.organization_id, limit=limit)

    return router


def visitor_country(request: Request) -> str | None:
    for header in (
        "cf-ipcountry",
        "x-vercel-ip-country",
        "cloudfront-viewer-country",
        "x-appengine-country",
        "x-country-code",
    ):
        value = request.headers.get(header)
        if value and value.upper() not in {"XX", "UNKNOWN"}:
            return value[:8].upper()
    return None
