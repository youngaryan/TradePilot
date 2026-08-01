from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..authz import require_auth_context, require_csrf, require_paid_context
from ..config import BackendSettings
from ..quotas import QuotaExceeded, QuotaService
from ..redaction import redact_paths
from ..saas import RequestContext
from ..schemas import BacktestRunRequest
from ..backtest_services import BacktestJobRunner, BacktestService


def build_backtest_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/backtests", tags=["backtests"])
    runner = BacktestJobRunner(settings)
    service = BacktestService(settings)
    quotas = QuotaService(settings)
    auth_context = require_auth_context(settings)
    job_read_context = require_auth_context(settings, machine_scope="backtests:read")
    paid_context = require_paid_context(settings, feature="Backtest jobs", machine_scope="backtests:run")
    csrf_guard = require_csrf(settings)

    @router.get("/templates")
    def list_templates() -> list[dict[str, Any]]:
        return service.templates()

    @router.get("/ohlc")
    def get_ohlc(
        symbol: str,
        start: str,
        end: str,
        interval: str = "1d",
        _ctx: RequestContext = Depends(auth_context),
    ) -> dict[str, Any]:
        """Real OHLCV bars for one symbol so the UI can draw candlestick charts."""
        from ...data.market import fetch_ohlc_frame

        try:
            frame = fetch_ohlc_frame(symbol=symbol.strip().upper(), start=start, end=end, interval=interval)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Cap the payload; the UI buckets to ~160 candles anyway.
        if len(frame) > 3000:
            frame = frame.iloc[-3000:]

        def _num(value: Any) -> float | None:
            try:
                out = float(value)
            except (TypeError, ValueError):
                return None
            return None if out != out else out

        rows = [
            {
                "timestamp": ts.isoformat(),
                "open": _num(row["open"]),
                "high": _num(row["high"]),
                "low": _num(row["low"]),
                "close": _num(row["close"]),
                "volume": _num(row["volume"]),
            }
            for ts, row in frame.iterrows()
        ]
        rows = [r for r in rows if r["close"] is not None]
        return {"symbol": symbol.strip().upper(), "interval": interval, "rows": rows}

    @router.post("/run", status_code=202)
    def run_backtest(request: BacktestRunRequest, ctx: RequestContext = Depends(paid_context), _: None = Depends(csrf_guard)) -> dict[str, Any]:
        try:
            service.validate_request(request, organization_id=ctx.organization_id, user_id=str(ctx.user.get("id") or ""))
            quotas.check_and_record(
                organization_id=ctx.organization_id,
                user_id=str(ctx.user.get("id") or ""),
                feature="backtest_job",
                properties={"pipeline": request.pipeline, "symbols": request.symbols, "trading_mode": request.trading_mode, "compare_modes": request.compare_modes},
                role=ctx.user.get("role")
            )
            return runner.submit(request, organization_id=ctx.organization_id, user_id=str(ctx.user.get("id") or ""))
        except QuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=exc.as_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/jobs")
    def list_jobs(ctx: RequestContext = Depends(job_read_context)) -> list[dict[str, Any]]:
        jobs = runner.list_jobs(organization_id=ctx.organization_id)
        return redact_paths(jobs) if settings.is_production else jobs

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, ctx: RequestContext = Depends(job_read_context)) -> dict[str, Any]:
        job = runner.get_job(job_id, organization_id=ctx.organization_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Backtest job not found: {job_id}")
        return redact_paths(job) if settings.is_production else job

    return router
