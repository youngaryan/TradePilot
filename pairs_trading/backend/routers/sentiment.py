from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..config import BackendSettings
from ..schemas import SentimentAccumulationRequest
from ..services import SentimentService


def build_sentiment_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/sentiment", tags=["sentiment"])
    service = SentimentService(settings)

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

    return router
