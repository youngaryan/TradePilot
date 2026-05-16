from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..research.market_research_agents import ResearchHorizon


SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^][A-Z0-9.^=_-]{0,31}$")


class MarketDataInterval(StrEnum):
    ONE_DAY = "1d"
    ONE_WEEK = "1wk"
    ONE_MONTH = "1mo"
    ONE_HOUR = "1h"


class SentimentProvider(StrEnum):
    RSS = "rss"
    LOCAL_WEB = "local_web"
    WEB = "web"
    LOCAL = "local"
    NEWSAPI = "newsapi"
    ALPHAVANTAGE = "alphavantage"
    BENZINGA = "benzinga"


def _normalized_date(value: str, *, field_name: str) -> str:
    raw = str(value).strip()
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name} must be formatted as YYYY-MM-DD.") from exc


def _normalized_symbols(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        symbol = str(value).strip().upper()
        if not symbol:
            continue
        if not SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError(f"Invalid symbol: {value!r}.")
        if symbol not in normalized:
            normalized.append(symbol)
    if len(normalized) > 100:
        raise ValueError("At most 100 symbols can be submitted at once.")
    return normalized


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "pairs-trading-backend"


class PaperRunRequest(BaseModel):
    deployment_config_path: Path | None = Field(
        default=None,
        description="Optional path to a paper deployment config. Defaults to backend settings.",
    )
    deployment_config: dict[str, Any] | None = Field(
        default=None,
        description="Optional inline paper deployment config with execution settings and strategy specs.",
    )
    asof_date: str | None = Field(
        default=None,
        description="Optional paper run as-of date, formatted as YYYY-MM-DD.",
    )
    asof_start: str | None = Field(
        default=None,
        description="Optional start date for a business-day paper replay range.",
    )
    asof_end: str | None = Field(
        default=None,
        description="Optional end date for a business-day paper replay range.",
    )


class BacktestRunRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    pipeline: str = Field(
        default="time_series_momentum",
        description="Pipeline or directional strategy id to backtest.",
    )
    symbols: list[str] = Field(
        default_factory=lambda: ["SPY", "QQQ", "TLT", "GLD"],
        description="Symbols used by directional, ETF, or event pipelines.",
    )
    start: str = Field(default="2018-01-01", description="Backtest start date, formatted as YYYY-MM-DD.")
    end: str = Field(default="2026-04-15", description="Backtest end date, formatted as YYYY-MM-DD.")
    interval: MarketDataInterval = Field(default=MarketDataInterval.ONE_DAY, description="Market data interval.")
    experiment_name: str | None = Field(default=None, description="Optional experiment name.")
    artifact_root: Path | None = Field(default=None, description="Optional artifact root. Defaults to backend settings.")
    artifact_id: str | None = Field(default=None, description="Tenant-owned artifact id for production reads.")
    sector_map_path: Path | None = Field(default=None, description="Sector map path for stat-arb runs.")
    sector_dataset_id: str | None = Field(default=None, description="Tenant-owned sector-map dataset id for production runs.")
    event_file: Path | None = Field(default=None, description="Event file path for event-driven runs.")
    event_dataset_id: str | None = Field(default=None, description="Tenant-owned event dataset id for production event runs.")
    use_sec_companyfacts: bool = Field(default=False, description="Use SEC company facts for event-driven runs.")
    include_sec_filings: bool = Field(default=False, description="Include official SEC filing events such as 8-K, 10-Q, and 10-K.")
    sec_filing_forms: list[str] = Field(
        default_factory=lambda: ["8-K", "10-Q", "10-K"],
        description="Official SEC filing forms to include when include_sec_filings is enabled.",
    )
    edgar_user_agent: str | None = Field(default=None, description="SEC EDGAR user-agent when SEC data is enabled.")
    train_bars: int = Field(default=252, ge=20)
    test_bars: int = Field(default=63, ge=5)
    step_bars: int = Field(default=63, ge=1)
    bars_per_year: int = Field(default=252, ge=1)
    purge_bars: int = Field(default=5, ge=0)
    embargo_bars: int = Field(default=0, ge=0)
    pbo_partitions: int = Field(default=8, ge=2)
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Strategy-specific parameters. Unknown keys are ignored by unsupported pipelines.",
    )

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        return _normalized_symbols(values)

    @field_validator("start")
    @classmethod
    def normalize_start(cls, value: str) -> str:
        return _normalized_date(value, field_name="start")

    @field_validator("end")
    @classmethod
    def normalize_end(cls, value: str) -> str:
        return _normalized_date(value, field_name="end")

    @model_validator(mode="after")
    def validate_date_order(self) -> "BacktestRunRequest":
        if date.fromisoformat(self.end) <= date.fromisoformat(self.start):
            raise ValueError("end must be after start.")
        return self


class MarketResearchRunRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    ticker: str = Field(default="AAPL", min_length=1, max_length=32, description="Ticker symbol to research.")
    analysis_date: str | None = Field(default=None, description="Analysis date formatted as YYYY-MM-DD. Defaults to today.")
    horizon: ResearchHorizon = Field(default=ResearchHorizon.SWING, description="Research horizon: intraday, swing, or long-term.")
    provider: str | None = Field(default=None, max_length=80, description="Optional research-stage LLM provider override.")
    model: str | None = Field(default=None, max_length=160, description="Optional research-stage model override.")
    sentiment_dataset_id: str | None = Field(default=None, max_length=160, description="Optional tenant-owned sentiment dataset id to include in the research context.")
    include_sentiment: bool = Field(default=True, description="Include existing tenant sentiment dataset rows when available.")
    include_financial_events: bool = Field(default=True, description="Include existing financial-events provider data when available.")
    lookback_days: int | None = Field(default=None, ge=5, le=900, description="Optional data lookback window override.")
    options: dict[str, Any] = Field(default_factory=dict, description="Non-secret provider/model options for future providers.")
    tickers: list[str] | None = Field(default=None, max_length=20, description="Multi-ticker research. The committee runs on each ticker.")
    pair: str | None = Field(default=None, max_length=65, description="Pair research mode. Two tickers separated by comma, e.g. 'KO,PEP'.")
    universe_filter: dict[str, Any] | None = Field(default=None, description="Filter stocks from the stock universe by sector/country/etc.")

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return _normalized_symbols([value])[0]

    @field_validator("pair")
    @classmethod
    def normalize_pair(cls, value: str | None) -> str | None:
        if value is None:
            return None
        raw_parts = [s.strip() for s in value.split(",") if s.strip()]
        if not raw_parts:
            return None
        parts = _normalized_symbols(raw_parts)
        if len(parts) != 2:
            raise ValueError("pair must contain exactly two valid ticker symbols separated by a comma.")
        return ",".join(parts)

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return _normalized_symbols(values)

    @field_validator("analysis_date")
    @classmethod
    def normalize_analysis_date(cls, value: str | None) -> str | None:
        if value is None or str(value).strip() == "":
            return None
        return _normalized_date(value, field_name="analysis_date")

    @field_validator("options")
    @classmethod
    def reject_secret_options(cls, value: dict[str, Any]) -> dict[str, Any]:
        sensitive = {"api_key", "apikey", "token", "secret", "password", "credential", "credentials"}

        def walk(payload: Any) -> None:
            if isinstance(payload, dict):
                for key, item in payload.items():
                    lowered = str(key).strip().lower()
                    if lowered in sensitive or any(part in lowered for part in sensitive):
                        raise ValueError("options must not contain raw secrets. Use environment or vault references for real providers.")
                    walk(item)
            elif isinstance(payload, list):
                for item in payload:
                    walk(item)

        for key in value:
            if any(part in str(key).strip().lower() for part in sensitive):
                raise ValueError("options must not contain raw secrets. Use environment or vault references for real providers.")
        walk(value)
        return value


class StockUniverseItem(BaseModel):
    ticker: str
    company_name: str = ""
    sector: str = "Unknown"
    industry: str = ""
    country: str = "US"
    exchange: str = "NYSE"
    currency: str = "USD"
    market_cap_category: str = ""
    avg_volume: int = 0
    is_liquid: bool = True


