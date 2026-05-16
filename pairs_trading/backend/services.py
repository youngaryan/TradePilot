from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from concurrent.futures import Future, ThreadPoolExecutor
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

import pandas as pd

from ..api import build_paper_dashboard_payload
from ..apps.cli import (
    DIRECTIONAL_PIPELINES,
    _build_directional_strategy_factory,
    json_ready,
    run_committee_signal_follower_pipeline,
    run_directional_pipeline,
    run_etf_trend_pipeline,
    run_event_driven_pipeline,
    run_graph_stat_arb_pipeline,
    run_pead_sentiment_pipeline,
    run_rule_based_strategy_pipeline,
    run_stat_arb_pipeline,
)
from ..operations.paper_trading import run_paper_batch
from ..data.news import AlphaVantageNewsProvider, BenzingaNewsProvider, CompositeHeadlineProvider, LocalNewsFileProvider, LocalWebSearchHeadlineProvider, NewsAPIHeadlineProvider, RSSHeadlineProvider, WebResearchHeadlineProvider
from ..data.sentiment_accumulator import ShadowSentimentAccumulator
from ..features.sentiment import FinBERTSentimentModel, build_best_available_sentiment_model
from ..platform import build_metadata_store
from .config import BackendSettings
from .backtest_visuals import build_backtest_visualization
from .job_queue import enqueue_quant_job
from .saas import build_lineage, build_readiness, sentiment_snapshot, SaaSService
from .schemas import BacktestRunRequest, SentimentAccumulationRequest
from .storage import ArtifactReference, build_artifact_storage
from .strategy_builder import parse_custom_strategy_pipeline
from .validators import validate_relative_path, validate_url


SENTIMENT_TABLE_ROW_LIMIT = 2_000
SENTIMENT_TABLE_ROWS_PER_LAST_RUN_TICKER = 200


@dataclass(frozen=True)
class PaperRunCommand:
    deployment_config_path: Path | None = None
    deployment_config: dict[str, Any] | None = None
    asof_date: str | None = None
    asof_start: str | None = None
    asof_end: str | None = None


@dataclass
class PaperRunJob:
    id: str
    status: str
    request: dict[str, Any]
    created_at_utc: str
    updated_at_utc: str
    organization_id: str | None = None
    user_id: str | None = None
    progress: float = 0.0
    stage: str = "queued"
    message: str = "Waiting for a paper worker."
    started_at_utc: str | None = None
    finished_at_utc: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "request": self.request,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "progress": self.progress,
            "stage": self.stage,
            "message": self.message,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "result": self.result,
            "error": self.error,
        }


