from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


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
    interval: str = Field(default="1d", description="Market data interval.")
    experiment_name: str | None = Field(default=None, description="Optional experiment name.")
    artifact_root: Path | None = Field(default=None, description="Optional artifact root. Defaults to backend settings.")
    sector_map_path: Path | None = Field(default=None, description="Sector map path for stat-arb runs.")
    event_file: Path | None = Field(default=None, description="Event file path for event-driven runs.")
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


class SentimentAccumulationRequest(BaseModel):
    symbols: list[str] = Field(
        default_factory=lambda: ["AAPL", "MSFT", "NVDA"],
        description="Tickers to fetch and score headlines for.",
    )
    start: str = Field(default="2024-01-01", description="Start date, formatted as YYYY-MM-DD.")
    end: str = Field(default="2024-02-10", description="End date, formatted as YYYY-MM-DD.")
    providers: list[str] = Field(
        default_factory=lambda: ["rss"],
        description="Headline sources: rss, local, newsapi, alphavantage, benzinga. API-key providers need request credentials or backend environment variables.",
    )
    rss_feed_urls: list[str] = Field(default_factory=list, description="Optional RSS URLs. Use {ticker} for per-symbol feeds.")
    news_files: list[str] = Field(default_factory=list, description="Optional local CSV/parquet headline files.")
    newsapi_api_key: str | None = Field(default=None, description="Optional NewsAPI.org key. Backend env NEWSAPI_API_KEY is also supported.")
    alphavantage_api_key: str | None = Field(default=None, description="Optional Alpha Vantage key. Backend env ALPHAVANTAGE_API_KEY is also supported.")
    benzinga_api_key: str | None = Field(default=None, description="Optional Benzinga key. Backend env BENZINGA_API_KEY is also supported.")
    output_dir: Path | None = Field(default=None, description="Output directory for raw/scored/daily sentiment files.")
    use_finbert: bool = Field(default=True, description="Use FinBERT when available; fallback model is used if local cache is unavailable.")
    local_finbert_only: bool = Field(default=True, description="Do not download FinBERT during UI runs.")
