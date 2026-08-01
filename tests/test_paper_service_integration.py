from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.services import JobClaimLostError, PaperRunCommand, PaperRunJobRunner, PaperService
from pairs_trading.backend.saas import SaaSService
from pairs_trading.operations.paper_state import (
    ledger_key,
    resolve_scoped_artifact_root,
    resolve_scoped_state_dir,
)
from pairs_trading.platform import SQLiteMetadataStore
from tests.common import fresh_test_dir


def _settings(name: str) -> BackendSettings:
    root = fresh_test_dir(f"artifacts/{name}")
    return BackendSettings(
        paper_state_dir=root / "state",
        paper_artifact_root=root / "runs",
        paper_job_state_dir=root / "jobs",
        metadata_db_path=root / "metadata.sqlite3",
        default_paper_config=root / "missing.json",
    )


def _organization(store: SQLiteMetadataStore) -> str:
    user = store.get_user_by_email("demo@quantops.local")
    assert user is not None
    organization_id = store.get_default_organization_id(user_id=str(user["id"]))
    assert organization_id is not None
    return str(organization_id)


def _config(name: str = "trend") -> dict[str, object]:
    return {
        "execution": {"initial_cash": 100_000.0},
        "strategies": [{"name": name, "pipeline": "etf_trend", "symbols": ["SPY"]}],
    }


