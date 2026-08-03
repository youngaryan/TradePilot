from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from copy import deepcopy
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock
import numpy as np
import pandas as pd

from ..core.portfolio import PortfolioManager
from ..core.timeframes import TradingMode, resolve_timeframe_spec
from ..data.market import CachedParquetProvider, MarketDataProvider, YahooFinanceProvider
from ..engines.backtesting import json_ready
from ..pipelines import (
    DirectionalPipelineConfig,
    DirectionalStrategyPipeline,
    ETFMomentumConfig,
    ETFTrendMomentumPipeline,
    EventDrivenConfig,
    EventDrivenPipeline,
    GraphStatArbConfig,
    GraphStatArbPipeline,
    MultiTimeframeSignalConfig,
    PEADSentimentConfig,
    PEADSentimentPipeline,
    SectorStatArbPipeline,
    StatArbConfig,
)
from ..reporting.paper import PaperDashboardVisualizer
from ..research import GraphClusterConfig, PairScreenConfig
from ..features.sentiment import SentimentConfig
from ..strategies import GraphClusterTradingConfig, build_rule_based_strategy_factory
from .paper_state import (
    PAPER_LEDGER_SCHEMA_VERSION,
    PaperStateScope,
    atomic_write_json,
    ledger_key,
    read_json,
    resolve_scoped_artifact_root,
    resolve_scoped_state_dir,
)


DIRECTIONAL_PAPER_PIPELINES = {
    "buy_and_hold",
    "ma_cross",
    "ema_cross",
    "rsi_mean_reversion",
    "sma_deviation",
    "stochastic_oscillator",
    "bollinger_mean_reversion",
    "macd_trend",
    "donchian_breakout",
    "keltner_breakout",
    "volatility_target_trend",
    "time_series_momentum",
    "adaptive_regime",
}


def _coerce_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return tuple()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


@dataclass(frozen=True)
class PaperExecutionSettings:
    initial_cash: float = 100_000.0
    commission_bps: float = 0.5
    slippage_bps: float = 1.0
    min_trade_notional: float = 100.0
    weight_tolerance: float = 0.0025


@dataclass(frozen=True)
class PaperStrategySpec:
    name: str
    pipeline: str
    symbols: tuple[str, ...] = field(default_factory=tuple)
    sector_map_path: str | None = None
    daily_sentiment_file: str | None = None
    news_provider_names: tuple[str, ...] = field(default_factory=tuple)
    news_files: tuple[str, ...] = field(default_factory=tuple)
    rss_feed_urls: tuple[str, ...] = field(default_factory=tuple)
    local_web_search_urls: tuple[str, ...] = field(default_factory=tuple)
    local_web_refresh_minutes: int = 60
    local_web_max_pages_per_source: int = 30
    web_research_urls: tuple[str, ...] = field(default_factory=tuple)
    web_research_domains: tuple[str, ...] = field(default_factory=tuple)
    web_research_query_terms: str = ""
    web_research_max_articles: int = 4
    web_research_fetch_article_text: bool = True
    newsapi_api_key: str | None = None
    use_finbert: bool = False
    local_finbert_only: bool = False
    news_topics: tuple[str, ...] = field(default_factory=tuple)
    event_file: str | None = None
    use_sec_companyfacts: bool = False
    include_sec_filings: bool = False
    sec_filing_forms: tuple[str, ...] = field(default_factory=lambda: ("8-K", "10-Q", "10-K"))
    edgar_user_agent: str | None = None
    interval: str = "1d"
    trading_mode: str | None = None
    lookback_bars: int | None = None
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def timeframe(self):
        return resolve_timeframe_spec(trading_mode=self.trading_mode, interval=self.interval)

    @property
    def effective_interval(self) -> str:
        return self.timeframe.execution_interval

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PaperStrategySpec":
        if "name" not in payload or "pipeline" not in payload:
            raise ValueError("Each paper strategy spec must include 'name' and 'pipeline'.")
        return cls(
            name=str(payload["name"]),
            pipeline=str(payload["pipeline"]),
            symbols=_coerce_str_tuple(payload.get("symbols")),
            sector_map_path=payload.get("sector_map_path"),
            daily_sentiment_file=payload.get("daily_sentiment_file"),
            news_provider_names=_coerce_str_tuple(payload.get("news_provider_names")),
            news_files=_coerce_str_tuple(payload.get("news_files")),
            rss_feed_urls=_coerce_str_tuple(payload.get("rss_feed_urls")),
            local_web_search_urls=_coerce_str_tuple(payload.get("local_web_search_urls")),
            local_web_refresh_minutes=int(payload.get("local_web_refresh_minutes", 60)),
            local_web_max_pages_per_source=int(payload.get("local_web_max_pages_per_source", 30)),
            web_research_urls=_coerce_str_tuple(payload.get("web_research_urls")),
            web_research_domains=_coerce_str_tuple(payload.get("web_research_domains")),
            web_research_query_terms=str(payload.get("web_research_query_terms", "")),
            web_research_max_articles=int(payload.get("web_research_max_articles", 4)),
            web_research_fetch_article_text=bool(payload.get("web_research_fetch_article_text", True)),
            newsapi_api_key=payload.get("newsapi_api_key"),
            use_finbert=bool(payload.get("use_finbert", False)),
            local_finbert_only=bool(payload.get("local_finbert_only", False)),
            news_topics=_coerce_str_tuple(payload.get("news_topics")),
            event_file=payload.get("event_file"),
            use_sec_companyfacts=bool(payload.get("use_sec_companyfacts", False)),
            include_sec_filings=bool(payload.get("include_sec_filings", False)),
            sec_filing_forms=_coerce_str_tuple(payload.get("sec_filing_forms")) or ("8-K", "10-Q", "10-K"),
            edgar_user_agent=payload.get("edgar_user_agent"),
            interval=str(payload.get("interval", "1d")),
            trading_mode=payload.get("trading_mode"),
            lookback_bars=None if payload.get("lookback_bars") is None else int(payload["lookback_bars"]),
            params=dict(payload.get("params", {})),
        )


