from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
import json
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

import pandas as pd

from ..data.market import CachedParquetProvider, MarketDataProvider, YahooFinanceProvider
from ..engines.backtesting import json_ready
from ..platform import build_metadata_store
from ..research.market_research_agents import (
    DataProvenance,
    DemoMarketResearchDataProvider,
    MarketResearchContext,
    MarketResearchInput,
    MarketResearchOrchestrator,
    MarketResearchReport,
    NewsItem,
    PriceBar,
    ResearchHorizon,
)
from .config import BackendSettings
from .job_queue import enqueue_quant_job
from .schemas import MarketResearchRunRequest
from .storage import build_artifact_storage


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class BackendMarketResearchDataProvider:
    def __init__(self, settings: BackendSettings, market_data_provider: MarketDataProvider | None = None) -> None:
        self.settings = settings
        self.market_data_provider = market_data_provider
        self.demo_provider = DemoMarketResearchDataProvider()

    def collect(self, request: MarketResearchInput) -> MarketResearchContext:
        if self.settings.market_research_data_provider != "cached_yahoo":
            context = self.demo_provider.collect(request)
            context.provider_metadata["backend_data_provider"] = self.settings.market_research_data_provider
            return context

        try:
            return self._collect_cached_yahoo(request)
        except Exception as exc:
            context = self.demo_provider.collect(request)
            warning = f"Cached Yahoo market data was unavailable, so demo data was used: {exc}"
            context.warnings.append(warning)
            context.data_quality_notes.append(warning)
            context.provider_metadata["backend_data_provider"] = "demo_fallback"
            return context

    def _provider(self) -> MarketDataProvider:
        if self.market_data_provider is None:
            self.market_data_provider = CachedParquetProvider(
                upstream=YahooFinanceProvider(tz_cache_dir=self.settings.price_cache_dir / "yfinance_tz_cache"),
                cache_dir=self.settings.price_cache_dir,
            )
        return self.market_data_provider

    @staticmethod
    def _lookback_days(horizon: ResearchHorizon | str) -> int:
        value = str(horizon)
        if value == ResearchHorizon.INTRADAY.value:
            return 45
        if value == ResearchHorizon.LONG_TERM.value:
            return 540
        return 180

    def _collect_cached_yahoo(self, request: MarketResearchInput) -> MarketResearchContext:
        asof = date.fromisoformat(request.analysis_date)
        start = asof - timedelta(days=self._lookback_days(request.horizon))
        end = asof + timedelta(days=1)
        prices = self._provider().get_close_prices(
            [request.ticker],
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
        )
        if prices.empty or request.ticker not in prices.columns:
            raise ValueError(f"No close-price rows returned for {request.ticker}.")
        series = pd.to_numeric(prices[request.ticker], errors="coerce").dropna().sort_index()
        if series.empty:
            raise ValueError(f"Close-price rows for {request.ticker} were empty after cleaning.")
        bars = [
            PriceBar(date=pd.Timestamp(index).strftime("%Y-%m-%d"), close=round(float(value), 6))
            for index, value in series.items()
        ]
        warnings = [
            "News/sentiment and fundamental providers are not configured for this market-research run.",
        ]
        return MarketResearchContext(
            ticker=request.ticker,
            analysis_date=request.analysis_date,
            horizon=request.horizon,
            price_history=bars,
            news=[
                NewsItem(
                    timestamp=f"{request.analysis_date}T00:00:00Z",
                    headline="No news provider configured for this run.",
                    source="not_configured",
                    sentiment_score=None,
                )
            ],
            provenance=[
                DataProvenance(
                    source="price_history",
                    provider="CachedParquetProvider/YahooFinanceProvider",
                    detail=f"Daily close prices from {start.isoformat()} through {request.analysis_date}.",
                ),
                DataProvenance(
                    source="news",
                    provider="not_configured",
                    detail="No news provider was called by the v1 market-research data collector.",
                ),
                DataProvenance(
                    source="fundamentals",
                    provider="not_configured",
                    detail="No fundamentals provider was called by the v1 market-research data collector.",
                ),
            ],
            data_quality_notes=warnings,
            warnings=warnings,
            provider_metadata={
                "backend_data_provider": "cached_yahoo",
                "price_rows": len(bars),
                "model": request.model,
            },
        )


