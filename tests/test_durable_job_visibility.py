from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable
from unittest.mock import patch

import pytest

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.market_research_services import MarketResearchJobRunner
from pairs_trading.backend.schemas import BacktestRunRequest, MarketResearchRunRequest, SentimentAccumulationRequest
from pairs_trading.backend.services import BacktestJobRunner, BacktestService, PaperRunCommand, PaperRunJobRunner, PaperService, SentimentJobRunner
from pairs_trading.backend.worker_tasks import run_queued_job
from pairs_trading.platform import build_metadata_store


RunnerFactory = Callable[..., Any]
Submitter = Callable[[Any], dict[str, Any]]


def _settings(root: Path, *, enable_in_process_jobs: bool = False) -> BackendSettings:
    return BackendSettings(
        enable_demo_accounts=False,
        enable_in_process_jobs=enable_in_process_jobs,
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


CASES: list[tuple[str, RunnerFactory, Submitter]] = [
    (
        "paper",
        lambda settings, **kwargs: PaperRunJobRunner(settings, **kwargs),
        lambda runner: runner.submit(
            PaperRunCommand(deployment_config={"strategies": [{"name": "durable-test"}]}),
            organization_id="org-a",
            user_id="user-a",
        ),
    ),
    (
        "backtest",
        lambda settings, **kwargs: BacktestJobRunner(settings, **kwargs),
        lambda runner: runner.submit(
            BacktestRunRequest(pipeline="buy_and_hold", symbols=["SPY"]),
            organization_id="org-a",
            user_id="user-a",
        ),
    ),
    (
        "sentiment",
        lambda settings, **kwargs: SentimentJobRunner(settings, **kwargs),
        lambda runner: runner.submit(
            SentimentAccumulationRequest(symbols=["SPY"], providers=["rss"]),
            organization_id="org-a",
            user_id="user-a",
        ),
    ),
    (
        "market_research",
        lambda settings, **kwargs: MarketResearchJobRunner(settings, **kwargs),
        lambda runner: runner.submit(
            MarketResearchRunRequest(ticker="SPY"),
            organization_id="org-a",
            user_id="user-a",
        ),
    ),
]


@pytest.mark.parametrize(("kind", "runner_factory", "submitter"), CASES)
def test_external_runners_share_durable_visibility_without_startup_mutation(
    kind: str,
    runner_factory: RunnerFactory,
    submitter: Submitter,
) -> None:
    with TemporaryDirectory(prefix=f"tradepilot-{kind}-visibility-") as temp_dir:
        settings = _settings(Path(temp_dir))
        with (
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
            patch("pairs_trading.backend.market_research_services.enqueue_quant_job", return_value={"queue": "test"}),
            patch.object(MarketResearchJobRunner, "_persist_report_record", return_value=None),
        ):
            api_runner = runner_factory(settings)
            submitted = submitter(api_runner)

            local_path = api_runner.jobs_dir / f"{submitted['id']}.json"
            assert not local_path.exists()
            local_path.write_text(
                json.dumps({**submitted, "status": "failed", "error": "stale local state"}),
                encoding="utf-8",
            )

            before_startup = api_runner.metadata_store.get_job(kind=kind, job_id=submitted["id"])
            assert before_startup is not None
            assert before_startup["status"] == "queued"

            worker_runner = runner_factory(settings)
            assert worker_runner.jobs == {}
            assert worker_runner.metadata_store.get_job(kind=kind, job_id=submitted["id"]) == before_startup

            visible = worker_runner.get_job(submitted["id"], organization_id="org-a")
            assert visible is not None
            assert visible["status"] == "queued"
            assert worker_runner.get_job(submitted["id"], organization_id="org-b") is None
            assert worker_runner.list_jobs(organization_id="org-b") == []

            worker_runner._set_status(
                submitted["id"],
                "completed",
                progress=1.0,
                stage="completed",
                message="Completed in another worker process.",
                result={"source": "durable-store"},
            )

            completed = api_runner.get_job(submitted["id"], organization_id="org-a")
            assert completed is not None
            assert completed["status"] == "completed"
            assert completed["result"] == {"source": "durable-store"}
            assert api_runner.jobs[submitted["id"]].status == "queued"
            assert api_runner.list_jobs(organization_id="org-a")[0]["status"] == "completed"


@pytest.mark.parametrize(("kind", "runner_factory", "submitter"), CASES)
def test_synchronous_fast_worker_completion_is_not_overwritten_after_enqueue(
    kind: str,
    runner_factory: RunnerFactory,
    submitter: Submitter,
) -> None:
    with TemporaryDirectory(prefix=f"tradepilot-{kind}-fast-worker-") as temp_dir:
        settings = _settings(Path(temp_dir))

        def complete_before_enqueue_returns(_settings: BackendSettings, *, kind: str, job_id: str) -> dict[str, str]:
            worker = runner_factory(settings)
            worker._set_status(
                job_id,
                "completed",
                progress=1.0,
                stage="completed",
                message="Synchronous test worker completed.",
                result={"source": "fast-worker"},
            )
            return {"queue": "test", "rq_job_id": f"rq-{job_id}"}

        with (
            patch("pairs_trading.backend.services.enqueue_quant_job", side_effect=complete_before_enqueue_returns),
            patch("pairs_trading.backend.market_research_services.enqueue_quant_job", side_effect=complete_before_enqueue_returns),
            patch.object(MarketResearchJobRunner, "_persist_report_record", return_value=None),
        ):
            submitted = submitter(runner_factory(settings))

        assert submitted["status"] == "completed"
        assert submitted["result"] == {"source": "fast-worker"}


@pytest.mark.parametrize(("kind", "runner_factory", "submitter"), CASES)
def test_enqueue_failure_leaves_durable_job_queued_for_reconciliation(
    kind: str,
    runner_factory: RunnerFactory,
    submitter: Submitter,
) -> None:
    provider_error = "redis://queue-user:queue-password@queue.invalid failed"
    with TemporaryDirectory(prefix=f"tradepilot-{kind}-enqueue-failure-") as temp_dir:
        settings = _settings(Path(temp_dir))
        runner = runner_factory(settings)
        with (
            patch("pairs_trading.backend.services.enqueue_quant_job", side_effect=RuntimeError(provider_error)),
            patch("pairs_trading.backend.market_research_services.enqueue_quant_job", side_effect=RuntimeError(provider_error)),
            patch.object(MarketResearchJobRunner, "_persist_report_record", return_value=None),
        ):
            submitted = submitter(runner)

        jobs = runner.metadata_store.list_jobs(kind=kind, organization_id="org-a", limit=10)
        assert len(jobs) == 1
        queued = jobs[0]
        assert submitted["id"] == queued["id"]
        assert submitted["status"] == "queued"
        assert queued["status"] == "queued"
        assert queued["stage"] == "dispatch_pending"
        assert queued["dispatch_state"] == "pending"
        assert queued["dispatch_error_class"] in {"redis_unavailable", "dispatch_error"}
        assert queued["dispatch_attempted_at_utc"]
        assert queued["progress"] == 0.02
        assert queued["finished_at_utc"] is None
        assert queued["error"] is None
        assert provider_error not in json.dumps(queued)


@pytest.mark.parametrize(("kind", "runner_factory", "submitter"), CASES)
def test_capped_development_load_never_imports_json_over_a_durable_record(
    kind: str,
    runner_factory: RunnerFactory,
    submitter: Submitter,
) -> None:
    with TemporaryDirectory(prefix=f"tradepilot-{kind}-capped-load-") as temp_dir:
        settings = _settings(Path(temp_dir))
        with (
            patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}),
            patch("pairs_trading.backend.market_research_services.enqueue_quant_job", return_value={"queue": "test"}),
            patch.object(MarketResearchJobRunner, "_persist_report_record", return_value=None),
        ):
            external_runner = runner_factory(settings)
            older = submitter(external_runner)
            newer = submitter(external_runner)

        stale_json = external_runner.jobs_dir / f"{older['id']}.json"
        stale_json.write_text(
            json.dumps({**older, "status": "running", "message": "stale local record"}),
            encoding="utf-8",
        )
        durable_before = external_runner.metadata_store.get_job(kind=kind, job_id=older["id"])
        assert durable_before is not None

        development_settings = replace(settings, enable_in_process_jobs=True)
        with (
            patch("pairs_trading.backend.services.ThreadPoolExecutor", return_value=_PendingExecutor()),
            patch("pairs_trading.backend.market_research_services.ThreadPoolExecutor", return_value=_PendingExecutor()),
        ):
            development_runner = runner_factory(development_settings, max_history=1)

        assert set(development_runner.jobs) == {newer["id"]}
        assert development_runner.metadata_store.get_job(kind=kind, job_id=older["id"]) == durable_before


