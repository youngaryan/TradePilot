from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..config import BackendSettings
from ..readiness import ReadinessChecker
from ..schemas import HealthResponse


def build_health_router(
    settings: BackendSettings,
    *,
    readiness_checker: ReadinessChecker | None = None,
) -> APIRouter:
    router = APIRouter(tags=["health"])
    checker = readiness_checker or ReadinessChecker(settings)

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Compatibility health endpoint; equivalent to process liveness."""

        return HealthResponse()

    @router.get("/health/live", response_model=HealthResponse)
    def live() -> HealthResponse:
        """Dependency-free process liveness probe."""

        return HealthResponse()

    @router.get("/health/ready")
    def ready() -> JSONResponse:
        payload = checker.check()
        return JSONResponse(payload, status_code=200 if payload["ready"] else 503)

    return router


# Preserve the module-level router for integrations that imported it directly.
router = build_health_router(BackendSettings())
