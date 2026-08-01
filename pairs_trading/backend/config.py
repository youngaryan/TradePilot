from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .dotenv import dotenv_value


_DOCUMENTED_SECRET_PREFIXES = (
    "dev-",
    "replace-with-",
)
_DOCUMENTED_SECRET_SUFFIXES = (
    "change-me",
)
_MIN_PRODUCTION_SECRET_LENGTH = 32
SUPPORTED_MARKET_RESEARCH_DATA_PROVIDERS = frozenset({"demo", "cached_yahoo"})
SUPPORTED_STRATEGY_BUILDER_MODES = frozenset({"rules", "llm"})
SUPPORTED_STRATEGY_BUILDER_PROVIDERS = frozenset({"openai", "anthropic", "deepinfra", "nvidia", "ollama"})


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


def secret_env_value(name: str, default: str | None = None, *, allow_dotenv: bool = False) -> str | None:
    """Read a sensitive value directly or from ``NAME_FILE``, never both.

    Error messages intentionally identify only the environment variable. They
    never include the configured path, file content, or direct secret value.
    """

    direct = os.getenv(name)
    file_name = os.getenv(f"{name}_FILE")
    has_direct = direct is not None and direct != ""
    has_file = file_name is not None and file_name.strip() != ""
    if has_direct and has_file:
        raise RuntimeError(f"Configure only one of {name} or {name}_FILE.")
    if has_file:
        try:
            secret_path = Path(str(file_name))
            if not secret_path.is_file() or secret_path.stat().st_size > 1_048_576:
                raise OSError("invalid secret file")
            value = secret_path.read_text(encoding="utf-8").rstrip("\r\n")
        except (OSError, UnicodeError):
            raise RuntimeError(f"Unable to load {name} from its configured secret file.") from None
        if not value:
            raise RuntimeError(f"The configured secret file for {name} is empty.")
        return value
    if direct is not None:
        return direct
    if allow_dotenv:
        dotenv = dotenv_value(name)
        if dotenv is not None:
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
    mfa_encryption_key: str | None = None
    session_ttl_hours: int = 12
    cookie_domain: str | None = None
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    trusted_hosts: tuple[str, ...] = ()
    hsts_enabled: bool = False
    hsts_max_age_seconds: int = 31_536_000
    hsts_include_subdomains: bool = True
    hsts_preload: bool = False
    enable_demo_accounts: bool = True
    allow_trial_entitlements: bool = False
    enable_in_process_jobs: bool = True
    job_lease_seconds: int = 180
    job_heartbeat_seconds: int = 30
    job_recovery_poll_seconds: int = 20
    job_max_attempts: int = 3
    job_recovery_batch_size: int = 100
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
    sentry_required: bool = False
    sentry_traces_sample_rate: float = 0.05
    release: str | None = None
    otel_exporter_otlp_endpoint: str | None = None
    otel_traces_sample_rate: float = 0.05
    observability_metrics_enabled: bool = False
    observability_metrics_token: str | None = None
    observability_metrics_port: int = 0
    log_level: str = "INFO"
    log_json: bool = False
    max_request_bytes: int = 2_000_000
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_max_requests: int = 120
    auth_attempt_window_seconds: int = 900
    auth_attempt_max_failures: int = 5
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
    market_research_allow_demo_fallback: bool = True
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
    market_research_news_providers: tuple[str, ...] = ()
    market_research_newsapi_api_key_ref: str | None = None
    market_research_alphavantage_api_key_ref: str | None = None
    market_research_benzinga_api_key_ref: str | None = None
    market_research_news_max_articles: int = 100
    market_research_news_lookback_days: int = 30
    market_research_provider_timeout_seconds: float = 20.0
    market_research_news_required: bool = False
    market_research_fundamentals_required: bool = False
    strategy_builder_mode: str = "rules"
    strategy_builder_llm_provider: str = "openai"
    strategy_builder_llm_model: str = "gpt-5-mini"
    strategy_builder_llm_timeout_seconds: float = 30.0
    strategy_builder_llm_max_retries: int = 1
    strategy_builder_llm_max_concurrency: int = 2
    strategy_builder_openai_api_key_ref: str | None = None
    strategy_builder_anthropic_api_key_ref: str | None = None
    strategy_builder_deepinfra_api_key_ref: str | None = None
    strategy_builder_deepinfra_base_url: str = "https://api.deepinfra.com/v1/openai"
    strategy_builder_nvidia_api_key_ref: str | None = None
    strategy_builder_nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    strategy_builder_ollama_base_url: str = "http://127.0.0.1:11434"
    marketplace_enabled: bool = False
    marketplace_creator_credits_enabled: bool = False
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

        self.validate_job_runtime()
        self.validate_market_research_data_provider()
        self.validate_capabilities()
        if not self.is_production:
            return
        missing: list[str] = []
        required = {
            "APP_BASE_URL": self.app_base_url,
            "DATABASE_URL": self.database_url,
            "REDIS_URL": self.redis_url,
            "SESSION_SECRET": self.session_secret,
            "CSRF_SECRET": self.csrf_secret,
            "MFA_ENCRYPTION_KEY": self.mfa_encryption_key,
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
            normalized = str(value or "").strip().lower()
            if (
                not normalized
                or normalized.startswith(_DOCUMENTED_SECRET_PREFIXES)
                or normalized.endswith(_DOCUMENTED_SECRET_SUFFIXES)
            ):
                missing.append(name)
        session_secret = self.session_secret.strip()
        csrf_secret = self.csrf_secret.strip()
        mfa_encryption_key = str(self.mfa_encryption_key or "").strip()
        if len(session_secret) < _MIN_PRODUCTION_SECRET_LENGTH:
            missing.append(f"SESSION_SECRET(at least {_MIN_PRODUCTION_SECRET_LENGTH} characters)")
        if len(csrf_secret) < _MIN_PRODUCTION_SECRET_LENGTH:
            missing.append(f"CSRF_SECRET(at least {_MIN_PRODUCTION_SECRET_LENGTH} characters)")
        if session_secret and session_secret == csrf_secret:
            missing.append("CSRF_SECRET(distinct from SESSION_SECRET)")
        if len(mfa_encryption_key) < _MIN_PRODUCTION_SECRET_LENGTH:
            missing.append(f"MFA_ENCRYPTION_KEY(at least {_MIN_PRODUCTION_SECRET_LENGTH} characters)")
        if mfa_encryption_key in {session_secret, csrf_secret}:
            missing.append("MFA_ENCRYPTION_KEY(distinct from session and CSRF secrets)")
        if self.auth_attempt_window_seconds < 60:
            missing.append("AUTH_ATTEMPT_WINDOW_SECONDS(at least 60)")
        if not 1 <= self.auth_attempt_max_failures <= 100:
            missing.append("AUTH_ATTEMPT_MAX_FAILURES(between 1 and 100)")
        if self.sentry_required and not self.sentry_dsn:
            missing.append("SENTRY_DSN(required because SENTRY_REQUIRED=true)")
        if not 0.0 <= self.sentry_traces_sample_rate <= 1.0:
            missing.append("SENTRY_TRACES_SAMPLE_RATE(between 0 and 1)")
        if not 0.0 <= self.otel_traces_sample_rate <= 1.0:
            missing.append("OTEL_TRACES_SAMPLE_RATE(between 0 and 1)")
        if self.observability_metrics_enabled and len(str(self.observability_metrics_token or "")) < 32:
            missing.append("OBSERVABILITY_METRICS_TOKEN(at least 32 characters when metrics are enabled)")
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
        invalid_hosts = (
            not self.trusted_hosts
            or "*" in self.trusted_hosts
            or any(
                not str(host).strip()
                or "://" in str(host)
                or "/" in str(host)
                or any(character.isspace() for character in str(host))
                for host in self.trusted_hosts
            )
        )
        if invalid_hosts:
            missing.append("TRUSTED_HOSTS(explicit hostnames, no universal wildcard)")
        if not self.hsts_enabled:
            missing.append("HSTS_ENABLED=true")
        if self.hsts_max_age_seconds < 31_536_000:
            missing.append("HSTS_MAX_AGE_SECONDS(at least 31536000)")
        if missing:
            raise RuntimeError(
                "Production startup blocked. Configure secure production settings for: "
                + ", ".join(dict.fromkeys(missing))
            )
        from .llm_config import validate_market_research_llm_settings

        validate_market_research_llm_settings(self)

    @property
    def capabilities(self) -> dict[str, str | bool]:
        """Server-owned feature availability exposed to authenticated clients."""

        return {
            "strategy_builder_mode": self.strategy_builder_mode.strip().lower(),
            "strategy_builder_provider": self.strategy_builder_llm_provider.strip().lower() if self.strategy_builder_mode.strip().lower() == "llm" else "deterministic",
            "strategy_builder_model": self.strategy_builder_llm_model if self.strategy_builder_mode.strip().lower() == "llm" else "",
            "market_research_data_mode": self.market_research_data_provider.strip().lower(),
            "marketplace_enabled": bool(self.marketplace_enabled),
            "marketplace_creator_credits_enabled": bool(
                self.marketplace_enabled and self.marketplace_creator_credits_enabled
            ),
            "live_broker_trading_enabled": False,
        }

    def validate_capabilities(self) -> None:
        mode = self.strategy_builder_mode.strip().lower()
        if mode not in SUPPORTED_STRATEGY_BUILDER_MODES:
            supported = ", ".join(sorted(SUPPORTED_STRATEGY_BUILDER_MODES))
            raise RuntimeError(
                "Unsupported PAIRS_TRADING_STRATEGY_BUILDER_MODE "
                f"{mode!r}. Supported modes: {supported}."
            )
        provider = self.strategy_builder_llm_provider.strip().lower()
        if provider not in SUPPORTED_STRATEGY_BUILDER_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_STRATEGY_BUILDER_PROVIDERS))
            raise RuntimeError(
                "Unsupported PAIRS_TRADING_STRATEGY_BUILDER_LLM_PROVIDER "
                f"{provider!r}. Supported providers: {supported}."
            )
        if not self.strategy_builder_llm_model.strip():
            raise RuntimeError("PAIRS_TRADING_STRATEGY_BUILDER_LLM_MODEL must not be empty.")
        if self.marketplace_creator_credits_enabled and not self.marketplace_enabled:
            raise RuntimeError(
                "PAIRS_TRADING_MARKETPLACE_CREATOR_CREDITS_ENABLED requires "
                "PAIRS_TRADING_MARKETPLACE_ENABLED=true."
            )
        if self.strategy_builder_llm_timeout_seconds <= 0:
            raise RuntimeError("PAIRS_TRADING_STRATEGY_BUILDER_LLM_TIMEOUT_SECONDS must be positive.")
        if self.strategy_builder_llm_max_retries < 0:
            raise RuntimeError("PAIRS_TRADING_STRATEGY_BUILDER_LLM_MAX_RETRIES must not be negative.")
        if self.strategy_builder_llm_max_concurrency < 1:
            raise RuntimeError("PAIRS_TRADING_STRATEGY_BUILDER_LLM_MAX_CONCURRENCY must be at least 1.")

    def validate_market_research_data_provider(self) -> None:
        """Validate real/demo market-data policy independently of the LLM."""

        provider = self.market_research_data_provider.strip().lower()
        if not 1 <= self.market_research_news_max_articles <= 500:
            raise RuntimeError("PAIRS_TRADING_MARKET_RESEARCH_NEWS_MAX_ARTICLES must be between 1 and 500.")
        if not 1 <= self.market_research_news_lookback_days <= 365:
            raise RuntimeError("PAIRS_TRADING_MARKET_RESEARCH_NEWS_LOOKBACK_DAYS must be between 1 and 365.")
        if not 1.0 <= self.market_research_provider_timeout_seconds <= 120.0:
            raise RuntimeError("PAIRS_TRADING_MARKET_RESEARCH_PROVIDER_TIMEOUT_SECONDS must be between 1 and 120.")
        supported_news = {"rss", "local_web", "web", "newsapi", "alphavantage", "benzinga"}
        unknown_news = sorted(set(self.market_research_news_providers).difference(supported_news))
        if unknown_news:
            raise RuntimeError("Unsupported market research news providers: " + ", ".join(unknown_news))
        if self.market_research_news_required and not self.market_research_news_providers:
            raise RuntimeError("Market research news is required but no providers are configured.")
        if provider not in SUPPORTED_MARKET_RESEARCH_DATA_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_MARKET_RESEARCH_DATA_PROVIDERS))
            raise RuntimeError(
                "Unsupported PAIRS_TRADING_MARKET_RESEARCH_DATA_PROVIDER "
                f"{provider!r}. Supported providers: {supported}."
            )
        if not self.is_production:
            return
        errors: list[str] = []
        if provider == "demo":
            errors.append("PAIRS_TRADING_MARKET_RESEARCH_DATA_PROVIDER=cached_yahoo")
        if self.market_research_allow_demo_fallback:
            errors.append("PAIRS_TRADING_MARKET_RESEARCH_ALLOW_DEMO_FALLBACK=false")
        if errors:
            raise RuntimeError(
                "Production startup blocked. Configure non-synthetic market research data for: "
                + ", ".join(errors)
            )

    def validate_job_runtime(self) -> None:
        """Validate settings shared by external job workers and recovery control."""

        errors: list[str] = []
        if self.job_lease_seconds <= 0:
            errors.append("JOB_LEASE_SECONDS must be positive")
        if self.job_heartbeat_seconds <= 0:
            errors.append("JOB_HEARTBEAT_SECONDS must be positive")
        if self.job_heartbeat_seconds * 2 >= self.job_lease_seconds:
            errors.append("JOB_HEARTBEAT_SECONDS must be less than half of JOB_LEASE_SECONDS")
        if self.job_recovery_poll_seconds <= 0:
            errors.append("JOB_RECOVERY_POLL_SECONDS must be positive")
        if self.job_max_attempts < 1:
            errors.append("JOB_MAX_ATTEMPTS must be at least 1")
        if not 1 <= self.job_recovery_batch_size <= 200:
            errors.append("JOB_RECOVERY_BATCH_SIZE must be between 1 and 200")
        if errors:
            raise RuntimeError("Invalid durable job settings: " + "; ".join(errors))

    def validate_external_job_runtime(self, *, role: str) -> None:
        """Fail fast when a queue worker/controller cannot share durable state."""

        self.validate_job_runtime()
        self.validate_market_research_data_provider()
        normalized_role = str(role).strip().lower()
        if normalized_role not in {"worker", "controller"}:
            raise ValueError("role must be worker or controller")
        errors: list[str] = []
        if self.enable_in_process_jobs:
            errors.append("ENABLE_IN_PROCESS_JOBS must be false")
        if not self.redis_url:
            errors.append("REDIS_URL is required")
        if not self.database_url:
            errors.append("DATABASE_URL is required")
        if errors:
            raise RuntimeError(f"Invalid external job {normalized_role} settings: " + "; ".join(errors))

    @classmethod
    def from_env(cls) -> "BackendSettings":
        app_env = os.getenv("APP_ENV", os.getenv("PAIRS_TRADING_APP_ENV", "development")).lower()
        production = app_env == "production"
        return cls(
            app_env=app_env,
            database_url=secret_env_value("DATABASE_URL"),
            redis_url=secret_env_value("REDIS_URL"),
            session_secret=secret_env_value("SESSION_SECRET", "dev-session-secret-change-me") or "",
            csrf_secret=secret_env_value("CSRF_SECRET", "dev-csrf-secret-change-me") or "",
            mfa_encryption_key=secret_env_value("MFA_ENCRYPTION_KEY"),
            session_ttl_hours=int(os.getenv("SESSION_TTL_HOURS", "12")),
            cookie_domain=os.getenv("COOKIE_DOMAIN") or None,
            cookie_secure=_env_bool("COOKIE_SECURE", production),
            cookie_samesite=os.getenv("COOKIE_SAMESITE", "lax"),
            trusted_hosts=_split_env(os.getenv("TRUSTED_HOSTS"), ()),
            hsts_enabled=_env_bool("HSTS_ENABLED", production),
            hsts_max_age_seconds=int(os.getenv("HSTS_MAX_AGE_SECONDS", "31536000")),
            hsts_include_subdomains=_env_bool("HSTS_INCLUDE_SUBDOMAINS", True),
            hsts_preload=_env_bool("HSTS_PRELOAD", False),
            enable_demo_accounts=_env_bool("ENABLE_DEMO_ACCOUNTS", not production),
            allow_trial_entitlements=_env_bool("ALLOW_TRIAL_ENTITLEMENTS", False),
            enable_in_process_jobs=_env_bool("ENABLE_IN_PROCESS_JOBS", not production),
            job_lease_seconds=int(os.getenv("JOB_LEASE_SECONDS", "180")),
            job_heartbeat_seconds=int(os.getenv("JOB_HEARTBEAT_SECONDS", "30")),
            job_recovery_poll_seconds=int(os.getenv("JOB_RECOVERY_POLL_SECONDS", "20")),
            job_max_attempts=int(os.getenv("JOB_MAX_ATTEMPTS", "3")),
            job_recovery_batch_size=int(os.getenv("JOB_RECOVERY_BATCH_SIZE", "100")),
            s3_endpoint_url=os.getenv("S3_ENDPOINT_URL"),
            s3_bucket=os.getenv("S3_BUCKET"),
            s3_access_key_id=secret_env_value("S3_ACCESS_KEY_ID"),
            s3_secret_access_key=secret_env_value("S3_SECRET_ACCESS_KEY"),
            smtp_host=os.getenv("SMTP_HOST"),
            smtp_port=int(os.getenv("SMTP_PORT")) if os.getenv("SMTP_PORT") else None,
            smtp_username=secret_env_value("SMTP_USERNAME"),
            smtp_password=secret_env_value("SMTP_PASSWORD"),
            smtp_starttls=_env_bool("SMTP_STARTTLS", True),
            email_from=os.getenv("EMAIL_FROM", "no-reply@quantops.local"),
            edgar_user_agent=_env_or_dotenv("SEC_EDGAR_USER_AGENT", "") or _env_or_dotenv("EDGAR_USER_AGENT", "") or None,
            sentry_dsn=secret_env_value("SENTRY_DSN"),
            sentry_required=_env_bool("SENTRY_REQUIRED", False),
            sentry_traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
            release=os.getenv("APP_RELEASE") or None,
            otel_exporter_otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
            otel_traces_sample_rate=float(os.getenv("OTEL_TRACES_SAMPLE_RATE", "0.05")),
            observability_metrics_enabled=_env_bool("OBSERVABILITY_METRICS_ENABLED", False),
            observability_metrics_token=secret_env_value("OBSERVABILITY_METRICS_TOKEN"),
            observability_metrics_port=int(os.getenv("OBSERVABILITY_METRICS_PORT", "0")),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            log_json=_env_bool("LOG_JSON", production),
            max_request_bytes=int(os.getenv("MAX_REQUEST_BYTES", "2000000")),
            rate_limit_enabled=_env_bool("RATE_LIMIT_ENABLED", True),
            rate_limit_window_seconds=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")),
            rate_limit_max_requests=int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "120")),
            auth_attempt_window_seconds=int(os.getenv("AUTH_ATTEMPT_WINDOW_SECONDS", "900")),
            auth_attempt_max_failures=int(os.getenv("AUTH_ATTEMPT_MAX_FAILURES", "5")),
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
            market_research_allow_demo_fallback=_env_bool_or_dotenv(
                "PAIRS_TRADING_MARKET_RESEARCH_ALLOW_DEMO_FALLBACK",
                not production,
            ),
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
            market_research_news_providers=tuple(
                item.lower()
                for item in _split_env(_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_NEWS_PROVIDERS", ""), ())
            ),
            market_research_newsapi_api_key_ref=_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_NEWSAPI_API_KEY_REF", "") or None,
            market_research_alphavantage_api_key_ref=_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_ALPHAVANTAGE_API_KEY_REF", "") or None,
            market_research_benzinga_api_key_ref=_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_BENZINGA_API_KEY_REF", "") or None,
            market_research_news_max_articles=int(_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_NEWS_MAX_ARTICLES", "100")),
            market_research_news_lookback_days=int(_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_NEWS_LOOKBACK_DAYS", "30")),
            market_research_provider_timeout_seconds=float(_env_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_PROVIDER_TIMEOUT_SECONDS", "20.0")),
            market_research_news_required=_env_bool_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_NEWS_REQUIRED", production),
            market_research_fundamentals_required=_env_bool_or_dotenv("PAIRS_TRADING_MARKET_RESEARCH_FUNDAMENTALS_REQUIRED", production),
            strategy_builder_mode=_env_or_dotenv("PAIRS_TRADING_STRATEGY_BUILDER_MODE", "rules").strip().lower() or "rules",
            strategy_builder_llm_provider=_env_or_dotenv("PAIRS_TRADING_STRATEGY_BUILDER_LLM_PROVIDER", "openai").strip().lower() or "openai",
            strategy_builder_llm_model=_env_or_dotenv("PAIRS_TRADING_STRATEGY_BUILDER_LLM_MODEL", "gpt-5-mini").strip() or "gpt-5-mini",
            strategy_builder_llm_timeout_seconds=float(_env_or_dotenv("PAIRS_TRADING_STRATEGY_BUILDER_LLM_TIMEOUT_SECONDS", "30.0")),
            strategy_builder_llm_max_retries=int(_env_or_dotenv("PAIRS_TRADING_STRATEGY_BUILDER_LLM_MAX_RETRIES", "1")),
            strategy_builder_llm_max_concurrency=int(_env_or_dotenv("PAIRS_TRADING_STRATEGY_BUILDER_LLM_MAX_CONCURRENCY", "2")),
            strategy_builder_openai_api_key_ref=_env_or_dotenv("PAIRS_TRADING_STRATEGY_BUILDER_OPENAI_API_KEY_REF", "") or None,
            strategy_builder_anthropic_api_key_ref=_env_or_dotenv("PAIRS_TRADING_STRATEGY_BUILDER_ANTHROPIC_API_KEY_REF", "") or None,
            strategy_builder_deepinfra_api_key_ref=_env_or_dotenv("PAIRS_TRADING_STRATEGY_BUILDER_DEEPINFRA_API_KEY_REF", "") or None,
            strategy_builder_deepinfra_base_url=_env_or_dotenv("PAIRS_TRADING_STRATEGY_BUILDER_DEEPINFRA_BASE_URL", "https://api.deepinfra.com/v1/openai").strip() or "https://api.deepinfra.com/v1/openai",
            strategy_builder_nvidia_api_key_ref=_env_or_dotenv("PAIRS_TRADING_STRATEGY_BUILDER_NVIDIA_API_KEY_REF", "") or None,
            strategy_builder_nvidia_base_url=_env_or_dotenv("PAIRS_TRADING_STRATEGY_BUILDER_NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip() or "https://integrate.api.nvidia.com/v1",
            strategy_builder_ollama_base_url=_env_or_dotenv("PAIRS_TRADING_STRATEGY_BUILDER_OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip() or "http://127.0.0.1:11434",
            marketplace_enabled=_env_bool_or_dotenv("PAIRS_TRADING_MARKETPLACE_ENABLED", False),
            marketplace_creator_credits_enabled=_env_bool_or_dotenv(
                "PAIRS_TRADING_MARKETPLACE_CREATOR_CREDITS_ENABLED", False
            ),
            price_cache_dir=Path(os.getenv("PAIRS_TRADING_PRICE_CACHE_DIR", "data/cache")),
            sentiment_cache_dir=Path(os.getenv("PAIRS_TRADING_SENTIMENT_CACHE_DIR", "data/sentiment_cache")),
            sentiment_job_state_dir=Path(os.getenv("PAIRS_TRADING_SENTIMENT_JOB_STATE_DIR", "artifacts/sentiment/jobs")),
            event_cache_dir=Path(os.getenv("PAIRS_TRADING_EVENT_CACHE_DIR", "data/event_cache")),
            cors_origins=_split_env(
                os.getenv("CORS_ORIGINS") or os.getenv("PAIRS_TRADING_CORS_ORIGINS"),
                ("http://localhost:5173", "http://127.0.0.1:5173"),
            ),
            app_base_url=os.getenv("APP_BASE_URL") or os.getenv("PAIRS_TRADING_APP_BASE_URL", "http://127.0.0.1:5173"),
            stripe_secret_key=secret_env_value("STRIPE_SECRET_KEY"),
            stripe_pro_price_id=os.getenv("STRIPE_PRO_PRICE_ID"),
            stripe_price_pro_monthly=os.getenv("STRIPE_PRICE_PRO_MONTHLY"),
            stripe_price_team_monthly=os.getenv("STRIPE_PRICE_TEAM_MONTHLY"),
            stripe_success_url=os.getenv("STRIPE_SUCCESS_URL"),
            stripe_cancel_url=os.getenv("STRIPE_CANCEL_URL"),
            stripe_webhook_secret=secret_env_value("STRIPE_WEBHOOK_SECRET"),
            telemetry_enabled=os.getenv("PAIRS_TRADING_TELEMETRY_ENABLED", "true").lower() not in {"0", "false", "no"},
            telemetry_sample_rate=float(os.getenv("PAIRS_TRADING_TELEMETRY_SAMPLE_RATE", "1.0")),
            refresh_interval_hours=int(os.getenv("PAIRS_TRADING_REFRESH_INTERVAL_HOURS", "24")),
            refresh_max_attempts=int(os.getenv("PAIRS_TRADING_REFRESH_MAX_ATTEMPTS", "3")),
            refresh_lock_minutes=int(os.getenv("PAIRS_TRADING_REFRESH_LOCK_MINUTES", "30")),
            refresh_scheduler_enabled=os.getenv("PAIRS_TRADING_REFRESH_SCHEDULER_ENABLED", "false").lower() in {"1", "true", "yes"},
            refresh_scheduler_poll_seconds=int(os.getenv("PAIRS_TRADING_REFRESH_SCHEDULER_POLL_SECONDS", "3600")),
        )