@pytest.mark.parametrize(("_kind", "runner_factory", "_submitter"), CASES)
@pytest.mark.parametrize("invalid", [False, 0, 201])
def test_runner_max_history_is_bounded(
    _kind: str,
    runner_factory: RunnerFactory,
    _submitter: Submitter,
    invalid: Any,
) -> None:
    with TemporaryDirectory(prefix="tradepilot-invalid-history-") as temp_dir:
        with pytest.raises(ValueError, match="max_history"):
            runner_factory(_settings(Path(temp_dir)), max_history=invalid)


def test_trim_only_removes_local_history_not_metadata_history() -> None:
    with TemporaryDirectory(prefix="tradepilot-job-trim-") as temp_dir:
        settings = _settings(Path(temp_dir))
        with patch("pairs_trading.backend.services.enqueue_quant_job", return_value={"queue": "test"}):
            runner = PaperRunJobRunner(settings, max_history=1)
            first = runner.submit(
                PaperRunCommand(deployment_config={"strategies": [{"name": "first"}]}),
                organization_id="org-a",
            )
            second = runner.submit(
                PaperRunCommand(deployment_config={"strategies": [{"name": "second"}]}),
                organization_id="org-a",
            )

        persisted = runner.metadata_store.list_jobs(kind="paper", organization_id="org-a", limit=10)
        assert {item["id"] for item in persisted} == {first["id"], second["id"]}
        assert set(runner.jobs) == {second["id"]}


