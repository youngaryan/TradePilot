from __future__ import annotations

from typing import Callable

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..platform import SQLiteMetadataStore
from .config import BackendSettings
from .saas import AuthService, RequestContext


bearer = HTTPBearer(auto_error=False)

PAID_PLANS = {"pro", "team", "enterprise", "pro_trial"}
PAID_STATUSES = {"active", "trialing"}


def is_paid_subscription(subscription: dict | None) -> bool:
    if not subscription:
        return False
    plan = str(subscription.get("plan") or "free").lower()
    status = str(subscription.get("status") or "").lower()
    return plan in PAID_PLANS and status in PAID_STATUSES


def require_auth_context(settings: BackendSettings) -> Callable[..., RequestContext]:
    auth_service = AuthService(settings)

    def dependency(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    ) -> RequestContext:
        if credentials is None:
            raise HTTPException(status_code=401, detail="Login required.")
        try:
            return auth_service.authenticate(token=credentials.credentials, organization_id=organization_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    return dependency


def require_admin_context(settings: BackendSettings) -> Callable[..., RequestContext]:
    auth_dependency = require_auth_context(settings)

    def dependency(ctx: RequestContext = Depends(auth_dependency)) -> RequestContext:
        if str(ctx.user.get("role") or "user").lower() != "admin":
            raise HTTPException(status_code=403, detail="Admin access required.")
        return ctx

    return dependency


def require_paid_context(settings: BackendSettings, *, feature: str = "premium feature") -> Callable[..., RequestContext]:
    auth_dependency = require_auth_context(settings)
    store = SQLiteMetadataStore(settings.metadata_db_path)

    def dependency(ctx: RequestContext = Depends(auth_dependency)) -> RequestContext:
        subscription = store.get_subscription(organization_id=ctx.organization_id)
        if not is_paid_subscription(subscription):
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "payment_required",
                    "feature": feature,
                    "message": f"{feature} requires an active paid plan.",
                    "plan": (subscription or {}).get("plan", "free"),
                    "status": (subscription or {}).get("status", "missing"),
                    "upgrade_path": "/pricing",
                },
            )
        return ctx

    return dependency
