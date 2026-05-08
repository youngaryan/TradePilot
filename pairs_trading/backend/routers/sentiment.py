from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..authz import require_auth_context, require_csrf, require_paid_context
from ..config import BackendSettings
from ..financial_events import FinancialEventsService
from ..quotas import QuotaExceeded, QuotaService
from ..redaction import redact_paths
from ..saas import RequestContext
from ..schemas import SentimentAccumulationRequest
from ..services import SentimentJobRunner, SentimentService


def build_sentiment_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/sentiment", tags=["sentiment"])
    service = SentimentService(settings)
    financial_events = FinancialEventsService(settings)
    runner = SentimentJobRunner(settings)
    quotas = QuotaService(settings)
    auth_context = require_auth_context(settings)
    paid_context = require_paid_context(settings, feature="Sentiment accumulation", machine_scope="sentiment:run")
    csrf_guard = require_csrf(settings)

    @router.get("/dataset")
    def get_dataset(
        dataset_id: str | None = Query(default=None, description="Optional tenant-owned sentiment dataset id."),
        ctx: RequestContext = Depends(auth_context),
    ) -> dict[str, Any]:
        return service.dataset(dataset_id=dataset_id, organization_id=ctx.organization_id)

    @router.get("/financial-events")
    def get_financial_events(
        symbols: str = Query(..., description="Comma-separated ticker symbols."),
        start: str = Query(..., description="Start date, formatted as YYYY-MM-DD."),
        end: str = Query(..., description="End date, formatted as YYYY-MM-DD."),
        limit: int = Query(default=80, ge=1, le=200),
        ctx: RequestContext = Depends(auth_context),
    ) -> dict[str, Any]:
        del ctx
        try:
            tickers = [symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()]
            return financial_events.events(tickers, start, end, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/accumulate")
    def accumulate(request: SentimentAccumulationRequest, ctx: RequestContext = Depends(paid_context), _: None = Depends(csrf_guard)) -> dict[str, Any]:
        if settings.is_production:
            raise HTTPException(
                status_code=409,
                detail={"code": "queued_execution_required", "message": "Production sentiment accumulation must use /api/sentiment/accumulate-job."},
            )
        try:
            service.validate_request(request)
            quotas.check_and_record(
                organization_id=ctx.organization_id,
                user_id=str(ctx.user.get("id") or ""),
                feature="sentiment_job",
                properties={"providers": request.providers, "symbols": request.symbols, "mode": "sync"},
                role=ctx.user.get("role")
            )
            payload = service.accumulate(request, organization_id=ctx.organization_id)
            return redact_paths(payload) if settings.is_production else payload
        except QuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=exc.as_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/accumulate-job", status_code=202)
    def accumulate_job(request: SentimentAccumulationRequest, ctx: RequestContext = Depends(paid_context), _: None = Depends(csrf_guard)) -> dict[str, Any]:
        try:
            service.validate_request(request)
            estimated_news_pages = 0.0
            if {"web", "local_web"} & {str(provider).lower() for provider in request.providers}:
                estimated_news_pages = float(max(1, len(request.symbols)) * max(1, request.web_research_max_articles))
                if request.local_web_search_urls or request.web_research_domains:
                    estimated_news_pages += float(max(1, len(request.local_web_search_urls) + len(request.web_research_domains)) * request.local_web_max_pages_per_source)
                quotas.check(organization_id=ctx.organization_id, feature="news_pages", quantity=estimated_news_pages, role=ctx.user.get("role"))
            quotas.check(organization_id=ctx.organization_id, feature="sentiment_job", role=ctx.user.get("role"))
            if estimated_news_pages:
                quotas.record(
                    organization_id=ctx.organization_id,
                    user_id=str(ctx.user.get("id") or ""),
                    feature="news_pages",
                    quantity=estimated_news_pages,
                    properties={"providers": request.providers, "symbols": request.symbols},
                )
            quotas.record(
                organization_id=ctx.organization_id,
                user_id=str(ctx.user.get("id") or ""),
                feature="sentiment_job",
                properties={"providers": request.providers, "symbols": request.symbols, "mode": "job"},
            )
            job = runner.submit(request, organization_id=ctx.organization_id, user_id=str(ctx.user.get("id") or ""))
            return redact_paths(job) if settings.is_production else job
        except QuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=exc.as_detail()) from exc
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