class _PendingExecutor:
    def submit(self, *_args: Any, **_kwargs: Any) -> Future[None]:
        return Future()


def test_in_process_sentiment_jobs_never_persist_or_return_request_secrets() -> None:
    secret = "sentinel-provider-secret-123"
    server_secret = "sentinel-server-secret-456"
    with TemporaryDirectory(prefix="tradepilot-job-secret-") as temp_dir:
        settings = _settings(Path(temp_dir), enable_in_process_jobs=True)
        with (
            patch("pairs_trading.backend.services.ThreadPoolExecutor", return_value=_PendingExecutor()),
            patch.dict("os.environ", {"NEWSAPI_API_KEY": server_secret}),
        ):
            runner = SentimentJobRunner(settings)
            submitted = runner.submit(
                SentimentAccumulationRequest(
                    symbols=["SPY"],
                    providers=["newsapi"],
                    newsapi_api_key=secret,
                ),
                organization_id="org-a",
            )

        runner._set_status(
            submitted["id"],
            "failed",
            error=f"provider rejected {secret} via {server_secret}",
            message=f"request using {secret} and {server_secret} failed",
            result={"api_key": secret, "echo": f"{secret}:{server_secret}"},
            warnings=[f"provider warning: {secret}:{server_secret}"],
        )

        returned = runner.get_job(submitted["id"], organization_id="org-a")
        persisted = runner.metadata_store.get_job(kind="sentiment", job_id=submitted["id"])
        local_json = (runner.jobs_dir / f"{submitted['id']}.json").read_text(encoding="utf-8")
        for payload in (submitted, returned, persisted):
            encoded = json.dumps(payload)
            assert secret not in encoded
            assert server_secret not in encoded
            assert "newsapi_api_key" not in encoded
        assert secret not in local_json
        assert server_secret not in local_json
        assert "newsapi_api_key" not in local_json
        assert returned is not None
        assert returned["error"] == "provider rejected [REDACTED] via [REDACTED]"
        assert returned["result"] == {"echo": "[REDACTED]:[REDACTED]"}
        assert returned["warnings"] == ["provider warning: [REDACTED]:[REDACTED]"]


def test_external_sentiment_job_rejects_request_credentials_before_persistence() -> None:
    with TemporaryDirectory(prefix="tradepilot-external-secret-") as temp_dir:
        runner = SentimentJobRunner(_settings(Path(temp_dir)))
        with pytest.raises(ValueError, match="External worker jobs cannot accept request-supplied credentials"):
            runner.submit(
                SentimentAccumulationRequest(
                    symbols=["SPY"],
                    providers=["newsapi"],
                    newsapi_api_key="must-not-persist",
                ),
                organization_id="org-a",
            )
        assert runner.metadata_store.list_jobs(kind="sentiment", organization_id="org-a") == []