class StockUniverseResponse(BaseModel):
    name: str = "default"
    description: str = ""
    total_stocks: int = 0
    stocks: list[StockUniverseItem] = Field(default_factory=list)
    sector_counts: list[dict[str, Any]] = Field(default_factory=list)
    country_counts: list[dict[str, Any]] = Field(default_factory=list)
    exchange_counts: list[dict[str, Any]] = Field(default_factory=list)


class CommitteeDecisionResponse(BaseModel):
    id: str
    ticker: str
    pair_ticker: str | None = None
    timestamp: str
    analysis_date: str = ""
    horizon: str = "swing"
    decision: str = ""
    confidence: int = 0
    reasoning: str = ""
    signals_summary: dict[str, Any] = Field(default_factory=dict)
    market_metrics: dict[str, Any] = Field(default_factory=dict)
    data_quality: dict[str, Any] = Field(default_factory=dict)
    evaluation: dict[str, Any] = Field(default_factory=dict)
    recommendation: str = ""
    llm_provider: str = ""
    llm_model: str = ""


class ChartDataResponse(BaseModel):
    charts: dict[str, Any] = Field(default_factory=dict)


class StrategyBuilderMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=5000)


class StrategyBuilderChatRequest(BaseModel):
    messages: list[StrategyBuilderMessage] = Field(default_factory=list, max_length=20)
    draft_spec: dict[str, Any] | None = Field(default=None, description="Optional structured spec revision to validate.")


class StrategyBuilderApprovalRequest(BaseModel):
    spec: dict[str, Any]
    approved: bool = Field(default=False)
    approval_text: str = Field(default="", max_length=500)


class AdminStrategyStatusUpdateRequest(BaseModel):
    status: str = Field(pattern="^(active|disabled)$")


class SentimentAccumulationRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    symbols: list[str] = Field(
        default_factory=lambda: ["AAPL", "MSFT", "NVDA"],
        description="Tickers to fetch and score headlines for.",
    )
    start: str = Field(default="2024-01-01", description="Start date, formatted as YYYY-MM-DD.")
    end: str = Field(default="2024-02-10", description="End date, formatted as YYYY-MM-DD.")
    providers: list[SentimentProvider] = Field(
        default_factory=lambda: [SentimentProvider.RSS, SentimentProvider.LOCAL_WEB],
        description="Headline sources: rss, local_web, web, local, newsapi, alphavantage, benzinga. API-key providers need request credentials or backend environment variables.",
    )
    rss_feed_urls: list[str] = Field(default_factory=list, description="Optional RSS URLs. Use {ticker} for per-symbol feeds.")
    local_web_search_urls: list[str] = Field(default_factory=list, description="Optional RSS/Atom URLs for local cached web search. Use {ticker} for per-symbol feeds.")
    local_web_refresh_minutes: int = Field(default=60, ge=0, description="Refresh interval for the local web-search cache. Set 0 to refetch.")
    local_web_max_pages_per_source: int = Field(default=30, ge=1, le=250, description="Maximum pages to crawl from each local web seed URL or domain.")
    web_research_urls: list[str] = Field(default_factory=list, description="Optional direct web pages to fetch and summarize. Use {ticker} for per-symbol pages.")
    web_research_domains: list[str] = Field(default_factory=list, description="Optional source domains for GDELT-backed web research, for example reuters.com or cnbc.com.")
    web_research_query_terms: str = Field(default="", description="Optional extra GDELT query terms, such as earnings OR guidance.")
    web_research_max_articles: int = Field(default=4, ge=1, le=25, description="Maximum discovered web articles per symbol.")
    web_research_fetch_article_text: bool = Field(default=True, description="Fetch article pages and create lightweight extractive summaries when possible.")
    news_files: list[str] = Field(default_factory=list, description="Optional local CSV/parquet headline files.")
    newsapi_api_key: str | None = Field(default=None, description="Optional NewsAPI.org key. Backend env NEWSAPI_API_KEY is also supported.")
    alphavantage_api_key: str | None = Field(default=None, description="Optional Alpha Vantage key. Backend env ALPHAVANTAGE_API_KEY is also supported.")
    benzinga_api_key: str | None = Field(default=None, description="Optional Benzinga key. Backend env BENZINGA_API_KEY is also supported.")
    output_dir: Path | None = Field(default=None, description="Development-only output directory. Production rejects raw paths and registers tenant dataset ids.")
    use_finbert: bool = Field(default=False, description="Use FinBERT when available; fallback model is used if local cache is unavailable.")
    local_finbert_only: bool = Field(default=True, description="Do not download FinBERT during UI runs.")

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        return _normalized_symbols(values)

    @field_validator("start")
    @classmethod
    def normalize_start(cls, value: str) -> str:
        return _normalized_date(value, field_name="start")

    @field_validator("end")
    @classmethod
    def normalize_end(cls, value: str) -> str:
        return _normalized_date(value, field_name="end")

    @field_validator("providers")
    @classmethod
    def dedupe_providers(cls, values: list[SentimentProvider]) -> list[SentimentProvider]:
        deduped: list[SentimentProvider] = []
        for provider in values:
            if provider not in deduped:
                deduped.append(provider)
        return deduped

    @model_validator(mode="after")
    def validate_date_order(self) -> "SentimentAccumulationRequest":
        if date.fromisoformat(self.end) <= date.fromisoformat(self.start):
            raise ValueError("end must be after start.")
        return self


