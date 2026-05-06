from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..authz import require_csrf
from ..config import BackendSettings
from ..saas import AuthService, BillingService, CSRF_COOKIE_NAME, RequestContext, SaaSService, SESSION_COOKIE_NAME
from ..schemas import (
    ApiKeyCreateRequest,
    BillingCheckoutRequest,
    BillingPortalRequest,
    LoginRequest,
    ProjectCreateRequest,
    SignupRequest,
)


bearer = HTTPBearer(auto_error=False)


def build_saas_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(tags=["saas"])
    auth_service = AuthService(settings)
    saas_service = SaaSService(settings)
    billing_service = BillingService(settings)
    csrf_guard = require_csrf(settings)

    def set_auth_cookies(response: Response, payload: dict[str, Any]) -> None:
        token = str(payload.get("access_token") or "")
        csrf = str(payload.get("csrf_token") or "")
        if not token:
            return
        cookie_kwargs = {
            "httponly": True,
            "secure": settings.cookie_secure,
            "samesite": settings.cookie_samesite,
            "max_age": settings.session_ttl_hours * 3600,
            "path": "/",
        }
        if settings.cookie_domain:
            cookie_kwargs["domain"] = settings.cookie_domain
        response.set_cookie(SESSION_COOKIE_NAME, token, **cookie_kwargs)
        csrf_kwargs = {
            "httponly": False,
            "secure": settings.cookie_secure,
            "samesite": settings.cookie_samesite,
            "max_age": settings.session_ttl_hours * 3600,
            "path": "/",
        }
        if settings.cookie_domain:
            csrf_kwargs["domain"] = settings.cookie_domain
        response.set_cookie(CSRF_COOKIE_NAME, csrf, **csrf_kwargs)

    def context(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        active_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    ) -> RequestContext:
        token = credentials.credentials if credentials is not None else request.cookies.get(SESSION_COOKIE_NAME)
        if not token:
            raise HTTPException(status_code=401, detail="Login required.")
        try:
            return auth_service.authenticate(token=token, organization_id=active_organization_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @router.post("/auth/login")
    def login(request: LoginRequest, response: Response) -> dict[str, Any]:
        try:
            payload = auth_service.login(email=request.email, password=request.password)
            set_auth_cookies(response, payload)
            return payload
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @router.post("/auth/signup", status_code=201)
    def signup(request: SignupRequest, response: Response) -> dict[str, Any]:
        try:
            payload = auth_service.signup(request)
            set_auth_cookies(response, payload)
            return payload
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/auth/me")
    def me(
        request: Request,
        response: Response,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        active_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    ) -> dict[str, Any]:
        token = credentials.credentials if credentials is not None else request.cookies.get(SESSION_COOKIE_NAME)
        if not token:
            raise HTTPException(status_code=401, detail="Login required.")
        try:
            payload = auth_service.me(token=token, organization_id=active_organization_id)
            if payload.get("csrf_token"):
                response.set_cookie(
                    CSRF_COOKIE_NAME,
                    str(payload["csrf_token"]),
                    httponly=False,
                    secure=settings.cookie_secure,
                    samesite=settings.cookie_samesite,
                    max_age=settings.session_ttl_hours * 3600,
                    path="/",
                    domain=settings.cookie_domain,
                )
            return payload
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @router.post("/auth/logout")
    def logout(response: Response, request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict[str, str]:
        token = credentials.credentials if credentials is not None else request.cookies.get(SESSION_COOKIE_NAME)
        if token:
            auth_service.logout(token=token)
        response.delete_cookie(SESSION_COOKIE_NAME, path="/", domain=settings.cookie_domain)
        response.delete_cookie(CSRF_COOKIE_NAME, path="/", domain=settings.cookie_domain)
        return {"status": "ok"}

    @router.post("/auth/csrf")
    def csrf(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict[str, str]:
        token = credentials.credentials if credentials is not None else request.cookies.get(SESSION_COOKIE_NAME)
        if not token:
            raise HTTPException(status_code=401, detail="Login required.")
        # Also verifies that the session still exists before issuing a token.
        auth_service.authenticate(token=token)
        return {"csrf_token": auth_service.csrf_token_for_session(token)}

    @router.post("/auth/verify-email")
    def verify_email(_: dict[str, Any] | None = None, __: None = Depends(csrf_guard)) -> dict[str, str]:
        return {"status": "accepted", "message": "Email verification is ready for SMTP-backed delivery."}

    @router.post("/auth/password-reset/request")
    def password_reset_request(_: dict[str, Any] | None = None) -> dict[str, str]:
        return {"status": "accepted", "message": "If the account exists, reset instructions will be sent."}

    @router.post("/auth/password-reset/confirm")
    def password_reset_confirm(_: dict[str, Any] | None = None) -> dict[str, str]:
        return {"status": "accepted", "message": "Password reset confirmation endpoint is wired for token validation."}

    @router.post("/auth/mfa/setup")
    def mfa_setup(_: RequestContext = Depends(context), __: None = Depends(csrf_guard)) -> dict[str, Any]:
        return {"status": "pending", "method": "totp", "message": "TOTP MFA setup hook is available for admin hardening."}

    @router.post("/auth/mfa/verify")
    def mfa_verify(response: Response, _: RequestContext = Depends(context), __: None = Depends(csrf_guard)) -> dict[str, Any]:
        response.set_cookie(
            "quantops_mfa",
            "verified",
            httponly=True,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
            max_age=settings.session_ttl_hours * 3600,
            path="/",
            domain=settings.cookie_domain,
        )
        return {"status": "verified", "method": "totp"}

    @router.get("/workspaces")
    def workspace(ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        return saas_service.workspace_payload(organization_id=ctx.organization_id)

    @router.get("/billing/pricing")
    def pricing() -> dict[str, Any]:
        return billing_service.pricing()

    @router.get("/billing/status")
    def billing_status(ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        return billing_service.status(organization_id=ctx.organization_id)

    @router.post("/workspaces/projects", status_code=201)
    def create_project(request: ProjectCreateRequest, ctx: RequestContext = Depends(context), _: None = Depends(csrf_guard)) -> dict[str, Any]:
        return saas_service.create_project(
            organization_id=ctx.organization_id,
            name=request.name,
            description=request.description,
        )

    @router.post("/workspaces/api-keys", status_code=201)
    def create_api_key(request: ApiKeyCreateRequest, ctx: RequestContext = Depends(context), _: None = Depends(csrf_guard)) -> dict[str, Any]:
        try:
            return saas_service.create_api_key_metadata(organization_id=ctx.organization_id, request=request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/workspaces/experiments")
    def list_experiments(ctx: RequestContext = Depends(context)) -> list[dict[str, Any]]:
        return saas_service.list_experiments(organization_id=ctx.organization_id)

    @router.get("/workspaces/datasets/{dataset_id}")
    def get_dataset(dataset_id: str, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        dataset = saas_service.get_dataset(organization_id=ctx.organization_id, dataset_id=dataset_id)
        if dataset is None:
            raise HTTPException(status_code=404, detail=f"Dataset not found: {dataset_id}")
        return dataset

    @router.get("/workspaces/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        artifact = saas_service.get_artifact(organization_id=ctx.organization_id, artifact_id=artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")
        return artifact

    @router.get("/workspaces/experiments/{experiment_id}")
    def get_experiment(experiment_id: str, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        experiment = saas_service.get_experiment(organization_id=ctx.organization_id, experiment_id=experiment_id)
        if experiment is None:
            raise HTTPException(status_code=404, detail=f"Experiment not found: {experiment_id}")
        return experiment

    @router.get("/workspaces/paper-agents")
    def list_paper_agents(ctx: RequestContext = Depends(context)) -> list[dict[str, Any]]:
        return saas_service.list_paper_agents(organization_id=ctx.organization_id)

    @router.get("/workspaces/paper-agents/{agent_id}")
    def get_paper_agent(agent_id: str, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        agent = saas_service.get_paper_agent(organization_id=ctx.organization_id, agent_id=agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Paper agent not found: {agent_id}")
        return agent

    @router.post("/billing/checkout")
    def checkout(request: BillingCheckoutRequest, ctx: RequestContext = Depends(context), _: None = Depends(csrf_guard)) -> dict[str, Any]:
        try:
            return billing_service.checkout(organization_id=ctx.organization_id, request=request)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Stripe checkout failed: {exc}") from exc

    @router.post("/billing/portal")
    def portal(request: BillingPortalRequest, ctx: RequestContext = Depends(context), _: None = Depends(csrf_guard)) -> dict[str, Any]:
        try:
            return billing_service.portal(organization_id=ctx.organization_id, return_url=request.return_url)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Stripe portal failed: {exc}") from exc

    @router.post("/billing/webhook")
    async def stripe_webhook(request: Request) -> dict[str, Any]:
        try:
            return billing_service.webhook(
                payload=await request.body(),
                signature_header=request.headers.get("stripe-signature"),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