class MarketResearchService:
    def __init__(
        self,
        settings: BackendSettings,
        *,
        data_provider: BackendMarketResearchDataProvider | None = None,
    ) -> None:
        self.settings = settings
        self.data_provider = data_provider or BackendMarketResearchDataProvider(settings)

    def validate_request(self, request: MarketResearchRunRequest) -> MarketResearchInput:
        return self._input(request)

    def _input(self, request: MarketResearchRunRequest) -> MarketResearchInput:
        return MarketResearchInput(
            ticker=request.ticker,
            analysis_date=request.analysis_date or date.today().isoformat(),
            horizon=ResearchHorizon(str(request.horizon)),
            provider=request.provider or "mock",
            model=request.model or "mock-research-v1",
            options=request.options or {},
        )

    def generate_report(self, request: MarketResearchRunRequest) -> MarketResearchReport:
        research_input = self._input(request)
        context = self.data_provider.collect(research_input)
        orchestrator = MarketResearchOrchestrator(
            per_agent_timeout_seconds=self.settings.market_research_agent_timeout_seconds,
        )
        return orchestrator.run(context)


@dataclass
class MarketResearchJob:
    id: str
    status: str
    request: dict[str, Any]
    created_at_utc: str
    updated_at_utc: str
    organization_id: str | None = None
    user_id: str | None = None
    progress: float = 0.0
    stage: str = "queued"
    message: str = "Waiting for a market research worker."
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


