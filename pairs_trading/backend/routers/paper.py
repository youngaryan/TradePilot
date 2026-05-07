from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..authz import require_auth_context, require_csrf, require_paid_context
from ..config import BackendSettings
from ..quotas import QuotaExceeded, QuotaService
from ..redaction import redact_paths
from ..saas import RequestContext
from ..schemas import PaperRunRequest
from ..services import PaperRunCommand, PaperRunJobRunner, PaperService


def build_paper_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/paper", tags=["paper"])
    service = PaperService(settings)
    runner = PaperRunJobRunner(settings)
    quotas = QuotaService(settings)
    auth_context = require_auth_context(settings)
    paid_context = require_paid_context(settings, feature="Paper trading agents", machine_scope="paper:run")
    csrf_guard = require_csrf(settings)

    @router.get("/summary")
    def get_summary(
        paper_agent_id: str | None = Query(default=None, description="Optional tenant-owned paper agent id."),
        ctx: RequestContext = Depends(auth_context),
    ) -> dict[str, Any]:
        payload = service.build_dashboard_payload(organization_id=ctx.organization_id, paper_agent_id=paper_agent_id)
        return redact_paths(payload) if settings.is_production else payload

    @router.get("/strategies")
    def list_strategies(
        paper_agent_id: str | None = Query(default=None, description="Optional tenant-owned paper agent id."),
        ctx: RequestContext = Depends(auth_context),
    ) -> list[dict[str, Any]]:
        strategies = service.list_strategies(organization_id=ctx.organization_id, paper_agent_id=paper_agent_id)
        return redact_paths(strategies) if settings.is_production else strategies

    @router.get("/strategies/{strategy_name}")
    def get_strategy(
        strategy_name: str,
        paper_agent_id: str | None = Query(default=None, description="Optional tenant-owned paper agent id."),
        ctx: RequestContext = Depends(auth_context),
    ) -> dict[str, Any]:
        strategy = service.get_strategy(organization_id=ctx.organization_id, strategy_name=strategy_name, paper_agent_id=paper_agent_id)
        if strategy is None:
            raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_name}")
        return redact_paths(strategy) if settings.is_production else strategy

    @router.post("/run")
    def run_batch(request: PaperRunRequest, ctx: RequestContext = Depends(paid_context), _: None = Depends(csrf_guard)) -> dict[str, Any]:
        if settings.is_production:
            raise HTTPException(
                status_code=409,
                detail={"code": "queued_execution_required", "message": "Production paper execution must use /api/paper/run-job."},
            )
        try:
            quotas.check_and_record(
                organization_id=ctx.organization_id,
                user_id=str(ctx.user.get("id") or ""),
                feature="paper_job",
                properties={"mode": "sync", "asof_start": request.asof_start, "asof_end": request.asof_end},
            )
            payload = service.run_paper_batch(
                PaperRunCommand(
                    deployment_config_path=Path(request.deployment_config_path) if request.deployment_config_path else None,
                    deployment_config=request.deployment_config,
                    asof_date=request.asof_date,
                    asof_start=request.asof_start,
                    asof_end=request.asof_end,
                ),
                organization_id=ctx.organization_id,
            )
            return redact_paths(payload) if settings.is_production else payload
        except QuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=exc.as_detail()) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/run-job", status_code=202)
    def run_batch_job(request: PaperRunRequest, ctx: RequestContext = Depends(paid_context), _: None = Depends(csrf_guard)) -> dict[str, Any]:
        if settings.is_production and request.deployment_config_path is not None:
            raise HTTPException(
                status_code=400,
                detail={"code": "raw_path_rejected", "message": "Production paper jobs require inline deployment_config or tenant-owned config records, not raw filesystem paths."},
            )
        try:
            quotas.check_and_record(
                organization_id=ctx.organization_id,
                user_id=str(ctx.user.get("id") or ""),
                feature="paper_job",
                properties={"mode": "job", "asof_start": request.asof_start, "asof_end": request.asof_end},
            )
            job = runner.submit(
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
            return redact_paths(job) if settings.is_production else job
        except QuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=exc.as_detail()) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/jobs")
    def list_jobs(ctx: RequestContext = Depends(auth_context)) -> list[dict[str, Any]]:
        jobs = runner.list_jobs(organization_id=ctx.organization_id)
        return redact_paths(jobs) if settings.is_production else jobs

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, ctx: RequestContext = Depends(auth_context)) -> dict[str, Any]:
        job = runner.get_job(job_id, organization_id=ctx.organization_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Paper job not found: {job_id}")
        return redact_paths(job) if settings.is_production else job

    return router
