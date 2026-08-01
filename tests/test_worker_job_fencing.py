from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.market_research_services import MarketResearchJobRunner
from pairs_trading.research.decision_history import CommitteeDecision
from pairs_trading.backend.services import (
    BacktestJobRunner,
    JobClaimCheckError,
    JobClaimLostError,
    PaperRunJobRunner,
    SentimentJobRunner,
)
from pairs_trading.backend.worker_tasks import _JobHeartbeat, run_queued_job
from pairs_trading.platform import build_metadata_store


def _future_lease() -> str:
    return (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")


def _settings(root: Path) -> BackendSettings:
    settings = BackendSettings(
        enable_demo_accounts=False,
        enable_in_process_jobs=False,
        redis_url="redis://queue.invalid:6379/0",
        metadata_db_path=root / "metadata.sqlite3",
        paper_state_dir=root / "paper_state",
        paper_artifact_root=root / "paper_artifacts",
        paper_job_state_dir=root / "paper_jobs",
        default_paper_config=root / "missing-paper-config.json",
        backtest_artifact_root=root / "backtest_artifacts",
        backtest_job_state_dir=root / "backtest_jobs",
        sentiment_cache_dir=root / "sentiment_cache",
        sentiment_job_state_dir=root / "sentiment_jobs",
        market_research_artifact_root=root / "research_artifacts",
        market_research_job_state_dir=root / "research_jobs",
        market_research_data_provider="demo",
        market_research_llm_provider="mock",
        market_research_llm_model="mock-research-v1",
    )
    object.__setattr__(settings, "job_lease_seconds", 3)
    object.__setattr__(settings, "job_heartbeat_seconds", 0.05)
    object.__setattr__(settings, "job_max_attempts", 3)
    return settings


REQUESTS: dict[str, dict[str, Any]] = {
    "backtest": {"pipeline": "buy_and_hold", "symbols": ["SPY"]},
    "paper": {"deployment_config": {"strategies": [{"name": "fenced-paper"}]}},
    "sentiment": {"symbols": ["SPY"], "providers": ["rss"]},
    "market_research": {"ticker": "SPY"},
}


def _insert_queued_job(
    store: Any,
    *,
    kind: str,
    job_id: str,
    request: dict[str, Any] | None = None,
    max_attempts: int = 3,
    organization_id: str = "org-a",
    user_id: str = "user-owner",
    report_id: str | None = None,
) -> None:
    payload = {
        "id": job_id,
        "status": "queued",
        "request": REQUESTS[kind] if request is None else request,
        "organization_id": organization_id,
        "user_id": user_id,
        "created_at_utc": "2026-08-01T00:00:00Z",
        "updated_at_utc": "2026-08-01T00:00:00Z",
        "progress": 0.02,
        "stage": "queued",
        "message": "Waiting for a worker.",
        "max_attempts": max_attempts,
    }
    if report_id is not None:
        payload["report_id"] = report_id
    store.upsert_job(
        kind=kind,
        payload=payload,
    )


def _runner_patch(kind: str) -> str:
    return {
        "backtest": "pairs_trading.backend.worker_tasks.BacktestJobRunner",
        "paper": "pairs_trading.backend.worker_tasks.PaperRunJobRunner",
        "sentiment": "pairs_trading.backend.worker_tasks.SentimentJobRunner",
        "market_research": "pairs_trading.backend.worker_tasks.MarketResearchJobRunner",
    }[kind]


@pytest.mark.parametrize("kind", list(REQUESTS))
def test_duplicate_delivery_executes_each_job_only_once(kind: str) -> None:
    with TemporaryDirectory(prefix=f"tradepilot-{kind}-duplicate-") as temp_dir:
        settings = _settings(Path(temp_dir))
        store = build_metadata_store(settings)
        job_id = f"duplicate-{kind}"
        _insert_queued_job(store, kind=kind, job_id=job_id)
        started = Event()
        unblock = Event()
        calls = 0
        calls_lock = Lock()

        class BlockingRunner:
            def __init__(self, _settings: BackendSettings, *, claimed_worker_id: str, **_kwargs: Any) -> None:
                self.worker_id = claimed_worker_id

            def _run_job(self, *_args: Any, **_kwargs: Any) -> None:
                nonlocal calls
                with calls_lock:
                    calls += 1
                started.set()
                assert unblock.wait(timeout=5)
                released = store.release_job_claim(
                    kind=kind,
                    job_id=job_id,
                    worker_id=self.worker_id,
                    status="completed",
                    updates={"progress": 1.0, "stage": "completed", "result": {"worker": self.worker_id}},
                )
                assert released is not None

        with (
            patch("pairs_trading.backend.worker_tasks.BackendSettings.from_env", return_value=settings),
            patch("pairs_trading.backend.worker_tasks.build_metadata_store", return_value=store),
            patch(_runner_patch(kind), BlockingRunner),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            winner = executor.submit(run_queued_job, kind, job_id)
            assert started.wait(timeout=5)
            loser_result = run_queued_job(kind, job_id)
            unblock.set()
            winner_result = winner.result(timeout=5)

        assert calls == 1
        assert loser_result["status"] == "running"
        assert winner_result["status"] == "completed"
        durable = store.get_job(kind=kind, job_id=job_id)
        assert durable is not None
        assert durable["status"] == "completed"
        assert durable["attempt"] == 1


def test_worker_heartbeats_and_releases_its_claim() -> None:
    with TemporaryDirectory(prefix="tradepilot-heartbeat-") as temp_dir:
        settings = _settings(Path(temp_dir))
        store = build_metadata_store(settings)
        job_id = "heartbeat-backtest"
        _insert_queued_job(store, kind="backtest", job_id=job_id)
        heartbeat_seen = Event()
        original_heartbeat = store.heartbeat_job

        def recording_heartbeat(**kwargs: Any) -> dict[str, Any] | None:
            result = original_heartbeat(**kwargs)
            if result is not None:
                heartbeat_seen.set()
            return result

        class HeartbeatRunner:
            def __init__(self, _settings: BackendSettings, *, claimed_worker_id: str, **_kwargs: Any) -> None:
                self.worker_id = claimed_worker_id

            def _run_job(self, *_args: Any, **_kwargs: Any) -> None:
                assert heartbeat_seen.wait(timeout=5)
                released = store.release_job_claim(
                    kind="backtest",
                    job_id=job_id,
                    worker_id=self.worker_id,
                    status="completed",
                    updates={"progress": 1.0, "stage": "completed", "result": {"ok": True}},
                )
                assert released is not None

        with (
            patch("pairs_trading.backend.worker_tasks.BackendSettings.from_env", return_value=settings),
            patch("pairs_trading.backend.worker_tasks.build_metadata_store", return_value=store),
            patch.object(store, "heartbeat_job", side_effect=recording_heartbeat),
            patch("pairs_trading.backend.worker_tasks.BacktestJobRunner", HeartbeatRunner),
        ):
            result = run_queued_job("backtest", job_id)

        assert result["status"] == "completed"
        assert result["worker_id"] is None
        assert result["lease_expires_at_utc"] is None
        assert result["heartbeat_at_utc"] is not None
        assert result["version"] >= 3  # claim, heartbeat, release


def test_heartbeat_uncertainty_blocks_publication_until_a_refresh_recovers() -> None:
    settings = BackendSettings(job_heartbeat_seconds=0.05)
    failed = Event()
    recovered = Event()

    class Store:
        calls = 0

        def heartbeat_job(self, **_kwargs: Any) -> dict[str, Any]:
            self.calls += 1
            if self.calls == 1:
                failed.set()
                raise OSError("temporary database outage")
            recovered.set()
            return {"status": "running"}

    heartbeat = _JobHeartbeat(
        settings, store=Store(), kind="sentiment", job_id="uncertain-job", worker_id="worker-a"
    )
    heartbeat.start()
    try:
        assert failed.wait(timeout=2)
        with pytest.raises(JobClaimCheckError):
            heartbeat.assert_publishable()
        assert recovered.wait(timeout=2)
        deadline = time.monotonic() + 2
        while heartbeat.uncertain_event.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        heartbeat.assert_publishable()
    finally:
        heartbeat.stop()


@pytest.mark.parametrize("kind", ["backtest", "sentiment", "market_research"])
def test_claim_locked_domain_publication_rejects_old_owner_and_is_retry_idempotent(kind: str) -> None:
    """Model claim loss after compute/blob upload but before authoritative publish."""

    with TemporaryDirectory(prefix=f"tradepilot-{kind}-domain-publish-") as temp_dir:
        settings = _settings(Path(temp_dir))
        store = build_metadata_store(settings)
        workspace = store.create_user_workspace(
            email=f"publish-{kind}@example.test",
            display_name="Publisher",
            password_hash="unused",
            organization_name=f"Publish {kind}",
        )
        organization_id = workspace["organization_id"]
        user_id = workspace["user_id"]
        job_id = f"publish-{kind}"
        report_id = f"report-{job_id}"
        _insert_queued_job(
            store, kind=kind, job_id=job_id, organization_id=organization_id,
            user_id=user_id, report_id=report_id if kind == "market_research" else None,
        )
        assert store.claim_job(
            kind=kind, job_id=job_id, worker_id="worker-old",
            lease_expires_at_utc="2026-07-31T23:59:00Z",
        )
        store.recover_expired_jobs(now_utc="2026-08-01T00:00:00Z")
        current = store.claim_job(
            kind=kind, job_id=job_id, worker_id="worker-new", lease_expires_at_utc=_future_lease()
        )
        assert current is not None
        artifact_id = store.stable_id("art", f"{organization_id}:{kind}:{job_id}")

        def publish(tx: Any) -> None:
            artifact = tx.upsert_artifact(
                organization_id=organization_id,
                payload={
                    "id": artifact_id, "artifact_type": kind, "source_id": job_id,
                    "provider": "local", "key": f"attempts/{kind}/{job_id}",
                    "uri": f"local://attempts/{kind}/{job_id}",
                },
            )
            if kind == "backtest":
                experiment_id = store.stable_id("exp", f"{organization_id}:{job_id}")
                tx.save_experiment_run(
                    experiment_id=experiment_id, kind="backtest", summary={"winner": True},
                    organization_id=organization_id, artifact_dir=artifact["uri"],
                )
                tx.upsert_experiment(
                    organization_id=organization_id,
                    payload={"id": experiment_id, "job_id": job_id, "name": "winner", "pipeline": "test"},
                )
            elif kind == "sentiment":
                tx.upsert_dataset(
                    organization_id=organization_id,
                    payload={
                        "id": store.stable_id("dst", f"{organization_id}:{job_id}:sentiment_daily"),
                        "name": "winner", "kind": "sentiment_daily", "path": artifact["uri"],
                    },
                )
            else:
                tx.upsert_committee_decision(
                    payload={
                        "id": store.stable_id("decision", f"{organization_id}:{job_id}:SPY"),
                        "organization_id": organization_id, "user_id": user_id, "job_id": job_id,
                        "ticker": "SPY", "decision": "HOLD", "timestamp": _future_lease(),
                    }
                )
                tx.upsert_market_research_report(
                    organization_id=organization_id, user_id=user_id,
                    payload={
                        "id": report_id, "job_id": job_id, "ticker": "SPY", "status": "completed",
                        "artifact_id": artifact["id"], "report": {"ticker": "SPY", "decision": "HOLD"},
                    },
                )

        stale, _ = store.publish_claimed_job(
            kind=kind, job_id=job_id, worker_id="worker-old", publisher=publish
        )
        assert stale is False
        won, _ = store.publish_claimed_job(
            kind=kind, job_id=job_id, worker_id="worker-new", publisher=publish
        )
        repeated, _ = store.publish_claimed_job(
            kind=kind, job_id=job_id, worker_id="worker-new", publisher=publish
        )
        assert won is repeated is True
        assert len(store.list_artifacts(organization_id=organization_id, source_id=job_id)) == 1
        if kind == "backtest":
            assert len(store.list_experiments(organization_id=organization_id)) == 1
            assert len(store.list_experiment_runs(kind="backtest", organization_id=organization_id)) == 1
        elif kind == "sentiment":
            assert len(store.list_datasets(organization_id=organization_id)) == 1
        else:
            assert len(store.list_jobs(kind="committee_decision", organization_id=organization_id)) == 1
            report = store.get_market_research_report(
                organization_id=organization_id, report_id=report_id, user_id=user_id
            )
            assert report is not None and report["status"] == "completed"


@pytest.mark.parametrize("kind", ["backtest", "sentiment", "market_research"])
def test_runner_claim_loss_after_compute_leaves_no_authoritative_domain_rows(kind: str) -> None:
    with TemporaryDirectory(prefix=f"tradepilot-{kind}-post-compute-loss-") as temp_dir:
        root = Path(temp_dir)
        settings = _settings(root)
        store = build_metadata_store(settings)
        workspace = store.create_user_workspace(
            email=f"post-compute-{kind}@example.test", display_name="Owner", password_hash="unused",
            organization_name=f"Post Compute {kind}",
        )
        organization_id = workspace["organization_id"]
        user_id = workspace["user_id"]
        job_id = f"post-compute-{kind}"
        report_id = f"report-{job_id}"
        _insert_queued_job(
            store, kind=kind, job_id=job_id, organization_id=organization_id, user_id=user_id,
            report_id=report_id if kind == "market_research" else None,
        )
        claimed = store.claim_job(
            kind=kind, job_id=job_id, worker_id="worker-old", lease_expires_at_utc=_future_lease()
        )
        assert claimed is not None

        def handoff() -> None:
            released = store.release_job_claim(
                kind=kind, job_id=job_id, worker_id="worker-old", status="queued",
                updates={"message": "lease recovered"},
            )
            assert released is not None
            assert store.claim_job(
                kind=kind, job_id=job_id, worker_id="worker-new", lease_expires_at_utc=_future_lease()
            ) is not None

        if kind == "backtest":
            compute_dir = root / "computed-backtest"
            compute_dir.mkdir()
            runner = BacktestJobRunner(
                settings, mark_interrupted_on_load=False, claimed_worker_id="worker-old",
                claimed_attempt=int(claimed["attempt"]),
            )

            def steal_backtest(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
                handoff()
                return {"provider": "local", "key": "attempt-old", "uri": "local://attempt-old", "file_count": 1, "byte_count": 1}

            with (
                patch("pairs_trading.backend.services.BacktestService.run_backtest", return_value={
                    "summary": {"strategy": "test", "experiment_id": "compute-old"},
                    "validation": {}, "artifact_dir": str(compute_dir),
                }),
                patch("pairs_trading.backend.services._publish_directory_reference", side_effect=steal_backtest),
                pytest.raises(JobClaimLostError),
            ):
                runner._run_job(job_id, __import__("pairs_trading.backend.schemas", fromlist=["BacktestRunRequest"]).BacktestRunRequest.model_validate(REQUESTS[kind]), organization_id, user_id)
        elif kind == "sentiment":
            compute_dir = root / "computed-sentiment"
            compute_dir.mkdir()
            runner = SentimentJobRunner(
                settings, mark_interrupted_on_load=False, claimed_worker_id="worker-old",
                claimed_attempt=int(claimed["attempt"]),
            )

            def steal_sentiment(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
                handoff()
                return {"provider": "local", "key": "attempt-old", "uri": "local://attempt-old", "file_count": 1, "byte_count": 1}

            with (
                patch("pairs_trading.backend.services.SentimentService.accumulate", return_value={
                    "compute_output_dir": str(compute_dir), "compute_daily_rows": 1,
                    "metadata": {"fetched_headlines": 1}, "summary": {"daily_rows": 1}, "warnings": [],
                }),
                patch("pairs_trading.backend.services._publish_directory_reference", side_effect=steal_sentiment),
                pytest.raises(JobClaimLostError),
            ):
                runner._run_job(job_id, __import__("pairs_trading.backend.schemas", fromlist=["SentimentAccumulationRequest"]).SentimentAccumulationRequest.model_validate(REQUESTS[kind]), organization_id)
        else:
            runner = MarketResearchJobRunner(
                settings, mark_interrupted_on_load=False, claimed_worker_id="worker-old",
                claimed_attempt=int(claimed["attempt"]),
            )
            report_payload = {
                "ticker": "SPY", "analysis_date": "2026-08-01", "time_horizon": "swing",
                "decision": "HOLD", "confidence": 50, "summary": "computed", "warnings": [],
                "metadata": {}, "provenance": [], "source_references": [],
            }
            fake_report = SimpleNamespace(model_dump=lambda **_kwargs: dict(report_payload))

            def steal_market(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
                handoff()
                return SimpleNamespace(provider="local", key="attempt-old", uri="local://attempt-old", file_count=1, byte_count=1)

            with (
                patch("pairs_trading.backend.market_research_services.MarketResearchService.preflight_runtime"),
                patch("pairs_trading.backend.market_research_services.MarketResearchService.generate_report", return_value=fake_report),
                patch("pairs_trading.backend.market_research_services.MarketResearchService.record_decision", return_value=CommitteeDecision(ticker="SPY", organization_id=organization_id, user_id=user_id, job_id=job_id)),
                patch.object(type(runner.artifact_storage), "publish_file", side_effect=steal_market),
                pytest.raises(JobClaimLostError),
            ):
                runner._run_job(job_id, __import__("pairs_trading.backend.schemas", fromlist=["MarketResearchRunRequest"]).MarketResearchRunRequest.model_validate(REQUESTS[kind]), organization_id)

        assert store.get_job(kind=kind, job_id=job_id)["worker_id"] == "worker-new"
        assert store.list_artifacts(organization_id=organization_id, source_id=job_id) == []
        if kind == "backtest":
            assert store.list_experiments(organization_id=organization_id) == []
        elif kind == "sentiment":
            assert store.list_datasets(organization_id=organization_id) == []
        else:
            assert store.list_jobs(kind="committee_decision", organization_id=organization_id) == []
            saved = store.get_market_research_report(
                organization_id=organization_id, report_id=report_id, user_id=user_id
            )
            assert saved is not None and saved["status"] == "running"


RUNNERS = [
    ("paper", PaperRunJobRunner),
    ("backtest", BacktestJobRunner),
    ("sentiment", SentimentJobRunner),
    ("market_research", MarketResearchJobRunner),
]


@pytest.mark.parametrize(("kind", "runner_type"), RUNNERS)
def test_claimed_runner_hydrates_metadata_and_never_uses_ordinary_upsert(kind: str, runner_type: type[Any]) -> None:
    with TemporaryDirectory(prefix=f"tradepilot-{kind}-hydrate-") as temp_dir:
        settings = _settings(Path(temp_dir))
        store = build_metadata_store(settings)
        job_id = f"hydrate-{kind}"
        _insert_queued_job(store, kind=kind, job_id=job_id)
        claimed = store.claim_job(
            kind=kind,
            job_id=job_id,
            worker_id="worker-a",
            lease_expires_at_utc=_future_lease(),
        )
        assert claimed is not None
        runner = runner_type(settings, mark_interrupted_on_load=False, claimed_worker_id="worker-a")
        with patch.object(runner.metadata_store, "upsert_job", side_effect=AssertionError("ordinary upsert used")):
            runner._set_status(job_id, "running", progress=0.5, stage="working", message="Working.")
            hydrated = runner.jobs[job_id]
            assert hydrated.worker_id == "worker-a"
            assert hydrated.attempt == 1
            assert hydrated.max_attempts == 3
            assert hydrated.version >= 2
            assert hydrated.heartbeat_at_utc
            assert hydrated.lease_expires_at_utc
            runner._set_status(
                job_id,
                "completed",
                progress=1.0,
                stage="completed",
                message="Completed.",
                result={"kind": kind},
            )

        durable = store.get_job(kind=kind, job_id=job_id)
        assert durable is not None
        assert durable["status"] == "completed"
        assert durable["worker_id"] is None
        assert durable["result"] == {"kind": kind}


@pytest.mark.parametrize(("kind", "runner_type"), RUNNERS)
def test_lost_owner_cannot_publish_stale_completion(kind: str, runner_type: type[Any]) -> None:
    with TemporaryDirectory(prefix=f"tradepilot-{kind}-stale-owner-") as temp_dir:
        settings = _settings(Path(temp_dir))
        store = build_metadata_store(settings)
        job_id = f"stale-{kind}"
        _insert_queued_job(store, kind=kind, job_id=job_id)
        assert store.claim_job(
            kind=kind,
            job_id=job_id,
            worker_id="worker-old",
            lease_expires_at_utc="2026-07-31T23:59:00Z",
        )
        old_runner = runner_type(settings, mark_interrupted_on_load=False, claimed_worker_id="worker-old")
        recovered = store.recover_expired_jobs(now_utc="2026-08-01T00:00:00Z")
        assert [item["id"] for item in recovered] == [job_id]
        assert store.claim_job(
            kind=kind,
            job_id=job_id,
            worker_id="worker-new",
            lease_expires_at_utc=_future_lease(),
        )

        with pytest.raises(JobClaimLostError):
            old_runner._set_status(
                job_id,
                "completed",
                progress=1.0,
                stage="completed",
                result={"stale": True},
            )

        durable = store.get_job(kind=kind, job_id=job_id)
        assert durable is not None
        assert durable["status"] == "running"
        assert durable["worker_id"] == "worker-new"
        assert durable.get("result") != {"stale": True}


@pytest.mark.parametrize("max_attempts, expected_status", [(3, "queued"), (1, "failed")])
def test_unexpected_worker_error_releases_safely(max_attempts: int, expected_status: str) -> None:
    with TemporaryDirectory(prefix="tradepilot-worker-error-") as temp_dir:
        settings = _settings(Path(temp_dir))
        store = build_metadata_store(settings)
        job_id = f"invalid-backtest-{max_attempts}"
        _insert_queued_job(
            store,
            kind="backtest",
            job_id=job_id,
            max_attempts=max_attempts,
        )

        class ExplodingRunner:
            def __init__(self, _settings: BackendSettings, **_kwargs: Any) -> None:
                pass

            def _run_job(self, *_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError("provider rejected a sensitive worker credential")

        with (
            patch("pairs_trading.backend.worker_tasks.BackendSettings.from_env", return_value=settings),
            patch("pairs_trading.backend.worker_tasks.build_metadata_store", return_value=store),
            patch("pairs_trading.backend.worker_tasks.BacktestJobRunner", ExplodingRunner),
            pytest.raises(RuntimeError, match="Queued backtest worker execution failed"),
        ):
            run_queued_job("backtest", job_id)

        durable = store.get_job(kind="backtest", job_id=job_id)
        assert durable is not None
        assert durable["status"] == expected_status
        assert durable["worker_id"] is None
        assert durable["lease_expires_at_utc"] is None
        assert "symbols" not in str(durable.get("error") or "")
        if expected_status == "failed":
            assert durable["error"] == "The worker failed before completing the job."
        else:
            assert durable["error"] is None


DOMAIN_FAILURES = [
    ("paper", PaperRunJobRunner, "pairs_trading.backend.services.PaperService.run_paper_batch"),
    ("backtest", BacktestJobRunner, "pairs_trading.backend.services.BacktestService.run_backtest"),
    ("sentiment", SentimentJobRunner, "pairs_trading.backend.services.SentimentService.accumulate"),
    (
        "market_research",
        MarketResearchJobRunner,
        "pairs_trading.backend.market_research_services.MarketResearchService.preflight_runtime",
    ),
]


@pytest.mark.parametrize(("kind", "runner_type", "failure_target"), DOMAIN_FAILURES)
def test_claimed_domain_failures_retry_then_fail_safely(
    kind: str,
    runner_type: type[Any],
    failure_target: str,
) -> None:
    sentinel = f"SENTINEL-{kind}-PROVIDER-SECRET"
    with TemporaryDirectory(prefix=f"tradepilot-{kind}-domain-retry-") as temp_dir:
        root = Path(temp_dir)
        settings = _settings(root)
        object.__setattr__(settings, "job_lease_seconds", 30)
        object.__setattr__(settings, "job_heartbeat_seconds", 5)
        store = build_metadata_store(settings)
        workspace = store.create_user_workspace(
            email=f"{kind}@example.test",
            display_name="Worker Test",
            password_hash="unused-test-hash",
            organization_name=f"{kind} Worker Test",
        )
        organization_id = workspace["organization_id"]
        user_id = workspace["user_id"]
        job_id = f"domain-retry-{kind}"
        report_id = f"report-{job_id}" if kind == "market_research" else None
        _insert_queued_job(
            store,
            kind=kind,
            job_id=job_id,
            max_attempts=2,
            organization_id=organization_id,
            user_id=user_id,
            report_id=report_id,
        )

        with (
            patch("pairs_trading.backend.worker_tasks.BackendSettings.from_env", return_value=settings),
            patch("pairs_trading.backend.worker_tasks.build_metadata_store", return_value=store),
            patch(failure_target, side_effect=RuntimeError(sentinel)),
        ):
            with pytest.raises(RuntimeError, match=f"Queued {kind} worker execution failed") as first_error:
                run_queued_job(kind, job_id)
            first = store.get_job(kind=kind, job_id=job_id)
            assert first is not None
            assert first["status"] == "queued"
            assert first["attempt"] == 1
            assert first["worker_id"] is None
            assert first["lease_expires_at_utc"] is None
            assert first["error"] is None
            assert sentinel not in str(first_error.value)

            with pytest.raises(RuntimeError, match=f"Queued {kind} worker execution failed") as final_error:
                run_queued_job(kind, job_id)

        final = store.get_job(kind=kind, job_id=job_id)
        assert final is not None
        assert final["status"] == "failed"
        assert final["attempt"] == 2
        assert final["max_attempts"] == 2
        assert final["worker_id"] is None
        assert final["lease_expires_at_utc"] is None
        assert final["error"] == "The worker failed before completing the job."
        assert sentinel not in str(final_error.value)

        api_payload = runner_type(settings, mark_interrupted_on_load=False).get_job(
            job_id,
            organization_id=organization_id,
        )
        assert api_payload == final
        assert sentinel not in str(api_payload)

        if report_id is not None:
            report = store.get_market_research_report(
                organization_id=organization_id,
                report_id=report_id,
                user_id=user_id,
            )
            assert report is not None
            assert report["status"] == "failed"
            assert report["error"] == "Market research worker attempt failed."
            assert sentinel not in str(report)

        for path in root.rglob("*"):
            if path.is_file():
                assert sentinel.encode() not in path.read_bytes()
