from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..authz import require_auth_context, require_paid_context
from ..config import BackendSettings
from ..saas import RequestContext
from ..schemas import SentimentAccumulationRequest
from ..services import SentimentJobRunner, SentimentService


def build_sentiment_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/sentiment", tags=["sentiment"])
    service = SentimentService(settings)
    runner = SentimentJobRunner(settings)
    auth_context = require_auth_context(settings)
    paid_context = require_paid_context(settings, feature="Sentiment accumulation")

    @router.get("/dataset")
    def get_dataset(
        output_dir: str | None = Query(default=None, description="Optional sentiment dataset directory."),
    ) -> dict[str, Any]:
        return service.dataset(output_dir=output_dir)

    @router.post("/accumulate")
    def accumulate(request: SentimentAccumulationRequest, _: RequestContext = Depends(paid_context)) -> dict[str, Any]:
        try:
            return service.accumulate(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/accumulate-job", status_code=202)
    def accumulate_job(request: SentimentAccumulationRequest, ctx: RequestContext = Depends(paid_context)) -> dict[str, Any]:
        try:
            return runner.submit(request, organization_id=ctx.organization_id, user_id=str(ctx.user.get("id") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/jobs")
    def list_jobs(ctx: RequestContext = Depends(auth_context)) -> list[dict[str, Any]]:
        return runner.list_jobs(organization_id=ctx.organization_id)

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, ctx: RequestContext = Depends(auth_context)) -> dict[str, Any]:
        job = runner.get_job(job_id, organization_id=ctx.organization_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Sentiment job not found: {job_id}")
        return job

    return router