@pytest.mark.parametrize("in_process", [False, True])
def test_paper_inline_config_rejects_secrets_before_any_job_or_config_write(in_process: bool) -> None:
    secret = "paper-inline-secret-123"
    with TemporaryDirectory(prefix="tradepilot-paper-secret-") as temp_dir:
        root = Path(temp_dir)
        settings = _settings(root, enable_in_process_jobs=in_process)
        config = {"strategies": [{"name": "secret-test"}], "provider": {"api_key": secret}}
        with patch("pairs_trading.backend.services.ThreadPoolExecutor", return_value=_PendingExecutor()):
            runner = PaperRunJobRunner(settings)
            with pytest.raises(ValueError, match="Inline paper deployment config cannot contain raw credentials"):
                runner.submit(PaperRunCommand(deployment_config=config), organization_id="org-a")
            with pytest.raises(ValueError, match="Inline paper deployment config cannot contain raw credentials"):
                PaperService(settings).run_paper_batch(PaperRunCommand(deployment_config=config), organization_id="org-a")

        assert runner.metadata_store.list_jobs(kind="paper", organization_id="org-a") == []
        assert not (settings.paper_artifact_root.parent / "inline_deployments").exists()
        for path in root.rglob("*"):
            if path.is_file():
                assert secret.encode() not in path.read_bytes()


def test_backtest_experiment_lineage_and_sentiment_use_sanitized_request() -> None:
    secret = "backtest-parameter-secret-123"
    with TemporaryDirectory(prefix="tradepilot-backtest-lineage-") as temp_dir:
        settings = _settings(Path(temp_dir), enable_in_process_jobs=True)
        request = BacktestRunRequest(
            pipeline="buy_and_hold",
            symbols=["SPY"],
            parameters={"api_key": secret, "lookback": 20},
        )
        with patch("pairs_trading.backend.services.ThreadPoolExecutor", return_value=_PendingExecutor()):
            runner = BacktestJobRunner(settings)
            submitted = runner.submit(request, organization_id="org-a", user_id="user-a")

        fake_result = {
            "summary": {"experiment_id": "exp-sanitized", "strategy": "buy_and_hold"},
            "validation": {},
            "artifact_dir": None,
        }
        with (
            patch.object(BacktestService, "run_backtest", return_value=fake_result),
            patch.object(runner.metadata_store, "save_experiment_run"),
            patch.object(runner.metadata_store, "upsert_experiment") as upsert_experiment,
        ):
            runner._run_job(submitted["id"], request, "org-a", "user-a")

        experiment = upsert_experiment.call_args.kwargs["payload"]
        encoded = json.dumps({"lineage": experiment["lineage"], "sentiment": experiment["sentiment"]})
        assert secret not in encoded
        assert "api_key" not in encoded
        assert experiment["lineage"]["parameters"] == {"lookback": 20}


def test_worker_passes_persisted_user_id_to_custom_backtest_execution() -> None:
    with TemporaryDirectory(prefix="tradepilot-worker-user-") as temp_dir:
        settings = _settings(Path(temp_dir))
        store = build_metadata_store(settings)
        request = BacktestRunRequest(pipeline="user_strategy:custom-1", symbols=["SPY"])
        store.upsert_job(
            kind="backtest",
            payload={
                "id": "job-custom-user",
                "status": "queued",
                "request": request.model_dump(mode="json"),
                "organization_id": "org-a",
                "user_id": "user-owner",
                "created_at_utc": "2026-08-01T00:00:00Z",
                "updated_at_utc": "2026-08-01T00:00:00Z",
            },
        )
        captured: dict[str, Any] = {}

        class _CapturingBacktestRunner:
            def __init__(self, _settings: BackendSettings, **_kwargs: Any) -> None:
                pass

            def _run_job(
                self,
                job_id: str,
                worker_request: BacktestRunRequest,
                organization_id: str,
                user_id: str | None,
            ) -> None:
                captured.update(
                    job_id=job_id,
                    pipeline=worker_request.pipeline,
                    organization_id=organization_id,
                    user_id=user_id,
                )

            def get_job(self, job_id: str, *, organization_id: str) -> dict[str, Any]:
                return {"id": job_id, "organization_id": organization_id}

        with (
            patch("pairs_trading.backend.worker_tasks.BackendSettings.from_env", return_value=settings),
            patch("pairs_trading.backend.worker_tasks.BacktestJobRunner", _CapturingBacktestRunner),
        ):
            run_queued_job("backtest", "job-custom-user")

        assert captured == {
            "job_id": "job-custom-user",
            "pipeline": "user_strategy:custom-1",
            "organization_id": "org-a",
            "user_id": "user-owner",
        }
