from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import BackendSettings
from ..saas import AuthService, BillingService, RequestContext, SaaSService
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

    def context(
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

    @router.post("/auth/login")
    def login(request: LoginRequest) -> dict[str, Any]:
        try:
            return auth_service.login(email=request.email, password=request.password)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @router.post("/auth/signup", status_code=201)
    def signup(request: SignupRequest) -> dict[str, Any]:
        try:
            return auth_service.signup(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/auth/me")
    def me(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    ) -> dict[str, Any]:
        if credentials is None:
            raise HTTPException(status_code=401, detail="Login required.")
        try:
            return auth_service.me(token=credentials.credentials, organization_id=organization_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @router.post("/auth/logout")
    def logout(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict[str, str]:
        if credentials is not None:
            auth_service.logout(token=credentials.credentials)
        return {"status": "ok"}

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
    def create_project(request: ProjectCreateRequest, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        return saas_service.create_project(
            organization_id=ctx.organization_id,
            name=request.name,
            description=request.description,
        )

    @router.post("/workspaces/api-keys", status_code=201)
    def create_api_key(request: ApiKeyCreateRequest, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        try:
            return saas_service.create_api_key_metadata(organization_id=ctx.organization_id, request=request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/workspaces/experiments")
    def list_experiments(ctx: RequestContext = Depends(context)) -> list[dict[str, Any]]:
        return saas_service.list_experiments(organization_id=ctx.organization_id)

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
    def checkout(request: BillingCheckoutRequest, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
        try:
            return billing_service.checkout(organization_id=ctx.organization_id, request=request)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Stripe checkout failed: {exc}") from exc

    @router.post("/billing/portal")
    def portal(request: BillingPortalRequest, ctx: RequestContext = Depends(context)) -> dict[str, Any]:
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
