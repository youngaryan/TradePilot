from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .dotenv import dotenv_value


def _split_env(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_or_dotenv(name: str, default: str) -> str:
    value = os.getenv(name)
    if value:
        return value.strip()
    dotenv = dotenv_value(name)
    if dotenv:
        return dotenv
    return default


def _env_bool_or_dotenv(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        value = dotenv_value(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BackendSettings:
    app_env: str = "development"
    database_url: str | None = None
    redis_url: str | None = None
    session_secret: str = "dev-session-secret-change-me"
    csrf_secret: str = "dev-csrf-secret-change-me"
    session_ttl_hours: int = 12
    cookie_domain: str | None = None
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    enable_demo_accounts: bool = True
    allow_trial_entitlements: bool = False
    enable_in_process_jobs: bool = True
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    email_from: str = "no-reply@quantops.local"
    edgar_user_agent: str | None = None
    sentry_dsn: str | None = None
    otel_exporter_otlp_endpoint: str | None = None
    max_request_bytes: int = 2_000_000
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_max_requests: int = 120
    allowed_web_domains: tuple[str, ...] = (
        "finance.yahoo.com",
        "feeds.finance.yahoo.com",
        "marketwatch.com",
        "www.marketwatch.com",
        "cnbc.com",
        "www.cnbc.com",
        "sec.gov",
        "www.sec.gov",
    )
    paper_state_dir: Path = Path("artifacts/paper/state")
    paper_artifact_root: Path = Path("artifacts/paper/runs")
    paper_job_state_dir: Path = Path("artifacts/paper/jobs")
    metadata_db_path: Path = Path("artifacts/metadata/app.sqlite3")
    default_paper_config: Path = Path("examples/paper_deployment.sample.json")
    backtest_artifact_root: Path = Path("artifacts/backtests/experiments")
    backtest_job_state_dir: Path = Path("artifacts/backtests/jobs")
    market_research_artifact_root: Path = Path("artifacts/market_research/reports")
    market_research_job_state_dir: Path = Path("artifacts/market_research/jobs")
    market_research_data_provider: str = "demo"
    market_research_agent_timeout_seconds: float = 120.0
    secret_backend: str = "env"
    market_research_llm_provider: str = "mock"
    market_research_llm_model: str = "mock-research-v1"
    market_research_llm_timeout_seconds: float = 120.0
    market_research_llm_max_retries: int = 1
    market_research_llm_max_concurrency: int = 1
    market_research_free_endpoint_timeout_cap_seconds: float = 45.0
    market_research_llm_fail_fast_after_failures: int = 1
    market_research_allow_request_model_override: bool = True
    market_research_ollama_base_url: str = "http://127.0.0.1:11434"
    market_research_openai_api_key_ref: str | None = None
    market_research_anthropic_api_key_ref: str | None = None
    market_research_nvidia_api_key_ref: str | None = None
    market_research_nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
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
    stripe_price_pro_monthly: str | None = None
    stripe_price_team_monthly: str | None = None
    stripe_success_url: str | None = None
    stripe_cancel_url: str | None = None
    stripe_webhook_secret: str | None = None
    telemetry_enabled: bool = True
    telemetry_sample_rate: float = 1.0
    refresh_interval_hours: int = 24
    refresh_max_attempts: int = 3
    refresh_lock_minutes: int = 30
    refresh_scheduler_enabled: bool = False
    refresh_scheduler_poll_seconds: int = 3600

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def effective_stripe_pro_price_id(self) -> str | None:
        return self.stripe_price_pro_monthly or self.stripe_pro_price_id

    @property
    def stripe_plan_price_ids(self) -> dict[str, str | None]:
        return {
            "pro": self.effective_stripe_pro_price_id,
            "team": self.stripe_price_team_monthly,
        }

    def validate_for_startup(self) -> None:
        """Fail closed when the API is explicitly started in production mode."""

        if not self.is_production:
            return
        missing: list[str] = []
        required = {
            "APP_BASE_URL": self.app_base_url,
            "DATABASE_URL": self.database_url,
            "REDIS_URL": self.redis_url,
            "SESSION_SECRET": self.session_secret,
            "CSRF_SECRET": self.csrf_secret,
            "CORS_ORIGINS": ",".join(self.cors_origins),
            "STRIPE_SECRET_KEY": self.stripe_secret_key,
            "STRIPE_WEBHOOK_SECRET": self.stripe_webhook_secret,
            "STRIPE_PRICE_PRO_MONTHLY": self.effective_stripe_pro_price_id,
            "S3_ENDPOINT_URL": self.s3_endpoint_url,
            "S3_BUCKET": self.s3_bucket,
            "S3_ACCESS_KEY_ID": self.s3_access_key_id,
            "S3_SECRET_ACCESS_KEY": self.s3_secret_access_key,
            "SMTP_HOST": self.smtp_host,
            "SMTP_PORT": self.smtp_port,
            "EMAIL_FROM": self.email_from,
        }
        for name, value in required.items():
            if not value or str(value).startswith("dev-") or str(value).endswith("change-me"):
                missing.append(name)
        if "localhost" in self.app_base_url or "127.0.0.1" in self.app_base_url:
            missing.append("APP_BASE_URL(non-localhost)")
        if self.database_url and self.database_url.startswith("sqlite"):
            missing.append("DATABASE_URL(non-sqlite)")
        if self.enable_demo_accounts:
            missing.append("ENABLE_DEMO_ACCOUNTS=false")
        if self.enable_in_process_jobs:
            missing.append("ENABLE_IN_PROCESS_JOBS=false")
        if not self.cookie_secure:
            missing.append("COOKIE_SECURE=true")
        if missing:
            raise RuntimeError(
                "Production startup blocked. Configure secure production settings for: "
                + ", ".join(dict.fromkeys(missing))
            )
        from .llm_config import validate_market_research_llm_settings

        validate_market_research_llm_settings(self)

    @classmethod
    def from_env(cls) -> "BackendSettings":
        app_env = os.getenv("APP_ENV", os.getenv("PAIRS_TRADING_APP_ENV", "development")).lower()
        production = app_env == "production"
        return cls(
            app_env=app_env,
            database_url=os.getenv("DATABASE_URL"),
            redis_url=os.getenv("REDIS_URL"),
            session_secret=os.getenv("SESSION_SECRET", "dev-session-secret-change-me"),
            csrf_secret=os.getenv("CSRF_SECRET", "dev-csrf-secret-change-me"),
            session_ttl_hours=int(os.getenv("SESSION_TTL_HOURS", "12")),
            cookie_domain=os.getenv("COOKIE_DOMAIN") or None,
            cookie_secure=_env_bool("COOKIE_SECURE", production),
            cookie_samesite=os.getenv("COOKIE_SAMESITE", "lax"),
            enable_demo_accounts=_env_bool("ENABLE_DEMO_ACCOUNTS", not production),
            allow_trial_entitlements=_env_bool("ALLOW_TRIAL_ENTITLEMENTS", False),
            enable_in_process_jobs=_env_bool("ENABLE_IN_PROCESS_JOBS", not production),
            s3_endpoint_url=os.getenv("S3_ENDPOINT_URL"),
            s3_bucket=os.getenv("S3_BUCKET"),
            s3_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
            s3_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
            smtp_host=os.getenv("SMTP_HOST"),
            smtp_port=int(os.getenv("SMTP_PORT")) if os.getenv("SMTP_PORT") else None,
            smtp_username=os.getenv("SMTP_USERNAME"),
            smtp_password=os.getenv("SMTP_PASSWORD"),
            smtp_starttls=_env_bool("SMTP_STARTTLS", True),
            email_from=os.getenv("EMAIL_FROM", "no-reply@quantops.local"),
            edgar_user_agent=_env_or_dotenv("SEC_EDGAR_USER_AGENT", "") or _env_or_dotenv("EDGAR_USER_AGENT", "") or None,
            sentry_dsn=os.getenv("SENTRY_DSN"),
            otel_exporter_otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
            max_request_bytes=int(os.getenv("MAX_REQUEST_BYTES", "2000000")),
            rate_limit_enabled=_env_bool("RATE_LIMIT_ENABLED", True),
            rate_limit_window_seconds=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")),
            rate_limit_max_requests=int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "120")),
            allowed_web_domains=_split_env(os.getenv("ALLOWED_WEB_DOMAINS"), cls.allowed_web_domains),
            paper_state_dir=Path(os.getenv("PAIRS_TRADING_PAPER_STATE_DIR", "artifacts/paper/state")),
            paper_artifact_root=Path(os.getenv("PAIRS_TRADING_PAPER_ARTIFACT_ROOT", "artifacts/paper/runs")),
            paper_job_state_dir=Path(os.getenv("PAIRS_TRADING_PAPER_JOB_STATE_DIR", "artifacts/paper/jobs")),
            metadata_db_path=Path(os.getenv("PAIRS_TRADING_METADATA_DB_PATH", "artifacts/metadata/app.sqlite3")),
            default_paper_config=Path(os.getenv("PAIRS_TRADING_PAPER_CONFIG", "examples/paper_deployment.sample.json")),
            backtest_artifact_root=Path(os.getenv("PAIRS_TRADING_BACKTEST_ARTIFACT_ROOT", "artifacts/backtests/experiments")),
            backtest_job_state_dir=Path(os.getenv("PAIRS_TRADING_BACKTEST_JOB_STATE_DIR", "artifacts/backtests/jobs")),
            market_research_artifact_root=Path(os.getenv("PAIRS_TRADING_MARKET_RESEARCH_ARTIFACT_ROOT", "artifacts/market_research/reports")),
            market_research_job_state_dir=Path(os.getenv("PAIRS_TRADING_MARKET_RESEARCH_JOB_STATE_DIR", "artifacts/market_research/jobs")),
            market_research_data_provider=_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_DATA_PROVIDER", "demo").strip().lower() or "demo",
            market_research_agent_timeout_seconds=float(_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_AGENT_TIMEOUT_SECONDS", "120.0")),
            secret_backend=os.getenv("PAIRS_TRADING_SECRET_BACKEND", "env").strip().lower() or "env",
            market_research_llm_provider=_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_LLM_PROVIDER", "mock").strip().lower() or "mock",
            market_research_llm_model=_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_LLM_MODEL", "mock-research-v1").strip() or "mock-research-v1",
            market_research_llm_timeout_seconds=float(_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_LLM_TIMEOUT_SECONDS", "120.0")),
            market_research_llm_max_retries=int(_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_LLM_MAX_RETRIES", "1")),
            market_research_llm_max_concurrency=int(_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_LLM_MAX_CONCURRENCY", "1")),
            market_research_free_endpoint_timeout_cap_seconds=float(
                _env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_FREE_ENDPOINT_TIMEOUT_CAP_SECONDS", "45.0")
            ),
            market_research_llm_fail_fast_after_failures=int(
                _env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_LLM_FAIL_FAST_AFTER_FAILURES", "1")
            ),
            market_research_allow_request_model_override=_env_bool_or_dotenv(
                "PAIRS_TRADING_MARKET_RESEARCH_ALLOW_REQUEST_MODEL_OVERRIDE",
                not production,
            ),
            market_research_ollama_base_url=_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip() or "http://127.0.0.1:11434",
            market_research_openai_api_key_ref=_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_OPENAI_API_KEY_REF", "") or None,
            market_research_anthropic_api_key_ref=_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_ANTHROPIC_API_KEY_REF", "") or None,
            market_research_nvidia_api_key_ref=_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_NVIDIA_API_KEY_REF", "") or None,
            market_research_nvidia_base_url=_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip() or "https://integrate.api.nvidia.com/v1",
            price_cache_dir=Path(os.getenv("PAIRS_TRADING_PRICE_CACHE_DIR", "data/cache")),
            sentiment_cache_dir=Path(os.getenv("PAIRS_TRADING_SENTIMENT_CACHE_DIR", "data/sentiment_cache")),
            sentiment_job_state_dir=Path(os.getenv("PAIRS_TRADING_SENTIMENT_JOB_STATE_DIR", "artifacts/sentiment/jobs")),
            event_cache_dir=Path(os.getenv("PAIRS_TRADING_EVENT_CACHE_DIR", "data/event_cache")),
            cors_origins=_split_env(
                os.getenv("CORS_ORIGINS") or os.getenv("PAIRS_TRADING_CORS_ORIGINS"),
                ("http://localhost:5173", "http://127.0.0.1:5173"),
            ),
            app_base_url=os.getenv("APP_BASE_URL") or os.getenv("PAIRS_TRADING_APP_BASE_URL", "http://127.0.0.1:5173"),
            stripe_secret_key=os.getenv("STRIPE_SECRET_KEY"),
            stripe_pro_price_id=os.getenv("STRIPE_PRO_PRICE_ID"),
            stripe_price_pro_monthly=os.getenv("STRIPE_PRICE_PRO_MONTHLY"),
            stripe_price_team_monthly=os.getenv("STRIPE_PRICE_TEAM_MONTHLY"),
            stripe_success_url=os.getenv("STRIPE_SUCCESS_URL"),
            stripe_cancel_url=os.getenv("STRIPE_CANCEL_URL"),
            stripe_webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET"),
            telemetry_enabled=os.getenv("PAIRS_TRADING_TELEMETRY_ENABLED", "true").lower() not in {"0", "false", "no"},
            telemetry_sample_rate=float(os.getenv("PAIRS_TRADING_TELEMETRY_SAMPLE_RATE", "1.0")),
            refresh_interval_hours=int(os.getenv("PAIRS_TRADING_REFRESH_INTERVAL_HOURS", "24")),
            refresh_max_attempts=int(os.getenv("PAIRS_TRADING_REFRESH_MAX_ATTEMPTS", "3")),
            refresh_lock_minutes=int(os.getenv("PAIRS_TRADING_REFRESH_LOCK_MINUTES", "30")),
            refresh_scheduler_enabled=os.getenv("PAIRS_TRADING_REFRESH_SCHEDULER_ENABLED", "false").lower() in {"1", "true", "yes"},
            refresh_scheduler_poll_seconds=int(os.getenv("PAIRS_TRADING_REFRESH_SCHEDULER_POLL_SECONDS", "3600")),
        )
