from __future__ import annotations

from typing import Callable

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..platform import build_metadata_store
from .config import BackendSettings
from .saas import AuthService, MFA_COOKIE_NAME, RequestContext, SESSION_COOKIE_NAME


bearer = HTTPBearer(auto_error=False)

PAID_PLANS = {"pro", "team", "enterprise", "pro_trial"}
PAID_STATUSES = {"active"}


def is_admin_role(role: object) -> bool:
    return str(role or "user").lower() == "admin"


def is_paid_subscription(subscription: dict | None, *, allow_trial_entitlements: bool = False) -> bool:
    if not subscription:
        return False
    plan = str(subscription.get("plan") or "free").lower()
    status = str(subscription.get("status") or "").lower()
    statuses = set(PAID_STATUSES)
    if allow_trial_entitlements:
        statuses.add("trialing")
    return plan in PAID_PLANS and status in statuses


def require_auth_context(settings: BackendSettings) -> Callable[..., RequestContext]:
    auth_service = AuthService(settings)

    def dependency(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        active_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    ) -> RequestContext:
        if credentials is not None and not credentials.credentials.startswith("qops_"):
            raise HTTPException(
                status_code=401,
                detail={"code": "machine_api_key_required", "message": "Bearer auth is reserved for scoped machine API keys; browser sessions use HttpOnly cookies."},
            )
        token = credentials.credentials if credentials is not None else request.cookies.get(SESSION_COOKIE_NAME)
        if not token:
            raise HTTPException(status_code=401, detail="Login required.")
        try:
            return auth_service.authenticate(token=token, organization_id=active_organization_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    return dependency


def require_csrf(settings: BackendSettings) -> Callable[..., None]:
    auth_service = AuthService(settings)

    def dependency(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> None:
        # Bearer clients are kept for API/backward compatibility. Browser cookie
        # sessions must prove same-origin intent on mutating routes.
        if credentials is not None:
            return
        session_token = request.cookies.get(SESSION_COOKIE_NAME)
        if not session_token:
            return
        if not auth_service.verify_csrf_token(session_token=session_token, csrf_token=csrf_token):
            raise HTTPException(status_code=403, detail={"code": "csrf_required", "message": "Missing or invalid CSRF token."})

    return dependency


def require_admin_context(settings: BackendSettings) -> Callable[..., RequestContext]:
    auth_dependency = require_auth_context(settings)
    auth_service = AuthService(settings)

    def dependency(request: Request, ctx: RequestContext = Depends(auth_dependency)) -> RequestContext:
        if str(ctx.user.get("role") or "user").lower() != "admin":
            raise HTTPException(status_code=403, detail="Admin access required.")
        session_token = request.cookies.get(SESSION_COOKIE_NAME)
        mfa_ok = auth_service.verify_mfa_cookie(
            session_token=session_token or "",
            user_id=str(ctx.user.get("id") or ""),
            cookie_value=request.cookies.get(MFA_COOKIE_NAME),
        )
        if settings.is_production and not mfa_ok:
            raise HTTPException(
                status_code=403,
                detail={"code": "admin_mfa_required", "message": "Admin MFA verification is required before accessing admin APIs."},
            )
        return ctx

    return dependency


def require_paid_context(settings: BackendSettings, *, feature: str = "premium feature", machine_scope: str | None = None) -> Callable[..., RequestContext]:
    auth_dependency = require_auth_context(settings)
    store = build_metadata_store(settings)

    def dependency(ctx: RequestContext = Depends(auth_dependency)) -> RequestContext:
        if ctx.user.get("machine") and machine_scope:
            scopes = {str(scope).lower() for scope in ctx.user.get("scopes", [])}
            if "*" not in scopes and machine_scope.lower() not in scopes:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "api_key_scope_required",
                        "scope": machine_scope,
                        "message": f"This machine API key is missing the required scope: {machine_scope}.",
                    },
                )
        if is_admin_role(ctx.user.get("role")):
            return ctx
        subscription = store.get_subscription(organization_id=ctx.organization_id)
        if not is_paid_subscription(subscription, allow_trial_entitlements=settings.allow_trial_entitlements):
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