class MarketResearchJobRunner:
    def __init__(self, settings: BackendSettings, *, max_workers: int = 1, max_history: int = 50, mark_interrupted_on_load: bool = True) -> None:
        self.settings = settings
        self.max_history = max_history
        self.mark_interrupted_on_load = mark_interrupted_on_load
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="market-research") if settings.enable_in_process_jobs else None
        self.lock = Lock()
        self.jobs: dict[str, MarketResearchJob] = {}
        self.jobs_dir = settings.market_research_job_state_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.report_root = settings.market_research_artifact_root
        self.report_root.mkdir(parents=True, exist_ok=True)
        self.metadata_store = build_metadata_store(settings)
        self.artifact_storage = build_artifact_storage(settings)
        self._load_jobs()

    def submit(self, request: MarketResearchRunRequest, *, organization_id: str, user_id: str | None = None) -> dict[str, Any]:
        MarketResearchService(self.settings).validate_request(request)
        now = _utc_now_iso()
        job = MarketResearchJob(
            id=uuid4().hex,
            status="queued",
            request=json_ready(request.model_dump(mode="json")),
            created_at_utc=now,
            updated_at_utc=now,
            organization_id=organization_id,
            user_id=user_id,
            progress=0.02,
            stage="queued",
            message="Queued locally. The research committee will collect data and run analyst agents.",
        )
        with self.lock:
            self.jobs[job.id] = job
            self._save_locked(job)
            self._trim_locked()

        if self.settings.enable_in_process_jobs and self.executor is not None:
            future = self.executor.submit(self._run_job, job.id, request, organization_id)
            future.add_done_callback(lambda completed: self._finalize_unhandled(job.id, completed))
        else:
            queue_payload = enqueue_quant_job(self.settings, kind="market_research", job_id=job.id)
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
            return job.to_dict()

    def _set_status(self, job_id: str, status: str, **updates: Any) -> None:
        now = _utc_now_iso()
        with self.lock:
            job = self.jobs[job_id]
            job.status = status
            job.updated_at_utc = now
            for key, value in updates.items():
                setattr(job, key, value)
            self._save_locked(job)

    def _run_job(self, job_id: str, request: MarketResearchRunRequest, organization_id: str) -> None:
        self._set_status(
            job_id,
            "running",
            started_at_utc=_utc_now_iso(),
            progress=0.12,
            stage="collecting_data",
            message="Collecting market research context and provenance.",
        )
        try:
            report = MarketResearchService(self.settings).generate_report(request)
            result = report.model_dump(mode="json")
            self._set_status(
                job_id,
                "running",
                progress=0.86,
                stage="persisting_report",
                message="Research committee finished. Persisting the report artifact.",
                warnings=result.get("warnings", []),
            )
            report_path = self._write_report(job_id, result)
            reference = self.artifact_storage.publish_file(
                report_path,
                organization_id=organization_id,
                artifact_type="market_research",
                artifact_id=job_id,
            )
            artifact_record = self.metadata_store.upsert_artifact(
                organization_id=organization_id,
                payload={
                    "artifact_type": "market_research",
                    "source_id": job_id,
                    "provider": reference.provider,
                    "key": reference.key,
                    "uri": reference.uri,
                    "file_count": reference.file_count,
                    "byte_count": reference.byte_count,
                    "metadata": {
                        "ticker": result["ticker"],
                        "analysis_date": result["analysis_date"],
                        "decision": result["decision"],
                        "confidence": result["confidence"],
                        "agent_versions": result.get("metadata", {}).get("agent_versions", {}),
                        "prompt_version": result.get("metadata", {}).get("prompt_version"),
                        "warnings": result.get("warnings", []),
                    },
                },
            )
            result["artifact"] = {
                "provider": reference.provider,
                "key": reference.key,
                "uri": reference.uri,
                "file_count": reference.file_count,
                "byte_count": reference.byte_count,
            }
            result["artifact_id"] = artifact_record["id"]
            result["report_path"] = str(report_path)
            self._set_status(
                job_id,
                "completed",
                result=json_ready(result),
                warnings=result.get("warnings", []),
                finished_at_utc=_utc_now_iso(),
                progress=1.0,
                stage="completed",
                message=f"Market research completed for {result['ticker']} with simulated decision {result['decision']}.",
            )
        except Exception as exc:
            self._set_status(
                job_id,
                "failed",
                error=str(exc),
                finished_at_utc=_utc_now_iso(),
                progress=1.0,
                stage="failed",
                message="Market research failed. Review the error and request inputs.",
            )

    def _write_report(self, job_id: str, result: dict[str, Any]) -> Path:
        output_dir = self.report_root / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "report.json"
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(json_ready(result), indent=2), encoding="utf-8")
        tmp_path.replace(path)
        return path

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
            message="The market research worker crashed before returning a report.",
        )

    def _save_locked(self, job: MarketResearchJob) -> None:
        payload = json_ready(job.to_dict())
        path = self.jobs_dir / f"{job.id}.json"
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(path)
        self.metadata_store.upsert_job(kind="market_research", payload=payload)

    def _load_jobs(self) -> None:
        for payload in self.metadata_store.list_jobs(kind="market_research"):
            try:
                job = MarketResearchJob(**payload)
            except Exception:
                continue
            self._load_job_instance(job)

        for path in sorted(self.jobs_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                job = MarketResearchJob(**payload)
            except Exception:
                continue
            if job.id in self.jobs:
                continue
            self._load_job_instance(job)

    def _load_job_instance(self, job: MarketResearchJob) -> None:
        changed = False
        if self.mark_interrupted_on_load and job.status in {"queued", "running"}:
            job.status = "interrupted"
            job.stage = "interrupted"
            job.progress = 1.0
            job.message = "The backend restarted before this market research job finished. Please rerun it."
            job.finished_at_utc = job.finished_at_utc or _utc_now_iso()
            changed = True
        self.jobs[job.id] = job
        if changed:
            self._save_locked(job)
        else:
            self.metadata_store.upsert_job(kind="market_research", payload=json_ready(job.to_dict()))

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
            self.metadata_store.delete_job(kind="market_research", job_id=job.id)
