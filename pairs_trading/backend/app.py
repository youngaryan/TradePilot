from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import BackendSettings
from .routers.admin import build_admin_router
from .routers.backtests import build_backtest_router
from .routers.health import build_health_router
from .routers.market_research import build_market_research_router
from .routers.marketplace import build_marketplace_router
from .routers.metrics import build_metrics_router
from .routers.paper import build_paper_router
from .routers.refresh import build_refresh_router
from .routers.saas import build_saas_router
from .routers.sentiment import build_sentiment_router
from .routers.strategies import build_strategy_router
from .routers.system import build_system_router
from .routers.telemetry import build_telemetry_router
from .security import install_security_middleware
from .telemetry import DailyRefreshScheduler
from .observability import configure_role_observability, log_exception, safe_route


def configure_observability(settings: BackendSettings) -> None:
    configure_role_observability(settings, role="api")


def scrub_trace_request_span(active_span: Any, scope: dict[str, object]) -> None:
    """Replace raw request targets with bounded route templates before export."""

    if active_span is None or not getattr(active_span, "is_recording", lambda: False)():
        return
    route = safe_route(getattr(scope.get("route"), "path", None))
    active_span.set_attribute("http.route", route)
    active_span.set_attribute("http.target", route)
    active_span.set_attribute("url.path", route)
    active_span.set_attribute("url.full", "[REDACTED]")
    if scope.get("query_string"):
        active_span.set_attribute("url.query", "[REDACTED]")


def instrument_app(app: FastAPI, settings: BackendSettings) -> None:
    if not settings.otel_exporter_otlp_endpoint:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, server_request_hook=scrub_trace_request_span)
    except Exception as error:  # pragma: no cover - optional exporter
        log_exception(logging.getLogger("pairs_trading.api"), "otel_instrumentation_failed", error)


def create_app(settings: BackendSettings | None = None) -> FastAPI:
    app_settings = settings or BackendSettings.from_env()
    app_settings.validate_for_startup()
    configure_observability(app_settings)
    scheduler = DailyRefreshScheduler(app_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = FastAPI(
        title="Pairs Trading Quant API",
        version="0.1.0",
        description="Backend API for paper trading dashboards, research artifacts, and future live operations.",
        lifespan=lifespan,
    )
    app.state.settings = app_settings

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, error: Exception) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", None)
        log_exception(logging.getLogger("pairs_trading.api"), "api_unhandled_exception", error)
        return JSONResponse(
            {"detail": {"code": "internal_error", "message": "An unexpected server error occurred."}},
            status_code=500,
            headers={"X-Correlation-ID": correlation_id} if correlation_id else None,
        )
    instrument_app(app, app_settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    install_security_middleware(app, app_settings)
    app.include_router(build_health_router(app_settings), prefix="/api")
    app.include_router(build_strategy_router(app_settings), prefix="/api")
    app.include_router(build_backtest_router(app_settings), prefix="/api")
    app.include_router(build_market_research_router(app_settings), prefix="/api")
    app.include_router(build_marketplace_router(app_settings), prefix="/api")
    app.include_router(build_paper_router(app_settings), prefix="/api")
    app.include_router(build_saas_router(app_settings), prefix="/api")
    app.include_router(build_admin_router(app_settings), prefix="/api")
    app.include_router(build_refresh_router(app_settings), prefix="/api")
    app.include_router(build_sentiment_router(app_settings), prefix="/api")
    app.include_router(build_system_router(app_settings), prefix="/api")
    app.include_router(build_telemetry_router(app_settings), prefix="/api")
    app.include_router(build_metrics_router(app_settings))
    return app


app = create_app()