def _fake_low_level(calls: list[dict[str, object]]):
    def run(**kwargs):
        calls.append(kwargs)
        scope = kwargs["scope"]
        state_dir = resolve_scoped_state_dir(kwargs["state_dir"], scope)
        artifact_root = resolve_scoped_artifact_root(kwargs["artifact_root"], scope)
        state_dir.mkdir(parents=True, exist_ok=True)
        run_dir = artifact_root / f"run-{len(calls)}"
        run_dir.mkdir(parents=True, exist_ok=False)
        key = ledger_key("trend")
        state_dir.joinpath(f"{key}.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "revision": 1,
                    "organization_id": scope.organization_id,
                    "deployment_id": scope.deployment_id,
                    "project_id": scope.project_id,
                    "strategy_name": "trend",
                    "ledger_key": key,
                    "mode": "asset",
                    "initial_cash": 100_000.0,
                    "cash": 90_000.0,
                    "positions": {"SPY": 100.0},
                    "latest_orders": [],
                    "history": [
                        {
                            "timestamp": "2026-07-31T00:00:00",
                            "mode": "asset",
                            "equity_after": 101_000.0,
                            "daily_pnl": 1_000.0,
                            "cash_after": 90_000.0,
                            "positions": {"SPY": 100.0},
                            "metadata": {"pipeline": "etf_trend"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        summary = {
            "run_id": f"run-{len(calls)}",
            "run_timestamp_utc": "2026-07-31T12:00:00Z",
            "asof_date": str(kwargs["asof_date"]),
            "strategies": {"trend": {"equity_after": 101_000.0}},
            "leaderboard": [],
            "artifact_dir": str(run_dir),
            "state_dir": str(state_dir),
            "scope": scope.to_dict(),
            "visuals": {},
        }
        run_dir.joinpath("paper_batch_summary.json").write_text(json.dumps(summary), encoding="utf-8")
        return summary

    return run


def test_job_retry_returns_exact_completed_aggregate_without_reexecution() -> None:
    settings = _settings("test_paper_service_exact_run")
    service = PaperService(settings)
    store = SQLiteMetadataStore(settings.metadata_db_path)
    organization_id = _organization(store)
    calls: list[dict[str, object]] = []
    command = PaperRunCommand(deployment_config=_config(), asof_date="2026-07-31")

    with patch("pairs_trading.backend.services.run_paper_batch", side_effect=_fake_low_level(calls)):
        first = service.run_paper_batch(command, organization_id=organization_id, job_id="job-one")
        second = service.run_paper_batch(command, organization_id=organization_id, job_id="job-one")

    assert second == first
    assert len(calls) == 1
    deployment_id = first["deployment_id"]
    assert calls[0]["scope"].organization_id == organization_id
    assert calls[0]["scope"].deployment_id == deployment_id
    assert calls[0]["execution_idempotency_key"] == first["paper_run_id"]
    completed = store.get_paper_run(organization_id=organization_id, run_id=first["paper_run_id"])
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["aggregate_payload"] == first
    assert service.build_dashboard_payload(
        organization_id=organization_id,
        deployment_id=deployment_id,
    ) == first


def test_deployment_lookup_is_tenant_scoped_and_rejects_config_mismatch() -> None:
    settings = _settings("test_paper_service_deployment_scope")
    service = PaperService(settings)
    store = SQLiteMetadataStore(settings.metadata_db_path)
    organization_id = _organization(store)
    deployment = store.create_paper_deployment(
        organization_id=organization_id,
        idempotency_key="owned",
        payload={"id": "owned", "name": "Owned", "config": _config(), "status": "active"},
    )

    with pytest.raises(ValueError, match="does not match"):
        service.run_paper_batch(
            PaperRunCommand(deployment_id=deployment["id"], deployment_config=_config("other")),
            organization_id=organization_id,
        )
    with pytest.raises(ValueError, match="not found"):
        service.run_paper_batch(
            PaperRunCommand(deployment_id=deployment["id"]),
            organization_id="another-organization",
        )


def test_failed_execution_marks_durable_paper_run_failed() -> None:
    settings = _settings("test_paper_service_failure")
    service = PaperService(settings)
    store = SQLiteMetadataStore(settings.metadata_db_path)
    organization_id = _organization(store)

    with patch("pairs_trading.backend.services.run_paper_batch", side_effect=RuntimeError("broker unavailable")):
        with pytest.raises(RuntimeError, match="broker unavailable"):
            service.run_paper_batch(
                PaperRunCommand(deployment_config=_config(), asof_date="2026-07-31"),
                organization_id=organization_id,
                job_id="job-failed",
            )

    runs = store.list_paper_runs(organization_id=organization_id)
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert runs[0]["error"] == "broker unavailable"


def test_same_strategy_name_has_distinct_agents_per_deployment() -> None:
    settings = _settings("test_paper_agent_deployment_scope")
    store = SQLiteMetadataStore(settings.metadata_db_path)
    organization_id = _organization(store)
    first = store.create_paper_deployment(
        organization_id=organization_id,
        idempotency_key="first",
        payload={"id": "first", "name": "First", "config": _config(), "status": "active"},
    )
    second = store.create_paper_deployment(
        organization_id=organization_id,
        idempotency_key="second",
        payload={"id": "second", "name": "Second", "config": _config(), "status": "active"},
    )
    payload = {
        "strategies": [
            {"name": "trend", "pipeline": "etf_trend", "equity": 100_000.0, "cash": 100_000.0}
        ]
    }
    service = SaaSService(settings)
    service.sync_paper_agents_from_dashboard(
        organization_id=organization_id,
        deployment_id=first["id"],
        payload=payload,
    )
    service.sync_paper_agents_from_dashboard(
        organization_id=organization_id,
        deployment_id=second["id"],
        payload=payload,
    )

    first_agents = service.list_paper_agents(organization_id=organization_id, deployment_id=first["id"])
    second_agents = service.list_paper_agents(organization_id=organization_id, deployment_id=second["id"])
    assert len(first_agents) == len(second_agents) == 1
    assert first_agents[0]["id"] != second_agents[0]["id"]
    assert first_agents[0]["deployment_id"] == first["id"]
    assert second_agents[0]["deployment_id"] == second["id"]


def test_claim_loss_after_ledger_apply_resumes_same_run_without_double_apply() -> None:
    settings = _settings("test_paper_service_claim_recovery")
    service = PaperService(settings)
    store = SQLiteMetadataStore(settings.metadata_db_path)
    organization_id = _organization(store)
    calls: list[dict[str, object]] = []
    applied_keys: set[str] = set()
    ledger_apply_count = 0
    low_level = _fake_low_level(calls)

    def idempotent_low_level(**kwargs):
        nonlocal ledger_apply_count
        key = str(kwargs["execution_idempotency_key"])
        if key not in applied_keys:
            applied_keys.add(key)
            ledger_apply_count += 1
        return low_level(**kwargs)

    guard_calls = 0

    def losing_guard() -> None:
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 4:
            raise JobClaimLostError("lease transferred")

    command = PaperRunCommand(deployment_config=_config(), asof_date="2026-07-31")
    with patch("pairs_trading.backend.services.run_paper_batch", side_effect=idempotent_low_level):
        with pytest.raises(JobClaimLostError, match="lease transferred"):
            service.run_paper_batch(
                command,
                organization_id=organization_id,
                job_id="recoverable-job",
                ownership_guard=losing_guard,
            )
        interrupted = store.list_paper_runs(organization_id=organization_id)[0]
        assert interrupted["status"] == "running"

        completed_payload = service.run_paper_batch(
            command,
            organization_id=organization_id,
            job_id="recoverable-job",
            ownership_guard=lambda: None,
        )

    assert len(calls) == 2
    assert ledger_apply_count == 1
    assert completed_payload["paper_run_id"] == interrupted["id"]
    completed = store.get_paper_run(organization_id=organization_id, run_id=interrupted["id"])
    assert completed is not None
    assert completed["status"] == "completed"


def test_retryable_post_ledger_failure_reopens_failed_run_with_same_identity() -> None:
    settings = _settings("test_paper_service_failed_recovery")
    service = PaperService(settings)
    store = SQLiteMetadataStore(settings.metadata_db_path)
    organization_id = _organization(store)
    calls: list[dict[str, object]] = []
    applied_keys: set[str] = set()
    ledger_apply_count = 0
    low_level = _fake_low_level(calls)

    def idempotent_low_level(**kwargs):
        nonlocal ledger_apply_count
        key = str(kwargs["execution_idempotency_key"])
        if key not in applied_keys:
            applied_keys.add(key)
            ledger_apply_count += 1
        return low_level(**kwargs)

    command = PaperRunCommand(deployment_config=_config(), asof_date="2026-07-31")
    with (
        patch("pairs_trading.backend.services.run_paper_batch", side_effect=idempotent_low_level),
        patch(
            "pairs_trading.backend.services._publish_directory_reference",
            side_effect=[RuntimeError("storage unavailable"), None],
        ),
    ):
        with pytest.raises(RuntimeError, match="storage unavailable"):
            service.run_paper_batch(
                command,
                organization_id=organization_id,
                job_id="retryable-job",
                ownership_guard=lambda: None,
            )
        failed = store.list_paper_runs(organization_id=organization_id)[0]
        assert failed["status"] == "failed"

        completed_payload = service.run_paper_batch(
            command,
            organization_id=organization_id,
            job_id="retryable-job",
            ownership_guard=lambda: None,
        )

    assert len(calls) == 2
    assert ledger_apply_count == 1
    assert completed_payload["paper_run_id"] == failed["id"]
    assert store.get_paper_run(organization_id=organization_id, run_id=failed["id"])["status"] == "completed"


def test_job_request_round_trips_deployment_and_project_scope() -> None:
    settings = _settings("test_paper_job_scope_round_trip")
    settings = BackendSettings(**{**settings.__dict__, "enable_in_process_jobs": False})
    runner = PaperRunJobRunner(settings)
    with patch("pairs_trading.backend.services.enqueue_quant_job"):
        job = runner.submit(
            PaperRunCommand(
                deployment_id="deployment-owned",
                project_id="project-owned",
                asof_date="2026-07-31",
            ),
            organization_id="organization-owned",
        )

    assert job["request"]["deployment_id"] == "deployment-owned"
    assert job["request"]["project_id"] == "project-owned"


def test_empty_dashboard_payload_is_well_formed_before_any_run() -> None:
    settings = _settings("test_paper_service_empty_dashboard")
    service = PaperService(settings)
    store = SQLiteMetadataStore(settings.metadata_db_path)
    organization_id = _organization(store)

    payload = service.build_dashboard_payload(organization_id=organization_id)

    assert payload["strategies"] == []
    assert payload["leaderboard"] == []
    assert payload["totals"]["equity"] == 0
    assert payload["totals"]["daily_pnl"] == 0
    assert payload["asof_date"] is None
    assert payload["run_timestamp_utc"] is None