class PaperRunJobRunner:
    def __init__(self, settings: BackendSettings, *, max_workers: int = 1, max_history: int = 50, mark_interrupted_on_load: bool = True) -> None:
        self.settings = settings
        self.max_history = max_history
        self.mark_interrupted_on_load = mark_interrupted_on_load
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="paper-live") if settings.enable_in_process_jobs else None
        self.lock = Lock()
        self.jobs: dict[str, PaperRunJob] = {}
        self.jobs_dir = settings.paper_job_state_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_store = build_metadata_store(settings)
        self.artifact_storage = build_artifact_storage(settings)
        self._load_jobs()

    def submit(self, command: PaperRunCommand, *, organization_id: str, user_id: str | None = None) -> dict[str, Any]:
        if command.deployment_config is not None:
            strategies = command.deployment_config.get("strategies", [])
            if not isinstance(strategies, list) or not strategies:
                raise ValueError("Inline paper deployment config must include at least one strategy.")
        config_path = command.deployment_config_path or (None if command.deployment_config is not None else self.settings.default_paper_config)
        validate_relative_path(config_path, settings=self.settings, field_name="deployment_config_path")
        if config_path is not None and not config_path.exists():
            raise FileNotFoundError(f"Paper deployment config not found: {config_path}")

        now = _utc_now_iso()
        job = PaperRunJob(
            id=uuid4().hex,
            status="queued",
            request={
                "deployment_config_path": str(command.deployment_config_path) if command.deployment_config_path else None,
                "deployment_config": command.deployment_config,
                "asof_date": command.asof_date,
                "asof_start": command.asof_start,
                "asof_end": command.asof_end,
            },
            created_at_utc=now,
            updated_at_utc=now,
            organization_id=organization_id,
            user_id=user_id,
            progress=0.02,
            stage="queued",
            message="Queued paper execution. Waiting for the shadow broker worker.",
        )
        with self.lock:
            self.jobs[job.id] = job
            self._save_locked(job)
            self._trim_locked()

        if self.settings.enable_in_process_jobs and self.executor is not None:
            future = self.executor.submit(self._run_job, job.id, command, organization_id)
            future.add_done_callback(lambda completed: self._finalize_unhandled(job.id, completed))
        else:
            queue_payload = enqueue_quant_job(self.settings, kind="paper", job_id=job.id)
            self._set_status(job.id, "queued", message=f"Queued in Redis/RQ ({queue_payload['queue']}). Waiting for worker heartbeat.")
        return job.to_dict()

    def list_jobs(self, *, organization_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return [
                job.to_dict()
                for job in sorted(self.jobs.values(), key=lambda item: item.created_at_utc, reverse=True)
                if job.organization_id == organization_id
            ]

    def get_job(self, job_id: str, *, organization_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job.organization_id != organization_id:
                return None
            return None if job is None else job.to_dict()

    def _set_status(self, job_id: str, status: str, **updates: Any) -> None:
        now = _utc_now_iso()
        with self.lock:
            job = self.jobs[job_id]
            job.status = status
            job.updated_at_utc = now
            for key, value in updates.items():
                setattr(job, key, value)
            self._save_locked(job)

    def _deployment_config_path(self, job_id: str, command: PaperRunCommand, *, organization_id: str) -> Path:
        if command.deployment_config is None:
            return command.deployment_config_path or self.settings.default_paper_config
        path = self.jobs_dir / f"{job_id}_deployment.json"
        config = json_ready(command.deployment_config)
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        self.metadata_store.save_deployment_config(
            config_id=job_id,
            source="paper_inline",
            config=config,
            organization_id=organization_id,
            path=path,
        )
        return path

    @staticmethod
    def _asof_dates(command: PaperRunCommand) -> list[str | None]:
        if command.asof_start and command.asof_end:
            dates = pd.bdate_range(start=command.asof_start, end=command.asof_end)
            if dates.empty:
                raise ValueError("Date range did not contain any business days.")
            return [date.strftime("%Y-%m-%d") for date in dates]
        return [command.asof_date]

    def _run_job(self, job_id: str, command: PaperRunCommand, organization_id: str) -> None:
        self._set_status(
            job_id,
            "running",
            started_at_utc=_utc_now_iso(),
            progress=0.10,
            stage="loading_config",
            message="Loading deployment config and execution settings.",
        )
        try:
            config_path = self._deployment_config_path(job_id, command, organization_id=organization_id)
            asof_dates = self._asof_dates(command)
            self._set_status(
                job_id,
                "running",
                progress=0.25,
                stage="building_signals",
                message=f"Preparing {len(asof_dates)} paper execution date(s) from deployment config.",
            )
            service = PaperService(self.settings)
            result: dict[str, Any] | None = None
            completed_dates: list[str | None] = []
            total_dates = len(asof_dates)
            for index, asof_date in enumerate(asof_dates):
                fraction = index / max(total_dates, 1)
                self._set_status(
                    job_id,
                    "running",
                    progress=0.30 + 0.50 * fraction,
                    stage="simulating_orders",
                    message=f"Running paper execution {index + 1} of {total_dates} for {asof_date or 'today'}.",
                )
                result = service.run_paper_batch(
                    PaperRunCommand(
                        deployment_config_path=config_path,
                        asof_date=asof_date,
                    ),
                    organization_id=organization_id,
                )
                completed_dates.append(asof_date)
            self._set_status(
                job_id,
                "running",
                progress=0.88,
                stage="saving_ledgers",
                message="Saving fake-money ledgers, latest orders, dashboards, and API payload.",
            )
            result = result or service.build_dashboard_payload(organization_id=organization_id)
            result["run_sequence"] = {
                "dates": completed_dates,
                "count": len(completed_dates),
                "deployment_config_path": str(config_path),
            }
        except Exception as exc:  # pragma: no cover - covered by API-level tests
            self._set_status(
                job_id,
                "failed",
                error=str(exc),
                progress=1.0,
                stage="failed",
                message="Paper execution failed. Review the error and deployment config.",
                finished_at_utc=_utc_now_iso(),
            )
            return
        self._set_status(
            job_id,
            "completed",
            result=result,
            progress=1.0,
            stage="completed",
            message="Paper execution completed. Ledgers and dashboard payload are updated.",
            finished_at_utc=_utc_now_iso(),
        )

    def _finalize_unhandled(self, job_id: str, future: Future[None]) -> None:
        exception = future.exception()
        if exception is None:
            return
        self._set_status(
            job_id,
            "failed",
            error=str(exception),
            progress=1.0,
            stage="failed",
            message="The paper worker crashed before returning a result.",
            finished_at_utc=_utc_now_iso(),
        )

    def _save_locked(self, job: PaperRunJob) -> None:
        payload = json_ready(job.to_dict())
        path = self.jobs_dir / f"{job.id}.json"
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        self.metadata_store.upsert_job(kind="paper", payload=payload)

    def _load_jobs(self) -> None:
        for payload in self.metadata_store.list_jobs(kind="paper"):
            try:
                job = PaperRunJob(**payload)
            except Exception:
                continue
            self._load_job_instance(job)

        for path in sorted(self.jobs_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                job = PaperRunJob(**payload)
            except Exception:
                continue
            if job.id in self.jobs:
                continue
            self._load_job_instance(job)

    def _load_job_instance(self, job: PaperRunJob) -> None:
        changed = False
        if self.mark_interrupted_on_load and job.status in {"queued", "running"}:
            job.status = "interrupted"
            job.stage = "interrupted"
            job.progress = 1.0
            job.message = "The backend restarted before this paper run finished. Please rerun it."
            job.finished_at_utc = job.finished_at_utc or _utc_now_iso()
            changed = True
        self.jobs[job.id] = job
        if changed:
            self._save_locked(job)
        else:
            self.metadata_store.upsert_job(kind="paper", payload=json_ready(job.to_dict()))

    def _trim_locked(self) -> None:
        if len(self.jobs) <= self.max_history:
            return
        removable = sorted(self.jobs.values(), key=lambda item: item.created_at_utc)[: len(self.jobs) - self.max_history]
        for job in removable:
            self.jobs.pop(job.id, None)
            try:
                (self.jobs_dir / f"{job.id}.json").unlink()
            except FileNotFoundError:
                pass
            self.metadata_store.delete_job(kind="paper", job_id=job.id)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_metric(mapping: dict[str, Any], key: str) -> float | None:
    value = mapping.get(key)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _equity_curve_points(equity_curve: Any, *, max_points: int = 500) -> list[dict[str, float | str]]:
    if not hasattr(equity_curve, "empty") or equity_curve.empty or "net_return" not in equity_curve.columns:
        return []
    frame = equity_curve.copy()
    net_returns = frame["net_return"].fillna(0.0)
    if "equity_curve" in frame.columns:
        equity = pd.to_numeric(frame["equity_curve"], errors="coerce").ffill().fillna(1.0)
    else:
        equity = (1.0 + net_returns).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    sampled = frame.assign(_equity=equity, _drawdown=drawdown, _net_return=net_returns).tail(max_points)
    points: list[dict[str, float | str]] = []
    for timestamp, row in sampled.iterrows():
        points.append(
            {
                "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
                "equity": float(row["_equity"]),
                "drawdown": float(row["_drawdown"]),
                "net_return": float(row["_net_return"]),
            }
        )
    return points


def _decision_report(summary: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add_check(name: str, value: float | None, passed: bool, message: str) -> None:
        checks.append({"name": name, "value": value, "passed": passed, "message": message})

    sharpe = _safe_metric(summary, "sharpe")
    dsr = _safe_metric(validation, "dsr")
    pbo = _safe_metric(validation, "pbo")
    max_drawdown = _safe_metric(summary, "max_drawdown")
    avg_turnover = _safe_metric(summary, "avg_turnover")
    folds = _safe_metric(summary, "folds")

    add_check("Sharpe", sharpe, sharpe is not None and sharpe >= 1.0, "Prefer Sharpe above 1.0 after costs.")
    add_check("DSR", dsr, dsr is not None and dsr >= 0.60, "DSR adjusts for multiple testing and non-normality.")
    add_check("PBO", pbo, pbo is not None and pbo <= 0.30, "Lower PBO means lower estimated overfitting risk.")
    add_check("Drawdown", max_drawdown, max_drawdown is not None and max_drawdown >= -0.25, "Large drawdowns can make live trading impossible.")
    add_check("Turnover", avg_turnover, avg_turnover is not None and avg_turnover <= 1.50, "High turnover is fragile after slippage and spread.")
    add_check("Folds", folds, folds is not None and folds >= 3.0, "More walk-forward folds make the estimate less brittle.")

    passed_count = sum(1 for check in checks if check["passed"])
    if passed_count >= 5:
        verdict = "paper_candidate"
        headline = "Candidate for shadow paper trading"
    elif passed_count >= 3:
        verdict = "research_more"
        headline = "Promising but needs more research"
    else:
        verdict = "reject_or_redesign"
        headline = "Do not promote yet"

    return {
        "verdict": verdict,
        "headline": headline,
        "passed_checks": passed_count,
        "total_checks": len(checks),
        "checks": checks,
    }


def _result_payload(run_output: dict[str, Any]) -> dict[str, Any]:
    result = run_output.get("result")
    artifact_dir = getattr(result, "artifact_dir", None)
    fold_metrics = getattr(result, "fold_metrics", None)
    equity_curve = getattr(result, "equity_curve", None)
    ledger = getattr(result, "ledger", {}) or {}
    summary = json_ready(run_output.get("summary", {}))
    validation = json_ready(run_output.get("validation", {}))
    visualization = build_backtest_visualization(
        equity_curve=equity_curve,
        prices=run_output.get("prices"),
        ledger=ledger,
        bars_per_year=int(run_output.get("bars_per_year") or summary.get("walk_forward_config", {}).get("bars_per_year", 252) or 252),
        completed_folds=int(summary.get("folds") or 0),
        total_folds=int(summary.get("folds") or 0),
        status="completed",
    )
    baseline_metrics = {
        "baseline_total_return": visualization.get("metrics", {}).get("baseline_total_return"),
        "baseline_cagr": visualization.get("metrics", {}).get("baseline_cagr"),
        "baseline_sharpe": visualization.get("metrics", {}).get("baseline_sharpe"),
        "baseline_max_drawdown": visualization.get("metrics", {}).get("baseline_max_drawdown"),
        "benchmark_outperformance": visualization.get("metrics", {}).get("benchmark_outperformance"),
    }
    summary = {**summary, **baseline_metrics}
    return {
        "summary": summary,
        "validation": validation,
        "visuals": json_ready(run_output.get("visuals", {})),
        "artifact_dir": str(artifact_dir) if artifact_dir is not None else None,
        "fold_metrics_tail": json_ready(fold_metrics.tail(12)) if hasattr(fold_metrics, "tail") else [],
        "equity_curve_tail": json_ready(equity_curve.tail(80)) if hasattr(equity_curve, "tail") else [],
        "equity_curve_points": _equity_curve_points(equity_curve),
        "visualization": json_ready(visualization),
        "ledger": json_ready(ledger),
        "trade_events": json_ready(visualization.get("trade_events", [])),
        "trade_summary": json_ready(visualization.get("trade_summary", [])),
        "decision": _decision_report(summary, validation),
    }


def _clean_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in parameters.items() if value is not None}


def _directional_min_history(pipeline: str, params: dict[str, Any]) -> int:
    _, min_history = _build_directional_strategy_factory(
        pipeline,
        fast_window=int(params.get("fast_window", 20)),
        slow_window=int(params.get("slow_window", 80)),
        ema_fast_window=int(params.get("ema_fast_window", 12)),
        ema_slow_window=int(params.get("ema_slow_window", 48)),
        rsi_window=int(params.get("rsi_window", 14)),
        sma_window=int(params.get("sma_window", 40)),
        stochastic_window=int(params.get("stochastic_window", 14)),
        stochastic_smooth_window=int(params.get("stochastic_smooth_window", 3)),
        bollinger_window=int(params.get("bollinger_window", 20)),
        macd_fast_window=int(params.get("macd_fast_window", 12)),
        macd_slow_window=int(params.get("macd_slow_window", 26)),
        macd_signal_window=int(params.get("macd_signal_window", 9)),
        breakout_window=int(params.get("breakout_window", 55)),
        breakout_exit_window=int(params.get("breakout_exit_window", 20)),
        keltner_window=int(params.get("keltner_window", 40)),
        trend_window=int(params.get("trend_window", 120)),
        volatility_window=int(params.get("volatility_window", 20)),
        momentum_lookbacks=params.get("momentum_lookbacks"),
        regime_fast_window=int(params.get("regime_fast_window", 30)),
        regime_slow_window=int(params.get("regime_slow_window", 120)),
        regime_mean_reversion_window=int(params.get("regime_mean_reversion_window", 40)),
        regime_volatility_window=int(params.get("regime_volatility_window", 30)),
    )
    return min_history


def _publish_directory_reference(storage, *, source_dir: str | Path | None, organization_id: str, artifact_type: str, artifact_id: str) -> dict[str, Any] | None:
    if not source_dir:
        return None
    source = Path(str(source_dir))
    if not source.exists() or not source.is_dir():
        return None
    reference = storage.publish_directory(
        source,
        organization_id=organization_id,
        artifact_type=artifact_type,
        artifact_id=artifact_id,
    )
    return {
        "provider": reference.provider,
        "key": reference.key,
        "uri": reference.uri,
        "file_count": reference.file_count,
        "byte_count": reference.byte_count,
    }


def _record_artifact(metadata_store, *, organization_id: str, artifact_type: str, source_id: str, reference: dict[str, Any] | None, metadata: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not reference:
        return None
    return metadata_store.upsert_artifact(
        organization_id=organization_id,
        payload={
            "artifact_type": artifact_type,
            "source_id": source_id,
            "provider": reference.get("provider"),
            "key": reference.get("key"),
            "uri": reference.get("uri"),
            "file_count": reference.get("file_count", 0),
            "byte_count": reference.get("byte_count", 0),
            "metadata": metadata or {},
        },
    )


@dataclass
class BacktestJob:
    id: str
    status: str
    request: dict[str, Any]
    created_at_utc: str
    updated_at_utc: str
    organization_id: str | None = None
    user_id: str | None = None
    progress: float = 0.0
    stage: str = "queued"
    message: str = "Waiting for a worker."
    warnings: list[str] = field(default_factory=list)
    progress_snapshot: dict[str, Any] | None = None
    started_at_utc: str | None = None
    finished_at_utc: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "request": self.request,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "progress": self.progress,
            "stage": self.stage,
            "message": self.message,
            "warnings": self.warnings,
            "progress_snapshot": self.progress_snapshot,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "result": self.result,
            "error": self.error,
        }


class BacktestJobRunner:
    def __init__(self, settings: BackendSettings, *, max_workers: int = 2, max_history: int = 50, mark_interrupted_on_load: bool = True) -> None:
        self.settings = settings
        self.max_history = max_history
        self.mark_interrupted_on_load = mark_interrupted_on_load
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="backtest-agent") if settings.enable_in_process_jobs else None
        self.lock = Lock()
        self.jobs: dict[str, BacktestJob] = {}
        self.jobs_dir = settings.backtest_job_state_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_store = build_metadata_store(settings)
        self.artifact_storage = build_artifact_storage(settings)
        self._load_jobs()

    def submit(self, request: BacktestRunRequest, *, organization_id: str, user_id: str | None = None) -> dict[str, Any]:
        BacktestService(self.settings).validate_request(request, organization_id=organization_id, user_id=user_id)
        now = _utc_now_iso()
        job = BacktestJob(
            id=uuid4().hex,
            status="queued",
            request=json_ready(request.model_dump(mode="json")),
            created_at_utc=now,
            updated_at_utc=now,
            organization_id=organization_id,
            user_id=user_id,
            progress=0.02,
            stage="queued",
            message="Queued locally. A backtest worker will pick this up next.",
        )
        with self.lock:
            self.jobs[job.id] = job
            self._save_locked(job)
            self._trim_locked()

        if self.settings.enable_in_process_jobs and self.executor is not None:
            future = self.executor.submit(self._run_job, job.id, request, organization_id, user_id)
            future.add_done_callback(lambda completed: self._finalize_unhandled(job.id, completed))
        else:
            queue_payload = enqueue_quant_job(self.settings, kind="backtest", job_id=job.id)
            self._set_status(job.id, "queued", message=f"Queued in Redis/RQ ({queue_payload['queue']}). Waiting for worker heartbeat.")
        return job.to_dict()

    def list_jobs(self, *, organization_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return [
                job.to_dict()
                for job in sorted(self.jobs.values(), key=lambda item: item.created_at_utc, reverse=True)
                if job.organization_id == organization_id
            ]

    def get_job(self, job_id: str, *, organization_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job.organization_id != organization_id:
                return None
            return None if job is None else job.to_dict()

    def _set_status(self, job_id: str, status: str, **updates: Any) -> None:
        now = _utc_now_iso()
        with self.lock:
            job = self.jobs[job_id]
            job.status = status
            job.updated_at_utc = now
            for key, value in updates.items():
                setattr(job, key, value)
            self._save_locked(job)

    def _run_job(self, job_id: str, request: BacktestRunRequest, organization_id: str, user_id: str | None = None) -> None:
        def progress(stage: str, message: str, value: float, snapshot: dict[str, Any] | None = None) -> None:
            updates: dict[str, Any] = {
                "stage": stage,
                "message": message,
                "progress": float(max(0.0, min(value, 0.98))),
            }
            if snapshot is not None:
                updates["progress_snapshot"] = snapshot
            self._set_status(
                job_id,
                "running",
                **updates,
            )

        self._set_status(
            job_id,
            "running",
            started_at_utc=_utc_now_iso(),
            stage="starting",
            message="Worker started. Validating inputs and preparing the strategy.",
            progress=0.08,
        )
        try:
            result = BacktestService(self.settings).run_backtest(request, organization_id=organization_id, user_id=user_id, progress=progress)
            summary = result.get("summary", {})
            validation = result.get("validation", {})
            experiment_id = str(summary.get("experiment_id") or job_id)
            artifact_reference = _publish_directory_reference(
                self.artifact_storage,
                source_dir=result.get("artifact_dir"),
                organization_id=organization_id,
                artifact_type="backtests",
                artifact_id=experiment_id,
            )
            if artifact_reference:
                result["artifact"] = artifact_reference
                artifact_record = _record_artifact(
                    self.metadata_store,
                    organization_id=organization_id,
                    artifact_type="backtests",
                    source_id=experiment_id,
                    reference=artifact_reference,
                    metadata={"job_id": job_id, "pipeline": request.pipeline},
                )
                if artifact_record:
                    result["artifact_id"] = artifact_record["id"]
                    summary = {**summary, "artifact_id": artifact_record["id"]}
                summary = {**summary, "artifact": artifact_reference}
                result["summary"] = summary
            self.metadata_store.save_experiment_run(
                experiment_id=experiment_id,
                kind="backtest",
                summary=json_ready(summary),
                organization_id=organization_id,
                artifact_dir=result.get("artifact_dir"),
            )
            self.metadata_store.upsert_experiment(
                organization_id=organization_id,
                payload={
                    "id": experiment_id,
                    "job_id": job_id,
                    "name": str(summary.get("strategy") or request.experiment_name or experiment_id),
                    "pipeline": request.pipeline,
                    "status": "completed",
                    "artifact_dir": artifact_reference.get("uri") if artifact_reference else result.get("artifact_dir"),
                    "summary": json_ready(summary),
                    "validation": json_ready(validation),
                    "lineage": build_lineage(
                        request=json_ready(request.model_dump(mode="json")),
                        artifact_dir=result.get("artifact_dir"),
                        settings=self.settings,
                    ),
                    "readiness": build_readiness(summary=json_ready(summary), validation=json_ready(validation)),
                    "trades": [],
                    "sentiment": sentiment_snapshot(
                        request=json_ready(request.model_dump(mode="json")),
                        artifact_dir=Path(str(result.get("artifact_dir") or "")),
                    ),
                },
            )
        except Exception as exc:  # pragma: no cover - exercised through API tests
            self._set_status(
                job_id,
                "failed",
                error=str(exc),
                finished_at_utc=_utc_now_iso(),
                progress=1.0,
                stage="failed",
                message="The backtest failed. Review the error and inputs.",
            )
            return
        self._set_status(
            job_id,
            "completed",
            result=result,
            progress_snapshot=result.get("visualization"),
            finished_at_utc=_utc_now_iso(),
            progress=1.0,
            stage="completed",
            message="Backtest completed. Review validation before promoting to paper trading.",
        )

    def _finalize_unhandled(self, job_id: str, future: Future[None]) -> None:
        exception = future.exception()
        if exception is None:
            return
        self._set_status(
            job_id,
            "failed",
            error=str(exception),
            finished_at_utc=_utc_now_iso(),
            progress=1.0,
            stage="failed",
            message="The worker crashed before returning a result.",
        )

    def _trim_locked(self) -> None:
        if len(self.jobs) <= self.max_history:
            return
        removable = sorted(self.jobs.values(), key=lambda item: item.created_at_utc)[: len(self.jobs) - self.max_history]
        for job in removable:
            self.jobs.pop(job.id, None)
            try:
                (self.jobs_dir / f"{job.id}.json").unlink()
            except FileNotFoundError:
                pass
            self.metadata_store.delete_job(kind="backtest", job_id=job.id)

    def _save_locked(self, job: BacktestJob) -> None:
        payload = json_ready(job.to_dict())
        path = self.jobs_dir / f"{job.id}.json"
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        self.metadata_store.upsert_job(kind="backtest", payload=payload)

    def _load_jobs(self) -> None:
        for payload in self.metadata_store.list_jobs(kind="backtest"):
            try:
                job = BacktestJob(**payload)
            except Exception:
                continue
            self._load_job_instance(job)

        for path in sorted(self.jobs_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                job = BacktestJob(**payload)
            except Exception:
                continue
            if job.id in self.jobs:
                continue
            self._load_job_instance(job)

    def _load_job_instance(self, job: BacktestJob) -> None:
        changed = False
        if self.mark_interrupted_on_load and job.status in {"queued", "running"}:
            job.status = "interrupted"
            job.stage = "interrupted"
            job.progress = 1.0
            job.message = "The backend restarted before this job finished. Please rerun it."
            job.finished_at_utc = job.finished_at_utc or _utc_now_iso()
            changed = True
        self.jobs[job.id] = job
        if changed:
            self._save_locked(job)
        else:
            self.metadata_store.upsert_job(kind="backtest", payload=json_ready(job.to_dict()))


class BacktestService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.metadata_store = build_metadata_store(settings)
        self.artifact_storage = build_artifact_storage(settings)

    def _materialize_dataset_file(self, *, organization_id: str | None, dataset_id: str | None, suffixes: tuple[str, ...]) -> Path | None:
        if not organization_id or not dataset_id:
            return None
        dataset = self.metadata_store.get_dataset(organization_id=organization_id, dataset_id=dataset_id)
        if dataset is None:
            raise ValueError(f"Dataset not found: {dataset_id}")
        provider = dataset.get("provider") if isinstance(dataset.get("provider"), dict) else {}
        artifact_id = provider.get("artifact_id")
        artifact = self.metadata_store.get_artifact(organization_id=organization_id, artifact_id=str(artifact_id)) if artifact_id else None
        reference_payload = artifact or provider.get("artifact")
        if isinstance(reference_payload, dict):
            reference = ArtifactReference(
                provider=str(reference_payload.get("provider") or "local"),
                key=str(reference_payload.get("storage_key") or reference_payload.get("key") or ""),
                uri=str(reference_payload.get("uri") or ""),
            )
            root = self.settings.backtest_artifact_root / "materialized" / organization_id / dataset_id
            materialized = self.artifact_storage.materialize_directory(reference, root)
            files = [path for path in materialized.rglob("*") if path.is_file() and path.suffix.lower() in suffixes]
            if files:
                return sorted(files)[0]
        if not self.settings.is_production and dataset.get("path"):
            path = Path(str(dataset["path"]))
            if path.is_file():
                return path
            return next((item for item in path.rglob("*") if item.is_file() and item.suffix.lower() in suffixes), None)
        raise ValueError(f"Dataset {dataset_id} does not expose a usable artifact file.")

    def validate_request(self, request: BacktestRunRequest, *, organization_id: str | None = None, user_id: str | None = None) -> None:
        if not request.pipeline:
            raise ValueError("Choose a pipeline before launching a backtest.")
        custom_strategy_id = parse_custom_strategy_pipeline(request.pipeline)
        if custom_strategy_id:
            if not organization_id or not user_id:
                raise ValueError("User-created strategies require an authenticated workspace.")
            strategy = self.metadata_store.get_user_strategy(
                organization_id=organization_id,
                strategy_id=custom_strategy_id,
                owner_user_id=user_id,
                active_only=True,
            )
            if strategy is None:
                raise ValueError("User-created strategy not found, disabled, or not owned by this user.")
            spec = strategy.get("spec") if isinstance(strategy.get("spec"), dict) else {}
            symbols = request.symbols or spec.get("asset_universe", {}).get("symbols", [])
            if not symbols:
                raise ValueError("User-created strategies require at least one symbol.")
        if request.pipeline in (set(DIRECTIONAL_PIPELINES) | {"etf_trend", "edgar_event", "pead_sentiment", "committee_signal_follower"}) and not request.symbols:
            raise ValueError("This pipeline requires at least one symbol.")
        if request.pipeline in {"edgar_event", "pead_sentiment"} and not request.event_file and not request.event_dataset_id and not request.use_sec_companyfacts and not request.include_sec_filings:
            raise ValueError("Event backtests require an event file, SEC company facts, or official SEC filings.")
        if request.pipeline in {"stat_arb", "graph_stat_arb"} and request.symbols and request.sector_map_path is None and request.sector_dataset_id is None:
            # The stat-arb runner can use its default sector map, but user-supplied symbols would be ignored.
            raise ValueError("Stat-arb symbol lists require a sector map path so sectors are explicit.")
        if self.settings.is_production and any([request.artifact_root, request.sector_map_path, request.event_file]):
            raise ValueError("Production backtests use tenant-owned artifact_id, sector_dataset_id, or event_dataset_id instead of raw filesystem paths.")
        validate_relative_path(request.artifact_root, settings=self.settings, field_name="artifact_root")
        validate_relative_path(request.sector_map_path, settings=self.settings, field_name="sector_map_path")
        validate_relative_path(request.event_file, settings=self.settings, field_name="event_file")
        if request.pipeline != "committee_signal_follower" and request.train_bars <= request.purge_bars + 5:
            raise ValueError("Training bars must be meaningfully larger than purge bars.")
        if request.step_bars < request.test_bars:
            raise ValueError("step_bars must be greater than or equal to test_bars to avoid overlapping out-of-sample accounting.")
        if request.pipeline in DIRECTIONAL_PIPELINES:
            params = _clean_parameters(request.parameters)
            min_history = _directional_min_history(request.pipeline, params)
            if request.train_bars < min_history:
                raise ValueError(
                    f"{request.pipeline} requires at least {min_history} train_bars for indicator warmup; "
                    f"received {request.train_bars}."
                )

    def run_backtest(
        self,
        request: BacktestRunRequest,
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
        progress: Callable[[str, str, float, dict[str, Any] | None], None] | None = None,
    ) -> dict[str, Any]:
        def report(stage: str, message: str, value: float, snapshot: dict[str, Any] | None = None) -> None:
            if progress is not None:
                progress(stage, message, value, snapshot)

        def snapshot_progress(payload: dict[str, Any]) -> None:
            completed_folds = int(payload.get("completed_folds") or 0)
            total_folds = int(payload.get("total_folds") or 0)
            snapshot = build_backtest_visualization(
                equity_curve=payload.get("equity_curve"),
                prices=payload.get("prices"),
                ledger=payload.get("ledger"),
                bars_per_year=int(payload.get("bars_per_year") or request.bars_per_year),
                completed_folds=completed_folds,
                total_folds=total_folds,
                status="running",
            )
            fold_fraction = completed_folds / max(total_folds, 1)
            report(
                "simulating_folds",
                f"Completed walk-forward fold {completed_folds} of {total_folds}. Strategy and baseline curves are synchronized through the latest test bar.",
                0.22 + 0.66 * fold_fraction,
                snapshot,
            )

        self.validate_request(request, organization_id=organization_id, user_id=user_id)
        sector_file = self._materialize_dataset_file(organization_id=organization_id, dataset_id=request.sector_dataset_id, suffixes=(".json", ".csv", ".parquet"))
        event_file = self._materialize_dataset_file(organization_id=organization_id, dataset_id=request.event_dataset_id, suffixes=(".csv", ".json", ".parquet"))
        if sector_file or event_file:
            request = request.model_copy(update={
                "sector_map_path": sector_file or request.sector_map_path,
                "event_file": event_file or request.event_file,
            })
        pipeline = request.pipeline
        params = _clean_parameters(request.parameters)
        artifact_root = str(request.artifact_root or self.settings.backtest_artifact_root)
        experiment_name = request.experiment_name or f"{pipeline}_ui"

        custom_strategy_id = parse_custom_strategy_pipeline(pipeline)
        if custom_strategy_id:
            if not organization_id or not user_id:
                raise ValueError("User-created strategies require an authenticated workspace.")
            strategy_record = self.metadata_store.get_user_strategy(
                organization_id=organization_id,
                strategy_id=custom_strategy_id,
                owner_user_id=user_id,
                active_only=True,
            )
            if strategy_record is None:
                raise ValueError("User-created strategy not found, disabled, or not owned by this user.")
            spec = strategy_record.get("spec") if isinstance(strategy_record.get("spec"), dict) else {}
            spec_symbols = spec.get("asset_universe", {}).get("symbols", []) if isinstance(spec.get("asset_universe"), dict) else []
            symbols = request.symbols or list(spec_symbols)
            report("running_user_strategy", f"Running user strategy {strategy_record.get('name')} across {len(symbols)} symbols.", 0.22)
            run_output = run_rule_based_strategy_pipeline(
                spec=spec,
                symbols=symbols,
                start=request.start,
                end=request.end,
                interval=request.interval,
                experiment_name=experiment_name if request.experiment_name else str(strategy_record.get("name") or custom_strategy_id),
                price_cache_dir=str(self.settings.price_cache_dir),
                artifact_root=artifact_root,
                train_bars=request.train_bars,
                test_bars=request.test_bars,
                step_bars=request.step_bars,
                bars_per_year=request.bars_per_year,
                purge_bars=request.purge_bars,
                embargo_bars=request.embargo_bars,
                pbo_partitions=request.pbo_partitions,
                progress_callback=snapshot_progress,
            )
            self.metadata_store.increment_user_strategy_backtest_count(strategy_id=custom_strategy_id)
            report("collecting_results", "Backtest finished. Building charts and validation summary.", 0.92)
            payload = _result_payload(run_output)
            payload["user_strategy"] = {
                "id": custom_strategy_id,
                "name": strategy_record.get("name"),
                "version": strategy_record.get("version"),
                "risk_level": strategy_record.get("risk_level"),
            }
            return payload

        if pipeline in DIRECTIONAL_PIPELINES:
            report("running_directional", f"Running {pipeline} across {len(request.symbols)} symbols.", 0.22)
            run_output = run_directional_pipeline(
                strategy_name=pipeline,
                symbols=request.symbols,
                start=request.start,
                end=request.end,
                interval=request.interval,
                experiment_name=experiment_name,
                price_cache_dir=str(self.settings.price_cache_dir),
                artifact_root=artifact_root,
                train_bars=request.train_bars,
                test_bars=request.test_bars,
                step_bars=request.step_bars,
                bars_per_year=request.bars_per_year,
                fast_window=int(params.get("fast_window", 20)),
                slow_window=int(params.get("slow_window", 80)),
                ema_fast_window=int(params.get("ema_fast_window", 12)),
                ema_slow_window=int(params.get("ema_slow_window", 48)),
                rsi_window=int(params.get("rsi_window", 14)),
                lower_entry=float(params.get("lower_entry", 30.0)),
                upper_entry=float(params.get("upper_entry", 70.0)),
                exit_level=float(params.get("exit_level", 50.0)),
                sma_window=int(params.get("sma_window", 40)),
                z_entry=float(params.get("z_entry", 1.25)),
                z_exit=float(params.get("z_exit", 0.25)),
                stochastic_window=int(params.get("stochastic_window", 14)),
                stochastic_smooth_window=int(params.get("stochastic_smooth_window", 3)),
                stochastic_lower_entry=float(params.get("stochastic_lower_entry", 20.0)),
                stochastic_upper_entry=float(params.get("stochastic_upper_entry", 80.0)),
                bollinger_window=int(params.get("bollinger_window", 20)),
                bollinger_num_std=float(params.get("bollinger_num_std", 2.0)),
                macd_fast_window=int(params.get("macd_fast_window", 12)),
                macd_slow_window=int(params.get("macd_slow_window", 26)),
                macd_signal_window=int(params.get("macd_signal_window", 9)),
                breakout_window=int(params.get("breakout_window", 55)),
                breakout_exit_window=int(params.get("breakout_exit_window", 20)),
                keltner_window=int(params.get("keltner_window", 40)),
                keltner_atr_multiplier=float(params.get("keltner_atr_multiplier", 1.5)),
                trend_window=int(params.get("trend_window", 120)),
                volatility_window=int(params.get("volatility_window", 20)),
                target_volatility=float(params.get("target_volatility", 0.15)),
                max_position=float(params.get("max_position", 1.5)),
                momentum_lookbacks=params.get("momentum_lookbacks"),
                momentum_min_agreement=float(params.get("momentum_min_agreement", 0.25)),
                regime_fast_window=int(params.get("regime_fast_window", 30)),
                regime_slow_window=int(params.get("regime_slow_window", 120)),
                regime_mean_reversion_window=int(params.get("regime_mean_reversion_window", 40)),
                regime_volatility_window=int(params.get("regime_volatility_window", 30)),
                regime_volatility_quantile=float(params.get("regime_volatility_quantile", 0.70)),
                strategy_cost_bps=float(params.get("strategy_cost_bps", 2.0)),
                purge_bars=request.purge_bars,
                embargo_bars=request.embargo_bars,
                pbo_partitions=request.pbo_partitions,
                progress_callback=snapshot_progress,
            )
            report("collecting_results", "Backtest finished. Building charts and validation summary.", 0.92)
            return _result_payload(run_output)

        if pipeline == "etf_trend":
            report("running_etf_trend", f"Running ETF trend agent across {len(request.symbols)} symbols.", 0.22)
            run_output = run_etf_trend_pipeline(
                symbols=request.symbols,
                start=request.start,
                end=request.end,
                interval=request.interval,
                experiment_name=experiment_name,
                price_cache_dir=str(self.settings.price_cache_dir),
                artifact_root=artifact_root,
                purge_bars=request.purge_bars,
                embargo_bars=request.embargo_bars,
                pbo_partitions=request.pbo_partitions,
                progress_callback=snapshot_progress,
            )
            report("collecting_results", "Backtest finished. Building charts and validation summary.", 0.92)
            return _result_payload(run_output)

        if pipeline == "stat_arb":
            report("running_stat_arb", "Running sector-neutral stat-arb agent.", 0.22)
            run_output = run_stat_arb_pipeline(
                sector_map_path=str(request.sector_map_path) if request.sector_map_path else None,
                start=request.start,
                end=request.end,
                interval=request.interval,
                experiment_name=experiment_name,
                price_cache_dir=str(self.settings.price_cache_dir),
                sentiment_cache_dir=str(self.settings.sentiment_cache_dir),
                artifact_root=artifact_root,
                daily_sentiment_file=str(params["daily_sentiment_file"]) if params.get("daily_sentiment_file") else None,
                news_provider_names=params.get("news_provider_names"),
                news_files=params.get("news_files"),
                news_api_key=params.get("news_api_key"),
                alphavantage_api_key=params.get("alphavantage_api_key"),
                benzinga_api_key=params.get("benzinga_api_key"),
                newsapi_api_key=params.get("newsapi_api_key"),
                news_topics=params.get("news_topics"),
                rss_feed_urls=params.get("rss_feed_urls"),
                local_web_search_urls=params.get("local_web_search_urls"),
                local_web_refresh_minutes=int(params.get("local_web_refresh_minutes", 60)),
                local_web_max_pages_per_source=int(params.get("local_web_max_pages_per_source", 30)),
                web_research_urls=params.get("web_research_urls"),
                web_research_domains=params.get("web_research_domains"),
                web_research_query_terms=str(params.get("web_research_query_terms", "")),
                web_research_max_articles=int(params.get("web_research_max_articles", 4)),
                web_research_fetch_article_text=bool(params.get("web_research_fetch_article_text", True)),
                use_finbert=bool(params.get("use_finbert", False)),
                local_finbert_only=bool(params.get("local_finbert_only", False)),
                purge_bars=request.purge_bars,
                embargo_bars=request.embargo_bars,
                pbo_partitions=request.pbo_partitions,
                progress_callback=snapshot_progress,
            )
            report("collecting_results", "Backtest finished. Building charts and validation summary.", 0.92)
            return _result_payload(run_output)

        if pipeline == "graph_stat_arb":
            report("running_graph_stat_arb", "Running graph-cluster residual stat-arb agent.", 0.22)
            run_output = run_graph_stat_arb_pipeline(
                sector_map_path=str(request.sector_map_path) if request.sector_map_path else None,
                start=request.start,
                end=request.end,
                interval=request.interval,
                experiment_name=experiment_name,
                price_cache_dir=str(self.settings.price_cache_dir),
                artifact_root=artifact_root,
                cluster_correlation_floor=float(params.get("cluster_correlation_floor", 0.55)),
                cluster_min_size=int(params.get("cluster_min_size", 3)),
                cluster_max_size=int(params.get("cluster_max_size", 8)),
                cluster_min_history=int(params.get("cluster_min_history", 180)),
                residual_lookback=int(params.get("residual_lookback", 60)),
                entry_z=float(params.get("entry_z", 1.25)),
                top_n_per_side=int(params.get("top_n_per_side", 2)),
                transaction_cost_bps=float(params.get("transaction_cost_bps", 3.0)),
                purge_bars=request.purge_bars,
                embargo_bars=request.embargo_bars,
                pbo_partitions=request.pbo_partitions,
                progress_callback=snapshot_progress,
            )
            report("collecting_results", "Backtest finished. Building charts and validation summary.", 0.92)
            return _result_payload(run_output)

        if pipeline == "edgar_event":
            report("running_events", "Running event-driven EDGAR agent.", 0.22)
            run_output = run_event_driven_pipeline(
                symbols=request.symbols,
                start=request.start,
                end=request.end,
                interval=request.interval,
                experiment_name=experiment_name,
                price_cache_dir=str(self.settings.price_cache_dir),
                event_cache_dir=str(self.settings.event_cache_dir),
                artifact_root=artifact_root,
                event_file=str(request.event_file) if request.event_file else None,
                edgar_user_agent=request.edgar_user_agent,
                use_sec_companyfacts=request.use_sec_companyfacts,
                include_sec_filings=request.include_sec_filings,
                sec_filing_forms=request.sec_filing_forms,
                purge_bars=request.purge_bars,
                embargo_bars=request.embargo_bars,
                pbo_partitions=request.pbo_partitions,
                progress_callback=snapshot_progress,
            )
            report("collecting_results", "Backtest finished. Building charts and validation summary.", 0.92)
            return _result_payload(run_output)

        if pipeline == "pead_sentiment":
            report("running_pead_sentiment", "Running PEAD + sentiment event agent.", 0.22)
            run_output = run_pead_sentiment_pipeline(
                symbols=request.symbols,
                start=request.start,
                end=request.end,
                interval=request.interval,
                experiment_name=experiment_name,
                price_cache_dir=str(self.settings.price_cache_dir),
                event_cache_dir=str(self.settings.event_cache_dir),
                sentiment_cache_dir=str(self.settings.sentiment_cache_dir),
                artifact_root=artifact_root,
                event_file=str(request.event_file) if request.event_file else None,
                edgar_user_agent=request.edgar_user_agent,
                use_sec_companyfacts=request.use_sec_companyfacts,
                include_sec_filings=request.include_sec_filings,
                sec_filing_forms=request.sec_filing_forms,
                daily_sentiment_file=str(params["daily_sentiment_file"]) if params.get("daily_sentiment_file") else None,
                news_provider_names=params.get("news_provider_names"),
                news_files=params.get("news_files"),
                news_api_key=params.get("news_api_key"),
                alphavantage_api_key=params.get("alphavantage_api_key"),
                benzinga_api_key=params.get("benzinga_api_key"),
                newsapi_api_key=params.get("newsapi_api_key"),
                use_finbert=bool(params.get("use_finbert", False)),
                local_finbert_only=bool(params.get("local_finbert_only", False)),
                news_topics=params.get("news_topics"),
                rss_feed_urls=params.get("rss_feed_urls"),
                local_web_search_urls=params.get("local_web_search_urls"),
                local_web_refresh_minutes=int(params.get("local_web_refresh_minutes", 60)),
                local_web_max_pages_per_source=int(params.get("local_web_max_pages_per_source", 30)),
                web_research_urls=params.get("web_research_urls"),
                web_research_domains=params.get("web_research_domains"),
                web_research_query_terms=str(params.get("web_research_query_terms", "")),
                web_research_max_articles=int(params.get("web_research_max_articles", 4)),
                web_research_fetch_article_text=bool(params.get("web_research_fetch_article_text", True)),
                holding_period_bars=int(params.get("holding_period_bars", 5)),
                entry_threshold=float(params.get("entry_threshold", 0.20)),
                event_weight=float(params.get("event_weight", 0.45)),
                sentiment_weight=float(params.get("sentiment_weight", 0.55)),
                sentiment_window_days=int(params.get("sentiment_window_days", 2)),
                require_sentiment=bool(params.get("require_sentiment", False)),
                require_earnings_event=bool(params.get("require_earnings_event", True)),
                purge_bars=request.purge_bars,
                embargo_bars=request.embargo_bars,
                pbo_partitions=request.pbo_partitions,
                progress_callback=snapshot_progress,
            )
            report("collecting_results", "Backtest finished. Building charts and validation summary.", 0.92)
            return _result_payload(run_output)

        if pipeline == "committee_signal_follower":
            report(
                "running_committee_signal_follower",
                f"Running committee signal follower across {len(request.symbols)} symbols with decisions from DecisionHistoryStore.",
                0.22,
            )
            from ..research.decision_history import DecisionHistoryStore
            decision_store = DecisionHistoryStore(self.settings)
            run_output = run_committee_signal_follower_pipeline(
                symbols=request.symbols,
                decision_store=decision_store,
                start=request.start,
                end=request.end,
                interval=request.interval,
                experiment_name=experiment_name,
                price_cache_dir=str(self.settings.price_cache_dir),
                artifact_root=artifact_root,
                position_sizing=str(params.get("position_sizing", "confidence_weighted")),
                max_position_pct=float(params.get("max_position_pct", 0.25)),
                confidence_threshold=int(params.get("confidence_threshold", 30)),
                scale_in_confidence_delta=int(params.get("scale_in_confidence_delta", 10)),
                scale_out_on_opposite=bool(params.get("scale_out_on_opposite", True)),
                flat_on_avoid=bool(params.get("flat_on_avoid", True)),
                train_bars=1,
                test_bars=None,
                step_bars=None,
                purge_bars=0,
                embargo_bars=0,
                pbo_partitions=request.pbo_partitions,
                progress_callback=snapshot_progress,
            )
            report("collecting_results", "Backtest finished. Building charts and validation summary.", 0.92)
            return _result_payload(run_output)

        raise ValueError(f"Unsupported backtest pipeline: {pipeline}")

    @staticmethod
    def templates() -> list[dict[str, Any]]:
        return [
            {
                "id": "trend_agent",
                "name": "ETF Momentum Agent",
                "pipeline": "etf_trend",
                "symbols": ["SPY", "QQQ", "IWM", "TLT", "GLD", "XLK"],
                "start": "2015-01-01",
                "end": "2026-04-15",
                "parameters": {},
                "description": "Clean first sleeve: liquid ETFs, momentum rotation, and realistic validation.",
                "objective": "Find a robust liquid ETF rotation candidate.",
                "risk_level": "Medium",
                "validation_focus": "DSR, PBO, drawdown, turnover, and sensitivity to rebalance cadence.",
            },
            {
                "id": "vol_target_agent",
                "name": "Volatility Target Trend Agent",
                "pipeline": "volatility_target_trend",
                "symbols": ["SPY", "QQQ", "TLT", "GLD"],
                "start": "2015-01-01",
                "end": "2026-04-15",
                "parameters": {"trend_window": 120, "volatility_window": 20, "target_volatility": 0.15},
                "description": "Directional trend model with dynamic risk scaling.",
                "objective": "Test whether volatility scaling improves trend-following stability.",
                "risk_level": "Medium",
                "validation_focus": "Drawdown control, turnover, and whether DSR survives nearby parameter changes.",
            },
            {
                "id": "reversion_agent",
                "name": "Bollinger Reversion Agent",
                "pipeline": "bollinger_mean_reversion",
                "symbols": ["SPY", "QQQ", "IWM"],
                "start": "2018-01-01",
                "end": "2026-04-15",
                "parameters": {"bollinger_window": 20, "bollinger_num_std": 2.0, "z_exit": 0.25},
                "description": "Mean-reversion lab for range-bound behavior.",
                "objective": "Test short-term reversion behavior against noisy ETF ranges.",
                "risk_level": "Medium-high",
                "validation_focus": "False breakouts, transaction costs, and drawdown during trend regimes.",
            },
            {
                "id": "adaptive_agent",
                "name": "Adaptive Regime Agent",
                "pipeline": "adaptive_regime",
                "symbols": ["SPY", "QQQ", "IWM", "TLT"],
                "start": "2015-01-01",
                "end": "2026-04-15",
                "parameters": {"regime_fast_window": 30, "regime_slow_window": 120, "regime_volatility_quantile": 0.7},
                "description": "Advanced regime switcher that alternates trend and mean-reversion behavior.",
                "objective": "Evaluate whether regime switching adds value after validation penalties.",
                "risk_level": "High",
                "validation_focus": "Overfitting risk, PBO, and stability across market regimes.",
            },
            {
                "id": "graph_stat_arb_agent",
                "name": "Graph Stat-Arb Agent",
                "pipeline": "graph_stat_arb",
                "symbols": [],
                "start": "2018-01-01",
                "end": "2026-04-15",
                "sector_map_path": "examples/sector_map.sample.json",
                "parameters": {
                    "cluster_correlation_floor": 0.55,
                    "cluster_min_size": 3,
                    "residual_lookback": 60,
                    "entry_z": 1.25,
                    "top_n_per_side": 2,
                },
                "description": "Cluster residual stat-arb using graph communities instead of one pair at a time.",
                "objective": "Test whether MST-style clusters reduce idiosyncratic pair risk.",
                "risk_level": "High",
                "validation_focus": "Cluster stability, turnover, leverage, and drawdown in correlation breaks.",
            },
            {
                "id": "pead_sentiment_agent",
                "name": "PEAD + Sentiment Agent",
                "pipeline": "pead_sentiment",
                "symbols": ["AAPL", "MSFT", "NVDA"],
                "start": "2018-01-01",
                "end": "2026-04-15",
                "event_file": "examples/events.sample.csv",
                "parameters": {
                    "holding_period_bars": 5,
                    "entry_threshold": 0.20,
                    "event_weight": 0.45,
                    "sentiment_weight": 0.55,
                    "daily_sentiment_file": "examples/daily_sentiment.sample.csv",
                    "require_earnings_event": True,
                },
                "description": "Post-earnings drift sleeve combining event/fundamental proxies with sentiment.",
                "objective": "Test whether official events plus positive/negative sentiment create post-event continuation.",
                "risk_level": "High",
                "validation_focus": "Look-ahead safety, event timestamp quality, sentiment coverage, and overfit thresholds.",
            },
            {
                "id": "committee_signal_agent",
                "name": "Committee Signal Follower",
                "pipeline": "committee_signal_follower",
                "symbols": ["AAPL", "MSFT", "GLD"],
                "start": "2025-01-01",
                "end": "2026-05-20",
                "train_bars": 1,
                "test_bars": 63,
                "step_bars": 63,
                "purge_bars": 0,
                "parameters": {
                    "max_position_pct": 0.25,
                    "confidence_threshold": 30,
                    "scale_in_confidence_delta": 10,
                    "scale_out_on_opposite": True,
                    "flat_on_avoid": True,
                },
                "description": "Simulates trading every historical committee research decision — BUY goes long, SELL goes short, AVOID flattens — to measure the real P&L of following the AI research agents.",
                "objective": "Validate whether past committee recommendations would have been profitable.",
                "risk_level": "Medium",
                "validation_focus": "Decision coverage, signal quality, drawdown during conflicting signals.",
            },
        ]


class PaperService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.metadata_store = build_metadata_store(settings)
        self.artifact_storage = build_artifact_storage(settings)

    def latest_batch_summary_path(self) -> Path | None:
        root = self.settings.paper_artifact_root
        if not root.exists():
            return None
        candidates = list(root.glob("*_paper_batch/paper_batch_summary.json"))
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def build_dashboard_payload(self, *, organization_id: str, paper_agent_id: str | None = None) -> dict[str, Any]:
        if paper_agent_id:
            agent = self.metadata_store.get_paper_agent(organization_id=organization_id, agent_id=paper_agent_id)
            return dict((agent or {}).get("latest_payload") or {})
        if self.settings.is_production:
            agents = self.metadata_store.list_paper_agents(organization_id=organization_id)
            return dict((agents[0] if agents else {}).get("latest_payload") or {})
        summary_path = self.latest_batch_summary_path()
        payload = build_paper_dashboard_payload(
            state_dir=self.settings.paper_state_dir,
            batch_summary_path=summary_path,
        )
        SaaSService(self.settings).sync_paper_agents_from_dashboard(
            organization_id=organization_id,
            payload=payload,
        )
        return payload

    def list_strategies(self, *, organization_id: str, paper_agent_id: str | None = None) -> list[dict[str, Any]]:
        return list(self.build_dashboard_payload(organization_id=organization_id, paper_agent_id=paper_agent_id).get("strategies", []))

    def get_strategy(self, *, organization_id: str, strategy_name: str, paper_agent_id: str | None = None) -> dict[str, Any] | None:
        normalized = strategy_name.casefold()
        for strategy in self.list_strategies(organization_id=organization_id, paper_agent_id=paper_agent_id):
            if str(strategy.get("name", "")).casefold() == normalized:
                return strategy
        return None

    def run_paper_batch(self, command: PaperRunCommand, *, organization_id: str) -> dict[str, Any]:
        if command.deployment_config is not None:
            deployment_dir = self.settings.paper_artifact_root.parent / "inline_deployments"
            deployment_dir.mkdir(parents=True, exist_ok=True)
            config_path = deployment_dir / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_deployment.json"
            config_path.write_text(json.dumps(json_ready(command.deployment_config), indent=2), encoding="utf-8")
        else:
            config_path = command.deployment_config_path or self.settings.default_paper_config
        validate_relative_path(config_path, settings=self.settings, field_name="deployment_config_path")
        if not config_path.exists():
            raise FileNotFoundError(f"Paper deployment config not found: {config_path}")

        asof_dates = PaperRunJobRunner._asof_dates(command)
        latest_payload: dict[str, Any] | None = None
        completed_dates: list[str | None] = []
        for asof_date in asof_dates:
            summary = run_paper_batch(
                deployment_config_path=config_path,
                asof_date=asof_date,
                state_dir=self.settings.paper_state_dir,
                artifact_root=self.settings.paper_artifact_root,
                price_cache_dir=str(self.settings.price_cache_dir),
                sentiment_cache_dir=str(self.settings.sentiment_cache_dir),
                event_cache_dir=str(self.settings.event_cache_dir),
            )
            paper_run_id = f"{summary.get('run_timestamp_utc') or _utc_now_iso()}_{asof_date or 'today'}_{len(completed_dates) + 1}"
            artifact_reference = _publish_directory_reference(
                self.artifact_storage,
                source_dir=summary.get("artifact_dir"),
                organization_id=organization_id,
                artifact_type="paper",
                artifact_id=paper_run_id,
            )
            if artifact_reference:
                summary = {**summary, "artifact": artifact_reference}
                artifact_record = _record_artifact(
                    self.metadata_store,
                    organization_id=organization_id,
                    artifact_type="paper",
                    source_id=paper_run_id,
                    reference=artifact_reference,
                    metadata={"asof_date": asof_date},
                )
                if artifact_record:
                    summary = {**summary, "artifact_id": artifact_record["id"]}
            self.metadata_store.save_experiment_run(
                experiment_id=paper_run_id,
                kind="paper",
                summary=json_ready(summary),
                organization_id=organization_id,
                artifact_dir=summary.get("artifact_dir"),
            )
            latest_payload = self.build_dashboard_payload(
                organization_id=organization_id,
            )
            completed_dates.append(asof_date)
        latest_payload = latest_payload or self.build_dashboard_payload(organization_id=organization_id)
        SaaSService(self.settings).sync_paper_agents_from_dashboard(
            organization_id=organization_id,
            payload=latest_payload,
        )
        latest_payload["run_sequence"] = {
            "dates": completed_dates,
            "count": len(completed_dates),
            "deployment_config_path": str(config_path),
        }
        return latest_payload


class SentimentService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.metadata_store = build_metadata_store(settings)
        self.artifact_storage = build_artifact_storage(settings)

    @property
    def default_output_dir(self) -> Path:
        return self.settings.sentiment_cache_dir / "shadow"

    def _output_dir(self, output_dir: str | Path | None = None, *, organization_id: str | None = None) -> Path:
        if output_dir is None and organization_id:
            return self.settings.sentiment_cache_dir / "organizations" / organization_id / "shadow"
        return Path(output_dir) if output_dir else self.default_output_dir

    @staticmethod
    def _read_frame(path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame()
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path)
        return pd.read_parquet(path)

    @staticmethod
    def _naive_timestamp(series: pd.Series) -> pd.Series:
        return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_convert(None)

    @staticmethod
    def _metadata_tickers(metadata: dict[str, Any]) -> list[str]:
        tickers = metadata.get("tickers", [])
        if isinstance(tickers, str):
            tickers = [tickers]
        if not isinstance(tickers, list):
            return []
        return [str(ticker).upper() for ticker in tickers if str(ticker).strip()]

    @staticmethod
    def _sort_headline_frame(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        if "timestamp" in frame.columns:
            return frame.sort_values("timestamp", ascending=False).reset_index(drop=True)
        return frame.reset_index(drop=True)

    @classmethod
    def _headline_preview_frame(cls, frame: pd.DataFrame, metadata: dict[str, Any]) -> pd.DataFrame:
        """Return a bounded UI preview while always surfacing the latest run's tickers."""
        ordered = cls._sort_headline_frame(frame)
        if len(ordered) <= SENTIMENT_TABLE_ROW_LIMIT:
            return ordered

        preview_parts = [ordered.head(SENTIMENT_TABLE_ROW_LIMIT)]
        tickers = cls._metadata_tickers(metadata)
        if tickers and "ticker" in ordered.columns:
            ticker_values = ordered["ticker"].astype(str).str.upper()
            for ticker in tickers:
                preview_parts.append(ordered.loc[ticker_values == ticker].head(SENTIMENT_TABLE_ROWS_PER_LAST_RUN_TICKER))

        preview = pd.concat(preview_parts, axis=0, ignore_index=False)
        preview = preview.loc[~preview.index.duplicated(keep="first")]
        return cls._sort_headline_frame(preview)

    def validate_request(self, request: SentimentAccumulationRequest) -> None:
        if not request.symbols:
            raise ValueError("Choose at least one symbol for sentiment accumulation.")
        if not request.providers:
            raise ValueError("Select at least one sentiment source.")
        selected = {str(provider).lower() for provider in request.providers}
        if self.settings.is_production and request.output_dir is not None:
            raise ValueError("Production sentiment runs use tenant-owned dataset ids; raw output_dir paths are not accepted.")
        if self.settings.is_production and request.news_files:
            raise ValueError("Production sentiment runs cannot read raw local news file paths. Register a dataset and refer to it by dataset_id.")
        if "local" in selected and not request.news_files:
            raise ValueError("Local sentiment accumulation requires at least one news file.")
        validate_relative_path(request.output_dir, settings=self.settings, field_name="output_dir")
        for path in request.news_files:
            validate_relative_path(path, settings=self.settings, field_name="news_files")
        for url in [*request.rss_feed_urls, *request.local_web_search_urls, *request.web_research_urls]:
            validate_url(url, settings=self.settings, field_name="sentiment URL")

    def _headline_provider(self, request: SentimentAccumulationRequest):
        providers = []
        selected = list(dict.fromkeys(provider.lower() for provider in request.providers))
        if not selected:
            selected = ["rss"]

        if "rss" in selected:
            providers.append(RSSHeadlineProvider(feed_urls=request.rss_feed_urls or None))

        if "local_web" in selected:
            providers.append(
                LocalWebSearchHeadlineProvider(
                    feed_urls=request.local_web_search_urls or None,
                    source_domains=request.web_research_domains,
                    direct_urls=request.web_research_urls,
                    query_terms=request.web_research_query_terms,
                    cache_dir=self.settings.sentiment_cache_dir / "local_web_index",
                    max_results_per_ticker=request.web_research_max_articles,
                    max_crawl_pages_per_source=request.local_web_max_pages_per_source,
                    refresh_minutes=request.local_web_refresh_minutes,
                    fetch_article_text=request.web_research_fetch_article_text,
                )
            )

        if "web" in selected:
            providers.append(
                WebResearchHeadlineProvider(
                    domains=request.web_research_domains,
                    research_urls=request.web_research_urls,
                    query_terms=request.web_research_query_terms,
                    max_articles_per_ticker=request.web_research_max_articles,
                    fetch_article_text=request.web_research_fetch_article_text,
                )
            )

        if "local" in selected:
            if not request.news_files:
                raise ValueError("Local sentiment accumulation requires at least one news file.")
            providers.extend(LocalNewsFileProvider(path) for path in request.news_files)

        if "newsapi" in selected:
            api_key = request.newsapi_api_key or os.getenv("NEWSAPI_API_KEY")
            if not api_key:
                raise ValueError("NewsAPI accumulation requires a NewsAPI key or NEWSAPI_API_KEY.")
            providers.append(NewsAPIHeadlineProvider(api_key=api_key))

        if "alphavantage" in selected:
            api_key = request.alphavantage_api_key or os.getenv("ALPHAVANTAGE_API_KEY")
            if not api_key:
                raise ValueError("Alpha Vantage accumulation requires an Alpha Vantage key or ALPHAVANTAGE_API_KEY.")
            providers.append(AlphaVantageNewsProvider(api_key=api_key))

        if "benzinga" in selected:
            api_key = request.benzinga_api_key or os.getenv("BENZINGA_API_KEY")
            if not api_key:
                raise ValueError("Benzinga accumulation requires a Benzinga key or BENZINGA_API_KEY.")
            providers.append(BenzingaNewsProvider(api_key=api_key))

        if not providers:
            raise ValueError("Choose at least one sentiment source.")
        return CompositeHeadlineProvider(providers, skip_errors=True)

    @staticmethod
    def _sentiment_model(use_finbert: bool, local_finbert_only: bool):
        if use_finbert:
            try:
                model = FinBERTSentimentModel(local_files_only=local_finbert_only)
                model.score_texts(["earnings beat expectations"])
                return model
            except Exception:
                return build_best_available_sentiment_model()
        return build_best_available_sentiment_model()

    @staticmethod
    def _accumulation_warnings(
        request: SentimentAccumulationRequest,
        result,
        provider_errors: list[str] | None = None,
    ) -> list[str]:
        selected = {provider.lower() for provider in request.providers}
        warnings: list[str] = list(dict.fromkeys(provider_errors or []))
        if result.fetched_headlines == 0:
            warnings.append(
                "No new headlines were fetched for the selected symbols and dates. "
                "If the tables still show data, it is from the existing sentiment cache rather than this run."
            )
            if "rss" in selected:
                warnings.append(
                    "No RSS headlines matched this date range. RSS feeds are live feeds, not historical archives; "
                    "use a recent window such as the last 7-30 days for Yahoo Finance RSS. "
                    "For FX pairs such as EURUSD, the backend queries Yahoo aliases such as EURUSD=X."
                )
            if "web" in selected:
                warnings.append(
                    "No lightweight web-research headlines matched. GDELT DOC discovery mainly covers a rolling recent window; "
                    "try the last 30-90 days, add source domains such as reuters.com or cnbc.com, or provide direct web URLs."
                )
            if "local_web" in selected:
                warnings.append(
                    "No local web-search headlines matched. This source searches the local RSS/page cache instead of a hosted search API; "
                    "add RSS/Atom feed URLs, use a recent date range, or provide direct web URLs for specific pages."
                )
            if "local" in selected:
                warnings.append("No local news-file rows matched the selected symbols and dates.")
            if selected & {"newsapi", "alphavantage", "benzinga"}:
                warnings.append("No API news rows matched the selected symbols and dates; check provider limits, symbols, and credentials.")
        elif result.daily_rows == 0:
            warnings.append("Headlines were fetched, but no daily sentiment rows were produced. Check timestamp and ticker columns.")
        return warnings

    @staticmethod
    def _write_accumulation_metadata(result, request: SentimentAccumulationRequest, warnings: list[str]) -> None:
        metadata_path = Path(result.metadata_path)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        metadata.update(
            {
                "providers": [str(provider).lower() for provider in request.providers],
                "rss_feed_urls": [str(url) for url in request.rss_feed_urls],
                "local_web_search_urls": [str(url) for url in request.local_web_search_urls],
                "local_web_refresh_minutes": request.local_web_refresh_minutes,
                "local_web_max_pages_per_source": request.local_web_max_pages_per_source,
                "web_research_urls": [str(url) for url in request.web_research_urls],
                "web_research_domains": [str(domain) for domain in request.web_research_domains],
                "web_research_query_terms": request.web_research_query_terms,
                "web_research_max_articles": request.web_research_max_articles,
                "web_research_fetch_article_text": request.web_research_fetch_article_text,
                "news_files": [str(path) for path in request.news_files],
                "warnings": warnings,
            }
        )
        metadata_path.write_text(json.dumps(json_ready(metadata), indent=2), encoding="utf-8")

    def accumulate(
        self,
        request: SentimentAccumulationRequest,
        *,
        organization_id: str,
        progress: Callable[[str, str, float], None] | None = None,
    ) -> dict[str, Any]:
        def report(stage: str, message: str, value: float) -> None:
            if progress is not None:
                progress(stage, message, float(max(0.0, min(value, 0.98))))

        self.validate_request(request)
        report("preparing_sources", "Preparing selected headline providers and checking credentials.", 0.08)
        output_dir = self._output_dir(request.output_dir, organization_id=organization_id)
        provider = self._headline_provider(request)
        report("loading_model", "Loading the sentiment scorer. FinBERT is skipped when the lightweight option is selected.", 0.18)
        model = self._sentiment_model(request.use_finbert, request.local_finbert_only)
        report("accumulating_sentiment", "Fetching, deduplicating, scoring, and aggregating sentiment data.", 0.25)
        result = ShadowSentimentAccumulator(
            headline_provider=provider,
            sentiment_model=model,
            output_dir=output_dir,
        ).run(
            tickers=request.symbols,
            start=request.start,
            end=request.end,
            progress=lambda stage, message, value: report(stage, message, 0.25 + 0.55 * value),
        )
        provider_errors = list(getattr(provider, "last_errors", []))
        warnings = self._accumulation_warnings(request, result, provider_errors=provider_errors)
        report("writing_metadata", "Writing run metadata, provider warnings, and dataset lineage.", 0.84)
        self._write_accumulation_metadata(result, request, warnings)
        report("registering_dataset", "Registering the sentiment dataset in the workspace metadata store.", 0.90)
        daily_path = Path(result.output_dir) / "daily_sentiment.parquet"
        dataset_id = self.metadata_store.stable_id("dst", f"{organization_id}:{daily_path}:sentiment_daily")
        artifact_reference = _publish_directory_reference(
            self.artifact_storage,
            source_dir=result.output_dir,
            organization_id=organization_id,
            artifact_type="sentiment",
            artifact_id=dataset_id,
        )
        artifact_record = _record_artifact(
            self.metadata_store,
            organization_id=organization_id,
            artifact_type="sentiment",
            source_id=dataset_id,
            reference=artifact_reference,
            metadata={"symbols": list(request.symbols), "providers": list(request.providers)},
        )
        self.metadata_store.upsert_dataset(
            organization_id=organization_id,
            payload={
                "id": dataset_id,
                "name": "Shadow Daily Sentiment",
                "kind": "sentiment_daily",
                "path": artifact_reference["uri"] if self.settings.is_production and artifact_reference else str(daily_path),
                "provider": {
                    "providers": list(request.providers),
                    "symbols": list(request.symbols),
                    "start": request.start,
                    "end": request.end,
                    "web_research_domains": list(request.web_research_domains),
                    "web_research_urls": list(request.web_research_urls),
                    "local_web_search_urls": list(request.local_web_search_urls),
                    "local_web_refresh_minutes": request.local_web_refresh_minutes,
                    "local_web_max_pages_per_source": request.local_web_max_pages_per_source,
                    "web_research_model": "lightweight_extractive_v1" if {"web", "local_web"} & {provider.lower() for provider in request.providers} else None,
                    "artifact": artifact_reference,
                    "artifact_id": artifact_record["id"] if artifact_record else None,
                },
                "schema": {"columns": list(self._read_frame(daily_path).columns) if daily_path.exists() else []},
                "row_count": int(result.daily_rows),
            },
        )
        report("loading_results", "Loading the latest sentiment tables and charts for the UI.", 0.95)
        return self.dataset(output_dir=None if self.settings.is_production else result.output_dir, organization_id=organization_id, dataset_id=dataset_id)

    def _dataset_output_path(self, *, dataset_id: str | None, output_dir: str | Path | None, organization_id: str | None) -> Path:
        if dataset_id and organization_id:
            dataset = self.metadata_store.get_dataset(organization_id=organization_id, dataset_id=dataset_id)
            if dataset is None:
                raise ValueError(f"Sentiment dataset not found: {dataset_id}")
            provider = dataset.get("provider") if isinstance(dataset.get("provider"), dict) else {}
            artifact_id = provider.get("artifact_id")
            artifact = self.metadata_store.get_artifact(organization_id=organization_id, artifact_id=str(artifact_id)) if artifact_id else None
            reference_payload = artifact or provider.get("artifact")
            if isinstance(reference_payload, dict):
                reference = ArtifactReference(
                    provider=str(reference_payload.get("provider") or "local"),
                    key=str(reference_payload.get("storage_key") or reference_payload.get("key") or ""),
                    uri=str(reference_payload.get("uri") or ""),
                    file_count=int(reference_payload.get("file_count", 0) or 0),
                    byte_count=int(reference_payload.get("byte_count", 0) or 0),
                )
                materialized = self.settings.sentiment_cache_dir / "materialized" / organization_id / dataset_id
                return self.artifact_storage.materialize_directory(reference, materialized)
            if not self.settings.is_production and dataset.get("path"):
                path = Path(str(dataset["path"]))
                return path.parent if path.suffix else path
            raise ValueError(f"Sentiment dataset has no readable artifact: {dataset_id}")
        if self.settings.is_production and organization_id:
            datasets = [
                dataset
                for dataset in self.metadata_store.list_datasets(organization_id=organization_id)
                if str(dataset.get("kind")) == "sentiment_daily"
            ]
            if datasets:
                return self._dataset_output_path(dataset_id=str(datasets[0]["id"]), output_dir=None, organization_id=organization_id)
        validate_relative_path(output_dir, settings=self.settings, field_name="output_dir")
        return self._output_dir(output_dir, organization_id=organization_id)

    def dataset(self, output_dir: str | Path | None = None, *, organization_id: str | None = None, dataset_id: str | None = None) -> dict[str, Any]:
        if self.settings.is_production and output_dir is not None:
            raise ValueError("Production sentiment reads use dataset_id, not raw output_dir paths.")
        output_path = self._dataset_output_path(dataset_id=dataset_id, output_dir=output_dir, organization_id=organization_id)
        raw_path = output_path / "raw_headlines.parquet"
        scored_path = output_path / "scored_headlines.parquet"
        daily_path = output_path / "daily_sentiment.parquet"
        metadata_path = output_path / "metadata.json"

        raw = self._read_frame(raw_path)
        scored = self._read_frame(scored_path)
        daily = self._read_frame(daily_path)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}

        if not raw.empty and "timestamp" in raw.columns:
            raw["timestamp"] = self._naive_timestamp(raw["timestamp"])
        if not scored.empty and "timestamp" in scored.columns:
            scored["timestamp"] = self._naive_timestamp(scored["timestamp"])
        if not daily.empty and "date" in daily.columns:
            daily["date"] = self._naive_timestamp(daily["date"]).dt.normalize()

        daily_points: list[dict[str, Any]] = []
        ticker_summary: list[dict[str, Any]] = []
        source_summary: list[dict[str, Any]] = []

        if not daily.empty:
            daily = daily.sort_values(["date", "ticker"]).reset_index(drop=True)
            daily_points = json_ready(daily.tail(500).to_dict("records"))
            grouped = daily.groupby("ticker", sort=True)
            ticker_summary = json_ready(
                [
                    {
                        "ticker": str(ticker),
                        "article_count": float(group["article_count"].sum()),
                        "avg_sentiment": float(group["sentiment_score"].mean()),
                        "avg_confidence": float(group["confidence"].mean()),
                        "latest_sentiment": float(group.sort_values("date").iloc[-1]["sentiment_score"]),
                    }
                    for ticker, group in grouped
                ]
            )

        if not raw.empty:
            source_col = "source" if "source" in raw.columns else "provider_name"
            if source_col in raw.columns:
                source_summary = json_ready(
                    raw.groupby(source_col, dropna=False)
                    .size()
                    .reset_index(name="headline_count")
                    .rename(columns={source_col: "source"})
                    .sort_values("headline_count", ascending=False)
                    .to_dict("records")
                )

        headlines = self._headline_preview_frame(raw, metadata)
        scored_tail = self._headline_preview_frame(scored, metadata)

        warnings = metadata.get("warnings", [])
        if not isinstance(warnings, list):
            warnings = [str(warnings)]
        if not warnings and raw.empty:
            warnings = ["No headlines are stored in this sentiment dataset yet."]

        paths_payload = {
            "output_dir": str(output_path),
            "raw_headlines_path": str(raw_path),
            "scored_headlines_path": str(scored_path),
            "daily_sentiment_path": str(daily_path),
            "metadata_path": str(metadata_path),
        }
        if self.settings.is_production:
            paths_payload = {
                "output_dir": None,
                "raw_headlines_path": None,
                "scored_headlines_path": None,
                "daily_sentiment_path": None,
                "metadata_path": None,
            }

        return {
            "dataset_id": dataset_id or self.metadata_store.stable_id("dst", f"{organization_id or 'local'}:{daily_path}:sentiment_daily"),
            **paths_payload,
            "metadata": json_ready(metadata),
            "warnings": json_ready(warnings),
            "summary": {
                "headline_count": int(len(raw)),
                "scored_headline_count": int(len(scored)),
                "returned_headline_count": int(len(headlines)),
                "returned_scored_headline_count": int(len(scored_tail)),
                "table_row_limit": SENTIMENT_TABLE_ROW_LIMIT,
                "table_rows_per_last_run_ticker": SENTIMENT_TABLE_ROWS_PER_LAST_RUN_TICKER,
                "headline_rows_truncated": bool(len(headlines) < len(raw)),
                "scored_headline_rows_truncated": bool(len(scored_tail) < len(scored)),
                "daily_rows": int(len(daily)),
                "ticker_count": int(daily["ticker"].nunique()) if not daily.empty and "ticker" in daily.columns else 0,
                "source_count": int(raw["source"].nunique()) if not raw.empty and "source" in raw.columns else 0,
            },
            "daily_points": daily_points,
            "ticker_summary": ticker_summary,
            "source_summary": source_summary,
            "headlines": json_ready(headlines.to_dict("records")),
            "scored_headlines": json_ready(scored_tail.to_dict("records")),
        }


@dataclass
class SentimentAccumulationJob:
    id: str
    status: str
    request: dict[str, Any]
    created_at_utc: str
    updated_at_utc: str
    organization_id: str | None = None
    user_id: str | None = None
    progress: float = 0.0
    stage: str = "queued"
    message: str = "Waiting for a sentiment worker."
    warnings: list[str] = field(default_factory=list)
    started_at_utc: str | None = None
    finished_at_utc: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "request": self.request,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "progress": self.progress,
            "stage": self.stage,
            "message": self.message,
            "warnings": self.warnings,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "result": self.result,
            "error": self.error,
        }


class SentimentJobRunner:
    def __init__(self, settings: BackendSettings, *, max_workers: int = 1, max_history: int = 50, mark_interrupted_on_load: bool = True) -> None:
        self.settings = settings
        self.max_history = max_history
        self.mark_interrupted_on_load = mark_interrupted_on_load
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="sentiment-agent") if settings.enable_in_process_jobs else None
        self.lock = Lock()
        self.jobs: dict[str, SentimentAccumulationJob] = {}
        self.jobs_dir = settings.sentiment_job_state_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_store = build_metadata_store(settings)
        self.artifact_storage = build_artifact_storage(settings)
        self._load_jobs()

    def submit(self, request: SentimentAccumulationRequest, *, organization_id: str, user_id: str | None = None) -> dict[str, Any]:
        SentimentService(self.settings).validate_request(request)
        now = _utc_now_iso()
        job = SentimentAccumulationJob(
            id=uuid4().hex,
            status="queued",
            request=json_ready(request.model_dump(mode="json")),
            created_at_utc=now,
            updated_at_utc=now,
            organization_id=organization_id,
            user_id=user_id,
            progress=0.02,
            stage="queued",
            message="Queued locally. A sentiment worker will start fetching headlines next.",
        )
        with self.lock:
            self.jobs[job.id] = job
            self._save_locked(job)
            self._trim_locked()

        if self.settings.enable_in_process_jobs and self.executor is not None:
            future = self.executor.submit(self._run_job, job.id, request, organization_id)
            future.add_done_callback(lambda completed: self._finalize_unhandled(job.id, completed))
        else:
            queue_payload = enqueue_quant_job(self.settings, kind="sentiment", job_id=job.id)
            self._set_status(job.id, "queued", message=f"Queued in Redis/RQ ({queue_payload['queue']}). Waiting for worker heartbeat.")
        return job.to_dict()

    def list_jobs(self, *, organization_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return [
                job.to_dict()
                for job in sorted(self.jobs.values(), key=lambda item: item.created_at_utc, reverse=True)
                if job.organization_id == organization_id
            ]

    def get_job(self, job_id: str, *, organization_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is not None and job.organization_id == organization_id:
                return job.to_dict()
        stored = self.metadata_store.get_job(kind="sentiment", job_id=job_id, organization_id=organization_id)
        if stored is not None:
            return stored
        path = self.jobs_dir / f"{job_id}.json"
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("organization_id") == organization_id:
                    return payload
            except Exception:
                pass
        return None

    def _set_status(self, job_id: str, status: str, **updates: Any) -> None:
        now = _utc_now_iso()
        with self.lock:
            job = self.jobs[job_id]
            job.status = status
            job.updated_at_utc = now
            for key, value in updates.items():
                setattr(job, key, value)
            self._save_locked(job)

    def _run_job(self, job_id: str, request: SentimentAccumulationRequest, organization_id: str) -> None:
        def progress(stage: str, message: str, value: float) -> None:
            self._set_status(
                job_id,
                "running",
                stage=stage,
                message=message,
                progress=float(max(0.0, min(value, 0.98))),
            )

        self._set_status(
            job_id,
            "running",
            started_at_utc=_utc_now_iso(),
            stage="starting",
            message="Worker started. Preparing sources, cache paths, and model settings.",
            progress=0.06,
        )
        try:
            result = SentimentService(self.settings).accumulate(request, organization_id=organization_id, progress=progress)
            summary = result.get("summary", {})
            warnings = [str(warning) for warning in result.get("warnings", [])]
            fetched = result.get("metadata", {}).get("fetched_headlines")
            daily_rows = summary.get("daily_rows")
            self._set_status(
                job_id,
                "completed",
                result=result,
                warnings=warnings,
                finished_at_utc=_utc_now_iso(),
                progress=1.0,
                stage="completed",
                message=f"Sentiment accumulation completed. Fetched {fetched if fetched is not None else 'n/a'} new headlines and built {daily_rows if daily_rows is not None else 'n/a'} daily rows.",
            )
        except Exception as exc:  # pragma: no cover - exercised through API tests
            self._set_status(
                job_id,
                "failed",
                error=str(exc),
                finished_at_utc=_utc_now_iso(),
                progress=1.0,
                stage="failed",
                message="Sentiment accumulation failed. Review the error, sources, credentials, and date range.",
            )

    def _finalize_unhandled(self, job_id: str, future: Future[None]) -> None:
        exception = future.exception()
        if exception is None:
            return
        self._set_status(
            job_id,
            "failed",
            error=str(exception),
            finished_at_utc=_utc_now_iso(),
            progress=1.0,
            stage="failed",
            message="The sentiment worker crashed before returning a result.",
        )

    def _save_locked(self, job: SentimentAccumulationJob) -> None:
        payload = json_ready(job.to_dict())
        path = self.jobs_dir / f"{job.id}.json"
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        self.metadata_store.upsert_job(kind="sentiment", payload=payload)

    def _load_jobs(self) -> None:
        for payload in self.metadata_store.list_jobs(kind="sentiment"):
            try:
                job = SentimentAccumulationJob(**payload)
            except Exception:
                continue
            self._load_job_instance(job)

        for path in sorted(self.jobs_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                job = SentimentAccumulationJob(**payload)
            except Exception:
                continue
            if job.id in self.jobs:
                continue
            self._load_job_instance(job)

    def _load_job_instance(self, job: SentimentAccumulationJob) -> None:
        changed = False
        if self.mark_interrupted_on_load and job.status in {"queued", "running"}:
            job.status = "interrupted"
            job.stage = "interrupted"
            job.progress = 1.0
            job.message = "The backend restarted before this sentiment run finished. Please rerun it."
            job.finished_at_utc = job.finished_at_utc or _utc_now_iso()
            changed = True
        self.jobs[job.id] = job
        if changed:
            self._save_locked(job)
        else:
            self.metadata_store.upsert_job(kind="sentiment", payload=json_ready(job.to_dict()))

    def _trim_locked(self) -> None:
        if len(self.jobs) <= self.max_history:
            return
        removable = sorted(self.jobs.values(), key=lambda item: item.created_at_utc)[: len(self.jobs) - self.max_history]
        for job in removable:
            self.jobs.pop(job.id, None)
            try:
                (self.jobs_dir / f"{job.id}.json").unlink()
            except FileNotFoundError:
                pass
            self.metadata_store.delete_job(kind="sentiment", job_id=job.id)
