from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..authz import require_csrf
from ..config import BackendSettings
from ..saas import AuthService, BillingService, CSRF_COOKIE_NAME, MFA_COOKIE_NAME, RequestContext, SaaSService, SESSION_COOKIE_NAME
from ..schemas import (
    ApiKeyCreateRequest,
    BillingCheckoutRequest,
    BillingPortalRequest,
    EmailVerificationRequest,
    EmailVerificationSendRequest,
    LoginRequest,
    MfaVerifyRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
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
        token = str(payload.get("session_token") or "")
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

    def public_auth_payload(payload: dict[str, Any]) -> dict[str, Any]:
        public = dict(payload)
        public.pop("session_token", None)
        public.pop("csrf_token", None)
        return public

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
            return public_auth_payload(payload)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @router.post("/auth/signup", status_code=201)
    def signup(request: SignupRequest, response: Response) -> dict[str, Any]:
        try:
            payload = auth_service.signup(request)
            set_auth_cookies(response, payload)
            return public_auth_payload(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/auth/me")
    def me(
        request: Request,
        response: Response,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        active_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    ) -> dict[str, Any]:
        if credentials is not None and not credentials.credentials.startswith("qops_"):
            raise HTTPException(
                status_code=401,
                detail={"code": "machine_api_key_required", "message": "Bearer auth is reserved for scoped machine API keys; browser sessions use HttpOnly cookies."},
            )
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
        if credentials is not None and not credentials.credentials.startswith("qops_"):
            raise HTTPException(
                status_code=401,
                detail={"code": "machine_api_key_required", "message": "Bearer auth is reserved for scoped machine API keys; browser sessions use HttpOnly cookies."},
            )
        token = credentials.credentials if credentials is not None else request.cookies.get(SESSION_COOKIE_NAME)
        if token:
            auth_service.logout(token=token)
        response.delete_cookie(SESSION_COOKIE_NAME, path="/", domain=settings.cookie_domain)
        response.delete_cookie(CSRF_COOKIE_NAME, path="/", domain=settings.cookie_domain)
        response.delete_cookie(MFA_COOKIE_NAME, path="/", domain=settings.cookie_domain)
        return {"status": "ok"}

    @router.post("/auth/csrf")
    def csrf(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict[str, str]:
        if credentials is not None and not credentials.credentials.startswith("qops_"):
            raise HTTPException(
                status_code=401,
                detail={"code": "machine_api_key_required", "message": "Bearer auth is reserved for scoped machine API keys; browser sessions use HttpOnly cookies."},
            )
        token = credentials.credentials if credentials is not None else request.cookies.get(SESSION_COOKIE_NAME)
        if not token:
            raise HTTPException(status_code=401, detail="Login required.")
        # Also verifies that the session still exists before issuing a token.
        auth_service.authenticate(token=token)
        return {"csrf_token": auth_service.csrf_token_for_session(token)}

    @router.get("/account/export")
    def export_account(ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        if ctx.user.get("machine"):
            raise HTTPException(status_code=403, detail="Machine API keys cannot export user accounts.")
        return saas_service.export_account(context=ctx)

    @router.delete("/account")
    def delete_account(
        response: Response,
        request: Request,
        ctx: RequestContext = Depends(context),
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        _: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        if ctx.user.get("machine"):
            raise HTTPException(status_code=403, detail="Machine API keys cannot delete user accounts.")
        try:
            payload = saas_service.delete_account(context=ctx)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        token = credentials.credentials if credentials is not None else request.cookies.get(SESSION_COOKIE_NAME)
        if token:
            auth_service.logout(token=token)
        response.delete_cookie(SESSION_COOKIE_NAME, path="/", domain=settings.cookie_domain)
        response.delete_cookie(CSRF_COOKIE_NAME, path="/", domain=settings.cookie_domain)
        response.delete_cookie(MFA_COOKIE_NAME, path="/", domain=settings.cookie_domain)
        return payload

    @router.post("/auth/verify-email/request")
    def verify_email_request(request: EmailVerificationSendRequest) -> dict[str, Any]:
        return auth_service.request_email_verification(email=request.email)

    @router.post("/auth/verify-email")
    def verify_email(request: EmailVerificationRequest) -> dict[str, Any]:
        try:
            return auth_service.verify_email(token=request.token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/auth/password-reset/request")
    def password_reset_request(request: PasswordResetRequest) -> dict[str, str]:
        return auth_service.request_password_reset(email=request.email)

    @router.post("/auth/password-reset/confirm")
    def password_reset_confirm(request: PasswordResetConfirmRequest) -> dict[str, str]:
        try:
            return auth_service.confirm_password_reset(token=request.token, new_password=request.new_password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/auth/mfa/verify")
    def mfa_verify(
        request_body: MfaVerifyRequest,
        response: Response,
        request: Request,
        ctx: RequestContext = Depends(context),
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        __: None = Depends(csrf_guard),
    ) -> dict[str, Any]:
        try:
            payload = auth_service.verify_mfa_code(user_id=str(ctx.user["id"]), code=request_body.code)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        token = credentials.credentials if credentials is not None else request.cookies.get(SESSION_COOKIE_NAME)
        if not token:
            raise HTTPException(status_code=401, detail="Login required.")
        response.set_cookie(
            MFA_COOKIE_NAME,
            auth_service.mfa_cookie_for_session(session_token=token, user_id=str(ctx.user["id"])),
            httponly=True,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
            max_age=settings.session_ttl_hours * 3600,
            path="/",
            domain=settings.cookie_domain,
        )
        return payload

    @router.post("/auth/mfa/setup")
    def mfa_setup(ctx: RequestContext = Depends(context), __: None = Depends(csrf_guard)) -> dict[str, Any]:
        try:
            return auth_service.setup_mfa(user_id=str(ctx.user["id"]))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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

    @router.post("/billing/sync")
    def sync_billing(ctx: RequestContext = Depends(context), _: None = Depends(csrf_guard)) -> dict[str, Any]:
        try:
            return billing_service.sync_subscription(organization_id=ctx.organization_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Stripe subscription sync failed: {exc}") from exc

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
