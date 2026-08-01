from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
import json
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

import pandas as pd

from ..data.market import CachedParquetProvider, MarketDataProvider, YahooFinanceProvider
from ..data.fundamentals import (
    FundamentalsProvider,
    SecCompanyFactsFundamentalsProvider,
)
from ..data.research_news import (
    HeadlineMarketResearchNewsProvider,
    MarketResearchNewsProvider,
)
from ..data.sec import SecCompanyFactsClient
from ..core.timeframes import TradingMode, resolve_timeframe_spec
from ..engines.backtesting import json_ready
from ..platform import build_metadata_store
from ..research.market_research_agents import (
    DataProvenance,
    DemoMarketResearchDataProvider,
    MarketResearchContext,
    MarketResearchInput,
    MarketResearchOrchestrator,
    MarketResearchReport,
    MultiStockReport,
    NewsItem,
    PriceBar,
    ResearchHorizon,
    SourceReference,
    normalize_ticker,
    run_multi_stock_research,
)
from ..research.market_research_prompts import RESEARCH_DISCLAIMER
from ..research.nvidia_model_catalog import resolve_nvidia_model
from ..research.stock_universe import StockUniverse, UniverseBuilder
from ..research.decision_history import CommitteeDecision, DecisionHistoryStore
from ..research.chart_data import ChartDataBuilder
from ..research.data_quality import DataQualityValidator, DecisionEvaluationMetrics
from .financial_events import FinancialEventsService
from .headline_factory import build_headline_provider
from .llm_config import build_structured_llm_provider, market_research_runtime_diagnostics, preflight_market_research_llm
from .config import BackendSettings
from .job_queue import QUEUE_NAME, enqueue_quant_job
from .job_dispatch import dispatch_initial_job
from .job_security import collect_secret_values
from .schemas import MarketResearchRunRequest
from .sentiment_services import SentimentService
from .services import JobClaimLostError, _prepare_job_request, _secret_safe_job_data, _validate_max_history
from .storage import build_artifact_storage


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ProviderUnavailable(RuntimeError):
    """Safe retryable failure for the configured market-research data source."""


