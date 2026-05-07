from __future__ import annotations

from pathlib import Path
from typing import Any

from ..platform import build_metadata_store
from .config import BackendSettings
from .schemas import BacktestRunRequest, SentimentAccumulationRequest
from .services import BacktestJobRunner, PaperRunCommand, PaperRunJobRunner, SentimentJobRunner


def run_queued_job(kind: str, job_id: str) -> dict[str, Any]:
    settings = BackendSettings.from_env()
    store = build_metadata_store(settings)
    job = store.get_job(kind=kind, job_id=job_id)
    if job is None:
        raise ValueError(f"Queued job not found: {kind}/{job_id}")
    organization_id = str(job.get("organization_id") or "")
    if not organization_id:
        raise ValueError(f"Queued job is missing organization_id: {kind}/{job_id}")
    request = job.get("request") or {}

    if kind == "backtest":
        runner = BacktestJobRunner(settings, mark_interrupted_on_load=False)
        runner._run_job(job_id, BacktestRunRequest.model_validate(request), organization_id)
        return runner.get_job(job_id, organization_id=organization_id) or {"id": job_id, "kind": kind}

    if kind == "paper":
        runner = PaperRunJobRunner(settings, mark_interrupted_on_load=False)
        command = PaperRunCommand(
            deployment_config_path=Path(request["deployment_config_path"]) if request.get("deployment_config_path") else None,
            deployment_config=request.get("deployment_config"),
            asof_date=request.get("asof_date"),
            asof_start=request.get("asof_start"),
            asof_end=request.get("asof_end"),
        )
        runner._run_job(job_id, command, organization_id)
        return runner.get_job(job_id, organization_id=organization_id) or {"id": job_id, "kind": kind}

    if kind == "sentiment":
        runner = SentimentJobRunner(settings, mark_interrupted_on_load=False)
        runner._run_job(job_id, SentimentAccumulationRequest.model_validate(request), organization_id)
        return runner.get_job(job_id, organization_id=organization_id) or {"id": job_id, "kind": kind}

    raise ValueError(f"Unsupported queued job kind: {kind}")
