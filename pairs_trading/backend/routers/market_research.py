from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..authz import require_auth_context, require_csrf, require_paid_context
from ..config import BackendSettings
from ..market_research_services import MarketResearchJobRunner, MarketResearchService, StockUniverseService
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
    job_read_context = require_auth_context(settings, machine_scope="market-research:read")
    paid_context = require_paid_context(settings, feature="Market research committee", machine_scope="market-research:run")
    csrf_guard = require_csrf(settings)
    universe_service = StockUniverseService()

    @router.post("/run-job", status_code=202)
    def run_market_research(
        request: MarketResearchRunRequest,
        ctx: RequestContext = Depends(paid_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        try:
            service.validate_request(request)
            service.preflight_runtime(request)
            runtime_settings = service._effective_settings(request)
            quotas.check_and_record(
                organization_id=ctx.organization_id,
                user_id=str(ctx.user.get("id") or ""),
                feature="market_research_job",
                properties={
                    "ticker": request.ticker,
                    "horizon": request.horizon,
                    "provider": runtime_settings.market_research_llm_provider,
                    "model": runtime_settings.market_research_llm_model,
                    "pair": request.pair or "",
                    "ticker_count": len(request.tickers or []),
                },
                role=ctx.user.get("role"),
            )
            job = runner.submit(request, organization_id=ctx.organization_id, user_id=str(ctx.user.get("id") or ""))
            return redact_paths(job) if settings.is_production else job
        except QuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=exc.as_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/runtime")
    def get_market_research_runtime(ctx: RequestContext = Depends(auth_context)) -> dict[str, Any]:
        del ctx
        return service.runtime_diagnostics()

    @router.get("/jobs")
    def list_jobs(ctx: RequestContext = Depends(job_read_context)) -> list[dict[str, Any]]:
        jobs = runner.list_jobs(organization_id=ctx.organization_id)
        return redact_paths(jobs) if settings.is_production else jobs

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, ctx: RequestContext = Depends(job_read_context)) -> dict[str, Any]:
        job = runner.get_job(job_id, organization_id=ctx.organization_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Market research job not found: {job_id}")
        return redact_paths(job) if settings.is_production else job

    @router.get("/universe")
    def get_stock_universe(
        sector: str | None = Query(default=None),
        country: str | None = Query(default=None),
        exchange: str | None = Query(default=None),
        currency: str | None = Query(default=None),
        ctx: RequestContext = Depends(auth_context),
    ) -> dict[str, Any]:
        del ctx
        universe = universe_service.get_filtered(
            sector=sector,
            country=country,
            exchange=exchange,
            currency=currency,
        )
        return {
            "name": universe.name,
            "description": universe.description,
            "total_stocks": len(universe.stocks),
            "stocks": [
                {
                    "ticker": s.ticker,
                    "company_name": s.company_name,
                    "sector": s.sector,
                    "industry": s.industry,
                    "country": s.country,
                    "exchange": s.exchange,
                    "currency": s.currency,
                    "market_cap_category": s.market_cap_category,
                    "avg_volume": s.avg_volume,
                    "is_liquid": s.is_liquid,
                }
                for s in universe.stocks
            ],
            "sector_counts": universe.sector_counts(),
            "country_counts": universe.country_counts(),
            "exchange_counts": universe.exchange_counts(),
        }

    @router.get("/universe/groups")
    def get_universe_groups(ctx: RequestContext = Depends(auth_context)) -> dict[str, Any]:
        del ctx
        universe = universe_service.get_universe()
        return {
            "sectors": universe.sector_counts(),
            "countries": universe.country_counts(),
            "exchanges": universe.exchange_counts(),
        }

    @router.get("/decisions")
    def list_decisions(
        ticker: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        ctx: RequestContext = Depends(auth_context),
    ) -> list[dict[str, Any]]:
        decisions = service.decision_store.list(ticker=ticker, organization_id=ctx.organization_id, limit=limit)
        return [d.model_dump(mode="json") for d in decisions]

    @router.get("/decisions/summary")
    def get_decisions_summary(ctx: RequestContext = Depends(auth_context)) -> dict[str, Any]:
        return service.decision_store.summary(organization_id=ctx.organization_id)

    @router.get("/decisions/{ticker}")
    def list_decisions_for_ticker(
        ticker: str,
        limit: int = Query(default=50, ge=1, le=200),
        ctx: RequestContext = Depends(auth_context),
    ) -> list[dict[str, Any]]:
        decisions = service.decision_store.list(ticker=ticker, organization_id=ctx.organization_id, limit=limit)
        return [d.model_dump(mode="json") for d in decisions]

    @router.get("/charts/{job_id}")
    def get_chart_data(
        job_id: str,
        ctx: RequestContext = Depends(auth_context),
    ) -> dict[str, Any]:
        job = runner.get_job(job_id, organization_id=ctx.organization_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Market research job not found: {job_id}")
        result = job.get("result")
        if not result:
            return {"charts": {}}
        request_data = job.get("request", {})
        try:
            req = MarketResearchRunRequest(**request_data) if isinstance(request_data, dict) else MarketResearchRunRequest()
        except Exception:
            return {"charts": {}}
        return service.get_chart_data(
            req,
            organization_id=ctx.organization_id,
            user_id=str(ctx.user.get("id") or "") if ctx.user else None,
        )

    return router
