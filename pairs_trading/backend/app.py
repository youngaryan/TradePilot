from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import BackendSettings
from .routers.backtests import build_backtest_router
from .routers.health import router as health_router
from .routers.paper import build_paper_router
from .routers.sentiment import build_sentiment_router
from .routers.strategies import router as strategies_router
from .routers.system import build_system_router


def create_app(settings: BackendSettings | None = None) -> FastAPI:
    app_settings = settings or BackendSettings.from_env()
    app = FastAPI(
        title="Pairs Trading Quant API",
        version="0.1.0",
        description="Backend API for paper trading dashboards, research artifacts, and future live operations.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(health_router, prefix="/api")
    app.include_router(strategies_router, prefix="/api")
    app.include_router(build_backtest_router(app_settings), prefix="/api")
    app.include_router(build_paper_router(app_settings), prefix="/api")
    app.include_router(build_sentiment_router(app_settings), prefix="/api")
    app.include_router(build_system_router(app_settings), prefix="/api")
    return app


app = create_app()
