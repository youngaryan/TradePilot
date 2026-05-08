from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import BackendSettings
from .routers.admin import build_admin_router
from .routers.backtests import build_backtest_router
from .routers.health import router as health_router
from .routers.market_research import build_market_research_router
from .routers.paper import build_paper_router
from .routers.refresh import build_refresh_router
from .routers.saas import build_saas_router
from .routers.sentiment import build_sentiment_router
from .routers.strategies import build_strategy_router
from .routers.system import build_system_router
from .routers.telemetry import build_telemetry_router
from .security import install_security_middleware
from .telemetry import DailyRefreshScheduler


def configure_observability(settings: BackendSettings) -> None:
    if settings.sentry_dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=settings.sentry_dsn,
                environment=settings.app_env,
                traces_sample_rate=0.1 if settings.is_production else 0.0,
            )
        except Exception:  # pragma: no cover - optional exporter
            import logging

            logging.getLogger("pairs_trading.api").exception("sentry_initialization_failed")


def instrument_app(app: FastAPI, settings: BackendSettings) -> None:
    if not settings.otel_exporter_otlp_endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": "quantops-api", "deployment.environment": settings.app_env}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
    except Exception:  # pragma: no cover - optional exporter
        import logging

        logging.getLogger("pairs_trading.api").exception("otel_initialization_failed")


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
    instrument_app(app, app_settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    install_security_middleware(app, app_settings)
    app.include_router(health_router, prefix="/api")
    app.include_router(build_strategy_router(app_settings), prefix="/api")
    app.include_router(build_backtest_router(app_settings), prefix="/api")
    app.include_router(build_market_research_router(app_settings), prefix="/api")
    app.include_router(build_paper_router(app_settings), prefix="/api")
    app.include_router(build_saas_router(app_settings), prefix="/api")
    app.include_router(build_admin_router(app_settings), prefix="/api")
    app.include_router(build_refresh_router(app_settings), prefix="/api")
    app.include_router(build_sentiment_router(app_settings), prefix="/api")
    app.include_router(build_system_router(app_settings), prefix="/api")
    app.include_router(build_telemetry_router(app_settings), prefix="/api")
    return app


app = create_app()
