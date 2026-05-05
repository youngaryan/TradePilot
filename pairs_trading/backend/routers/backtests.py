from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..authz import require_paid_context
from ..config import BackendSettings
from ..saas import RequestContext
from ..schemas import BacktestRunRequest
from ..services import BacktestJobRunner, BacktestService


def build_backtest_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/backtests", tags=["backtests"])
    runner = BacktestJobRunner(settings)
    service = BacktestService(settings)
    paid_context = require_paid_context(settings, feature="Backtest jobs")

    @router.get("/templates")
    def list_templates() -> list[dict[str, Any]]:
        return service.templates()

    @router.post("/run", status_code=202)
    def run_backtest(request: BacktestRunRequest, _: RequestContext = Depends(paid_context)) -> dict[str, Any]:
        try:
            return runner.submit(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return runner.list_jobs()

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = runner.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Backtest job not found: {job_id}")
        return job

    return router
