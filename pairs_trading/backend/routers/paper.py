from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..authz import require_auth_context, require_paid_context
from ..config import BackendSettings
from ..saas import RequestContext
from ..schemas import PaperRunRequest
from ..services import PaperRunCommand, PaperRunJobRunner, PaperService


def build_paper_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/paper", tags=["paper"])
    service = PaperService(settings)
    runner = PaperRunJobRunner(settings)
    auth_context = require_auth_context(settings)
    paid_context = require_paid_context(settings, feature="Paper trading agents")

    @router.get("/summary")
    def get_summary(
        batch_summary_path: str | None = Query(default=None, description="Optional explicit paper_batch_summary.json path."),
    ) -> dict[str, Any]:
        return service.build_dashboard_payload(batch_summary_path=batch_summary_path)

    @router.get("/strategies")
    def list_strategies(
        batch_summary_path: str | None = Query(default=None, description="Optional explicit paper_batch_summary.json path."),
    ) -> list[dict[str, Any]]:
        return service.list_strategies(batch_summary_path=batch_summary_path)

    @router.get("/strategies/{strategy_name}")
    def get_strategy(
        strategy_name: str,
        batch_summary_path: str | None = Query(default=None, description="Optional explicit paper_batch_summary.json path."),
    ) -> dict[str, Any]:
        strategy = service.get_strategy(strategy_name=strategy_name, batch_summary_path=batch_summary_path)
        if strategy is None:
            raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_name}")
        return strategy

    @router.post("/run")
    def run_batch(request: PaperRunRequest, _: RequestContext = Depends(paid_context)) -> dict[str, Any]:
        try:
            return service.run_paper_batch(
                PaperRunCommand(
                    deployment_config_path=Path(request.deployment_config_path) if request.deployment_config_path else None,
                    deployment_config=request.deployment_config,
                    asof_date=request.asof_date,
                    asof_start=request.asof_start,
                    asof_end=request.asof_end,
                )
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/run-job", status_code=202)
    def run_batch_job(request: PaperRunRequest, ctx: RequestContext = Depends(paid_context)) -> dict[str, Any]:
        try:
            return runner.submit(
                PaperRunCommand(
                    deployment_config_path=Path(request.deployment_config_path) if request.deployment_config_path else None,
                    deployment_config=request.deployment_config,
                    asof_date=request.asof_date,
                    asof_start=request.asof_start,
                    asof_end=request.asof_end,
                ),
                organization_id=ctx.organization_id,
                user_id=str(ctx.user.get("id") or ""),
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/jobs")
    def list_jobs(ctx: RequestContext = Depends(auth_context)) -> list[dict[str, Any]]:
        return runner.list_jobs(organization_id=ctx.organization_id)

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, ctx: RequestContext = Depends(auth_context)) -> dict[str, Any]:
        job = runner.get_job(job_id, organization_id=ctx.organization_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Paper job not found: {job_id}")
        return job

    return router