class BackendMarketResearchDataProvider:
    def __init__(
        self,
        settings: BackendSettings,
        market_data_provider: MarketDataProvider | None = None,
        *,
        news_provider: MarketResearchNewsProvider | None = None,
        fundamentals_provider: FundamentalsProvider | None = None,
    ) -> None:
        self.settings = settings
        self.settings.validate_market_research_data_provider()
        self.market_data_provider = market_data_provider
        self.news_provider = news_provider
        self.fundamentals_provider = fundamentals_provider
        self.demo_provider = DemoMarketResearchDataProvider()
        self.sentiment_service = SentimentService(settings)
        self.financial_events_service = FinancialEventsService(settings, market_data_provider=market_data_provider)

    def _news(self) -> MarketResearchNewsProvider | None:
        if self.news_provider is not None:
            return self.news_provider
        if not self.settings.market_research_news_providers:
            return None
        provider = build_headline_provider(
            self.settings,
            provider_names=self.settings.market_research_news_providers,
            options={
                "maximum_articles": self.settings.market_research_news_max_articles,
                "timeout_seconds": self.settings.market_research_provider_timeout_seconds,
                "secret_refs": {
                    "newsapi": self.settings.market_research_newsapi_api_key_ref,
                    "alphavantage": self.settings.market_research_alphavantage_api_key_ref,
                    "benzinga": self.settings.market_research_benzinga_api_key_ref,
                },
            },
        )
        self.news_provider = HeadlineMarketResearchNewsProvider(
            provider,
            maximum_articles=self.settings.market_research_news_max_articles,
        )
        return self.news_provider

    def _fundamentals(self) -> FundamentalsProvider | None:
        if self.fundamentals_provider is not None:
            return self.fundamentals_provider
        configured = self.settings.market_research_fundamentals_required or bool(self.settings.edgar_user_agent)
        if not configured:
            return None
        user_agent = self.settings.edgar_user_agent or f"TradePilot research {self.settings.email_from}"
        self.fundamentals_provider = SecCompanyFactsFundamentalsProvider(
            SecCompanyFactsClient(
                cache_dir=self.settings.event_cache_dir / "sec",
                user_agent=user_agent,
                timeout_seconds=self.settings.market_research_provider_timeout_seconds,
            )
        )
        return self.fundamentals_provider

    def collect(
        self,
        request: MarketResearchInput,
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
    ) -> MarketResearchContext:
        del user_id
        configured_provider = self.settings.market_research_data_provider.strip().lower()
        if configured_provider != "cached_yahoo":
            context = self.demo_provider.collect(request)
            context.provider_metadata.update(
                {
                    "backend_data_provider": "demo",
                    "configured_data_provider": configured_provider,
                    "effective_data_provider": "demo",
                    "synthetic_data_used": True,
                    "demo_fallback_used": False,
                    "degraded": True,
                    "degraded_components": ["live_market_data", "real_news", "fundamentals"],
                }
            )
            return context

        try:
            context = self._collect_cached_yahoo(request)
            return self._enrich_context(context, request, organization_id=organization_id)
        except Exception:
            if self.settings.is_production or not self.settings.market_research_allow_demo_fallback:
                raise ProviderUnavailable(
                    "The configured market research data provider is temporarily unavailable."
                ) from None
            context = self.demo_provider.collect(request)
            warning = "Cached Yahoo market data was unavailable; explicit development demo fallback was used."
            context.warnings.append(warning)
            context.data_quality_notes.append(warning)
            context.provider_metadata.update(
                {
                    "backend_data_provider": "demo_fallback",
                    "configured_data_provider": "cached_yahoo",
                    "effective_data_provider": "demo",
                    "synthetic_data_used": True,
                    "demo_fallback_used": True,
                    "degraded": True,
                    "degraded_components": ["price_history", "real_news", "fundamentals"],
                    "fallback_reason": "provider_unavailable",
                }
            )
            return context

    def _provider(self) -> MarketDataProvider:
        if self.market_data_provider is None:
            self.market_data_provider = CachedParquetProvider(
                upstream=YahooFinanceProvider(tz_cache_dir=self.settings.price_cache_dir / "yfinance_tz_cache"),
                cache_dir=self.settings.price_cache_dir,
            )
        return self.market_data_provider

    @staticmethod
    def _lookback_days(horizon: ResearchHorizon | str, override: int | None = None) -> int:
        if override is not None:
            return max(5, min(int(override), 900))
        value = str(horizon)
        if value == ResearchHorizon.INTRADAY.value:
            return 45
        if value == ResearchHorizon.LONG_TERM.value:
            return 540
        return 180

    def _collect_cached_yahoo(self, request: MarketResearchInput) -> MarketResearchContext:
        asof = date.fromisoformat(request.analysis_date)
        lookback = self._lookback_days(request.horizon, request.lookback_days)
        start = asof - timedelta(days=lookback)
        end = asof + timedelta(days=1)
        timeframe = resolve_timeframe_spec(
            trading_mode=TradingMode.SHORT_TERM if str(request.horizon) == ResearchHorizon.INTRADAY.value else TradingMode.DAILY,
        )
        prices = self._provider().get_close_prices(
            [request.ticker],
            start=start.isoformat(),
            end=end.isoformat(),
            interval=timeframe.execution_interval,
        )
        if prices.empty or request.ticker not in prices.columns:
            raise ValueError(f"No close-price rows returned for {request.ticker}.")
        series = pd.to_numeric(prices[request.ticker], errors="coerce").dropna().sort_index()
        if series.empty:
            raise ValueError(f"Close-price rows for {request.ticker} were empty after cleaning.")
        effective_start = start
        extension_warning: str | None = None
        used_extended_window = False
        if len(series) < 20 and lookback < 365:
            extended_start = asof - timedelta(days=365)
            try:
                extended_prices = self._provider().get_close_prices(
                    [request.ticker],
                    start=extended_start.isoformat(),
                    end=end.isoformat(),
                    interval=timeframe.execution_interval,
                )
            except Exception:
                extension_warning = "Price history had fewer than 20 rows and the 365-day extension was unavailable."
            else:
                if request.ticker in extended_prices.columns:
                    extended_series = pd.to_numeric(extended_prices[request.ticker], errors="coerce").dropna().sort_index()
                    if len(extended_series) > len(series):
                        series = extended_series
                        effective_start = extended_start
                        used_extended_window = True
        bars = [
            PriceBar(
                date=pd.Timestamp(index).strftime("%Y-%m-%dT%H:%M:%S") if timeframe.mode == TradingMode.SHORT_TERM else pd.Timestamp(index).strftime("%Y-%m-%d"),
                close=round(float(value), 6),
            )
            for index, value in series.items()
        ]
        warnings = [warning for warning in (extension_warning,) if warning]
        missing: list[str] = []
        degraded_components: list[str] = []
        normalized_news = []
        fundamentals = None
        component_provenance: list[DataProvenance] = []
        source_references: list[SourceReference] = []

        news_provider = self._news()
        fundamentals_provider = self._fundamentals()
        futures: dict[str, Future[Any]] = {}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="research-evidence") as executor:
            if news_provider is not None:
                news_start = asof - timedelta(days=self.settings.market_research_news_lookback_days)
                futures["news"] = executor.submit(news_provider.get_news, request.ticker, news_start, asof)
            if fundamentals_provider is not None:
                futures["fundamentals"] = executor.submit(fundamentals_provider.get_snapshot, request.ticker, asof)
            for component, future in futures.items():
                try:
                    value = future.result(timeout=self.settings.market_research_provider_timeout_seconds + 1.0)
                except Exception:
                    required = (
                        self.settings.market_research_news_required
                        if component == "news"
                        else self.settings.market_research_fundamentals_required
                    )
                    if required:
                        raise ProviderUnavailable(
                            f"The required market research {component} component is temporarily unavailable."
                        ) from None
                    missing.append(component)
                    degraded_components.append(component)
                    warnings.append(f"The {component} component was unavailable; no substitute evidence was generated.")
                else:
                    if component == "news":
                        normalized_news = list(value)
                    else:
                        fundamentals = value

        if news_provider is None:
            missing.append("direct_news_provider")
            degraded_components.append("direct_news")
            warnings.append("No direct news provider is configured for this report.")
        elif not normalized_news:
            missing.append("news")
            degraded_components.append("news")
            warnings.append("Configured news providers returned no point-in-time headlines.")
        else:
            component_provenance.append(
                DataProvenance(
                    source="news",
                    provider="/".join(sorted({item.provider for item in normalized_news})),
                    detail=f"{len(normalized_news)} normalized headline(s), filtered through {request.analysis_date}.",
                )
            )
            for index, item in enumerate(normalized_news):
                source_references.append(
                    SourceReference(
                        id=f"news-{index + 1}",
                        source="news",
                        provider=item.provider,
                        title=item.headline,
                        observed_at_utc=item.timestamp,
                        url=item.url,
                        confidence=item.confidence,
                        verified=bool(item.url),
                    )
                )

        if fundamentals_provider is None:
            missing.append("fundamentals_provider")
            degraded_components.append("fundamentals")
            warnings.append("No normalized fundamentals provider is configured for this report.")
        elif fundamentals is None:
            if "fundamentals" not in missing:
                missing.append("fundamentals")
                degraded_components.append("fundamentals")
        else:
            component_provenance.append(
                DataProvenance(
                    source="fundamentals",
                    provider=fundamentals.provider,
                    detail=f"SEC Company Facts snapshot available as of {request.analysis_date}; {len(fundamentals.missing_fields)} field(s) missing.",
                )
            )
            for field_name in (
                "revenue", "net_income", "diluted_eps", "cash", "total_assets",
                "total_liabilities", "debt", "shares_outstanding",
            ):
                fact = getattr(fundamentals, field_name)
                if fact is None:
                    continue
                source_references.append(
                    SourceReference(
                        id=f"fundamentals-{field_name}",
                        source="fundamentals",
                        provider=fundamentals.provider,
                        title=f"{field_name.replace('_', ' ').title()} ({fact.concept})",
                        observed_at_utc=f"{fact.filing_date}T00:00:00Z",
                        url=fact.source_url,
                        confidence=0.85,
                        verified=True,
                    )
                )

        if news_provider is None and fundamentals_provider is None:
            warnings.append(
                "Research context is degraded because direct news and fundamentals providers are not configured."
            )

        news_items = [
            NewsItem(
                timestamp=item.timestamp,
                headline=item.headline,
                source=item.source,
                url=item.url,
                sentiment_score=item.sentiment_score,
            )
            for item in normalized_news
        ]
        return MarketResearchContext(
            ticker=request.ticker,
            analysis_date=request.analysis_date,
            horizon=request.horizon,
            price_history=bars,
            news=news_items,
            fundamentals=fundamentals,
            provenance=[
                DataProvenance(
                    source="price_history",
                    provider="CachedParquetProvider/YahooFinanceProvider",
                    detail=f"{timeframe.execution_interval} close prices from {effective_start.isoformat()} through {request.analysis_date}.",
                ),
                *component_provenance,
            ],
            source_references=source_references,
            data_freshness={
                "prices": bars[-1].date if bars else None,
                "news": normalized_news[0].timestamp if normalized_news else None,
                "fundamentals": fundamentals.filing_dates[-1] if fundamentals and fundamentals.filing_dates else None,
            },
            missing_data_indicators=list(dict.fromkeys(missing)),
            data_quality_notes=list(warnings),
            warnings=list(warnings),
            provider_metadata={
                "backend_data_provider": "cached_yahoo",
                "configured_data_provider": "cached_yahoo",
                "effective_data_provider": "cached_yahoo",
                "synthetic_data_used": False,
                "degraded": bool(degraded_components),
                "degraded_components": list(dict.fromkeys(degraded_components)),
                "price_rows": len(bars),
                "news_rows": len(news_items),
                "fundamentals_fields": 0 if fundamentals is None else 8 - len(fundamentals.missing_fields),
                "trading_mode": str(timeframe.mode),
                "execution_interval": timeframe.execution_interval,
                "signal_intervals": list(timeframe.signal_intervals),
                "price_extended_window": used_extended_window,
                "model": request.model,
            },
        )

    def _enrich_context(self, context: MarketResearchContext, request: MarketResearchInput, *, organization_id: str | None) -> MarketResearchContext:
        enriched = context
        if request.include_sentiment:
            enriched = self._attach_sentiment(enriched, request, organization_id=organization_id)
        if request.include_financial_events:
            enriched = self._attach_financial_events(enriched, request)
        return enriched

    def _window(self, request: MarketResearchInput) -> tuple[date, date]:
        asof = date.fromisoformat(request.analysis_date)
        start = asof - timedelta(days=self._lookback_days(request.horizon, request.lookback_days))
        return start, asof

    def _attach_sentiment(
        self,
        context: MarketResearchContext,
        request: MarketResearchInput,
        *,
        organization_id: str | None,
    ) -> MarketResearchContext:
        if not organization_id:
            warning = "Sentiment context skipped because no tenant organization was available."
            return context.model_copy(
                update={
                    "warnings": [*context.warnings, warning],
                    "data_quality_notes": [*context.data_quality_notes, warning],
                    "missing_data_indicators": [*context.missing_data_indicators, "sentiment_dataset"],
                }
            )
        try:
            payload = self.sentiment_service.dataset(
                dataset_id=request.sentiment_dataset_id,
                organization_id=organization_id,
            )
        except Exception:
            warning = "The configured sentiment dataset was unavailable."
            return context.model_copy(
                update={
                    "warnings": [*context.warnings, warning],
                    "data_quality_notes": [*context.data_quality_notes, warning],
                    "missing_data_indicators": [*context.missing_data_indicators, "sentiment_dataset"],
                    "provenance": [
                        *context.provenance,
                        DataProvenance(source="sentiment", provider="SentimentService", detail=warning),
                    ],
                }
            )

        ticker = request.ticker.upper()
        start, end = self._window(request)
        daily_points = []
        for row in payload.get("daily_points", []) or []:
            if str(row.get("ticker") or "").upper() != ticker:
                continue
            row_date = pd.to_datetime(row.get("date"), errors="coerce")
            if pd.isna(row_date):
                continue
            current = row_date.date()
            if start <= current <= end:
                daily_points.append(row)
        daily_points = daily_points[-120:]

        news_items: list[NewsItem] = []
        source_refs: list[SourceReference] = list(context.source_references)
        for row in (payload.get("scored_headlines") or payload.get("headlines") or [])[:80]:
            row_ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
            if row_ticker and row_ticker != ticker:
                continue
            timestamp = str(row.get("timestamp") or row.get("date") or f"{request.analysis_date}T00:00:00Z")
            title = str(row.get("headline") or row.get("title") or row.get("summary") or "Sentiment headline")
            score = row.get("score", row.get("sentiment_score"))
            try:
                sentiment_score = float(score) if score is not None else None
            except (TypeError, ValueError):
                sentiment_score = None
            source = str(row.get("source") or row.get("provider_name") or "sentiment_dataset")
            url = row.get("url")
            news_items.append(NewsItem(timestamp=timestamp, headline=title[:500], source=source, url=str(url) if url else None, sentiment_score=sentiment_score))
            source_refs.append(
                SourceReference(
                    id=f"sentiment-{ticker}-{len(source_refs) + 1}",
                    source="sentiment",
                    provider=source,
                    title=title[:240],
                    url=str(url) if url else None,
                    confidence=float(row.get("confidence")) if isinstance(row.get("confidence"), (int, float)) else None,
                    verified=False,
                )
            )

        warnings = [str(item) for item in payload.get("warnings", []) if str(item).strip()]
        latest_date = None
        if daily_points:
            latest_date = max(str(point.get("date") or "")[:10] for point in daily_points)
        notes = list(context.data_quality_notes)
        missing = list(context.missing_data_indicators)
        if not daily_points:
            notes.append("No matching daily sentiment rows were found for the ticker and research window.")
            missing.append("sentiment_matrix")
        if not news_items:
            notes.append("No matching scored headlines were found for the ticker and research window.")
            missing.append("sentiment_headlines")

        analysis = {
            "summary": payload.get("summary", {}),
            "ticker_summary": [row for row in payload.get("ticker_summary", []) if str(row.get("ticker") or "").upper() == ticker],
            "source_summary": payload.get("source_summary", []),
            "dataset_id": payload.get("dataset_id"),
            "warnings": warnings,
        }
        return context.model_copy(
            update={
                "sentiment_matrix": daily_points,
                "sentiment_analysis": analysis,
                "news": news_items or context.news,
                "source_references": source_refs[:80],
                "provenance": [
                    *context.provenance,
                    DataProvenance(
                        source="sentiment",
                        provider="SentimentService",
                        detail=f"Loaded sentiment dataset {payload.get('dataset_id') or 'default'} with {len(daily_points)} matching daily row(s).",
                    ),
                ],
                "data_freshness": {**context.data_freshness, "sentiment": latest_date},
                "confidence_levels": {**context.confidence_levels, "sentiment": 0.6 if daily_points else 0.15},
                "missing_data_indicators": list(dict.fromkeys(missing)),
                "data_quality_notes": list(dict.fromkeys([*notes, *warnings])),
                "warnings": list(dict.fromkeys([*context.warnings, *warnings])),
                "provider_metadata": {
                    **context.provider_metadata,
                    "sentiment_dataset_id": payload.get("dataset_id"),
                    "sentiment_daily_rows": len(daily_points),
                    "sentiment_headline_rows": len(news_items),
                },
            }
        )

    def _attach_financial_events(self, context: MarketResearchContext, request: MarketResearchInput) -> MarketResearchContext:
        start, end = self._window(request)
        try:
            payload = self.financial_events_service.events([request.ticker], start.isoformat(), end.isoformat(), limit=80)
        except Exception:
            warning = "The financial-events provider was unavailable."
            return context.model_copy(
                update={
                    "warnings": [*context.warnings, warning],
                    "data_quality_notes": [*context.data_quality_notes, warning],
                    "missing_data_indicators": [*context.missing_data_indicators, "financial_events"],
                    "provenance": [
                        *context.provenance,
                        DataProvenance(source="financial_events", provider="FinancialEventsService", detail=warning),
                    ],
                }
            )
        rows = list(payload.get("events", []) or [])
        warnings = [str(item) for item in payload.get("warnings", []) if str(item).strip()]
        source_refs = list(context.source_references)
        for row in rows[:50]:
            url = row.get("source_url")
            source_refs.append(
                SourceReference(
                    id=str(row.get("id") or f"event-{request.ticker}-{len(source_refs) + 1}"),
                    source="financial_events",
                    provider=str(row.get("source") or "FinancialEventsService"),
                    title=str(row.get("event_title") or row.get("summary") or row.get("event_type") or "Financial event")[:240],
                    url=str(url) if url else None,
                    confidence=float(row.get("confidence")) if isinstance(row.get("confidence"), (int, float)) else None,
                    verified=True,
                )
            )
        notes = list(context.data_quality_notes)
        missing = list(context.missing_data_indicators)
        if not rows:
            notes.append("No verified financial event rows were found for the ticker and research window.")
            missing.append("financial_events")
        latest_date = max((str(row.get("date") or "")[:10] for row in rows), default=None)
        return context.model_copy(
            update={
                "financial_events": rows,
                "financial_events_analysis": payload.get("analysis", {}),
                "source_references": source_refs[:100],
                "provenance": [
                    *context.provenance,
                    DataProvenance(
                        source="financial_events",
                        provider="FinancialEventsService",
                        detail=f"Loaded {len(rows)} verified/inferred financial event row(s) from {start.isoformat()} through {end.isoformat()}.",
                    ),
                ],
                "data_freshness": {**context.data_freshness, "financial_events": latest_date},
                "confidence_levels": {**context.confidence_levels, "financial_events": 0.65 if rows else 0.15},
                "missing_data_indicators": list(dict.fromkeys(missing)),
                "data_quality_notes": list(dict.fromkeys([*notes, *warnings, *(payload.get("analysis", {}).get("missing_data", []) or [])])),
                "warnings": list(dict.fromkeys([*context.warnings, *warnings])),
                "provider_metadata": {
                    **context.provider_metadata,
                    "financial_event_rows": len(rows),
                    "financial_event_sources": payload.get("summary", {}).get("sources", []),
                },
            }
        )


