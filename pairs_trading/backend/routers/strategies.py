from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...api import build_strategy_catalog
from ..authz import require_auth_context, require_csrf
from ..config import BackendSettings
from ..saas import RequestContext
from ..schemas import StrategyBuilderApprovalRequest, StrategyBuilderChatRequest
from ..strategy_builder import StrategyBuilderService


def build_strategy_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/strategies", tags=["strategies"])
    auth_context = require_auth_context(settings)
    csrf_guard = require_csrf(settings)
    builder = StrategyBuilderService(settings)

    @router.get("/catalog")
    def get_strategy_catalog() -> list[dict[str, Any]]:
        return build_strategy_catalog()

    @router.get("/allowed")
    def get_allowed_strategy_catalog(ctx: RequestContext = Depends(auth_context)) -> list[dict[str, Any]]:
        return builder.allowed_catalog(
            organization_id=ctx.organization_id,
            user_id=str(ctx.user["id"]),
            base_catalog=build_strategy_catalog(),
        )

    @router.get("/catalog/{strategy_id}")
    def get_strategy_catalog_item(strategy_id: str) -> dict[str, Any]:
        normalized = strategy_id.casefold()
        for item in build_strategy_catalog():
            if str(item["id"]).casefold() == normalized:
                return item
        raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_id}")

    @router.get("/user")
    def list_user_strategies(ctx: RequestContext = Depends(auth_context)) -> list[dict[str, Any]]:
        return builder.user_strategies(organization_id=ctx.organization_id, user_id=str(ctx.user["id"]))

    @router.post("/builder/chat")
    def strategy_builder_chat(
        request: StrategyBuilderChatRequest,
        ctx: RequestContext = Depends(auth_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        if not request.messages:
            raise HTTPException(status_code=400, detail="Send at least one user message.")
        try:
            return builder.chat(
                organization_id=ctx.organization_id,
                user_id=str(ctx.user["id"]),
                messages=[message.model_dump(mode="json") for message in request.messages],
                draft_spec=request.draft_spec,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/builder/approve")
    def strategy_builder_approve(
        request: StrategyBuilderApprovalRequest,
        ctx: RequestContext = Depends(auth_context),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        if not request.approved:
            raise HTTPException(status_code=400, detail="Explicit approval is required before a strategy can be saved.")
        try:
            approval_text = request.approval_text.strip() or "Approved in strategy-builder UI"
            return builder.approve(
                organization_id=ctx.organization_id,
                user_id=str(ctx.user["id"]),
                spec=request.spec,
                approval_text=approval_text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