class LoginRequest(BaseModel):
    email: str = Field(default="demo@quantops.local")
    password: str = Field(default="quantops-demo")


class SignupRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=2, max_length=120)
    organization_name: str = Field(min_length=2, max_length=120)


class EmailVerificationRequest(BaseModel):
    token: str = Field(min_length=16, max_length=512)


class EmailVerificationSendRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=16, max_length=512)
    new_password: str = Field(min_length=8, max_length=256)


class MfaVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=12)


class AuthenticatedUser(BaseModel):
    id: str
    email: str
    display_name: str
    role: str = "user"
    status: str = "active"


class AuthResponse(BaseModel):
    user: AuthenticatedUser
    organizations: list[dict[str, Any]]
    active_organization_id: str | None = None


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    provider: str = Field(min_length=2, max_length=80)
    secret: str | None = Field(default=None, description="Accepted for masking only in the local prototype; production should use a vault.")
    secret_ref: str | None = Field(default=None, description="Environment/vault reference, for example NEWSAPI_API_KEY.")
    scopes: list[str] = Field(
        default_factory=lambda: ["read"],
        description="Machine API-key scopes, for example read, backtests:run, sentiment:run, paper:run.",
    )


class BillingCheckoutRequest(BaseModel):
    plan: str = Field(default="pro", description="Requested plan id, such as pro or team.")
    price_id: str | None = Field(default=None, description="Deprecated development-only override. Production maps plan ids to server-owned Stripe Price ids.")


class BillingPortalRequest(BaseModel):
    return_url: str | None = None


class AdminUserUpdateRequest(BaseModel):
    role: str | None = Field(default=None, description="Global app role: admin or user.")
    status: str | None = Field(default=None, description="Account status: active or inactive.")


class TelemetryEventRequest(BaseModel):
    name: str = Field(min_length=3, max_length=120, description="Stable snake_case event name, for example backtest_started.")
    category: str = Field(default="product", max_length=60, description="product, engineering, refresh, billing, error, or security.")
    properties: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    consent: str = Field(default="granted", description="granted, denied, system, or unknown.")
    anonymous_id: str | None = Field(default=None, max_length=128)
    occurred_at_utc: str | None = None


class TelemetryBatchRequest(BaseModel):
    events: list[TelemetryEventRequest] = Field(default_factory=list, max_length=50)


class DataRefreshRequest(BaseModel):
    force: bool = Field(default=False, description="Run even when the user's next refresh is not due.")
    user_id: str | None = Field(default=None, description="Admin/debug override. Defaults to the authenticated user.")


class DataRefreshTickRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000)
    force: bool = False
