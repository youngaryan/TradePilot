from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _split_env(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class BackendSettings:
    paper_state_dir: Path = Path("artifacts/paper/state")
    paper_artifact_root: Path = Path("artifacts/paper/runs")
    paper_job_state_dir: Path = Path("artifacts/paper/jobs")
    metadata_db_path: Path = Path("artifacts/metadata/app.sqlite3")
    default_paper_config: Path = Path("examples/paper_deployment.sample.json")
    backtest_artifact_root: Path = Path("artifacts/backtests/experiments")
    backtest_job_state_dir: Path = Path("artifacts/backtests/jobs")
    price_cache_dir: Path = Path("data/cache")
    sentiment_cache_dir: Path = Path("data/sentiment_cache")
    sentiment_job_state_dir: Path = Path("artifacts/sentiment/jobs")
    event_cache_dir: Path = Path("data/event_cache")
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    app_base_url: str = "http://127.0.0.1:5173"
    stripe_secret_key: str | None = None
    stripe_pro_price_id: str | None = None
    stripe_success_url: str | None = None
    stripe_cancel_url: str | None = None
    telemetry_enabled: bool = True
    telemetry_sample_rate: float = 1.0
    refresh_interval_hours: int = 24
    refresh_max_attempts: int = 3
    refresh_lock_minutes: int = 30
    refresh_scheduler_enabled: bool = False
    refresh_scheduler_poll_seconds: int = 3600

    @classmethod
    def from_env(cls) -> "BackendSettings":
        return cls(
            paper_state_dir=Path(os.getenv("PAIRS_TRADING_PAPER_STATE_DIR", "artifacts/paper/state")),
            paper_artifact_root=Path(os.getenv("PAIRS_TRADING_PAPER_ARTIFACT_ROOT", "artifacts/paper/runs")),
            paper_job_state_dir=Path(os.getenv("PAIRS_TRADING_PAPER_JOB_STATE_DIR", "artifacts/paper/jobs")),
            metadata_db_path=Path(os.getenv("PAIRS_TRADING_METADATA_DB_PATH", "artifacts/metadata/app.sqlite3")),
            default_paper_config=Path(os.getenv("PAIRS_TRADING_PAPER_CONFIG", "examples/paper_deployment.sample.json")),
            backtest_artifact_root=Path(os.getenv("PAIRS_TRADING_BACKTEST_ARTIFACT_ROOT", "artifacts/backtests/experiments")),
            backtest_job_state_dir=Path(os.getenv("PAIRS_TRADING_BACKTEST_JOB_STATE_DIR", "artifacts/backtests/jobs")),
            price_cache_dir=Path(os.getenv("PAIRS_TRADING_PRICE_CACHE_DIR", "data/cache")),
            sentiment_cache_dir=Path(os.getenv("PAIRS_TRADING_SENTIMENT_CACHE_DIR", "data/sentiment_cache")),
            sentiment_job_state_dir=Path(os.getenv("PAIRS_TRADING_SENTIMENT_JOB_STATE_DIR", "artifacts/sentiment/jobs")),
            event_cache_dir=Path(os.getenv("PAIRS_TRADING_EVENT_CACHE_DIR", "data/event_cache")),
            cors_origins=_split_env(
                os.getenv("PAIRS_TRADING_CORS_ORIGINS"),
                ("http://localhost:5173", "http://127.0.0.1:5173"),
            ),
            app_base_url=os.getenv("PAIRS_TRADING_APP_BASE_URL", "http://127.0.0.1:5173"),
            stripe_secret_key=os.getenv("STRIPE_SECRET_KEY"),
            stripe_pro_price_id=os.getenv("STRIPE_PRO_PRICE_ID"),
            stripe_success_url=os.getenv("STRIPE_SUCCESS_URL"),
            stripe_cancel_url=os.getenv("STRIPE_CANCEL_URL"),
            telemetry_enabled=os.getenv("PAIRS_TRADING_TELEMETRY_ENABLED", "true").lower() not in {"0", "false", "no"},
            telemetry_sample_rate=float(os.getenv("PAIRS_TRADING_TELEMETRY_SAMPLE_RATE", "1.0")),
            refresh_interval_hours=int(os.getenv("PAIRS_TRADING_REFRESH_INTERVAL_HOURS", "24")),
            refresh_max_attempts=int(os.getenv("PAIRS_TRADING_REFRESH_MAX_ATTEMPTS", "3")),
            refresh_lock_minutes=int(os.getenv("PAIRS_TRADING_REFRESH_LOCK_MINUTES", "30")),
            refresh_scheduler_enabled=os.getenv("PAIRS_TRADING_REFRESH_SCHEDULER_ENABLED", "false").lower() in {"1", "true", "yes"},
            refresh_scheduler_poll_seconds=int(os.getenv("PAIRS_TRADING_REFRESH_SCHEDULER_POLL_SECONDS", "3600")),
        )
