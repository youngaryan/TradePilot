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
from .llm_config import build_structured_llm_provider, market_research_runtime_diagnostics, preflight_market_research_llm
from .config import BackendSettings
from .job_queue import enqueue_quant_job
from .schemas import MarketResearchRunRequest
from .sentiment_services import SentimentService
from .storage import build_artifact_storage


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class BackendMarketResearchDataProvider:
    def __init__(self, settings: BackendSettings, market_data_provider: MarketDataProvider | None = None) -> None:
        self.settings = settings
        self.market_data_provider = market_data_provider
        self.demo_provider = DemoMarketResearchDataProvider()
        self.sentiment_service = SentimentService(settings)
        self.financial_events_service = FinancialEventsService(settings, market_data_provider=market_data_provider)

    def collect(
        self,
        request: MarketResearchInput,
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
    ) -> MarketResearchContext:
        del user_id
        if self.settings.market_research_data_provider != "cached_yahoo":
            context = self.demo_provider.collect(request)
            context.provider_metadata["backend_data_provider"] = self.settings.market_research_data_provider
            return context

        try:
            context = self._collect_cached_yahoo(request)
            return self._enrich_context(context, request, organization_id=organization_id)
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
                    interval="1d",
                )
            except Exception as exc:
                extension_warning = f"Price history had fewer than 20 rows and 365-day extension was unavailable: {exc}"
            else:
                if request.ticker in extended_prices.columns:
                    extended_series = pd.to_numeric(extended_prices[request.ticker], errors="coerce").dropna().sort_index()
                    if len(extended_series) > len(series):
                        series = extended_series
                        effective_start = extended_start
                        used_extended_window = True
        bars = [
            PriceBar(date=pd.Timestamp(index).strftime("%Y-%m-%d"), close=round(float(value), 6))
            for index, value in series.items()
        ]
        warnings = [extension_warning] if extension_warning else []
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
                    detail=f"Daily close prices from {effective_start.isoformat()} through {request.analysis_date}.",
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
        except Exception as exc:
            warning = f"Sentiment dataset was unavailable: {exc}"
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
        except Exception as exc:
            warning = f"Financial-events provider was unavailable: {exc}"
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

        self._record_decisions(result, organization_id=organization_id, user_id=user_id, job_id=job_id)
        return result

    def _record_decisions(
        self,
        multi_report: MultiStockReport,
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
        job_id: str | None = None,
    ) -> None:
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
            self.decision_store.add(decision)

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
    ) -> None:
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
        self.decision_store.add(decision)


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
        job = MarketResearchJob(
            id=job_id,
            status="queued",
            request={
                **json_ready(request.model_dump(mode="json")),
                "provider": runtime_settings.market_research_llm_provider,
                "model": runtime_settings.market_research_llm_model,
            },
            created_at_utc=now,
            updated_at_utc=now,
            organization_id=organization_id,
            user_id=user_id,
            report_id=self.metadata_store.stable_id("mrr", f"{organization_id}:{user_id or 'machine'}:{job_id}"),
            parent_report_id=parent_report_id,
            progress=0.02,
            stage="queued",
            message="Queued locally. The research committee will collect data and run analyst agents.",
        )
        with self.lock:
            self.jobs[job.id] = job
            self._save_locked(job)
            self._trim_locked()
        self._persist_report_record(job, status="queued")

        if self.settings.enable_in_process_jobs and self.executor is not None:
            future = self.executor.submit(self._run_job, job.id, request, organization_id)
            future.add_done_callback(lambda completed: self._finalize_unhandled(job.id, completed))
        else:
            queue_payload = enqueue_quant_job(self.settings, kind="market_research", job_id=job.id)
            self._set_status(job.id, "queued", message=f"Queued in Redis/RQ ({queue_payload['queue']}). Waiting for worker heartbeat.")
        return job.to_dict()

    def list_jobs(self, *, organization_id: str) -> list[dict[str, Any]]:
        persisted = {
            str(payload.get("id")): payload
            for payload in self.metadata_store.list_jobs(kind="market_research", organization_id=organization_id)
            if payload.get("id")
        }
        with self.lock:
            for job in self.jobs.values():
                if job.organization_id == organization_id:
                    persisted[job.id] = job.to_dict()
        return sorted(persisted.values(), key=lambda item: str(item.get("created_at_utc") or ""), reverse=True)

    def get_job(self, job_id: str, *, organization_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is not None and job.organization_id == organization_id:
                return job.to_dict()
        return self.metadata_store.get_job(kind="market_research", job_id=job_id, organization_id=organization_id)

    def _set_status(self, job_id: str, status: str, **updates: Any) -> None:
        now = _utc_now_iso()
        with self.lock:
            job = self.jobs[job_id]
            job.status = status
            job.updated_at_utc = now
            for key, value in updates.items():
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
        safe_event = self._safe_progress_event(event)
        progress, stage, message = self._progress_status_from_event(safe_event)
        now = _utc_now_iso()
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return
            job.status = "running"
            job.updated_at_utc = now
            job.progress = max(float(job.progress or 0.0), min(float(progress), 0.84))
            job.stage = stage
            job.message = message
            job.progress_events = [*job.progress_events, safe_event][-200:]
            self._save_locked(job)

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
                service.record_decision(report, organization_id=organization_id, user_id=job.user_id, job_id=job_id)
                storage_summary = self._summarize_result_for_storage(request, result)
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
                        "report_id": job.report_id,
                        "ticker": storage_summary["ticker"],
                        "analysis_date": result["analysis_date"],
                        "decision": storage_summary["decision"],
                        "confidence": storage_summary["confidence"],
                        "agent_versions": storage_summary["metadata"].get("agent_versions", {}),
                        "prompt_version": storage_summary["metadata"].get("prompt_version"),
                        "warnings": storage_summary["warnings"],
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
        except Exception as exc:
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
    ) -> dict[str, Any] | None:
        if not job.report_id or not job.organization_id:
            return None
        request = dict(job.request or {})
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
        return self.metadata_store.upsert_market_research_report(
            organization_id=job.organization_id,
            user_id=job.user_id,
            payload={
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
            },
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
