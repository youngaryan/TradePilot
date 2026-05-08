from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..authz import require_auth_context, require_csrf, require_paid_context
from ..config import BackendSettings
from ..market_research_services import MarketResearchJobRunner, MarketResearchService
from ..quotas import QuotaExceeded, QuotaService
from ..redaction import redact_paths
from ..saas import RequestContext
from ..schemas import MarketResearchRunRequest


def build_market_research_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/market-research", tags=["market-research"])
    service = MarketResearchService(settings)
    runner = MarketResearchJobRunner(settings)
    quotas = QuotaService(settings)
    auth_context = require_auth_context(settings)
    paid_context = require_paid_context(settings, feature="Market research committee", machine_scope="market-research:run")
    csrf_guard = require_csrf(settings)

    @router.post("/run-job", status_code=202)
    def run_market_research(
        request: MarketResearchRunRequest,
        ctx: RequestContext = Depends(paid_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        try:
            service.validate_request(request)
            quotas.check_and_record(
                organization_id=ctx.organization_id,
                user_id=str(ctx.user.get("id") or ""),
                feature="market_research_job",
                properties={"ticker": request.ticker, "horizon": request.horizon, "provider": request.provider},
                role=ctx.user.get("role"),
            )
            job = runner.submit(request, organization_id=ctx.organization_id, user_id=str(ctx.user.get("id") or ""))
            return redact_paths(job) if settings.is_production else job
        except QuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=exc.as_detail()) from exc
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
            raise HTTPException(status_code=404, detail=f"Market research job not found: {job_id}")
        return redact_paths(job) if settings.is_production else job

    return router