@dataclass(frozen=True)
class PaperDeploymentConfig:
    execution: PaperExecutionSettings = PaperExecutionSettings()
    strategies: tuple[PaperStrategySpec, ...] = field(default_factory=tuple)

    @classmethod
    def from_file(cls, path: str | Path) -> "PaperDeploymentConfig":
        source = Path(path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        execution = PaperExecutionSettings(**payload.get("execution", {}))
        strategies = tuple(PaperStrategySpec.from_dict(item) for item in payload.get("strategies", []))
        if not strategies:
            raise ValueError("Paper deployment config must include at least one strategy.")
        return cls(execution=execution, strategies=strategies)


@dataclass(frozen=True)
class PaperSignalSnapshot:
    strategy_name: str
    timestamp: pd.Timestamp
    mode: str
    target_weights: dict[str, float]
    instrument_prices: dict[str, float]
    diagnostics: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


class PaperLedger:
    def __init__(
        self,
        *,
        strategy_name: str,
        mode: str,
        settings: PaperExecutionSettings,
        state_dir: str | Path,
        scope: PaperStateScope | None = None,
        lock_timeout_seconds: float = 30.0,
    ) -> None:
        self.strategy_name = str(strategy_name)
        self.mode = mode
        self.settings = settings
        self.scope = scope or PaperStateScope(
            organization_id="local-development",
            deployment_id="legacy-default",
        )
        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_key = ledger_key(self.strategy_name)
        self.state_path = self.state_dir / f"{self.ledger_key}.json"
        self.orders_path = self.state_dir / f"{self.ledger_key}_latest_orders.json"
        lock_dir = self.state_dir / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path = lock_dir / f"{self.ledger_key}.lock"
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        with FileLock(str(self.lock_path), timeout=self.lock_timeout_seconds):
            self.state = self._load_or_initialize()

    def _load_or_initialize(self) -> dict[str, Any]:
        if self.state_path.exists():
            payload = read_json(self.state_path)
            if not isinstance(payload, dict):
                raise ValueError(f"Paper ledger is not a JSON object: {self.state_path}")
            for field_name, expected in (
                ("organization_id", self.scope.organization_id),
                ("deployment_id", self.scope.deployment_id),
                ("project_id", self.scope.project_id),
                ("strategy_name", self.strategy_name),
                ("ledger_key", self.ledger_key),
            ):
                actual = payload.get(field_name)
                if actual is not None and actual != expected:
                    raise ValueError(f"Paper ledger {field_name} does not match its requested scope")
            payload.setdefault("strategy_name", self.strategy_name)
            payload.setdefault("mode", self.mode)
            payload.setdefault("initial_cash", self.settings.initial_cash)
            payload.setdefault("cash", payload["initial_cash"])
            payload.setdefault("positions", {})
            payload.setdefault("instrument_prices", {})
            payload.setdefault("history", [])
            payload.setdefault("schema_version", PAPER_LEDGER_SCHEMA_VERSION)
            payload.setdefault("organization_id", self.scope.organization_id)
            payload.setdefault("deployment_id", self.scope.deployment_id)
            payload.setdefault("project_id", self.scope.project_id)
            payload.setdefault("ledger_key", self.ledger_key)
            payload.setdefault("revision", 0)
            payload.setdefault("latest_orders", [])
            payload.setdefault("applied_execution_keys", [])
            return payload
        return {
            "schema_version": PAPER_LEDGER_SCHEMA_VERSION,
            "organization_id": self.scope.organization_id,
            "deployment_id": self.scope.deployment_id,
            "project_id": self.scope.project_id,
            "ledger_key": self.ledger_key,
            "strategy_name": self.strategy_name,
            "mode": self.mode,
            "initial_cash": float(self.settings.initial_cash),
            "cash": float(self.settings.initial_cash),
            "positions": {},
            "instrument_prices": {},
            "history": [],
            "last_timestamp": None,
            "latest_orders": [],
            "applied_execution_keys": [],
            "revision": 0,
        }

    def _current_equity(self, instrument_prices: dict[str, float]) -> float:
        cash = float(self.state.get("cash", self.settings.initial_cash))
        market_value = 0.0
        for instrument, quantity in self.state.get("positions", {}).items():
            price = float(instrument_prices.get(instrument, self.state.get("instrument_prices", {}).get(instrument, 0.0)))
            market_value += float(quantity) * price
        return cash + market_value

    def _save(self, latest_orders: list[dict[str, Any]]) -> None:
        self.state["latest_orders"] = json_ready(latest_orders)
        atomic_write_json(self.state_path, json_ready(self.state))
        try:
            atomic_write_json(self.orders_path, json_ready(latest_orders))
        except Exception:
            # This file is a compatibility projection. The canonical ledger
            # above already contains the matching latest_orders revision.
            pass

    def latest_equity(self) -> float:
        history = self.state.get("history", [])
        if history:
            return float(history[-1]["equity_after"])
        return float(self.state.get("initial_cash", self.settings.initial_cash))

    def _apply_snapshot_unlocked(
        self,
        snapshot: PaperSignalSnapshot,
        *,
        execution_key: str,
    ) -> dict[str, Any]:
        prices = {instrument: float(price) for instrument, price in snapshot.instrument_prices.items() if float(price) > 0.0}
        if not prices:
            raise ValueError(f"{self.strategy_name} did not provide any instrument prices for paper execution.")

        existing_prices = dict(self.state.get("instrument_prices", {}))
        existing_prices.update(prices)
        self.state["instrument_prices"] = existing_prices

        pre_trade_equity = self._current_equity(existing_prices)
        previous_equity = self.latest_equity()
        cash = float(self.state.get("cash", self.settings.initial_cash))
        positions = {instrument: float(quantity) for instrument, quantity in self.state.get("positions", {}).items()}

        orders: list[dict[str, Any]] = []
        turnover_notional = 0.0
        threshold = max(float(self.settings.min_trade_notional), pre_trade_equity * float(self.settings.weight_tolerance))

        instruments = set(snapshot.target_weights) | set(positions)
        for instrument in sorted(instruments):
            price = float(existing_prices.get(instrument, 0.0))
            if price <= 0.0:
                continue

            target_weight = float(snapshot.target_weights.get(instrument, 0.0))
            current_quantity = float(positions.get(instrument, 0.0))
            current_value = current_quantity * price
            target_value = pre_trade_equity * target_weight
            delta_value = target_value - current_value
            if abs(delta_value) < threshold:
                continue

            execution_price = price * (1.0 + np.sign(delta_value) * self.settings.slippage_bps / 10_000.0)
            if execution_price <= 0.0:
                continue

            quantity_delta = delta_value / execution_price
            notional = abs(quantity_delta * execution_price)
            commission = notional * self.settings.commission_bps / 10_000.0
            cash -= quantity_delta * execution_price + commission

            new_quantity = current_quantity + quantity_delta
            if abs(new_quantity * price) < self.settings.min_trade_notional * 0.25 and abs(new_quantity) < 1e-6:
                positions.pop(instrument, None)
            else:
                positions[instrument] = new_quantity

            turnover_notional += notional
            orders.append(
                {
                    "instrument": instrument,
                    "side": "buy" if quantity_delta > 0.0 else "sell",
                    "quantity": float(quantity_delta),
                    "mark_price": price,
                    "execution_price": execution_price,
                    "target_weight": target_weight,
                    "commission": commission,
                    "notional": notional,
                }
            )

        self.state["cash"] = float(cash)
        self.state["positions"] = positions
        self.state["last_timestamp"] = snapshot.timestamp.isoformat()

        post_trade_equity = self._current_equity(existing_prices)
        gross_exposure_notional = sum(abs(quantity) * float(existing_prices.get(instrument, 0.0)) for instrument, quantity in positions.items())
        summary = {
            "idempotency_key": execution_key,
            "ledger_key": self.ledger_key,
            "timestamp": snapshot.timestamp.isoformat(),
            "mode": self.mode,
            "equity_before": float(pre_trade_equity),
            "equity_after": float(post_trade_equity),
            "daily_pnl": float(pre_trade_equity - previous_equity),
            "rebalance_cost_pnl": float(post_trade_equity - pre_trade_equity),
            "net_return_since_inception": float(post_trade_equity / self.state["initial_cash"] - 1.0),
            "cash_after": float(cash),
            "gross_exposure_notional": float(gross_exposure_notional),
            "gross_exposure_ratio": float(gross_exposure_notional / post_trade_equity) if post_trade_equity else 0.0,
            "position_count": int(len(positions)),
            "trade_count": int(len(orders)),
            "turnover_notional": float(turnover_notional),
            "positions": {instrument: float(quantity) for instrument, quantity in positions.items()},
            "target_weights": {instrument: float(weight) for instrument, weight in snapshot.target_weights.items()},
            "metadata": snapshot.metadata,
            "diagnostics": snapshot.diagnostics,
        }
        self.state.setdefault("history", []).append(summary)
        self.state.setdefault("applied_execution_keys", []).append(execution_key)
        self.state["last_execution_key"] = execution_key
        self.state["revision"] = int(self.state.get("revision", 0) or 0) + 1
        self._save(latest_orders=orders)
        return summary

    def apply_snapshot(
        self,
        snapshot: PaperSignalSnapshot,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        execution_key = str(
            idempotency_key
            or snapshot.metadata.get("idempotency_key")
            or snapshot.timestamp.isoformat()
        )
        if not execution_key.strip():
            raise ValueError("Paper execution idempotency key must not be empty")
        with FileLock(str(self.lock_path), timeout=self.lock_timeout_seconds):
            current = self._load_or_initialize()
            applied = set(str(item) for item in current.get("applied_execution_keys", []))
            if execution_key in applied:
                existing = next(
                    (
                        dict(item)
                        for item in reversed(current.get("history", []))
                        if str(item.get("idempotency_key")) == execution_key
                    ),
                    None,
                )
                if existing is None:
                    raise ValueError("Ledger idempotency index is inconsistent with its history")
                self.state = current
                return existing
            original = deepcopy(current)
            self.state = deepcopy(current)
            try:
                return self._apply_snapshot_unlocked(snapshot, execution_key=execution_key)
            except BaseException:
                self.state = original
                raise


class PaperTradingService:
    def __init__(
        self,
        *,
        deployment_config: PaperDeploymentConfig,
        price_provider: MarketDataProvider | None = None,
        state_dir: str | Path = "artifacts/paper/state",
        artifact_root: str | Path = "artifacts/paper/runs",
        price_cache_dir: str = "data/cache",
        sentiment_cache_dir: str = "data/sentiment_cache",
        event_cache_dir: str = "data/event_cache",
        scope: PaperStateScope | None = None,
        execution_idempotency_key: str | None = None,
    ) -> None:
        self.deployment_config = deployment_config
        self.scope = scope
        if execution_idempotency_key is not None and not str(execution_idempotency_key).strip():
            raise ValueError("Paper execution idempotency key must not be empty")
        self.execution_idempotency_key = (
            str(execution_idempotency_key) if execution_idempotency_key is not None else None
        )
        self.effective_scope = scope or PaperStateScope(
            organization_id="local-development",
            deployment_id="legacy-default",
        )
        self.state_dir = resolve_scoped_state_dir(state_dir, scope) if scope is not None else Path(state_dir).resolve()
        self.artifact_root = (
            resolve_scoped_artifact_root(artifact_root, scope)
            if scope is not None
            else Path(artifact_root).resolve()
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.sentiment_cache_dir = sentiment_cache_dir
        self.event_cache_dir = event_cache_dir
        self.price_provider = price_provider or CachedParquetProvider(
            upstream=YahooFinanceProvider(),
            cache_dir=price_cache_dir,
        )

    @staticmethod
    def _asof_timestamp(value: str | pd.Timestamp | None) -> pd.Timestamp:
        if value is None:
            return pd.Timestamp(datetime.now(UTC).date())
        return pd.Timestamp(value).tz_localize(None)

    @staticmethod
    def _history_start(asof: pd.Timestamp, bars: int) -> str:
        history_index = pd.bdate_range(end=asof, periods=max(int(bars), 5))
        return history_index[0].strftime("%Y-%m-%d")

    @staticmethod
    def _history_end(asof: pd.Timestamp) -> str:
        return (asof + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    @staticmethod
    def _default_lookback(spec: PaperStrategySpec) -> int:
        if spec.lookback_bars is not None:
            return int(spec.lookback_bars)
        if spec.pipeline.startswith(("user_strategy:", "marketplace_strategy:")):
            strategy_spec = spec.params.get("strategy_spec")
            if not isinstance(strategy_spec, dict):
                raise ValueError("Custom and community paper strategies require an embedded validated strategy_spec.")
            _, min_history = build_rule_based_strategy_factory(strategy_spec)
            return min_history + 10
        if spec.pipeline == "etf_trend":
            return 800
        if spec.pipeline in {"stat_arb", "graph_stat_arb"}:
            return 620
        if spec.pipeline in {"edgar_event", "pead_sentiment"}:
            return 520
        if spec.pipeline == "ma_cross":
            return max(160, int(spec.params.get("slow_window", 80)) + 40)
        if spec.pipeline == "ema_cross":
            return max(140, int(spec.params.get("ema_slow_window", 48)) + 50)
        if spec.pipeline == "rsi_mean_reversion":
            return max(120, int(spec.params.get("rsi_window", 14)) * 6)
        if spec.pipeline == "buy_and_hold":
            return 80
        if spec.pipeline == "sma_deviation":
            return max(180, int(spec.params.get("sma_window", 40)) * 4)
        if spec.pipeline == "stochastic_oscillator":
            return max(140, int(spec.params.get("stochastic_window", 14)) * 7)
        if spec.pipeline == "bollinger_mean_reversion":
            return max(160, int(spec.params.get("bollinger_window", 20)) * 6)
        if spec.pipeline == "macd_trend":
            return max(180, int(spec.params.get("macd_slow_window", 26)) * 6)
        if spec.pipeline == "donchian_breakout":
            return max(180, int(spec.params.get("breakout_window", 55)) + int(spec.params.get("breakout_exit_window", 20)) + 40)
        if spec.pipeline == "keltner_breakout":
            return max(180, int(spec.params.get("keltner_window", 40)) * 4)
        if spec.pipeline == "volatility_target_trend":
            return max(
                240,
                int(spec.params.get("trend_window", 120)) + int(spec.params.get("volatility_window", 20)) + 80,
            )
        if spec.pipeline == "time_series_momentum":
            lookbacks = spec.params.get("momentum_lookbacks", (21, 63, 126, 252))
            return max(320, max(int(value) for value in lookbacks) + 80)
        if spec.pipeline == "adaptive_regime":
            return max(
                300,
                int(spec.params.get("regime_slow_window", 120))
                + int(spec.params.get("regime_mean_reversion_window", 40))
                + int(spec.params.get("regime_volatility_window", 30))
                + 80,
            )
        raise ValueError(f"Unsupported paper pipeline: {spec.pipeline}")

    @staticmethod
    def _extract_asset_weights(output: StrategyOutput) -> dict[str, float]:
        if output.frame.empty:
            return {}
        latest = output.frame.iloc[-1]
        target_weights: dict[str, float] = {}
        for column in output.frame.columns:
            if not column.startswith("weight_"):
                continue
            instrument = column.removeprefix("weight_")
            weight = float(pd.to_numeric(latest[column], errors="coerce"))
            if abs(weight) > 1e-10:
                target_weights[instrument] = weight
        return target_weights

    def _price_history(self, symbols: list[str], *, asof: pd.Timestamp, lookback_bars: int, interval: str) -> pd.DataFrame:
        prices = self.price_provider.get_close_prices(
            symbols=symbols,
            start=self._history_start(asof, lookback_bars),
            end=self._history_end(asof),
            interval=interval,
        )
        prices = prices.dropna(how="all").sort_index()
        if prices.empty or len(prices) < 2:
            raise ValueError(f"Not enough price history to build a paper snapshot for symbols: {symbols}")
        return prices

    @staticmethod
    def _cli_helpers():
        from ..apps import cli as cli_module

        return cli_module

    def _build_directional_snapshot(self, spec: PaperStrategySpec, *, asof: pd.Timestamp) -> PaperSignalSnapshot:
        cli_module = self._cli_helpers()
        if not spec.symbols:
            raise ValueError(f"{spec.name} requires 'symbols' in the paper deployment config.")

        strategy_factory, min_history = cli_module._build_directional_strategy_factory(
            spec.pipeline,
            fast_window=int(spec.params.get("fast_window", 20)),
            slow_window=int(spec.params.get("slow_window", 80)),
            ema_fast_window=int(spec.params.get("ema_fast_window", 12)),
            ema_slow_window=int(spec.params.get("ema_slow_window", 48)),
            rsi_window=int(spec.params.get("rsi_window", 14)),
            lower_entry=float(spec.params.get("lower_entry", 30.0)),
            upper_entry=float(spec.params.get("upper_entry", 70.0)),
            exit_level=float(spec.params.get("exit_level", 50.0)),
            sma_window=int(spec.params.get("sma_window", 40)),
            z_entry=float(spec.params.get("z_entry", 1.25)),
            z_exit=float(spec.params.get("z_exit", 0.25)),
            stochastic_window=int(spec.params.get("stochastic_window", 14)),
            stochastic_smooth_window=int(spec.params.get("stochastic_smooth_window", 3)),
            stochastic_lower_entry=float(spec.params.get("stochastic_lower_entry", 20.0)),
            stochastic_upper_entry=float(spec.params.get("stochastic_upper_entry", 80.0)),
            bollinger_window=int(spec.params.get("bollinger_window", 20)),
            bollinger_num_std=float(spec.params.get("bollinger_num_std", 2.0)),
            macd_fast_window=int(spec.params.get("macd_fast_window", 12)),
            macd_slow_window=int(spec.params.get("macd_slow_window", 26)),
            macd_signal_window=int(spec.params.get("macd_signal_window", 9)),
            breakout_window=int(spec.params.get("breakout_window", 55)),
            breakout_exit_window=int(spec.params.get("breakout_exit_window", 20)),
            keltner_window=int(spec.params.get("keltner_window", 40)),
            keltner_atr_multiplier=float(spec.params.get("keltner_atr_multiplier", 1.5)),
            trend_window=int(spec.params.get("trend_window", 120)),
            volatility_window=int(spec.params.get("volatility_window", 20)),
            target_volatility=float(spec.params.get("target_volatility", 0.15)),
            max_position=float(spec.params.get("max_position", 1.5)),
            momentum_lookbacks=spec.params.get("momentum_lookbacks"),
            momentum_min_agreement=float(spec.params.get("momentum_min_agreement", 0.25)),
            regime_fast_window=int(spec.params.get("regime_fast_window", 30)),
            regime_slow_window=int(spec.params.get("regime_slow_window", 120)),
            regime_mean_reversion_window=int(spec.params.get("regime_mean_reversion_window", 40)),
            regime_volatility_window=int(spec.params.get("regime_volatility_window", 30)),
            regime_volatility_quantile=float(spec.params.get("regime_volatility_quantile", 0.70)),
            strategy_cost_bps=float(spec.params.get("strategy_cost_bps", 2.0)),
        )

        prices = self._price_history(list(spec.symbols), asof=asof, lookback_bars=max(self._default_lookback(spec), min_history + 10), interval=spec.effective_interval)
        pipeline = DirectionalStrategyPipeline(
            strategy_factory=strategy_factory,
            portfolio_manager=PortfolioManager(
                max_leverage=float(spec.params.get("max_leverage", 1.25)),
                risk_per_trade=float(spec.params.get("risk_per_trade", 0.06)),
                volatility_window=int(spec.params.get("volatility_window", 20)),
                max_strategy_weight=float(spec.params.get("max_strategy_weight", 0.35)),
            ),
            config=DirectionalPipelineConfig.from_symbols(symbols=list(spec.symbols), min_history=min_history),
            name=spec.name,
            multi_timeframe=MultiTimeframeSignalConfig(
                execution_interval=spec.timeframe.execution_interval,
                confirmation_interval="4h",
                fast_window=6,
                slow_window=24,
            ) if spec.timeframe.mode == TradingMode.SHORT_TERM else None,
            timeframe_metadata=spec.timeframe.to_metadata(),
        )
        output = pipeline.run_fold(train_data=prices.iloc[:-1], test_data=prices.iloc[-1:])
        target_weights = self._extract_asset_weights(output)
        instrument_prices = {symbol: float(prices.iloc[-1][symbol]) for symbol in prices.columns if pd.notna(prices.iloc[-1][symbol])}
        return PaperSignalSnapshot(
            strategy_name=spec.name,
            timestamp=pd.Timestamp(prices.index[-1]).tz_localize(None),
            mode="asset",
            target_weights=target_weights,
            instrument_prices=instrument_prices,
            diagnostics=output.diagnostics,
            metadata={"pipeline": spec.pipeline, "trading_mode": str(spec.timeframe.mode), "execution_interval": spec.timeframe.execution_interval},
        )

    def _build_user_strategy_snapshot(self, spec: PaperStrategySpec, *, asof: pd.Timestamp) -> PaperSignalSnapshot:
        strategy_spec = spec.params.get("strategy_spec")
        if not isinstance(strategy_spec, dict):
            raise ValueError("Custom and community paper strategies require an embedded validated strategy_spec.")
        symbols = list(spec.symbols or strategy_spec.get("asset_universe", {}).get("symbols", []))
        if not symbols:
            raise ValueError(f"{spec.name} requires at least one symbol.")
        strategy_factory, min_history = build_rule_based_strategy_factory(strategy_spec)
        prices = self._price_history(
            symbols,
            asof=asof,
            lookback_bars=max(self._default_lookback(spec), min_history + 10),
            interval=spec.effective_interval,
        )
        sizing = strategy_spec.get("position_sizing") if isinstance(strategy_spec.get("position_sizing"), dict) else {}
        max_position = float(sizing.get("max_position_per_symbol", 1.0))
        max_gross = float(sizing.get("max_gross_exposure", 1.0))
        risk_controls = strategy_spec.get("risk_controls") if isinstance(strategy_spec.get("risk_controls"), dict) else {}
        pipeline = DirectionalStrategyPipeline(
            strategy_factory=strategy_factory,
            portfolio_manager=PortfolioManager(
                max_leverage=max_gross,
                max_strategy_weight=max_position,
                allocation_method="equal_weight",
                max_active_positions=int(risk_controls.get("max_positions", len(symbols))),
            ),
            config=DirectionalPipelineConfig.from_symbols(symbols=symbols, min_history=min_history),
            name=spec.name,
            multi_timeframe=MultiTimeframeSignalConfig(
                execution_interval=spec.timeframe.execution_interval,
                confirmation_interval="4h",
                fast_window=6,
                slow_window=24,
            ) if spec.timeframe.mode == TradingMode.SHORT_TERM else None,
            timeframe_metadata=spec.timeframe.to_metadata(),
        )
        output = pipeline.run_fold(train_data=prices.iloc[:-1], test_data=prices.iloc[-1:])
        target_weights = self._extract_asset_weights(output)
        instrument_prices = {symbol: float(prices.iloc[-1][symbol]) for symbol in prices.columns if pd.notna(prices.iloc[-1][symbol])}
        return PaperSignalSnapshot(
            strategy_name=spec.name,
            timestamp=pd.Timestamp(prices.index[-1]).tz_localize(None),
            mode="asset",
            target_weights=target_weights,
            instrument_prices=instrument_prices,
            diagnostics=output.diagnostics,
            metadata={
                "pipeline": spec.pipeline,
                "user_strategy": spec.pipeline.startswith("user_strategy:"),
                "community_strategy": spec.pipeline.startswith("marketplace_strategy:"),
                "trading_mode": str(spec.timeframe.mode),
            },
        )

    def _build_etf_snapshot(self, spec: PaperStrategySpec, *, asof: pd.Timestamp) -> PaperSignalSnapshot:
        symbols = list(spec.symbols or ("SPY", "QQQ", "IWM", "DIA", "TLT", "IEF", "GLD", "SLV", "XLE", "XLF", "XLK", "XLV"))
        prices = self._price_history(symbols, asof=asof, lookback_bars=self._default_lookback(spec), interval=spec.effective_interval)
        pipeline = ETFTrendMomentumPipeline(
            ETFMomentumConfig.from_symbols(
                symbols,
                lookbacks=spec.params.get("lookbacks", (21, 63, 126, 252)),
                lookback_weights=spec.params.get("lookback_weights", (4.0, 3.0, 2.0, 1.0)),
                trend_window=int(spec.params.get("trend_window", 200)),
                volatility_window=int(spec.params.get("volatility_window", 20)),
                top_n=int(spec.params.get("top_n", 3)),
                rebalance_bars=int(spec.params.get("rebalance_bars", 21)),
                transaction_cost_bps=float(spec.params.get("transaction_cost_bps", 2.0)),
            ),
            name=spec.name,
        )
        output = pipeline.run_fold(train_data=prices.iloc[:-1], test_data=prices.iloc[-1:])
        target_weights = self._extract_asset_weights(output)
        instrument_prices = {symbol: float(prices.iloc[-1][symbol]) for symbol in prices.columns if pd.notna(prices.iloc[-1][symbol])}
        return PaperSignalSnapshot(
            strategy_name=spec.name,
            timestamp=pd.Timestamp(prices.index[-1]).tz_localize(None),
            mode="asset",
            target_weights=target_weights,
            instrument_prices=instrument_prices,
            diagnostics=output.diagnostics,
            metadata={"pipeline": spec.pipeline},
        )

    def _build_event_snapshot(self, spec: PaperStrategySpec, *, asof: pd.Timestamp) -> PaperSignalSnapshot:
        cli_module = self._cli_helpers()
        symbols = list(spec.symbols)
        if not symbols:
            raise ValueError(f"{spec.name} requires 'symbols' in the paper deployment config.")

        prices = self._price_history(symbols, asof=asof, lookback_bars=self._default_lookback(spec), interval=spec.effective_interval)
        events = cli_module.load_events(
            tickers=symbols,
            start=self._history_start(asof, self._default_lookback(spec)),
            end=self._history_end(asof),
            event_file=spec.event_file,
            event_cache_dir=self.event_cache_dir,
            edgar_user_agent=spec.edgar_user_agent,
            use_sec_companyfacts=spec.use_sec_companyfacts,
            include_sec_filings=spec.include_sec_filings,
            sec_filing_forms=list(spec.sec_filing_forms),
        )
        if events is None:
            raise ValueError(f"{spec.name} requires 'event_file', SEC company facts, or official SEC filings for paper deployment.")

        pipeline = EventDrivenPipeline(
            events=events,
            portfolio_manager=PortfolioManager(
                max_leverage=float(spec.params.get("max_leverage", 1.25)),
                risk_per_trade=float(spec.params.get("risk_per_trade", 0.05)),
                volatility_window=int(spec.params.get("volatility_window", 15)),
                max_strategy_weight=float(spec.params.get("max_strategy_weight", 0.25)),
            ),
            config=EventDrivenConfig.from_symbols(
                symbols,
                holding_period_bars=int(spec.params.get("holding_period_bars", 5)),
                entry_threshold=float(spec.params.get("entry_threshold", 0.15)),
                min_events=int(spec.params.get("min_events", 1)),
                transaction_cost_bps=float(spec.params.get("transaction_cost_bps", 2.0)),
            ),
            name=spec.name,
        )
        output = pipeline.run_fold(train_data=prices.iloc[:-1], test_data=prices.iloc[-1:])
        target_weights = self._extract_asset_weights(output)
        instrument_prices = {symbol: float(prices.iloc[-1][symbol]) for symbol in prices.columns if pd.notna(prices.iloc[-1][symbol])}
        return PaperSignalSnapshot(
            strategy_name=spec.name,
            timestamp=pd.Timestamp(prices.index[-1]).tz_localize(None),
            mode="asset",
            target_weights=target_weights,
            instrument_prices=instrument_prices,
            diagnostics=output.diagnostics,
            metadata={"pipeline": spec.pipeline, "event_count": int(len(events))},
        )

    def _build_pead_snapshot(self, spec: PaperStrategySpec, *, asof: pd.Timestamp) -> PaperSignalSnapshot:
        cli_module = self._cli_helpers()
        symbols = list(spec.symbols)
        if not symbols:
            raise ValueError(f"{spec.name} requires 'symbols' in the paper deployment config.")

        prices = self._price_history(symbols, asof=asof, lookback_bars=self._default_lookback(spec), interval=spec.effective_interval)
        history_start = self._history_start(asof, self._default_lookback(spec))
        history_end = self._history_end(asof)
        events = cli_module.load_events(
            tickers=symbols,
            start=history_start,
            end=history_end,
            event_file=spec.event_file,
            event_cache_dir=self.event_cache_dir,
            edgar_user_agent=spec.edgar_user_agent,
            use_sec_companyfacts=spec.use_sec_companyfacts,
            include_sec_filings=spec.include_sec_filings,
            sec_filing_forms=list(spec.sec_filing_forms),
        )
        if events is None:
            raise ValueError(f"{spec.name} requires 'event_file', SEC company facts, or official SEC filings for PEAD paper deployment.")

        daily_sentiment = cli_module.load_daily_sentiment(
            tickers=symbols,
            start=history_start,
            end=history_end,
            news_provider_names=list(spec.news_provider_names) or None,
            news_files=list(spec.news_files) or None,
            daily_sentiment_file=spec.daily_sentiment_file,
            use_finbert=spec.use_finbert,
            local_finbert_only=spec.local_finbert_only,
            sentiment_cache_dir=self.sentiment_cache_dir,
            news_api_key=None,
            alphavantage_api_key=None,
            benzinga_api_key=None,
            newsapi_api_key=spec.newsapi_api_key,
            news_topics=list(spec.news_topics) or None,
            rss_feed_urls=list(spec.rss_feed_urls) or None,
            local_web_search_urls=list(spec.local_web_search_urls) or None,
            local_web_refresh_minutes=spec.local_web_refresh_minutes,
            local_web_max_pages_per_source=spec.local_web_max_pages_per_source,
            web_research_urls=list(spec.web_research_urls) or None,
            web_research_domains=list(spec.web_research_domains) or None,
            web_research_query_terms=spec.web_research_query_terms,
            web_research_max_articles=spec.web_research_max_articles,
            web_research_fetch_article_text=spec.web_research_fetch_article_text,
        )

        pipeline = PEADSentimentPipeline(
            events=events,
            daily_sentiment=daily_sentiment,
            portfolio_manager=PortfolioManager(
                max_leverage=float(spec.params.get("max_leverage", 1.25)),
                risk_per_trade=float(spec.params.get("risk_per_trade", 0.05)),
                volatility_window=int(spec.params.get("volatility_window", 15)),
                max_strategy_weight=float(spec.params.get("max_strategy_weight", 0.25)),
            ),
            config=PEADSentimentConfig.from_symbols(
                symbols,
                holding_period_bars=int(spec.params.get("holding_period_bars", 5)),
                entry_threshold=float(spec.params.get("entry_threshold", 0.20)),
                event_weight=float(spec.params.get("event_weight", 0.45)),
                sentiment_weight=float(spec.params.get("sentiment_weight", 0.55)),
                sentiment_window_days=int(spec.params.get("sentiment_window_days", 2)),
                require_sentiment=bool(spec.params.get("require_sentiment", False)),
                require_earnings_event=bool(spec.params.get("require_earnings_event", True)),
                transaction_cost_bps=float(spec.params.get("transaction_cost_bps", 2.5)),
            ),
            name=spec.name,
        )
        output = pipeline.run_fold(train_data=prices.iloc[:-1], test_data=prices.iloc[-1:])
        target_weights = self._extract_asset_weights(output)
        instrument_prices = {symbol: float(prices.iloc[-1][symbol]) for symbol in prices.columns if pd.notna(prices.iloc[-1][symbol])}
        return PaperSignalSnapshot(
            strategy_name=spec.name,
            timestamp=pd.Timestamp(prices.index[-1]).tz_localize(None),
            mode="asset",
            target_weights=target_weights,
            instrument_prices=instrument_prices,
            diagnostics=output.diagnostics,
            metadata={
                "pipeline": spec.pipeline,
                "event_count": int(len(events)),
                "sentiment_enabled": daily_sentiment is not None and not daily_sentiment.empty,
            },
        )

    def _build_stat_arb_snapshot(self, spec: PaperStrategySpec, *, asof: pd.Timestamp) -> PaperSignalSnapshot:
        cli_module = self._cli_helpers()
        sector_map = cli_module.load_sector_map(spec.sector_map_path)
        tickers = list(sector_map.keys())
        prices = self._price_history(tickers, asof=asof, lookback_bars=self._default_lookback(spec), interval=spec.effective_interval)

        daily_sentiment = cli_module.load_daily_sentiment(
            tickers=tickers,
            start=self._history_start(asof, self._default_lookback(spec)),
            end=self._history_end(asof),
            news_provider_names=list(spec.news_provider_names) or None,
            news_files=list(spec.news_files) or None,
            daily_sentiment_file=spec.daily_sentiment_file,
            use_finbert=spec.use_finbert,
            local_finbert_only=spec.local_finbert_only,
            sentiment_cache_dir=self.sentiment_cache_dir,
            news_api_key=None,
            alphavantage_api_key=None,
            benzinga_api_key=None,
            newsapi_api_key=spec.newsapi_api_key,
            news_topics=list(spec.news_topics) or None,
            rss_feed_urls=list(spec.rss_feed_urls) or None,
            local_web_search_urls=list(spec.local_web_search_urls) or None,
            local_web_refresh_minutes=spec.local_web_refresh_minutes,
            local_web_max_pages_per_source=spec.local_web_max_pages_per_source,
            web_research_urls=list(spec.web_research_urls) or None,
            web_research_domains=list(spec.web_research_domains) or None,
            web_research_query_terms=spec.web_research_query_terms,
            web_research_max_articles=spec.web_research_max_articles,
            web_research_fetch_article_text=spec.web_research_fetch_article_text,
        )

        pipeline = SectorStatArbPipeline(
            sector_map=sector_map,
            portfolio_manager=PortfolioManager(
                max_leverage=float(spec.params.get("max_leverage", 1.5)),
                risk_per_trade=float(spec.params.get("risk_per_trade", 0.08)),
                volatility_window=int(spec.params.get("volatility_window", 20)),
                max_strategy_weight=float(spec.params.get("max_strategy_weight", 0.40)),
            ),
            screen_config=PairScreenConfig(
                min_history=int(spec.params.get("screen_min_history", 252)),
                correlation_floor=float(spec.params.get("screen_correlation_floor", 0.60)),
                coint_pvalue_threshold=float(spec.params.get("screen_coint_pvalue_threshold", 0.10)),
                min_half_life=float(spec.params.get("screen_min_half_life", 2.0)),
                max_half_life=float(spec.params.get("screen_max_half_life", 60.0)),
                target_half_life=float(spec.params.get("screen_target_half_life", 15.0)),
            ),
            stat_arb_config=StatArbConfig(
                include_residual_book=bool(spec.params.get("include_residual_book", True)),
                residual_lookback=int(spec.params.get("residual_lookback", 60)),
                residual_entry_z=float(spec.params.get("residual_entry_z", 1.5)),
                residual_exit_z=float(spec.params.get("residual_exit_z", 0.35)),
                residual_transaction_cost_bps=float(spec.params.get("residual_transaction_cost_bps", 2.0)),
                include_classic_pairs=bool(spec.params.get("include_classic_pairs", True)),
                top_n_pairs=int(spec.params.get("top_n_pairs", 3)),
                entry_z=float(spec.params.get("entry_z", 2.0)),
                exit_z=float(spec.params.get("exit_z", 0.35)),
                break_window=int(spec.params.get("break_window", 80)),
                break_pvalue=float(spec.params.get("break_pvalue", 0.20)),
                transaction_cost_bps=float(spec.params.get("transaction_cost_bps", 4.0)),
            ),
            daily_sentiment=daily_sentiment,
            sentiment_config=SentimentConfig() if daily_sentiment is not None else None,
            name=spec.name,
        )

        train_data = prices.iloc[:-1]
        test_data = prices.iloc[-1:]
        portfolio_output = pipeline.run_fold(train_data=train_data, test_data=test_data)
        component_outputs, _, _, _ = pipeline.build_component_outputs(train_data=train_data, test_data=test_data)
        latest_portfolio = portfolio_output.frame.iloc[-1] if not portfolio_output.frame.empty else pd.Series(dtype=float)
        target_weights = {
            component_name: float(pd.to_numeric(latest_portfolio.get(f"weight_{component_name}", 0.0), errors="coerce"))
            for component_name in component_outputs
            if abs(float(pd.to_numeric(latest_portfolio.get(f"weight_{component_name}", 0.0), errors="coerce"))) > 1e-10
        }
        synthetic_returns = {
            component_name: float(pd.to_numeric(output.frame["unit_return"].iloc[-1], errors="coerce"))
            for component_name, output in component_outputs.items()
            if not output.frame.empty
        }
        return PaperSignalSnapshot(
            strategy_name=spec.name,
            timestamp=pd.Timestamp(test_data.index[-1]).tz_localize(None),
            mode="synthetic",
            target_weights=target_weights,
            instrument_prices=synthetic_returns,
            diagnostics=portfolio_output.diagnostics,
            metadata={"pipeline": spec.pipeline, "synthetic_component_count": int(len(component_outputs))},
        )

    def _build_graph_stat_arb_snapshot(self, spec: PaperStrategySpec, *, asof: pd.Timestamp) -> PaperSignalSnapshot:
        cli_module = self._cli_helpers()
        sector_map = cli_module.load_sector_map(spec.sector_map_path)
        tickers = list(sector_map.keys())
        prices = self._price_history(tickers, asof=asof, lookback_bars=self._default_lookback(spec), interval=spec.effective_interval)

        pipeline = GraphStatArbPipeline(
            sector_map=sector_map,
            portfolio_manager=PortfolioManager(
                max_leverage=float(spec.params.get("max_leverage", 1.5)),
                risk_per_trade=float(spec.params.get("risk_per_trade", 0.07)),
                volatility_window=int(spec.params.get("volatility_window", 20)),
                max_strategy_weight=float(spec.params.get("max_strategy_weight", 0.35)),
            ),
            config=GraphStatArbConfig(
                cluster_config=GraphClusterConfig(
                    min_history=int(spec.params.get("cluster_min_history", 180)),
                    correlation_floor=float(spec.params.get("cluster_correlation_floor", 0.55)),
                    min_cluster_size=int(spec.params.get("cluster_min_size", 3)),
                    max_cluster_size=int(spec.params.get("cluster_max_size", 8)),
                ),
                trading_config=GraphClusterTradingConfig(
                    residual_lookback=int(spec.params.get("residual_lookback", 60)),
                    entry_z=float(spec.params.get("entry_z", 1.25)),
                    top_n_per_side=int(spec.params.get("top_n_per_side", 2)),
                    transaction_cost_bps=float(spec.params.get("transaction_cost_bps", 3.0)),
                ),
                max_clusters=int(spec.params.get("max_clusters", 8)),
            ),
            name=spec.name,
        )

        train_data = prices.iloc[:-1]
        test_data = prices.iloc[-1:]
        portfolio_output = pipeline.run_fold(train_data=train_data, test_data=test_data)
        component_outputs, _ = pipeline.build_component_outputs(train_data=train_data, test_data=test_data)
        latest_portfolio = portfolio_output.frame.iloc[-1] if not portfolio_output.frame.empty else pd.Series(dtype=float)
        target_weights = {
            component_name: float(pd.to_numeric(latest_portfolio.get(f"weight_{component_name}", 0.0), errors="coerce"))
            for component_name in component_outputs
            if abs(float(pd.to_numeric(latest_portfolio.get(f"weight_{component_name}", 0.0), errors="coerce"))) > 1e-10
        }
        synthetic_returns = {
            component_name: float(pd.to_numeric(output.frame["unit_return"].iloc[-1], errors="coerce"))
            for component_name, output in component_outputs.items()
            if not output.frame.empty
        }
        return PaperSignalSnapshot(
            strategy_name=spec.name,
            timestamp=pd.Timestamp(test_data.index[-1]).tz_localize(None),
            mode="synthetic",
            target_weights=target_weights,
            instrument_prices=synthetic_returns,
            diagnostics=portfolio_output.diagnostics,
            metadata={"pipeline": spec.pipeline, "synthetic_component_count": int(len(component_outputs))},
        )

    def build_snapshot(self, spec: PaperStrategySpec, *, asof: pd.Timestamp) -> PaperSignalSnapshot:
        if spec.pipeline.startswith(("user_strategy:", "marketplace_strategy:")):
            return self._build_user_strategy_snapshot(spec, asof=asof)
        if spec.pipeline == "etf_trend":
            return self._build_etf_snapshot(spec, asof=asof)
        if spec.pipeline == "stat_arb":
            return self._build_stat_arb_snapshot(spec, asof=asof)
        if spec.pipeline == "graph_stat_arb":
            return self._build_graph_stat_arb_snapshot(spec, asof=asof)
        if spec.pipeline == "edgar_event":
            return self._build_event_snapshot(spec, asof=asof)
        if spec.pipeline == "pead_sentiment":
            return self._build_pead_snapshot(spec, asof=asof)
        if spec.pipeline in DIRECTIONAL_PAPER_PIPELINES:
            return self._build_directional_snapshot(spec, asof=asof)
        raise ValueError(f"Unsupported paper pipeline: {spec.pipeline}")

    @staticmethod
    def _update_synthetic_prices(ledger: PaperLedger, snapshot: PaperSignalSnapshot) -> dict[str, float]:
        prior_prices = {instrument: float(price) for instrument, price in ledger.state.get("instrument_prices", {}).items()}
        updated: dict[str, float] = {}
        instruments = set(prior_prices) | set(snapshot.instrument_prices) | set(ledger.state.get("positions", {})) | set(snapshot.target_weights)
        for instrument in sorted(instruments):
            base_price = float(prior_prices.get(instrument, 100.0))
            component_return = float(snapshot.instrument_prices.get(instrument, 0.0))
            next_price = base_price * (1.0 + component_return)
            updated[instrument] = max(float(next_price), 1e-6)
        return updated

    def run(self, *, asof_date: str | pd.Timestamp | None = None) -> dict[str, Any]:
        asof = self._asof_timestamp(asof_date)
        run_now = datetime.now(UTC)
        run_timestamp = run_now.isoformat().replace("+00:00", "Z")
        run_id = f"{run_now.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex}"
        artifact_dir = self.artifact_root / run_id
        artifact_dir.mkdir(parents=True, exist_ok=False)

        results: dict[str, Any] = {}
        leaderboard_rows: list[dict[str, Any]] = []

        for spec in self.deployment_config.strategies:
            snapshot = self.build_snapshot(spec, asof=asof)
            ledger = PaperLedger(
                strategy_name=spec.name,
                mode=snapshot.mode,
                settings=self.deployment_config.execution,
                state_dir=self.state_dir,
                scope=self.effective_scope,
            )

            if snapshot.mode == "synthetic":
                snapshot = PaperSignalSnapshot(
                    strategy_name=snapshot.strategy_name,
                    timestamp=snapshot.timestamp,
                    mode=snapshot.mode,
                    target_weights=snapshot.target_weights,
                    instrument_prices=self._update_synthetic_prices(ledger, snapshot),
                    diagnostics=snapshot.diagnostics,
                    metadata=snapshot.metadata,
                )

            ledger_execution_key = (
                f"{self.execution_idempotency_key}:{ledger.ledger_key}"
                if self.execution_idempotency_key is not None
                else None
            )
            summary = ledger.apply_snapshot(snapshot, idempotency_key=ledger_execution_key)
            results[spec.name] = summary
            leaderboard_rows.append(
                {
                    "strategy": spec.name,
                    "pipeline": spec.pipeline,
                    "mode": snapshot.mode,
                    "equity_after": float(summary["equity_after"]),
                    "net_return_since_inception": float(summary["net_return_since_inception"]),
                    "daily_pnl": float(summary["daily_pnl"]),
                    "trade_count": int(summary["trade_count"]),
                    "gross_exposure_ratio": float(summary["gross_exposure_ratio"]),
                }
            )

        leaderboard = pd.DataFrame(leaderboard_rows).sort_values("net_return_since_inception", ascending=False)
        leaderboard.to_parquet(artifact_dir / "paper_leaderboard.parquet")
        (artifact_dir / "paper_leaderboard.json").write_text(
            json.dumps(json_ready(leaderboard), indent=2),
            encoding="utf-8",
        )

        batch_summary = {
            "run_id": run_id,
            "run_timestamp_utc": run_timestamp,
            "asof_date": asof.strftime("%Y-%m-%d"),
            "execution": asdict(self.deployment_config.execution),
            "strategies": results,
            "leaderboard": json_ready(leaderboard),
            "artifact_dir": str(artifact_dir),
            "state_dir": str(self.state_dir),
            "scope": self.effective_scope.to_dict(),
        }

        run_visuals = PaperDashboardVisualizer(artifact_dir / "visuals").create_dashboard(
            batch_summary=batch_summary,
            state_dir=self.state_dir,
        )
        live_dashboard_dir = self.artifact_root.parent / "live_dashboard"
        live_visuals = PaperDashboardVisualizer(live_dashboard_dir).create_dashboard(
            batch_summary=batch_summary,
            state_dir=self.state_dir,
        )
        batch_summary["visuals"] = {
            "run_dashboard": json_ready(run_visuals),
            "live_dashboard": json_ready(live_visuals),
        }
        (artifact_dir / "paper_batch_summary.json").write_text(
            json.dumps(json_ready(batch_summary), indent=2),
            encoding="utf-8",
        )
        return batch_summary


def run_paper_batch(
    deployment_config_path: str | Path,
    *,
    asof_date: str | pd.Timestamp | None = None,
    state_dir: str | Path = "artifacts/paper/state",
    artifact_root: str | Path = "artifacts/paper/runs",
    price_cache_dir: str = "data/cache",
    sentiment_cache_dir: str = "data/sentiment_cache",
    event_cache_dir: str = "data/event_cache",
    price_provider: MarketDataProvider | None = None,
    scope: PaperStateScope | None = None,
    execution_idempotency_key: str | None = None,
) -> dict[str, Any]:
    deployment_config = PaperDeploymentConfig.from_file(deployment_config_path)
    service = PaperTradingService(
        deployment_config=deployment_config,
        price_provider=price_provider,
        state_dir=state_dir,
        artifact_root=artifact_root,
        price_cache_dir=price_cache_dir,
        sentiment_cache_dir=sentiment_cache_dir,
        event_cache_dir=event_cache_dir,
        scope=scope,
        execution_idempotency_key=execution_idempotency_key,
    )
    return service.run(asof_date=asof_date)
