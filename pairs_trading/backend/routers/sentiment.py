from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..authz import require_auth_context, require_csrf, require_paid_context
from ..config import BackendSettings
from ..redaction import redact_paths
from ..saas import RequestContext
from ..schemas import SentimentAccumulationRequest
from ..services import SentimentJobRunner, SentimentService


def build_sentiment_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/sentiment", tags=["sentiment"])
    service = SentimentService(settings)
    runner = SentimentJobRunner(settings)
    auth_context = require_auth_context(settings)
    paid_context = require_paid_context(settings, feature="Sentiment accumulation")
    csrf_guard = require_csrf(settings)

    @router.get("/dataset")
    def get_dataset(
        output_dir: str | None = Query(default=None, description="Optional sentiment dataset directory."),
        ctx: RequestContext = Depends(auth_context),
    ) -> dict[str, Any]:
        return service.dataset(output_dir=output_dir, organization_id=ctx.organization_id)

    @router.post("/accumulate")
    def accumulate(request: SentimentAccumulationRequest, ctx: RequestContext = Depends(paid_context), _: None = Depends(csrf_guard)) -> dict[str, Any]:
        try:
            payload = service.accumulate(request, organization_id=ctx.organization_id)
            return redact_paths(payload) if settings.is_production else payload
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/accumulate-job", status_code=202)
    def accumulate_job(request: SentimentAccumulationRequest, ctx: RequestContext = Depends(paid_context), _: None = Depends(csrf_guard)) -> dict[str, Any]:
        try:
            job = runner.submit(request, organization_id=ctx.organization_id, user_id=str(ctx.user.get("id") or ""))
            return redact_paths(job) if settings.is_production else job
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/jobs")
    def list_jobs(ctx: RequestContext = Depends(auth_context)) -> list[dict[str, Any]]:
        jobs = runner.list_jobs(organization_id=ctx.organization_id)
        return redact_paths(jobs) if settings.is_production else jobs

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, ctx: RequestContext = Depends(auth_context)) -> dict[str, Any]:
        job = runner.get_job(job_id, organization_id=ctx.organization_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Sentiment job not found: {job_id}")
        return redact_paths(job) if settings.is_production else job

    return router