class StockUniverseService:
    def __init__(self, universe_path: str | Path = "data/stock_universe.json") -> None:
        self.builder = UniverseBuilder(universe_path)
        self._universe: StockUniverse | None = None

    def get_universe(self, force_reload: bool = False) -> StockUniverse:
        if self._universe is None or force_reload:
            self._universe = self.builder.load_or_build()
        return self._universe

    def get_filtered(
        self,
        *,
        sector: str | None = None,
        industry: str | None = None,
        country: str | None = None,
        exchange: str | None = None,
        currency: str | None = None,
        min_liquid: bool | None = None,
        min_liquidity: bool | None = None,
        is_liquid: bool | None = None,
        tickers: set[str] | None = None,
        **_: Any,
    ) -> StockUniverse:
        universe = self.get_universe()
        liquidity_filter = min_liquid
        if liquidity_filter is None:
            liquidity_filter = min_liquidity
        if liquidity_filter is None:
            liquidity_filter = is_liquid
        return universe.filter(
            sector=sector,
            industry=industry,
            country=country,
            exchange=exchange,
            currency=currency,
            min_liquid=liquidity_filter,
            tickers=tickers,
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
        self.universe_service = StockUniverseService()
        self.decision_store = DecisionHistoryStore(settings)
        self.chart_builder = ChartDataBuilder()
        self.quality_validator = DataQualityValidator()
        self.eval_metrics = DecisionEvaluationMetrics()

    def validate_request(self, request: MarketResearchRunRequest) -> MarketResearchInput:
        return self._input(request)

    def runtime_diagnostics(self) -> dict[str, object]:
        return market_research_runtime_diagnostics(self.settings)

    def preflight_runtime(self, request: MarketResearchRunRequest | None = None) -> None:
        try:
            preflight_market_research_llm(self._effective_settings(request))
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    def _with_provider_runtime_limits(self, settings: BackendSettings) -> BackendSettings:
        provider = settings.market_research_llm_provider.strip().lower()
        if provider != "nvidia":
            return settings
        timeout_cap = max(5.0, float(settings.market_research_free_endpoint_timeout_cap_seconds))
        llm_timeout = min(settings.market_research_llm_timeout_seconds, timeout_cap)
        agent_timeout = min(settings.market_research_agent_timeout_seconds, llm_timeout + 5.0, timeout_cap + 5.0)
        return replace(
            settings,
            market_research_agent_timeout_seconds=agent_timeout,
            market_research_llm_timeout_seconds=llm_timeout,
        )

    def _effective_settings(self, request: MarketResearchRunRequest | None = None) -> BackendSettings:
        if request is None or self.settings.is_production or not self.settings.market_research_allow_request_model_override:
            return self._with_provider_runtime_limits(self.settings)
        provider = str(request.provider or "").strip().lower()
        model = str(request.model or "").strip()
        if not provider and not model:
            return self._with_provider_runtime_limits(self.settings)
        provider = provider or self.settings.market_research_llm_provider
        model = model or self.settings.market_research_llm_model
        if provider == "nvidia":
            spec = resolve_nvidia_model(model)
            if spec is None:
                raise ValueError(f"NVIDIA model '{model}' is not in the vetted research catalog.")
            if not spec.market_research_compatible:
                raise ValueError(f"NVIDIA model '{spec.id}' is a {spec.category} endpoint and cannot run the market research committee.")
            model = spec.id
        return self._with_provider_runtime_limits(
            replace(
                self.settings,
                market_research_llm_provider=provider,
                market_research_llm_model=model,
            )
        )

    def _input(self, request: MarketResearchRunRequest, effective_settings: BackendSettings | None = None) -> MarketResearchInput:
        runtime_settings = effective_settings or self._effective_settings(request)
        return MarketResearchInput(
            ticker=request.ticker,
            analysis_date=request.analysis_date or date.today().isoformat(),
            horizon=ResearchHorizon(str(request.horizon)),
            provider=runtime_settings.market_research_llm_provider,
            model=runtime_settings.market_research_llm_model,
            sentiment_dataset_id=request.sentiment_dataset_id,
            include_sentiment=request.include_sentiment,
            include_financial_events=request.include_financial_events,
            lookback_days=request.lookback_days,
            options=request.options or {},
            pair=request.pair,
        )

    def _pair_for_request(self, request: MarketResearchRunRequest) -> tuple[str, str] | None:
        if not request.pair:
            return None
        parts = [normalize_ticker(part) for part in request.pair.split(",") if part.strip()]
        if len(parts) != 2:
            raise ValueError("pair must contain exactly two valid ticker symbols separated by a comma.")
        if parts[0] == parts[1]:
            raise ValueError("pair tickers must be different.")
        return parts[0], parts[1]

    def _tickers_for_request(self, request: MarketResearchRunRequest, *, max_universe_tickers: int = 20) -> list[str]:
        pair = self._pair_for_request(request)
        if pair is not None:
            return list(pair)
        if request.universe_filter:
            allowed_filter_keys = {
                "sector",
                "industry",
                "country",
                "exchange",
                "currency",
                "min_liquid",
                "min_liquidity",
                "is_liquid",
                "tickers",
            }
            raw_filter = {key: value for key, value in request.universe_filter.items() if key in allowed_filter_keys}
            raw_tickers = raw_filter.get("tickers")
            if isinstance(raw_tickers, list):
                raw_filter["tickers"] = {normalize_ticker(str(ticker)) for ticker in raw_tickers}
            universe = self.universe_service.get_filtered(**raw_filter)
            selected = universe.tickers()[:max_universe_tickers]
            if not selected:
                raise ValueError("Universe filter did not match any stocks.")
            return selected
        if request.tickers:
            return [normalize_ticker(ticker) for ticker in request.tickers]
        return [normalize_ticker(request.ticker)]

    def generate_report(
        self,
        request: MarketResearchRunRequest,
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
        job_id: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> MarketResearchReport:
        runtime_settings = self._effective_settings(request)
        research_input = self._input(request, runtime_settings)
        if progress_callback is not None:
            progress_callback(
                {
                    "event_type": "data_collection_started",
                    "provider": runtime_settings.market_research_llm_provider,
                    "model": runtime_settings.market_research_llm_model,
                }
            )
        context = self.data_provider.collect(research_input, organization_id=organization_id, user_id=user_id)
        if progress_callback is not None:
            progress_callback(
                {
                    "event_type": "data_collection_completed",
                    "provider": runtime_settings.market_research_llm_provider,
                    "model": runtime_settings.market_research_llm_model,
                    "price_bar_count": len(context.price_history),
                    "news_count": len(context.news),
                    "financial_event_count": len(context.financial_events),
                    "warning_count": len(context.warnings),
                }
            )
        context.provider_metadata["job_id"] = job_id
        context.provider_metadata["user_scoped"] = bool(user_id)
        orchestrator = MarketResearchOrchestrator(
            llm_provider=build_structured_llm_provider(runtime_settings),
            per_agent_timeout_seconds=runtime_settings.market_research_agent_timeout_seconds,
            max_llm_failures=runtime_settings.market_research_llm_fail_fast_after_failures,
            progress_callback=progress_callback,
        )
        return orchestrator.run(context)

    def generate_multi_report(
        self,
        request: MarketResearchRunRequest,
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
        job_id: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        persist_decisions: bool = True,
    ) -> MultiStockReport:
        runtime_settings = self._effective_settings(request)
        tickers = self._tickers_for_request(request)
        pair_tuple = self._pair_for_request(request)
        llm_provider = build_structured_llm_provider(runtime_settings)

        result = run_multi_stock_research(
            tickers=tickers,
            analysis_date=request.analysis_date or date.today().isoformat(),
            horizon=ResearchHorizon(str(request.horizon)),
            llm_provider=llm_provider,
            data_provider=self.data_provider,
            data_provider_kwargs={"organization_id": organization_id, "user_id": user_id},
            per_agent_timeout_seconds=runtime_settings.market_research_agent_timeout_seconds,
            max_llm_failures=runtime_settings.market_research_llm_fail_fast_after_failures,
            progress_callback=progress_callback,
            pair=pair_tuple,
        )

        if persist_decisions:
            self._record_decisions(result, organization_id=organization_id, user_id=user_id, job_id=job_id)
        return result

    def _record_decisions(
        self,
        multi_report: MultiStockReport,
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
        job_id: str | None = None,
        persist: bool = True,
    ) -> list[CommitteeDecision]:
        decisions: list[CommitteeDecision] = []
        for report in multi_report.reports:
            signals_summary = {
                s.label: {"direction": s.direction, "strength": s.strength}
                for s in report.technical_signals + report.fundamental_signals + report.news_sentiment_signals
            }
            quality = self.quality_validator.validate_report(report)
            evaluation = self.eval_metrics.evaluate(report)
            pair_ticker = None
            if multi_report.pair:
                parts = multi_report.pair.split(",")
                if len(parts) == 2:
                    other = parts[1] if parts[0].upper() == report.ticker.upper() else parts[0]
                    pair_ticker = other

            decision = CommitteeDecision(
                ticker=report.ticker,
                pair_ticker=pair_ticker,
                analysis_date=report.analysis_date,
                horizon=str(report.time_horizon),
                decision=report.decision,
                confidence=report.confidence,
                reasoning=report.summary,
                signals_summary=signals_summary,
                market_metrics=evaluation,
                data_quality=quality,
                evaluation=evaluation,
                recommendation=report.decision,
                llm_provider=report.metadata.get("llm_provider", ""),
                llm_model=report.metadata.get("llm_model", ""),
                organization_id=organization_id,
                user_id=user_id,
                job_id=job_id,
                report_id=report.report_id if hasattr(report, "report_id") else None,
            )
            decisions.append(decision)
            if persist:
                self.decision_store.add(decision)
        return decisions

    def get_chart_data(
        self,
        request: MarketResearchRunRequest,
        report: MarketResearchReport | None = None,
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        tickers = self._tickers_for_request(request)

        prices: dict[str, list[PriceBar]] = {}
        for t in tickers:
            inp = self._input(request.model_copy(update={"ticker": t}))
            try:
                context = self.data_provider.collect(inp, organization_id=organization_id, user_id=user_id)
                prices[t] = context.price_history
            except Exception:
                prices[t] = []

        pair_tuple = self._pair_for_request(request)

        rec_dates = None
        if report:
            rec_dates = [report.analysis_date]

        return self.chart_builder.build_all(
            prices=prices,
            tickers=tickers,
            recommendation_dates=rec_dates,
            pair=pair_tuple,
        )

    def record_decision(
        self,
        report: MarketResearchReport,
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
        job_id: str | None = None,
        persist: bool = True,
    ) -> CommitteeDecision:
        signals_summary = {
            s.label: {"direction": s.direction, "strength": s.strength}
            for s in report.technical_signals + report.fundamental_signals + report.news_sentiment_signals
        }
        quality = self.quality_validator.validate_report(report)
        evaluation = self.eval_metrics.evaluate(report)
        decision = CommitteeDecision(
            ticker=report.ticker,
            analysis_date=report.analysis_date,
            horizon=str(report.time_horizon),
            decision=report.decision,
            confidence=report.confidence,
            reasoning=report.summary,
            signals_summary=signals_summary,
            market_metrics=evaluation,
            data_quality=quality,
            evaluation=evaluation,
            recommendation=report.decision,
            llm_provider=report.metadata.get("llm_provider", ""),
            llm_model=report.metadata.get("llm_model", ""),
            organization_id=organization_id,
            user_id=user_id,
            job_id=job_id,
        )
        if persist:
            self.decision_store.add(decision)
        return decision


@dataclass
class MarketResearchJob:
    id: str
    status: str
    request: dict[str, Any]
    created_at_utc: str
    updated_at_utc: str
    organization_id: str | None = None
    user_id: str | None = None
    report_id: str | None = None
    parent_report_id: str | None = None
    progress: float = 0.0
    stage: str = "queued"
    message: str = "Waiting for a market research worker."
    warnings: list[str] = field(default_factory=list)
    progress_events: list[dict[str, Any]] = field(default_factory=list)
    started_at_utc: str | None = None
    finished_at_utc: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    version: int = 0
    attempt: int = 0
    max_attempts: int = 3
    worker_id: str | None = None
    heartbeat_at_utc: str | None = None
    lease_expires_at_utc: str | None = None
    rq_job_id: str | None = None
    kind: str | None = None
    dispatch_state: str | None = None
    dispatch_attempted_at_utc: str | None = None
    dispatch_error_class: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "request": self.request,
            "created_at_utc": self.created_at_utc,
            "updated_at_utc": self.updated_at_utc,
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "report_id": self.report_id,
            "parent_report_id": self.parent_report_id,
            "progress": self.progress,
            "stage": self.stage,
            "message": self.message,
            "warnings": self.warnings,
            "progress_events": self.progress_events,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "result": self.result,
            "error": self.error,
            "version": self.version,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "worker_id": self.worker_id,
            "heartbeat_at_utc": self.heartbeat_at_utc,
            "lease_expires_at_utc": self.lease_expires_at_utc,
            "rq_job_id": self.rq_job_id,
            "kind": self.kind,
            "dispatch_state": self.dispatch_state,
            "dispatch_attempted_at_utc": self.dispatch_attempted_at_utc,
            "dispatch_error_class": self.dispatch_error_class,
        }


class MarketResearchJobRunner:
    def __init__(
        self,
        settings: BackendSettings,
        *,
        max_workers: int = 1,
        max_history: int = 50,
        mark_interrupted_on_load: bool = True,
        claimed_worker_id: str | None = None,
        claimed_attempt: int = 0,
        ownership_guard: Callable[[], None] | None = None,
    ) -> None:
        self.settings = settings
        self.max_history = _validate_max_history(max_history)
        self.mark_interrupted_on_load = mark_interrupted_on_load
        self.claimed_worker_id = claimed_worker_id
        self.claimed_attempt = int(claimed_attempt)
        self.ownership_guard = ownership_guard
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="market-research") if settings.enable_in_process_jobs else None
        self.lock = Lock()
        self.jobs: dict[str, MarketResearchJob] = {}
        self._secret_values: dict[str, set[str]] = {}
        self.jobs_dir = settings.market_research_job_state_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.report_root = settings.market_research_artifact_root
        self.report_root.mkdir(parents=True, exist_ok=True)
        self.metadata_store = build_metadata_store(settings)
        self.artifact_storage = build_artifact_storage(settings)
        if settings.enable_in_process_jobs:
            self._load_jobs()

    def _assert_claim(self, job_id: str) -> None:
        if self.claimed_worker_id is None:
            return
        if self.ownership_guard is not None:
            self.ownership_guard()
        try:
            owned = self.metadata_store.assert_job_claim(
                kind="market_research", job_id=job_id, worker_id=self.claimed_worker_id
            )
        except JobClaimLostError:
            raise
        except Exception as exc:
            from .services import JobClaimCheckError

            raise JobClaimCheckError("Market research claim ownership could not be confirmed.") from exc
        if owned is None:
            raise JobClaimLostError(f"Market research job claim is no longer owned by this worker: {job_id}")

    def _attempt_id(self, job_id: str) -> str:
        if self.claimed_worker_id is None:
            return job_id
        return self.metadata_store.stable_id(
            "attempt", f"{job_id}:{self.claimed_attempt}:{self.claimed_worker_id}"
        )

    def submit(
        self,
        request: MarketResearchRunRequest,
        *,
        organization_id: str,
        user_id: str | None = None,
        parent_report_id: str | None = None,
    ) -> dict[str, Any]:
        service = MarketResearchService(self.settings)
        service.validate_request(request)
        service.preflight_runtime(request)
        runtime_settings = service._effective_settings(request)
        now = _utc_now_iso()
        job_id = uuid4().hex
        persisted_request, secrets = _prepare_job_request(
            {
                **json_ready(request.model_dump(mode="json")),
                "provider": runtime_settings.market_research_llm_provider,
                "model": runtime_settings.market_research_llm_model,
            },
            external_worker=not self.settings.enable_in_process_jobs,
        )
        job = MarketResearchJob(
            id=job_id,
            status="queued",
            request=persisted_request,
            created_at_utc=now,
            updated_at_utc=now,
            organization_id=organization_id,
            user_id=user_id,
            report_id=self.metadata_store.stable_id("mrr", f"{organization_id}:{user_id or 'machine'}:{job_id}"),
            parent_report_id=parent_report_id,
            progress=0.02,
            stage="queued",
            message=(
                "Queued locally. The research committee will collect data and run analyst agents."
                if self.settings.enable_in_process_jobs
                else f"Queued in Redis/RQ ({QUEUE_NAME}). Waiting for worker heartbeat."
            ),
            max_attempts=self.settings.job_max_attempts,
        )
        with self.lock:
            self._secret_values[job.id] = secrets
            self.jobs[job.id] = job
            self._save_locked(job)
            self._trim_locked()
        self._persist_report_record(job, status="queued")

        if self.settings.enable_in_process_jobs and self.executor is not None:
            future = self.executor.submit(self._run_job, job.id, request, organization_id)
            future.add_done_callback(lambda completed: self._finalize_unhandled(job.id, completed))
        else:
            dispatch_initial_job(
                self.settings,
                kind="market_research",
                job_id=job.id,
                metadata_store=self.metadata_store,
                enqueue=enqueue_quant_job,
            )
        return self.get_job(job.id, organization_id=organization_id) or _secret_safe_job_data(job.to_dict(), secrets)

    def list_jobs(self, *, organization_id: str) -> list[dict[str, Any]]:
        return [
            _secret_safe_job_data(payload)
            for payload in self.metadata_store.list_jobs(
                kind="market_research",
                organization_id=organization_id,
                limit=self.max_history,
            )
        ]

    def get_job(self, job_id: str, *, organization_id: str) -> dict[str, Any] | None:
        payload = self.metadata_store.get_job(kind="market_research", job_id=job_id, organization_id=organization_id)
        return None if payload is None else _secret_safe_job_data(payload)

    def _load_job_for_update_locked(self, job_id: str) -> MarketResearchJob:
        job = self.jobs.get(job_id)
        if job is not None:
            return job
        payload = self.metadata_store.get_job(kind="market_research", job_id=job_id)
        if payload is None:
            raise KeyError(job_id)
        safe_payload = _secret_safe_job_data(payload)
        job = MarketResearchJob(**safe_payload)
        self.jobs[job.id] = job
        self._secret_values.setdefault(job.id, collect_secret_values(payload))
        return job

    def _set_status(self, job_id: str, status: str, **updates: Any) -> None:
        now = _utc_now_iso()
        with self.lock:
            job = self._load_job_for_update_locked(job_id)
            safe_updates = _secret_safe_job_data(updates, self._secret_values.get(job_id))
            if self.claimed_worker_id is not None:
                if status == "running":
                    payload = self.metadata_store.update_claimed_job(
                        kind="market_research",
                        job_id=job_id,
                        worker_id=self.claimed_worker_id,
                        updates=safe_updates,
                    )
                else:
                    payload = self.metadata_store.release_job_claim(
                        kind="market_research",
                        job_id=job_id,
                        worker_id=self.claimed_worker_id,
                        status=status,
                        updates=safe_updates,
                    )
                if payload is None:
                    raise JobClaimLostError(f"Market-research job claim is no longer owned by this worker: {job_id}")
                self.jobs[job_id] = MarketResearchJob(**_secret_safe_job_data(payload, self._secret_values.get(job_id)))
                return
            job.status = status
            job.updated_at_utc = now
            for key, value in safe_updates.items():
                setattr(job, key, value)
            self._save_locked(job)

    @staticmethod
    def _safe_progress_event(event: dict[str, Any]) -> dict[str, Any]:
        allowed_keys = {
            "event_type",
            "timestamp_utc",
            "provider",
            "model",
            "ticker",
            "agent_name",
            "display_name",
            "agent_version",
            "agent_index",
            "total_agents",
            "status",
            "duration_ms",
            "latency_ms",
            "confidence",
            "signal_count",
            "warning_count",
            "price_bar_count",
            "news_count",
            "financial_event_count",
            "usage",
            "error",
        }
        safe = {key: value for key, value in event.items() if key in allowed_keys}
        safe.setdefault("timestamp_utc", _utc_now_iso())
        safe["event_type"] = str(safe.get("event_type") or "progress")
        if "error" in safe:
            safe["error"] = str(safe["error"])[:500]
        if "usage" in safe and not isinstance(safe["usage"], dict):
            safe.pop("usage", None)
        return json_ready(safe)

    @staticmethod
    def _progress_status_from_event(event: dict[str, Any]) -> tuple[float, str, str]:
        event_type = str(event.get("event_type") or "progress")
        display_name = str(event.get("display_name") or event.get("agent_name") or "research agent")
        provider = str(event.get("provider") or "unknown")
        model = str(event.get("model") or "unknown")
        index = int(event.get("agent_index") or 0)
        total = max(1, int(event.get("total_agents") or 8))
        ordinal = min(total, max(1, index + 1))
        offset_by_event = {
            "agent_started": 0.02,
            "deterministic_baseline_started": 0.10,
            "deterministic_baseline_completed": 0.24,
            "llm_refinement_started": 0.42,
            "llm_refinement_completed": 0.78,
            "llm_refinement_failed": 0.78,
            "llm_refinement_skipped": 0.78,
            "agent_completed": 0.96,
            "agent_timeout": 0.96,
            "agent_failed": 0.96,
        }
        if event_type == "data_collection_started":
            return 0.12, "collecting_data", "Collecting market research context and provenance."
        if event_type == "data_collection_completed":
            return 0.16, "preparing_agents", "Data context collected. Starting the research committee."
        if event_type == "llm_refinement_started":
            return 0.16 + 0.68 * ((index + offset_by_event[event_type]) / total), "calling_llm", (
                f"Calling {provider}/{model} for {display_name} ({ordinal}/{total})."
            )
        if event_type == "llm_refinement_completed":
            latency = event.get("latency_ms")
            detail = f" in {latency} ms" if latency is not None else ""
            return 0.16 + 0.68 * ((index + offset_by_event[event_type]) / total), "llm_completed", (
                f"{display_name} LLM refinement completed{detail} ({ordinal}/{total})."
            )
        if event_type == "llm_refinement_failed":
            return 0.16 + 0.68 * ((index + offset_by_event[event_type]) / total), "llm_fallback", (
                f"{display_name} LLM refinement failed; deterministic fallback is being used ({ordinal}/{total})."
            )
        if event_type == "llm_refinement_skipped":
            return 0.16 + 0.68 * ((index + offset_by_event[event_type]) / total), "llm_skipped", (
                f"{display_name} skipped hosted LLM after prior provider failure; deterministic baseline is being used ({ordinal}/{total})."
            )
        if event_type in offset_by_event:
            readable = event_type.replace("_", " ")
            return 0.16 + 0.68 * ((index + offset_by_event[event_type]) / total), "running_agent", (
                f"{display_name}: {readable} ({ordinal}/{total})."
            )
        return 0.16, "running_agent", "Market research committee is running."

    def _record_progress_event(self, job_id: str, event: dict[str, Any]) -> None:
        safe_event = _secret_safe_job_data(
            self._safe_progress_event(event),
            self._secret_values.get(job_id),
        )
        progress, stage, message = self._progress_status_from_event(safe_event)
        with self.lock:
            job = self._load_job_for_update_locked(job_id)
            progress_events = [*job.progress_events, safe_event][-200:]
            next_progress = max(float(job.progress or 0.0), min(float(progress), 0.84))
        self._set_status(
            job_id,
            "running",
            progress=next_progress,
            stage=stage,
            message=message,
            progress_events=progress_events,
        )

    @staticmethod
    def _summarize_result_for_storage(request: MarketResearchRunRequest, result: dict[str, Any]) -> dict[str, Any]:
        if isinstance(result.get("reports"), list):
            reports = [item for item in result.get("reports", []) if isinstance(item, dict)]
            tickers = [str(item.get("ticker") or "").upper() for item in reports if item.get("ticker")]
            if not tickers:
                tickers = [str(item).upper() for item in result.get("tickers", []) if str(item).strip()]
            ticker_label = ",".join(tickers[:6]) if tickers else request.ticker
            if len(tickers) > 6:
                ticker_label += f",+{len(tickers) - 6}"
            confidence_values = [
                int(item.get("confidence"))
                for item in reports
                if isinstance(item.get("confidence"), int)
            ]
            decision_counts: dict[str, int] = {}
            for item in reports:
                decision = str(item.get("decision") or "").upper()
                if decision:
                    decision_counts[decision] = decision_counts.get(decision, 0) + 1
            aggregate_decision = max(decision_counts.items(), key=lambda item: item[1])[0] if decision_counts else None
            warnings = list(
                dict.fromkeys(
                    str(warning)
                    for item in reports
                    for warning in (item.get("warnings") or [])
                    if str(warning).strip()
                )
            )
            first_metadata = next((item.get("metadata") for item in reports if isinstance(item.get("metadata"), dict)), {})
            return {
                "ticker": ticker_label,
                "decision": aggregate_decision,
                "confidence": round(sum(confidence_values) / len(confidence_values)) if confidence_values else None,
                "warnings": warnings,
                "metadata": {
                    "report_type": "multi_stock",
                    "tickers": tickers,
                    "pair": result.get("pair"),
                    "decision_breakdown": decision_counts,
                    "agent_versions": {
                        str(item.get("ticker") or f"report_{index + 1}"): item.get("metadata", {}).get("agent_versions", {})
                        for index, item in enumerate(reports)
                        if isinstance(item.get("metadata"), dict)
                    },
                    "prompt_version": first_metadata.get("prompt_version") if isinstance(first_metadata, dict) else None,
                    "llm_provider": first_metadata.get("llm_provider") if isinstance(first_metadata, dict) else None,
                    "llm_model": first_metadata.get("llm_model") if isinstance(first_metadata, dict) else None,
                },
            }
        return {
            "ticker": str(result.get("ticker") or request.ticker).upper(),
            "decision": result.get("decision"),
            "confidence": result.get("confidence"),
            "warnings": result.get("warnings", []),
            "metadata": result.get("metadata", {}) if isinstance(result.get("metadata"), dict) else {},
        }

    def _run_job(self, job_id: str, request: MarketResearchRunRequest, organization_id: str) -> None:
        self._set_status(
            job_id,
            "running",
            started_at_utc=_utc_now_iso(),
            progress=0.08,
            stage="checking_llm_provider",
            message="Checking market research LLM provider configuration.",
        )
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            raise ValueError(f"Market research job not found: {job_id}")
        self._persist_report_record(job, status="running")
        try:
            pending_decisions: list[CommitteeDecision] = []
            service = MarketResearchService(self.settings)
            service.preflight_runtime(request)
            self._set_status(
                job_id,
                "running",
                progress=0.12,
                stage="collecting_data",
                message="Collecting market research context and provenance.",
            )
            if request.tickers or request.pair or request.universe_filter:
                multi_report = service.generate_multi_report(
                    request,
                    organization_id=organization_id,
                    user_id=job.user_id,
                    job_id=job_id,
                    progress_callback=lambda event: self._record_progress_event(job_id, event),
                    persist_decisions=self.claimed_worker_id is None,
                )
                if self.claimed_worker_id is not None:
                    pending_decisions = service._record_decisions(
                        multi_report,
                        organization_id=organization_id,
                        user_id=job.user_id,
                        job_id=job_id,
                        persist=False,
                    )
                result = multi_report.model_dump(mode="json")
                result["report_id"] = job.report_id
                result["report_type"] = "multi_stock"
                storage_summary = self._summarize_result_for_storage(request, result)
                result.setdefault("ticker", storage_summary["ticker"])
                result.setdefault("decision", storage_summary["decision"])
                result.setdefault("confidence", storage_summary["confidence"])
                result.setdefault("warnings", storage_summary["warnings"])
                result["metadata"] = {**storage_summary["metadata"], **dict(result.get("metadata") or {})}
            else:
                report = service.generate_report(
                    request,
                    organization_id=organization_id,
                    user_id=job.user_id,
                    job_id=job_id,
                    progress_callback=lambda event: self._record_progress_event(job_id, event),
                )
                result = report.model_dump(mode="json")
                result["report_id"] = job.report_id
                result["report_type"] = "single_stock"
                decision = service.record_decision(
                    report,
                    organization_id=organization_id,
                    user_id=job.user_id,
                    job_id=job_id,
                    persist=self.claimed_worker_id is None,
                )
                if self.claimed_worker_id is not None:
                    pending_decisions = [decision]
                storage_summary = self._summarize_result_for_storage(request, result)
            self._set_status(
                job_id,
                "running",
                progress=0.86,
                stage="persisting_report",
                message="Research committee finished. Persisting the report artifact.",
                warnings=result.get("warnings", []),
            )
            self._assert_claim(job_id)
            attempt_id = self._attempt_id(job_id)
            report_path = self._write_report(job_id, result, organization_id=organization_id)
            reference = self.artifact_storage.publish_file(
                report_path,
                organization_id=organization_id,
                artifact_type="market_research",
                artifact_id=attempt_id,
            )
            artifact_payload = {
                    "id": self.metadata_store.stable_id("art", f"{organization_id}:market_research:{job_id}"),
                    "artifact_type": "market_research",
                    "source_id": job_id,
                    "provider": reference.provider,
                    "key": reference.key,
                    "uri": reference.uri,
                    "file_count": reference.file_count,
                    "byte_count": reference.byte_count,
                    "metadata": {
                        "report_id": job.report_id,
                        "ticker": storage_summary["ticker"],
                        "analysis_date": result["analysis_date"],
                        "decision": storage_summary["decision"],
                        "confidence": storage_summary["confidence"],
                        "agent_versions": storage_summary["metadata"].get("agent_versions", {}),
                        "prompt_version": storage_summary["metadata"].get("prompt_version"),
                        "warnings": storage_summary["warnings"],
                        "attempt": self.claimed_attempt or 1,
                        "fence": attempt_id,
                    },
                }
            self._assert_claim(job_id)
            if self.claimed_worker_id is None:
                artifact_record = self.metadata_store.upsert_artifact(
                    organization_id=organization_id, payload=artifact_payload
                )
            else:
                for decision in pending_decisions:
                    decision.id = self.metadata_store.stable_id(
                        "decision", f"{organization_id}:{job_id}:{decision.ticker}:{decision.pair_ticker or ''}"
                    )

                def publish_domain(tx: Any) -> dict[str, Any]:
                    record = tx.upsert_artifact(organization_id=organization_id, payload=artifact_payload)
                    result["artifact_id"] = record["id"]
                    result["artifact"] = {
                        "provider": reference.provider, "key": reference.key, "uri": reference.uri,
                        "file_count": reference.file_count, "byte_count": reference.byte_count,
                    }
                    for pending in pending_decisions:
                        tx.upsert_committee_decision(payload=pending.model_dump(mode="json"))
                    self._persist_report_record(
                        job, status="completed", result=result, artifact_id=record["id"], transaction=tx
                    )
                    return record

                published, artifact_record = self.metadata_store.publish_claimed_job(
                    kind="market_research", job_id=job_id, worker_id=self.claimed_worker_id,
                    publisher=publish_domain,
                )
                if not published:
                    raise JobClaimLostError(f"Market research job claim is no longer owned by this worker: {job_id}")
            result["artifact"] = {
                "provider": reference.provider,
                "key": reference.key,
                "uri": reference.uri,
                "file_count": reference.file_count,
                "byte_count": reference.byte_count,
            }
            result["artifact_id"] = artifact_record["id"]
            result["report_path"] = str(report_path)
            if self.claimed_worker_id is None:
                self._persist_report_record(job, status="completed", result=result, artifact_id=artifact_record["id"])
            self._set_status(
                job_id,
                "completed",
                result=json_ready(result),
                warnings=result.get("warnings", []),
                finished_at_utc=_utc_now_iso(),
                progress=1.0,
                stage="completed",
                message=f"Market research completed for {storage_summary['ticker']} with simulated decision {storage_summary['decision'] or 'n/a'}.",
            )
        except JobClaimLostError:
            raise
        except Exception as exc:
            if self.claimed_worker_id is not None:
                # A heartbeat/database uncertainty must not be converted into a
                # new authoritative report mutation. Confirm the claim (and the
                # in-memory heartbeat guard) again before recording attempt
                # failure; otherwise let the controller recover the lease.
                self._assert_claim(job_id)
                self._persist_report_record(
                    job,
                    status="failed",
                    error="Market research worker attempt failed.",
                )
                raise
            self._persist_report_record(job, status="failed", error=str(exc))
            self._set_status(
                job_id,
                "failed",
                error=str(exc),
                finished_at_utc=_utc_now_iso(),
                progress=1.0,
                stage="failed",
                message="Market research failed. Review the error and request inputs.",
            )

    def _persist_report_record(
        self,
        job: MarketResearchJob,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        artifact_id: str | None = None,
        error: str | None = None,
        transaction: Any | None = None,
    ) -> dict[str, Any] | None:
        if not job.report_id or not job.organization_id:
            return None
        secrets = self._secret_values.get(job.id, set())
        request = dict(_secret_safe_job_data(job.request or {}, secrets))
        result = _secret_safe_job_data(result or {}, secrets)
        error = None if error is None else _secret_safe_job_data(error, secrets)
        ticker = str((result or {}).get("ticker") or request.get("ticker") or "UNKNOWN").upper()
        analysis_date = str((result or {}).get("analysis_date") or request.get("analysis_date") or _utc_now_iso()[:10])
        horizon = str((result or {}).get("time_horizon") or request.get("horizon") or "swing")
        context = {
            "request": request,
            "provenance": (result or {}).get("provenance", []),
            "data_freshness": (result or {}).get("data_freshness", {}),
            "confidence_levels": (result or {}).get("confidence_levels", {}),
            "missing_data_indicators": (result or {}).get("missing_data_indicators", []),
            "sentiment_analysis": (result or {}).get("sentiment_analysis", {}),
            "financial_events_analysis": (result or {}).get("financial_events_analysis", {}),
        }
        provider_metadata = dict((result or {}).get("metadata") or {})
        provider_metadata.setdefault("llm_provider", request.get("provider") or self.settings.market_research_llm_provider)
        provider_metadata.setdefault("llm_model", request.get("model") or self.settings.market_research_llm_model)
        provider_metadata.setdefault("prompt_version", (result or {}).get("metadata", {}).get("prompt_version"))
        provider_metadata["job_id"] = job.id
        provider_metadata["user_scoped"] = bool(job.user_id)
        payload = {
                "id": job.report_id,
                "job_id": job.id,
                "parent_report_id": job.parent_report_id,
                "ticker": ticker,
                "analysis_date": analysis_date,
                "horizon": horizon,
                "report_type": "market_research_committee",
                "title": f"{ticker} {horizon} research - {analysis_date}",
                "status": status,
                "decision": (result or {}).get("decision"),
                "confidence": (result or {}).get("confidence"),
                "summary": (result or {}).get("summary"),
                "disclaimer": (result or {}).get("disclaimer") or RESEARCH_DISCLAIMER,
                "context": context,
                "report": result or {},
                "source_references": (result or {}).get("source_references", []),
                "provider_metadata": provider_metadata,
                "warnings": (result or {}).get("warnings", []) if result else job.warnings,
                "artifact_id": artifact_id or (result or {}).get("artifact_id"),
                "error": error,
                "created_at_utc": job.created_at_utc,
                "updated_at_utc": _utc_now_iso(),
                "completed_at_utc": _utc_now_iso() if status == "completed" else None,
            }
        if transaction is not None:
            return transaction.upsert_market_research_report(
                organization_id=job.organization_id, user_id=job.user_id, payload=payload
            )
        if self.claimed_worker_id is None:
            return self.metadata_store.upsert_market_research_report(
                organization_id=job.organization_id, user_id=job.user_id, payload=payload
            )

        published, report_record = self.metadata_store.publish_claimed_job(
            kind="market_research",
            job_id=job.id,
            worker_id=self.claimed_worker_id,
            publisher=lambda tx: tx.upsert_market_research_report(
                organization_id=job.organization_id, user_id=job.user_id, payload=payload
            ),
        )
        if not published:
            raise JobClaimLostError(f"Market research job claim is no longer owned by this worker: {job.id}")
        return report_record

    def _write_report(self, job_id: str, result: dict[str, Any], *, organization_id: str | None = None) -> Path:
        output_dir = self.report_root / job_id
        if self.claimed_worker_id is not None:
            output_dir = self.report_root / ".attempts" / str(organization_id or "unknown") / self._attempt_id(job_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "report.json"
        tmp_path = path.with_suffix(".tmp")
        safe_result = _secret_safe_job_data(result, self._secret_values.get(job_id))
        tmp_path.write_text(json.dumps(json_ready(safe_result), indent=2), encoding="utf-8")
        tmp_path.replace(path)
        return path

    def _finalize_unhandled(self, job_id: str, future: Future[None]) -> None:
        exception = future.exception()
        if exception is None:
            return
        with self.lock:
            job = self.jobs.get(job_id)
        if job is not None:
            self._persist_report_record(job, status="failed", error=str(exception))
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
        payload = json_ready(_secret_safe_job_data(job.to_dict(), self._secret_values.get(job.id)))
        if self.settings.enable_in_process_jobs:
            path = self.jobs_dir / f"{job.id}.json"
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp_path.replace(path)
        self.metadata_store.upsert_job(kind="market_research", payload=payload)

    def _load_jobs(self) -> None:
        for payload in self.metadata_store.list_jobs(kind="market_research", limit=self.max_history):
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
            if self.metadata_store.get_job(kind="market_research", job_id=job.id) is not None:
                continue
            self._load_job_instance(job)

    def _load_job_instance(self, job: MarketResearchJob) -> None:
        self._secret_values[job.id] = collect_secret_values(job.to_dict())
        job = MarketResearchJob(**_secret_safe_job_data(job.to_dict(), self._secret_values[job.id]))
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

    def _trim_locked(self) -> None:
        if len(self.jobs) <= self.max_history:
            return
        removable = sorted(self.jobs.values(), key=lambda item: item.created_at_utc)[: len(self.jobs) - self.max_history]
        for job in removable:
            self.jobs.pop(job.id, None)
            self._secret_values.pop(job.id, None)
            try:
                (self.jobs_dir / f"{job.id}.json").unlink()
            except FileNotFoundError:
                pass
