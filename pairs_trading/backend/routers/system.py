from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...platform import SQLiteMetadataStore
from ..config import BackendSettings


def build_system_router(settings: BackendSettings) -> APIRouter:
    router = APIRouter(prefix="/system", tags=["system"])
    metadata_store = SQLiteMetadataStore(settings.metadata_db_path)

    @router.get("/metadata")
    def get_metadata_summary() -> dict[str, Any]:
        counts = metadata_store.counts()
        return {
            "metadata_db_path": str(settings.metadata_db_path),
            "counts": {
                "jobs": counts.jobs,
                "deployment_configs": counts.deployment_configs,
                "experiment_runs": counts.experiment_runs,
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
            },
        }

    return router
