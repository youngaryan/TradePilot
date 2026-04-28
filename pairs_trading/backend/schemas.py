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
