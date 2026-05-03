from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..config import BackendSettings
from ..schemas import SentimentAccumulationRequest
from ..services import SentimentJobRunner, SentimentService


def build_sentiment_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/sentiment", tags=["sentiment"])
    service = SentimentService(settings)
    runner = SentimentJobRunner(settings)

    @router.get("/dataset")
    def get_dataset(
        output_dir: str | None = Query(default=None, description="Optional sentiment dataset directory."),
    ) -> dict[str, Any]:
        return service.dataset(output_dir=output_dir)

    @router.post("/accumulate")
    def accumulate(request: SentimentAccumulationRequest) -> dict[str, Any]:
        try:
            return service.accumulate(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/accumulate-job", status_code=202)
    def accumulate_job(request: SentimentAccumulationRequest) -> dict[str, Any]:
        try:
            return runner.submit(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return runner.list_jobs()

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = runner.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Sentiment job not found: {job_id}")
        return job

    return router
