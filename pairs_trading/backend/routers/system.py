from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ...platform import build_metadata_store
from ..authz import require_admin_context
from ..config import BackendSettings
from ..saas import RequestContext


def _counts_payload(metadata_store) -> dict[str, int]:
    counts = metadata_store.counts()
    return {
        "jobs": counts.jobs,
        "deployment_configs": counts.deployment_configs,
        "experiment_runs": counts.experiment_runs,
        "artifacts": counts.artifacts,
        "users": counts.users,
        "organizations": counts.organizations,
        "projects": counts.projects,
        "experiments": counts.experiments,
        "paper_agents": counts.paper_agents,
        "datasets": counts.datasets,
        "api_keys": counts.api_keys,
        "subscriptions": counts.subscriptions,
        "telemetry_events": counts.telemetry_events,
        "refresh_runs": counts.refresh_runs,
        "refresh_statuses": counts.refresh_statuses,
    }


def build_system_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/system", tags=["system"])
    metadata_store = build_metadata_store(settings)
    admin_context = require_admin_context(settings)

    @router.get("/metadata")
    def get_metadata_summary() -> dict[str, Any]:
        return {
            "app_env": settings.app_env,
            "job_backend": "in_process" if settings.enable_in_process_jobs else "external",
            "storage_provider": "s3" if settings.s3_bucket else "local",
            "telemetry_enabled": settings.telemetry_enabled,
        }

    @router.get("/admin-counts")
    def get_admin_counts(_: RequestContext = Depends(admin_context)) -> dict[str, Any]:
        return {"counts": _counts_payload(metadata_store)}

    return router
