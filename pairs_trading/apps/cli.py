from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from ..core.portfolio import PortfolioManager
from ..data.events import CachedEventProvider, CompositeEventProvider, LocalEventFileProvider, SecCompanyFactsEventProvider, SecCompanyFilingsEventProvider
from ..data.market import CachedParquetProvider, YahooFinanceProvider
from ..data.news import (
    AlphaVantageNewsProvider,
    BenzingaNewsProvider,
    CachedNewsSentimentProvider,
    CompositeHeadlineProvider,
    DailySentimentFileProvider,
    LocalNewsFileProvider,
    LocalWebSearchHeadlineProvider,
    NewsAPIHeadlineProvider,
    RSSHeadlineProvider,
    WebResearchHeadlineProvider,
)
from ..engines.backtesting import (
    CostModel,
    ExperimentResult,
    WalkForwardBacktester,
    WalkForwardConfig,
    json_ready,
    run_trial_grid,
)
from ..research.decision_history import DecisionHistoryStore
from ..engines.broker import BrokerConfig, SimulatedBroker
from ..engines.execution import ExecutionConfig
from ..engines.risk import RiskConfig
from ..engines.validation import ValidationConfig
from ..pipelines import (
    CommitteeSignalFollowerPipeline,
    DirectionalPipelineConfig,
    DirectionalStrategyPipeline,
    ETFMomentumConfig,
    ETFTrendMomentumPipeline,
    EventDrivenConfig,
    EventDrivenPipeline,
    GraphStatArbConfig,
    GraphStatArbPipeline,
    PEADSentimentConfig,
    PEADSentimentPipeline,
    SectorStatArbPipeline,
    StatArbConfig,
)
from ..reporting.experiment import ExperimentVisualizer
from ..research import GraphClusterConfig, PairScreenConfig
from ..features.sentiment import FinBERTSentimentModel, SentimentConfig, build_best_available_sentiment_model
from ..strategies import (
    AdaptiveRegimeStrategy,
    BollingerBandMeanReversionStrategy,
    BuyAndHoldStrategy,
    DonchianBreakoutStrategy,
    EMACrossStrategy,
    KeltnerChannelBreakoutStrategy,
    MACDTrendStrategy,
    MovingAverageCrossStrategy,
    PriceSMADeviationStrategy,
    RSIMeanReversionStrategy,
    StochasticOscillatorStrategy,
    TimeSeriesMomentumStrategy,
    VolatilityTargetTrendStrategy,
    GraphClusterTradingConfig,
    build_rule_based_strategy_factory,
)


DEFAULT_SECTOR_MAP = {
    "KO": "Beverages",
    "PEP": "Beverages",
    "KDP": "Beverages",
    "XOM": "Energy",
    "CVX": "Energy",
    "COP": "Energy",
    "JPM": "Banks",
    "BAC": "Banks",
    "WFC": "Banks",
    "C": "Banks",
}

DEFAULT_ETF_UNIVERSE = [
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "TLT",
    "IEF",
    "GLD",
    "SLV",
    "XLE",
    "XLF",
    "XLK",
    "XLV",
]

DEFAULT_EVENT_SYMBOLS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "JPM",
    "XOM",
]

ADVANCED_PIPELINES = ["graph_stat_arb", "pead_sentiment", "committee_signal_follower"]

DIRECTIONAL_PIPELINES = [
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
]


def load_sector_map(path: str | Path | None) -> dict[str, str]:
    if path is None:
        return dict(DEFAULT_SECTOR_MAP)

    source = Path(path)
    if source.suffix.lower() == ".json":
        data = json.loads(source.read_text(encoding="utf-8"))
        return {str(ticker).upper(): str(sector) for ticker, sector in data.items()}

    if source.suffix.lower() == ".csv":
        frame = pd.read_csv(source)
        if not {"ticker", "sector"} <= set(frame.columns):
            raise ValueError("Sector CSV must contain 'ticker' and 'sector' columns.")
        return {
            str(row["ticker"]).upper(): str(row["sector"])
            for _, row in frame[["ticker", "sector"]].dropna().iterrows()
        }

    raise ValueError(f"Unsupported sector map format: {source.suffix}")


def load_daily_sentiment(
    tickers: list[str],
    start: str,
    end: str,
    news_provider_names: list[str] | None,
    news_files: list[str] | None,
    daily_sentiment_file: str | None,
    use_finbert: bool,
    local_finbert_only: bool,
    sentiment_cache_dir: str,
    news_api_key: str | None,
    alphavantage_api_key: str | None,
    benzinga_api_key: str | None,
    newsapi_api_key: str | None = None,
    news_topics: list[str] | None = None,
    rss_feed_urls: list[str] | None = None,
    local_web_search_urls: list[str] | None = None,
    local_web_refresh_minutes: int = 60,
    local_web_max_pages_per_source: int = 30,
    web_research_urls: list[str] | None = None,
    web_research_domains: list[str] | None = None,
    web_research_query_terms: str = "",
    web_research_max_articles: int = 4,
    web_research_fetch_article_text: bool = True,
):
    def coerce_list(value) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value]

    news_provider_names = coerce_list(news_provider_names)
    news_files = coerce_list(news_files)
    news_topics = coerce_list(news_topics)
    rss_feed_urls = coerce_list(rss_feed_urls)
    local_web_search_urls = coerce_list(local_web_search_urls)
    web_research_urls = coerce_list(web_research_urls)
    web_research_domains = coerce_list(web_research_domains)

    if daily_sentiment_file:
        provider = DailySentimentFileProvider(daily_sentiment_file)
        return provider.get_daily_sentiment(tickers=tickers, start=start, end=end)

    if not news_provider_names:
        return None

    providers = []
    provider_names = list(dict.fromkeys(str(provider).lower() for provider in news_provider_names))

    if "local" in provider_names:
        if not news_files:
            raise ValueError("The local news provider requires at least one --news-file.")
        providers.extend(LocalNewsFileProvider(news_file) for news_file in news_files)

    if "alphavantage" in provider_names:
        api_key = alphavantage_api_key or news_api_key or os.getenv("ALPHAVANTAGE_API_KEY")
        if not api_key:
            raise ValueError("Alpha Vantage news requires --alphavantage-api-key, --news-api-key, or ALPHAVANTAGE_API_KEY.")
        providers.append(AlphaVantageNewsProvider(api_key=api_key, topics=news_topics))

    if "benzinga" in provider_names:
        api_key = benzinga_api_key or news_api_key or os.getenv("BENZINGA_API_KEY")
        if not api_key:
            raise ValueError("Benzinga news requires --benzinga-api-key, --news-api-key, or BENZINGA_API_KEY.")
        providers.append(BenzingaNewsProvider(api_key=api_key))

    if "newsapi" in provider_names:
        api_key = newsapi_api_key or news_api_key or os.getenv("NEWSAPI_API_KEY")
        if not api_key:
            raise ValueError("NewsAPI requires --newsapi-api-key, --news-api-key, or NEWSAPI_API_KEY.")
        providers.append(NewsAPIHeadlineProvider(api_key=api_key))

    if "rss" in provider_names:
        providers.append(RSSHeadlineProvider(feed_urls=rss_feed_urls))

    if "local_web" in provider_names:
        providers.append(
            LocalWebSearchHeadlineProvider(
                feed_urls=local_web_search_urls,
                source_domains=web_research_domains,
                direct_urls=web_research_urls,
                query_terms=web_research_query_terms,
                cache_dir=Path(sentiment_cache_dir) / "local_web_index",
                max_results_per_ticker=web_research_max_articles,
                max_crawl_pages_per_source=local_web_max_pages_per_source,
                refresh_minutes=local_web_refresh_minutes,
                fetch_article_text=web_research_fetch_article_text,
            )
        )

    if "web" in provider_names:
        providers.append(
            WebResearchHeadlineProvider(
                domains=web_research_domains,
                research_urls=web_research_urls,
                query_terms=web_research_query_terms,
                max_articles_per_ticker=web_research_max_articles,
                fetch_article_text=web_research_fetch_article_text,
            )
        )

    if not providers:
        return None

    model = FinBERTSentimentModel(local_files_only=local_finbert_only) if use_finbert else build_best_available_sentiment_model()
    headline_provider = providers[0] if len(providers) == 1 else CompositeHeadlineProvider(providers)
    provider = CachedNewsSentimentProvider(
        headline_provider=headline_provider,
        sentiment_model=model,
        cache_dir=sentiment_cache_dir,
    )
    return provider.get_daily_sentiment(tickers=tickers, start=start, end=end)


def load_events(
    tickers: list[str],
    start: str,
    end: str,
    *,
    event_file: str | None,
    event_cache_dir: str,
    edgar_user_agent: str | None,
    use_sec_companyfacts: bool,
    include_sec_filings: bool = False,
    sec_filing_forms: list[str] | None = None,
) -> pd.DataFrame | None:
    providers = []
    if event_file:
        providers.append(LocalEventFileProvider(event_file))

    if not use_sec_companyfacts and not include_sec_filings:
        if not providers:
            return None
        provider = providers[0] if len(providers) == 1 else CompositeEventProvider(providers)
        return provider.get_events(tickers=tickers, start=start, end=end)

    if (use_sec_companyfacts or include_sec_filings) and not edgar_user_agent:
        raise ValueError("SEC EDGAR event loading requires --edgar-user-agent when official SEC event sources are enabled.")

    sec_cache_dir = Path(event_cache_dir) / "sec"
    if use_sec_companyfacts:
        providers.append(
            CachedEventProvider(
                upstream=SecCompanyFactsEventProvider(
                    user_agent=edgar_user_agent or "",
                    cache_dir=sec_cache_dir,
                ),
                cache_dir=Path(event_cache_dir) / "companyfacts_events",
            )
        )

    if include_sec_filings:
        providers.append(
            CachedEventProvider(
                upstream=SecCompanyFilingsEventProvider(
                    user_agent=edgar_user_agent or "",
                    cache_dir=sec_cache_dir,
                    forms=sec_filing_forms,
                ),
                cache_dir=Path(event_cache_dir) / "filing_events",
            )
        )

    if not providers:
        return None

    provider = providers[0] if len(providers) == 1 else CompositeEventProvider(providers)
    return provider.get_events(tickers=tickers, start=start, end=end)


def _build_directional_strategy_factory(
    strategy_name: str,
    *,
    fast_window: int = 20,
    slow_window: int = 80,
    ema_fast_window: int = 12,
    ema_slow_window: int = 48,
    rsi_window: int = 14,
    lower_entry: float = 30.0,
    upper_entry: float = 70.0,
    exit_level: float = 50.0,
    sma_window: int = 40,
    z_entry: float = 1.25,
    z_exit: float = 0.25,
    stochastic_window: int = 14,
    stochastic_smooth_window: int = 3,
    stochastic_lower_entry: float = 20.0,
    stochastic_upper_entry: float = 80.0,
    bollinger_window: int = 20,
    bollinger_num_std: float = 2.0,
    macd_fast_window: int = 12,
    macd_slow_window: int = 26,
    macd_signal_window: int = 9,
    breakout_window: int = 55,
    breakout_exit_window: int = 20,
    keltner_window: int = 40,
    keltner_atr_multiplier: float = 1.5,
    trend_window: int = 120,
    volatility_window: int = 20,
    target_volatility: float = 0.15,
    max_position: float = 1.5,
    momentum_lookbacks: list[int] | tuple[int, ...] | None = None,
    momentum_min_agreement: float = 0.25,
    regime_fast_window: int = 30,
    regime_slow_window: int = 120,
    regime_mean_reversion_window: int = 40,
    regime_volatility_window: int = 30,
    regime_volatility_quantile: float = 0.70,
    strategy_cost_bps: float = 2.0,
) -> tuple[Callable[[str], object], int]:
    if strategy_name == "buy_and_hold":
        return (
            lambda symbol: BuyAndHoldStrategy(
                symbol=symbol,
                transaction_cost_bps=strategy_cost_bps,
            ),
            20,
        )
    if strategy_name == "ma_cross":
        return (
            lambda symbol: MovingAverageCrossStrategy(
                symbol=symbol,
                fast_window=fast_window,
                slow_window=slow_window,
                transaction_cost_bps=strategy_cost_bps,
            ),
            max(120, slow_window + 20),
        )
    if strategy_name == "ema_cross":
        return (
            lambda symbol: EMACrossStrategy(
                symbol=symbol,
                fast_window=ema_fast_window,
                slow_window=ema_slow_window,
                transaction_cost_bps=strategy_cost_bps,
            ),
            max(100, ema_slow_window + 20),
        )
    if strategy_name == "rsi_mean_reversion":
        return (
            lambda symbol: RSIMeanReversionStrategy(
                symbol=symbol,
                rsi_window=rsi_window,
                lower_entry=lower_entry,
                upper_entry=upper_entry,
                exit_level=exit_level,
                transaction_cost_bps=strategy_cost_bps,
            ),
            max(80, rsi_window * 4),
        )
    if strategy_name == "sma_deviation":
        return (
            lambda symbol: PriceSMADeviationStrategy(
                symbol=symbol,
                window=sma_window,
                entry_z=z_entry,
                exit_z=z_exit,
                transaction_cost_bps=strategy_cost_bps,
            ),
            max(120, sma_window * 3),
        )
    if strategy_name == "stochastic_oscillator":
        return (
            lambda symbol: StochasticOscillatorStrategy(
                symbol=symbol,
                window=stochastic_window,
                smooth_window=stochastic_smooth_window,
                lower_entry=stochastic_lower_entry,
                upper_entry=stochastic_upper_entry,
                exit_level=exit_level,
                transaction_cost_bps=strategy_cost_bps,
            ),
            max(100, stochastic_window * 5),
        )
    if strategy_name == "bollinger_mean_reversion":
        return (
            lambda symbol: BollingerBandMeanReversionStrategy(
                symbol=symbol,
                window=bollinger_window,
                num_std=bollinger_num_std,
                exit_z=z_exit,
                transaction_cost_bps=strategy_cost_bps,
            ),
            max(120, bollinger_window * 5),
        )
    if strategy_name == "macd_trend":
        return (
            lambda symbol: MACDTrendStrategy(
                symbol=symbol,
                fast_window=macd_fast_window,
                slow_window=macd_slow_window,
                signal_window=macd_signal_window,
                transaction_cost_bps=strategy_cost_bps,
            ),
            max(120, macd_slow_window * 5),
        )
    if strategy_name == "donchian_breakout":
        return (
            lambda symbol: DonchianBreakoutStrategy(
                symbol=symbol,
                breakout_window=breakout_window,
                exit_window=breakout_exit_window,
                transaction_cost_bps=strategy_cost_bps,
            ),
            max(120, breakout_window + breakout_exit_window + 20),
        )
    if strategy_name == "keltner_breakout":
        return (
            lambda symbol: KeltnerChannelBreakoutStrategy(
                symbol=symbol,
                window=keltner_window,
                atr_multiplier=keltner_atr_multiplier,
                transaction_cost_bps=strategy_cost_bps,
            ),
            max(140, keltner_window * 4),
        )
    if strategy_name == "volatility_target_trend":
        return (
            lambda symbol: VolatilityTargetTrendStrategy(
                symbol=symbol,
                trend_window=trend_window,
                volatility_window=volatility_window,
                target_volatility=target_volatility,
                max_position=max_position,
                transaction_cost_bps=strategy_cost_bps,
            ),
            max(180, trend_window + volatility_window + 20),
        )
    if strategy_name == "time_series_momentum":
        lookbacks = tuple(momentum_lookbacks or (21, 63, 126, 252))
        return (
            lambda symbol: TimeSeriesMomentumStrategy(
                symbol=symbol,
                lookbacks=lookbacks,
                min_agreement=momentum_min_agreement,
                transaction_cost_bps=strategy_cost_bps,
            ),
            max(300, max(lookbacks) + 40),
        )
    if strategy_name == "adaptive_regime":
        return (
            lambda symbol: AdaptiveRegimeStrategy(
                symbol=symbol,
                fast_window=regime_fast_window,
                slow_window=regime_slow_window,
                mean_reversion_window=regime_mean_reversion_window,
                volatility_window=regime_volatility_window,
                volatility_quantile=regime_volatility_quantile,
                entry_z=z_entry,
                transaction_cost_bps=strategy_cost_bps,
            ),
            max(240, regime_slow_window + regime_mean_reversion_window + regime_volatility_window),
        )
    raise ValueError(f"Unsupported directional strategy: {strategy_name}")


def _build_broker(
    *,
    max_gross_leverage: float,
    max_net_leverage: float,
    max_turnover: float | None,
    cost_model: CostModel,
) -> SimulatedBroker:
    return SimulatedBroker(
        BrokerConfig(
            risk=RiskConfig(
                max_gross_leverage=max_gross_leverage,
                max_net_leverage=max_net_leverage,
                max_turnover=max_turnover,
            ),
            execution=ExecutionConfig(
                commission_bps=cost_model.commission_bps,
                spread_bps=cost_model.spread_bps,
                slippage_bps=cost_model.slippage_bps,
                market_impact_bps=cost_model.market_impact_bps,
                borrow_bps_annual=cost_model.borrow_bps_annual,
                funding_bps_annual=cost_model.funding_bps_annual,
                delay_bars=cost_model.delay_bars,
            ),
        )
    )


def _run_pipeline_with_validation(
    *,
    pipeline,
    prices: pd.DataFrame,
    config: WalkForwardConfig,
    cost_model: CostModel,
    broker: SimulatedBroker,
    experiment_name: str,
    artifact_root: str,
    trial_strategies: Mapping[str, object] | None = None,
    validation_config: ValidationConfig = ValidationConfig(),
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    trial_returns = None
    trial_metrics = pd.DataFrame()
    if trial_strategies:
        trial_returns, trial_metrics = run_trial_grid(
            prices=prices,
            strategy_factories=trial_strategies,
            config=config,
            cost_model=cost_model,
            broker=broker,
            experiment_root=Path(artifact_root) / "trial_grid" / experiment_name,
        )

    def report_walk_forward_progress(payload: dict[str, Any]) -> None:
        if progress_callback is None:
            return
        progress_callback(
            {
                **payload,
                "prices": prices,
                "bars_per_year": config.bars_per_year,
                "strategy_name": experiment_name,
            }
        )

    result = WalkForwardBacktester(
        strategy=pipeline,
        prices=prices,
        config=config,
        cost_model=cost_model,
        broker=broker,
        validation_config=validation_config,
        experiment_root=artifact_root,
    ).run(
        experiment_name=experiment_name,
        validation_trial_returns=trial_returns,
        progress_callback=report_walk_forward_progress if progress_callback is not None else None,
    )

    if not trial_metrics.empty:
        trial_metrics.to_parquet(result.artifact_dir / "validation_trial_metrics.parquet")
        result.summary["validation_trial_count"] = int(len(trial_metrics))
        result.validation["validation_trial_count"] = int(len(trial_metrics))
        (result.artifact_dir / "validation_trial_metrics.json").write_text(
            json.dumps(json_ready(trial_metrics), indent=2),
            encoding="utf-8",
        )
        (result.artifact_dir / "summary.json").write_text(
            json.dumps(json_ready(result.summary), indent=2),
            encoding="utf-8",
        )
        (result.artifact_dir / "validation.json").write_text(
            json.dumps(json_ready(result.validation), indent=2),
            encoding="utf-8",
        )

    visuals = ExperimentVisualizer(result.artifact_dir / "visuals").create_dashboard(result)
    return {
        "result": result,
        "visuals": visuals,
        "summary": json_ready(result.summary),
        "validation": json_ready(result.validation),
        "trial_metrics": trial_metrics,
        "prices": prices,
        "bars_per_year": config.bars_per_year,
    }


def _build_stat_arb_trial_grid(
    *,
    sector_map: Mapping[str, str],
    daily_sentiment: pd.DataFrame | None,
    experiment_name: str,
) -> dict[str, SectorStatArbPipeline]:
    common_kwargs = {
        "sector_map": sector_map,
        "portfolio_manager": PortfolioManager(
            max_leverage=1.5,
            risk_per_trade=0.08,
            volatility_window=20,
            max_strategy_weight=0.40,
        ),
        "screen_config": PairScreenConfig(
            min_history=252,
            correlation_floor=0.60,
            coint_pvalue_threshold=0.10,
            min_half_life=2.0,
            max_half_life=60.0,
            target_half_life=15.0,
        ),
        "daily_sentiment": daily_sentiment,
        "sentiment_config": SentimentConfig() if daily_sentiment is not None else None,
    }
    return {
        f"{experiment_name}_trial_base": SectorStatArbPipeline(
            stat_arb_config=StatArbConfig(),
            name=f"{experiment_name}_trial_base",
            **common_kwargs,
        ),
        f"{experiment_name}_trial_faster_residuals": SectorStatArbPipeline(
            stat_arb_config=StatArbConfig(
                residual_lookback=40,
                residual_entry_z=1.35,
                entry_z=1.85,
            ),
            name=f"{experiment_name}_trial_faster_residuals",
            **common_kwargs,
        ),
        f"{experiment_name}_trial_wider_entries": SectorStatArbPipeline(
            stat_arb_config=StatArbConfig(
                residual_lookback=80,
                residual_entry_z=1.75,
                entry_z=2.25,
            ),
            name=f"{experiment_name}_trial_wider_entries",
            **common_kwargs,
        ),
    }


def _build_etf_trial_grid(symbols: list[str], experiment_name: str) -> dict[str, ETFTrendMomentumPipeline]:
    return {
        f"{experiment_name}_trial_top2": ETFTrendMomentumPipeline(
            ETFMomentumConfig.from_symbols(symbols, top_n=2, trend_window=180),
            name=f"{experiment_name}_trial_top2",
        ),
        f"{experiment_name}_trial_top3": ETFTrendMomentumPipeline(
            ETFMomentumConfig.from_symbols(symbols, top_n=3, trend_window=200),
            name=f"{experiment_name}_trial_top3",
        ),
        f"{experiment_name}_trial_top4": ETFTrendMomentumPipeline(
            ETFMomentumConfig.from_symbols(symbols, top_n=4, trend_window=220),
            name=f"{experiment_name}_trial_top4",
        ),
    }


def _build_event_trial_grid(
    *,
    symbols: list[str],
    events: pd.DataFrame,
    experiment_name: str,
) -> dict[str, EventDrivenPipeline]:
    base_kwargs = {
        "events": events,
        "portfolio_manager": PortfolioManager(
            max_leverage=1.25,
            risk_per_trade=0.05,
            volatility_window=15,
            max_strategy_weight=0.25,
        ),
    }
    return {
        f"{experiment_name}_trial_fast": EventDrivenPipeline(
            config=EventDrivenConfig.from_symbols(symbols, holding_period_bars=3, entry_threshold=0.10),
            name=f"{experiment_name}_trial_fast",
            **base_kwargs,
        ),
        f"{experiment_name}_trial_base": EventDrivenPipeline(
            config=EventDrivenConfig.from_symbols(symbols, holding_period_bars=5, entry_threshold=0.15),
            name=f"{experiment_name}_trial_base",
            **base_kwargs,
        ),
        f"{experiment_name}_trial_slow": EventDrivenPipeline(
            config=EventDrivenConfig.from_symbols(symbols, holding_period_bars=10, entry_threshold=0.20),
            name=f"{experiment_name}_trial_slow",
            **base_kwargs,
        ),
    }


def _build_pead_trial_grid(
    *,
    symbols: list[str],
    events: pd.DataFrame,
    daily_sentiment: pd.DataFrame | None,
    experiment_name: str,
) -> dict[str, PEADSentimentPipeline]:
    base_kwargs = {
        "events": events,
        "daily_sentiment": daily_sentiment,
        "portfolio_manager": PortfolioManager(
            max_leverage=1.25,
            risk_per_trade=0.05,
            volatility_window=15,
            max_strategy_weight=0.25,
        ),
    }
    return {
        f"{experiment_name}_trial_sentiment_fast": PEADSentimentPipeline(
            config=PEADSentimentConfig.from_symbols(symbols, holding_period_bars=3, entry_threshold=0.15),
            name=f"{experiment_name}_trial_sentiment_fast",
            **base_kwargs,
        ),
        f"{experiment_name}_trial_sentiment_base": PEADSentimentPipeline(
            config=PEADSentimentConfig.from_symbols(symbols, holding_period_bars=5, entry_threshold=0.20),
            name=f"{experiment_name}_trial_sentiment_base",
            **base_kwargs,
        ),
        f"{experiment_name}_trial_sentiment_strict": PEADSentimentPipeline(
            config=PEADSentimentConfig.from_symbols(
                symbols,
                holding_period_bars=10,
                entry_threshold=0.25,
                require_sentiment=daily_sentiment is not None,
            ),
            name=f"{experiment_name}_trial_sentiment_strict",
            **base_kwargs,
        ),
    }


def run_stat_arb_pipeline(
    sector_map_path: str | None = None,
    start: str = "2018-01-01",
    end: str = "2026-04-15",
    interval: str = "1d",
    experiment_name: str = "serious_stat_arb",
    price_cache_dir: str = "data/cache",
    sentiment_cache_dir: str = "data/sentiment_cache",
    artifact_root: str = "artifacts/experiments",
    news_provider_names: list[str] | None = None,
    news_files: list[str] | None = None,
    daily_sentiment_file: str | None = None,
    use_finbert: bool = False,
    local_finbert_only: bool = False,
    news_api_key: str | None = None,
    alphavantage_api_key: str | None = None,
    benzinga_api_key: str | None = None,
    newsapi_api_key: str | None = None,
    news_topics: list[str] | None = None,
    rss_feed_urls: list[str] | None = None,
    local_web_search_urls: list[str] | None = None,
    local_web_refresh_minutes: int = 60,
    local_web_max_pages_per_source: int = 30,
    web_research_urls: list[str] | None = None,
    web_research_domains: list[str] | None = None,
    web_research_query_terms: str = "",
    web_research_max_articles: int = 4,
    web_research_fetch_article_text: bool = True,
    purge_bars: int = 5,
    embargo_bars: int = 0,
    pbo_partitions: int = 8,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    sector_map = load_sector_map(sector_map_path)
    tickers = list(sector_map.keys())

    price_provider = CachedParquetProvider(
        upstream=YahooFinanceProvider(),
        cache_dir=price_cache_dir,
    )
    prices = price_provider.get_close_prices(
        symbols=tickers,
        start=start,
        end=end,
        interval=interval,
    )

    daily_sentiment = load_daily_sentiment(
        tickers=tickers,
        start=start,
        end=end,
        news_provider_names=news_provider_names,
        news_files=news_files,
        daily_sentiment_file=daily_sentiment_file,
        use_finbert=use_finbert,
        local_finbert_only=local_finbert_only,
        sentiment_cache_dir=sentiment_cache_dir,
        news_api_key=news_api_key,
        alphavantage_api_key=alphavantage_api_key,
        benzinga_api_key=benzinga_api_key,
        newsapi_api_key=newsapi_api_key,
        news_topics=news_topics,
        rss_feed_urls=rss_feed_urls,
        local_web_search_urls=local_web_search_urls,
        local_web_refresh_minutes=local_web_refresh_minutes,
        local_web_max_pages_per_source=local_web_max_pages_per_source,
        web_research_urls=web_research_urls,
        web_research_domains=web_research_domains,
        web_research_query_terms=web_research_query_terms,
        web_research_max_articles=web_research_max_articles,
        web_research_fetch_article_text=web_research_fetch_article_text,
    )

    pipeline = SectorStatArbPipeline(
        sector_map=sector_map,
        portfolio_manager=PortfolioManager(
            max_leverage=1.5,
            risk_per_trade=0.08,
            volatility_window=20,
            max_strategy_weight=0.40,
        ),
        screen_config=PairScreenConfig(
            min_history=252,
            correlation_floor=0.60,
            coint_pvalue_threshold=0.10,
            min_half_life=2.0,
            max_half_life=60.0,
            target_half_life=15.0,
        ),
        stat_arb_config=StatArbConfig(
            include_residual_book=True,
            include_classic_pairs=True,
            top_n_pairs=3,
            entry_z=2.0,
            exit_z=0.35,
            break_window=80,
            break_pvalue=0.20,
            transaction_cost_bps=4.0,
        ),
        daily_sentiment=daily_sentiment,
        sentiment_config=SentimentConfig() if daily_sentiment is not None else None,
        name=experiment_name,
    )

    walk_forward = WalkForwardConfig(
        train_bars=504,
        test_bars=63,
        step_bars=63,
        bars_per_year=252,
        purge_bars=purge_bars,
        embargo_bars=embargo_bars,
    )
    cost_model = CostModel(
        commission_bps=0.5,
        spread_bps=1.0,
        slippage_bps=0.75,
        market_impact_bps=0.75,
        borrow_bps_annual=40.0,
        delay_bars=1,
    )
    broker = _build_broker(
        max_gross_leverage=1.5,
        max_net_leverage=1.0,
        max_turnover=1.0,
        cost_model=cost_model,
    )

    trial_strategies = _build_stat_arb_trial_grid(
        sector_map=sector_map,
        daily_sentiment=daily_sentiment,
        experiment_name=experiment_name,
    )
    return _run_pipeline_with_validation(
        pipeline=pipeline,
        prices=prices,
        config=walk_forward,
        cost_model=cost_model,
        broker=broker,
        experiment_name=experiment_name,
        artifact_root=artifact_root,
        trial_strategies=trial_strategies,
        validation_config=ValidationConfig(
            purge_bars=purge_bars,
            embargo_bars=embargo_bars,
            pbo_partitions=pbo_partitions,
        ),
        progress_callback=progress_callback,
    )


def run_graph_stat_arb_pipeline(
    sector_map_path: str | None = None,
    start: str = "2018-01-01",
    end: str = "2026-04-15",
    interval: str = "1d",
    experiment_name: str = "graph_stat_arb",
    price_cache_dir: str = "data/cache",
    artifact_root: str = "artifacts/experiments",
    cluster_correlation_floor: float = 0.55,
    cluster_min_size: int = 3,
    cluster_max_size: int = 8,
    cluster_min_history: int = 180,
    residual_lookback: int = 60,
    entry_z: float = 1.25,
    top_n_per_side: int = 2,
    transaction_cost_bps: float = 3.0,
    purge_bars: int = 5,
    embargo_bars: int = 0,
    pbo_partitions: int = 8,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    sector_map = load_sector_map(sector_map_path)
    tickers = list(sector_map.keys())
    price_provider = CachedParquetProvider(
        upstream=YahooFinanceProvider(),
        cache_dir=price_cache_dir,
    )
    prices = price_provider.get_close_prices(
        symbols=tickers,
        start=start,
        end=end,
        interval=interval,
    )

    pipeline = GraphStatArbPipeline(
        sector_map=sector_map,
        portfolio_manager=PortfolioManager(
            max_leverage=1.5,
            risk_per_trade=0.07,
            volatility_window=20,
            max_strategy_weight=0.35,
        ),
        config=GraphStatArbConfig(
            cluster_config=GraphClusterConfig(
                min_history=cluster_min_history,
                correlation_floor=cluster_correlation_floor,
                min_cluster_size=cluster_min_size,
                max_cluster_size=cluster_max_size,
            ),
            trading_config=GraphClusterTradingConfig(
                residual_lookback=residual_lookback,
                entry_z=entry_z,
                top_n_per_side=top_n_per_side,
                transaction_cost_bps=transaction_cost_bps,
            ),
        ),
        name=experiment_name,
    )

    walk_forward = WalkForwardConfig(
        train_bars=504,
        test_bars=63,
        step_bars=63,
        bars_per_year=252,
        purge_bars=purge_bars,
        embargo_bars=embargo_bars,
    )
    cost_model = CostModel(
        commission_bps=0.5,
        spread_bps=1.0,
        slippage_bps=0.75,
        market_impact_bps=0.75,
        borrow_bps_annual=40.0,
        delay_bars=1,
    )
    broker = _build_broker(
        max_gross_leverage=1.5,
        max_net_leverage=0.75,
        max_turnover=1.5,
        cost_model=cost_model,
    )
    return _run_pipeline_with_validation(
        pipeline=pipeline,
        prices=prices,
        config=walk_forward,
        cost_model=cost_model,
        broker=broker,
        experiment_name=experiment_name,
        artifact_root=artifact_root,
        validation_config=ValidationConfig(
            purge_bars=purge_bars,
            embargo_bars=embargo_bars,
            pbo_partitions=pbo_partitions,
        ),
        progress_callback=progress_callback,
    )


def run_directional_pipeline(
    strategy_name: str,
    symbols: list[str],
    start: str = "2018-01-01",
    end: str = "2026-04-15",
    interval: str = "1d",
    experiment_name: str | None = None,
    price_cache_dir: str = "data/cache",
    artifact_root: str = "artifacts/experiments",
    train_bars: int = 300,
    test_bars: int = 63,
    step_bars: int = 63,
    bars_per_year: int = 252,
    fast_window: int = 20,
    slow_window: int = 80,
    ema_fast_window: int = 12,
    ema_slow_window: int = 48,
    rsi_window: int = 14,
    lower_entry: float = 30.0,
    upper_entry: float = 70.0,
    exit_level: float = 50.0,
    sma_window: int = 40,
    z_entry: float = 1.25,
    z_exit: float = 0.25,
    stochastic_window: int = 14,
    stochastic_smooth_window: int = 3,
    stochastic_lower_entry: float = 20.0,
    stochastic_upper_entry: float = 80.0,
    bollinger_window: int = 20,
    bollinger_num_std: float = 2.0,
    macd_fast_window: int = 12,
    macd_slow_window: int = 26,
    macd_signal_window: int = 9,
    breakout_window: int = 55,
    breakout_exit_window: int = 20,
    keltner_window: int = 40,
    keltner_atr_multiplier: float = 1.5,
    trend_window: int = 120,
    volatility_window: int = 20,
    target_volatility: float = 0.15,
    max_position: float = 1.5,
    momentum_lookbacks: list[int] | tuple[int, ...] | None = None,
    momentum_min_agreement: float = 0.25,
    regime_fast_window: int = 30,
    regime_slow_window: int = 120,
    regime_mean_reversion_window: int = 40,
    regime_volatility_window: int = 30,
    regime_volatility_quantile: float = 0.70,
    strategy_cost_bps: float = 2.0,
    purge_bars: int = 5,
    embargo_bars: int = 0,
    pbo_partitions: int = 8,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if not symbols:
        raise ValueError("Directional pipelines require at least one symbol.")

    strategy_factory, min_history = _build_directional_strategy_factory(
        strategy_name,
        fast_window=fast_window,
        slow_window=slow_window,
        ema_fast_window=ema_fast_window,
        ema_slow_window=ema_slow_window,
        rsi_window=rsi_window,
        lower_entry=lower_entry,
        upper_entry=upper_entry,
        exit_level=exit_level,
        sma_window=sma_window,
        z_entry=z_entry,
        z_exit=z_exit,
        stochastic_window=stochastic_window,
        stochastic_smooth_window=stochastic_smooth_window,
        stochastic_lower_entry=stochastic_lower_entry,
        stochastic_upper_entry=stochastic_upper_entry,
        bollinger_window=bollinger_window,
        bollinger_num_std=bollinger_num_std,
        macd_fast_window=macd_fast_window,
        macd_slow_window=macd_slow_window,
        macd_signal_window=macd_signal_window,
        breakout_window=breakout_window,
        breakout_exit_window=breakout_exit_window,
        keltner_window=keltner_window,
        keltner_atr_multiplier=keltner_atr_multiplier,
        trend_window=trend_window,
        volatility_window=volatility_window,
        target_volatility=target_volatility,
        max_position=max_position,
        momentum_lookbacks=momentum_lookbacks,
        momentum_min_agreement=momentum_min_agreement,
        regime_fast_window=regime_fast_window,
        regime_slow_window=regime_slow_window,
        regime_mean_reversion_window=regime_mean_reversion_window,
        regime_volatility_window=regime_volatility_window,
        regime_volatility_quantile=regime_volatility_quantile,
        strategy_cost_bps=strategy_cost_bps,
    )
    if train_bars < min_history:
        raise ValueError(
            f"{strategy_name} requires at least {min_history} train_bars for its indicator warmup; "
            f"received {train_bars}."
        )

    price_provider = CachedParquetProvider(
        upstream=YahooFinanceProvider(),
        cache_dir=price_cache_dir,
    )
    prices = price_provider.get_close_prices(
        symbols=symbols,
        start=start,
        end=end,
        interval=interval,
    )

    pipeline_name = experiment_name or strategy_name
    pipeline = DirectionalStrategyPipeline(
        strategy_factory=strategy_factory,
        portfolio_manager=PortfolioManager(
            max_leverage=1.25,
            risk_per_trade=0.06,
            volatility_window=20,
            max_strategy_weight=0.35,
        ),
        config=DirectionalPipelineConfig.from_symbols(symbols=symbols, min_history=min_history),
        name=pipeline_name,
    )

    walk_forward = WalkForwardConfig(
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
        bars_per_year=bars_per_year,
        purge_bars=purge_bars,
        embargo_bars=embargo_bars,
    )
    cost_model = CostModel(
        commission_bps=0.5,
        spread_bps=1.0,
        slippage_bps=0.75,
        market_impact_bps=0.5,
        borrow_bps_annual=25.0,
        delay_bars=1,
    )
    broker = _build_broker(
        max_gross_leverage=1.25,
        max_net_leverage=1.0,
        max_turnover=1.0,
        cost_model=cost_model,
    )

    return _run_pipeline_with_validation(
        pipeline=pipeline,
        prices=prices,
        config=walk_forward,
        cost_model=cost_model,
        broker=broker,
        experiment_name=pipeline_name,
        artifact_root=artifact_root,
        validation_config=ValidationConfig(
            purge_bars=purge_bars,
            embargo_bars=embargo_bars,
            pbo_partitions=pbo_partitions,
        ),
        progress_callback=progress_callback,
    )


def run_rule_based_strategy_pipeline(
    *,
    spec: dict[str, Any],
    symbols: list[str],
    start: str = "2018-01-01",
    end: str = "2026-04-15",
    interval: str = "1d",
    experiment_name: str | None = None,
    price_cache_dir: str = "data/cache",
    artifact_root: str = "artifacts/experiments",
    train_bars: int = 252,
    test_bars: int = 63,
    step_bars: int = 63,
    bars_per_year: int = 252,
    purge_bars: int = 5,
    embargo_bars: int = 0,
    pbo_partitions: int = 8,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if not symbols:
        raise ValueError("User-created strategies require at least one symbol.")

    strategy_factory, min_history = build_rule_based_strategy_factory(spec)
    price_provider = CachedParquetProvider(
        upstream=YahooFinanceProvider(),
        cache_dir=price_cache_dir,
    )
    prices = price_provider.get_close_prices(
        symbols=symbols,
        start=start,
        end=end,
        interval=interval,
    )

    costs = spec.get("costs") if isinstance(spec.get("costs"), dict) else {}
    cost_model = CostModel(
        commission_bps=float(costs.get("commission_bps", 0.5)),
        spread_bps=float(costs.get("spread_bps", 1.0)),
        slippage_bps=float(costs.get("slippage_bps", 0.75)),
        market_impact_bps=float(costs.get("market_impact_bps", 0.5)),
        borrow_bps_annual=float(costs.get("borrow_bps_annual", 25.0)),
        delay_bars=int(costs.get("delay_bars", 1)),
    )
    sizing = spec.get("position_sizing") if isinstance(spec.get("position_sizing"), dict) else {}
    max_gross = float(sizing.get("max_gross_exposure", 1.0))
    pipeline_name = experiment_name or str(spec.get("name") or "user_rule_strategy")
    pipeline = DirectionalStrategyPipeline(
        strategy_factory=strategy_factory,
        portfolio_manager=PortfolioManager(
            max_leverage=max(0.1, max_gross),
            risk_per_trade=min(0.20, max(0.01, float(sizing.get("max_position_per_symbol", 0.25)))),
            volatility_window=20,
            max_strategy_weight=min(1.0, max(0.01, float(sizing.get("max_position_per_symbol", 0.25)))),
        ),
        config=DirectionalPipelineConfig.from_symbols(symbols=symbols, min_history=min_history),
        name=pipeline_name,
    )

    walk_forward = WalkForwardConfig(
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
        bars_per_year=bars_per_year,
        purge_bars=purge_bars,
        embargo_bars=embargo_bars,
    )
    broker = _build_broker(
        max_gross_leverage=max(0.1, max_gross),
        max_net_leverage=max(0.1, min(max_gross, 1.0)),
        max_turnover=1.0,
        cost_model=cost_model,
    )
    return _run_pipeline_with_validation(
        pipeline=pipeline,
        prices=prices,
        config=walk_forward,
        cost_model=cost_model,
        broker=broker,
        experiment_name=pipeline_name,
        artifact_root=artifact_root,
        validation_config=ValidationConfig(
            purge_bars=purge_bars,
            embargo_bars=embargo_bars,
            pbo_partitions=pbo_partitions,
        ),
        progress_callback=progress_callback,
    )


def run_etf_trend_pipeline(
    symbols: list[str] | None = None,
    start: str = "2010-01-01",
    end: str = "2026-04-15",
    interval: str = "1d",
    experiment_name: str = "etf_trend_momentum",
    price_cache_dir: str = "data/cache",
    artifact_root: str = "artifacts/experiments",
    purge_bars: int = 5,
    embargo_bars: int = 0,
    pbo_partitions: int = 8,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    symbols = list(dict.fromkeys(symbols or DEFAULT_ETF_UNIVERSE))
    price_provider = CachedParquetProvider(
        upstream=YahooFinanceProvider(),
        cache_dir=price_cache_dir,
    )
    prices = price_provider.get_close_prices(
        symbols=symbols,
        start=start,
        end=end,
        interval=interval,
    )

    pipeline = ETFTrendMomentumPipeline(
        ETFMomentumConfig.from_symbols(symbols),
        name=experiment_name,
    )
    walk_forward = WalkForwardConfig(
        train_bars=756,
        test_bars=63,
        step_bars=63,
        bars_per_year=252,
        purge_bars=purge_bars,
        embargo_bars=embargo_bars,
    )
    cost_model = CostModel(
        commission_bps=0.5,
        spread_bps=0.75,
        slippage_bps=0.75,
        market_impact_bps=0.50,
        borrow_bps_annual=0.0,
        delay_bars=1,
    )
    broker = _build_broker(
        max_gross_leverage=1.0,
        max_net_leverage=1.0,
        max_turnover=1.0,
        cost_model=cost_model,
    )

    return _run_pipeline_with_validation(
        pipeline=pipeline,
        prices=prices,
        config=walk_forward,
        cost_model=cost_model,
        broker=broker,
        experiment_name=experiment_name,
        artifact_root=artifact_root,
        trial_strategies=_build_etf_trial_grid(symbols, experiment_name),
        validation_config=ValidationConfig(
            purge_bars=purge_bars,
            embargo_bars=embargo_bars,
            pbo_partitions=pbo_partitions,
        ),
        progress_callback=progress_callback,
    )


def run_event_driven_pipeline(
    symbols: list[str] | None = None,
    start: str = "2018-01-01",
    end: str = "2026-04-15",
    interval: str = "1d",
    experiment_name: str = "edgar_event_drift",
    price_cache_dir: str = "data/cache",
    event_cache_dir: str = "data/event_cache",
    artifact_root: str = "artifacts/experiments",
    event_file: str | None = None,
    edgar_user_agent: str | None = None,
    use_sec_companyfacts: bool = False,
    include_sec_filings: bool = False,
    sec_filing_forms: list[str] | None = None,
    purge_bars: int = 5,
    embargo_bars: int = 0,
    pbo_partitions: int = 8,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    symbols = list(dict.fromkeys(symbols or DEFAULT_EVENT_SYMBOLS))
    price_provider = CachedParquetProvider(
        upstream=YahooFinanceProvider(),
        cache_dir=price_cache_dir,
    )
    prices = price_provider.get_close_prices(
        symbols=symbols,
        start=start,
        end=end,
        interval=interval,
    )
    events = load_events(
        tickers=symbols,
        start=start,
        end=end,
        event_file=event_file,
        event_cache_dir=event_cache_dir,
        edgar_user_agent=edgar_user_agent,
        use_sec_companyfacts=use_sec_companyfacts,
        include_sec_filings=include_sec_filings,
        sec_filing_forms=sec_filing_forms,
    )
    if events is None:
        raise ValueError("Event-driven backtests require --event-file, --use-sec-companyfacts, or --include-sec-filings with --edgar-user-agent.")

    pipeline = EventDrivenPipeline(
        events=events,
        portfolio_manager=PortfolioManager(
            max_leverage=1.25,
            risk_per_trade=0.05,
            volatility_window=15,
            max_strategy_weight=0.25,
        ),
        config=EventDrivenConfig.from_symbols(symbols),
        name=experiment_name,
    )
    walk_forward = WalkForwardConfig(
        train_bars=504,
        test_bars=63,
        step_bars=63,
        bars_per_year=252,
        purge_bars=purge_bars,
        embargo_bars=embargo_bars,
    )
    cost_model = CostModel(
        commission_bps=0.5,
        spread_bps=1.0,
        slippage_bps=1.0,
        market_impact_bps=0.75,
        borrow_bps_annual=30.0,
        delay_bars=1,
    )
    broker = _build_broker(
        max_gross_leverage=1.25,
        max_net_leverage=1.0,
        max_turnover=1.5,
        cost_model=cost_model,
    )

    return _run_pipeline_with_validation(
        pipeline=pipeline,
        prices=prices,
        config=walk_forward,
        cost_model=cost_model,
        broker=broker,
        experiment_name=experiment_name,
        artifact_root=artifact_root,
        trial_strategies=_build_event_trial_grid(symbols=symbols, events=events, experiment_name=experiment_name),
        validation_config=ValidationConfig(
            purge_bars=purge_bars,
            embargo_bars=embargo_bars,
            pbo_partitions=pbo_partitions,
        ),
        progress_callback=progress_callback,
    )


def run_pead_sentiment_pipeline(
    symbols: list[str] | None = None,
    start: str = "2018-01-01",
    end: str = "2026-04-15",
    interval: str = "1d",
    experiment_name: str = "pead_sentiment",
    price_cache_dir: str = "data/cache",
    event_cache_dir: str = "data/event_cache",
    sentiment_cache_dir: str = "data/sentiment_cache",
    artifact_root: str = "artifacts/experiments",
    event_file: str | None = None,
    edgar_user_agent: str | None = None,
    use_sec_companyfacts: bool = False,
    include_sec_filings: bool = False,
    sec_filing_forms: list[str] | None = None,
    news_provider_names: list[str] | None = None,
    news_files: list[str] | None = None,
    daily_sentiment_file: str | None = None,
    use_finbert: bool = False,
    local_finbert_only: bool = False,
    news_api_key: str | None = None,
    alphavantage_api_key: str | None = None,
    benzinga_api_key: str | None = None,
    newsapi_api_key: str | None = None,
    news_topics: list[str] | None = None,
    rss_feed_urls: list[str] | None = None,
    local_web_search_urls: list[str] | None = None,
    local_web_refresh_minutes: int = 60,
    local_web_max_pages_per_source: int = 30,
    web_research_urls: list[str] | None = None,
    web_research_domains: list[str] | None = None,
    web_research_query_terms: str = "",
    web_research_max_articles: int = 4,
    web_research_fetch_article_text: bool = True,
    holding_period_bars: int = 5,
    entry_threshold: float = 0.20,
    event_weight: float = 0.45,
    sentiment_weight: float = 0.55,
    sentiment_window_days: int = 2,
    require_sentiment: bool = False,
    require_earnings_event: bool = True,
    purge_bars: int = 5,
    embargo_bars: int = 0,
    pbo_partitions: int = 8,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    symbols = list(dict.fromkeys(symbols or DEFAULT_EVENT_SYMBOLS))
    price_provider = CachedParquetProvider(
        upstream=YahooFinanceProvider(),
        cache_dir=price_cache_dir,
    )
    prices = price_provider.get_close_prices(
        symbols=symbols,
        start=start,
        end=end,
        interval=interval,
    )
    events = load_events(
        tickers=symbols,
        start=start,
        end=end,
        event_file=event_file,
        event_cache_dir=event_cache_dir,
        edgar_user_agent=edgar_user_agent,
        use_sec_companyfacts=use_sec_companyfacts,
        include_sec_filings=include_sec_filings,
        sec_filing_forms=sec_filing_forms,
    )
    if events is None:
        raise ValueError("PEAD + Sentiment requires --event-file, --use-sec-companyfacts, or --include-sec-filings with --edgar-user-agent.")

    daily_sentiment = load_daily_sentiment(
        tickers=symbols,
        start=start,
        end=end,
        news_provider_names=news_provider_names,
        news_files=news_files,
        daily_sentiment_file=daily_sentiment_file,
        use_finbert=use_finbert,
        local_finbert_only=local_finbert_only,
        sentiment_cache_dir=sentiment_cache_dir,
        news_api_key=news_api_key,
        alphavantage_api_key=alphavantage_api_key,
        benzinga_api_key=benzinga_api_key,
        newsapi_api_key=newsapi_api_key,
        news_topics=news_topics,
        rss_feed_urls=rss_feed_urls,
        local_web_search_urls=local_web_search_urls,
        local_web_refresh_minutes=local_web_refresh_minutes,
        local_web_max_pages_per_source=local_web_max_pages_per_source,
        web_research_urls=web_research_urls,
        web_research_domains=web_research_domains,
        web_research_query_terms=web_research_query_terms,
        web_research_max_articles=web_research_max_articles,
        web_research_fetch_article_text=web_research_fetch_article_text,
    )

    pipeline = PEADSentimentPipeline(
        events=events,
        daily_sentiment=daily_sentiment,
        portfolio_manager=PortfolioManager(
            max_leverage=1.25,
            risk_per_trade=0.05,
            volatility_window=15,
            max_strategy_weight=0.25,
        ),
        config=PEADSentimentConfig.from_symbols(
            symbols,
            holding_period_bars=holding_period_bars,
            entry_threshold=entry_threshold,
            event_weight=event_weight,
            sentiment_weight=sentiment_weight,
            sentiment_window_days=sentiment_window_days,
            require_sentiment=require_sentiment,
            require_earnings_event=require_earnings_event,
        ),
        name=experiment_name,
    )
    walk_forward = WalkForwardConfig(
        train_bars=504,
        test_bars=63,
        step_bars=63,
        bars_per_year=252,
        purge_bars=purge_bars,
        embargo_bars=embargo_bars,
    )
    cost_model = CostModel(
        commission_bps=0.5,
        spread_bps=1.0,
        slippage_bps=1.0,
        market_impact_bps=0.75,
        borrow_bps_annual=30.0,
        delay_bars=1,
    )
    broker = _build_broker(
        max_gross_leverage=1.25,
        max_net_leverage=1.0,
        max_turnover=1.5,
        cost_model=cost_model,
    )

    return _run_pipeline_with_validation(
        pipeline=pipeline,
        prices=prices,
        config=walk_forward,
        cost_model=cost_model,
        broker=broker,
        experiment_name=experiment_name,
        artifact_root=artifact_root,
        trial_strategies=_build_pead_trial_grid(
            symbols=symbols,
            events=events,
            daily_sentiment=daily_sentiment,
            experiment_name=experiment_name,
        ),
        validation_config=ValidationConfig(
            purge_bars=purge_bars,
            embargo_bars=embargo_bars,
            pbo_partitions=pbo_partitions,
        ),
        progress_callback=progress_callback,
    )


def run_committee_signal_follower_pipeline(
    symbols: list[str],
    decision_store: DecisionHistoryStore,
    start: str = "2018-01-01",
    end: str = "2026-04-15",
    interval: str = "1d",
    experiment_name: str = "committee_signal_follower",
    price_cache_dir: str = "data/cache",
    artifact_root: str = "artifacts/experiments",
    position_sizing: str = "confidence_weighted",
    max_position_pct: float = 0.25,
    confidence_threshold: int = 30,
    scale_in_confidence_delta: int = 10,
    scale_out_on_opposite: bool = True,
    flat_on_avoid: bool = True,
    train_bars: int = 1,
    test_bars: int | None = None,
    step_bars: int | None = None,
    purge_bars: int = 5,
    embargo_bars: int = 0,
    pbo_partitions: int = 8,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if not symbols:
        raise ValueError("Committee signal follower requires at least one symbol.")

    symbols = list(dict.fromkeys(str(s).upper() for s in symbols))
    price_provider = CachedParquetProvider(
        upstream=YahooFinanceProvider(),
        cache_dir=price_cache_dir,
    )
    prices = price_provider.get_close_prices(
        symbols=symbols,
        start=start,
        end=end,
        interval=interval,
    )

    pipeline = CommitteeSignalFollowerPipeline(
        symbols=symbols,
        decision_store=decision_store,
        position_sizing=position_sizing,
        max_position_pct=max_position_pct,
        confidence_threshold=confidence_threshold,
        scale_in_confidence_delta=scale_in_confidence_delta,
        scale_out_on_opposite=scale_out_on_opposite,
        flat_on_avoid=flat_on_avoid,
        name=experiment_name,
    )
    n_bars = len(prices)
    test_bars = test_bars if test_bars is not None else max(20, n_bars - train_bars - purge_bars)
    step_bars = step_bars if step_bars is not None else test_bars
    walk_forward = WalkForwardConfig(
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
        bars_per_year=252,
        purge_bars=purge_bars,
        embargo_bars=embargo_bars,
    )
    cost_model = CostModel(
        commission_bps=0.5,
        spread_bps=1.0,
        slippage_bps=0.75,
        market_impact_bps=0.5,
        borrow_bps_annual=25.0,
        delay_bars=1,
    )
    broker = _build_broker(
        max_gross_leverage=1.25,
        max_net_leverage=1.0,
        max_turnover=None,
        cost_model=cost_model,
    )

    return _run_pipeline_with_validation(
        pipeline=pipeline,
        prices=prices,
        config=walk_forward,
        cost_model=cost_model,
        broker=broker,
        experiment_name=experiment_name,
        artifact_root=artifact_root,
        validation_config=ValidationConfig(
            purge_bars=purge_bars,
            embargo_bars=embargo_bars,
            pbo_partitions=pbo_partitions,
        ),
        progress_callback=progress_callback,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the quant walk-forward research pipeline.")
    parser.add_argument(
        "--deploy-paper-config",
        help="JSON config for multi-strategy shadow paper trading deployment. When provided, runs paper mode instead of backtests.",
    )
    parser.add_argument(
        "--pipeline",
        default="stat_arb",
        choices=["stat_arb", "etf_trend", "edgar_event", *ADVANCED_PIPELINES, *DIRECTIONAL_PIPELINES],
        help="Research pipeline to run.",
    )
    parser.add_argument("--symbols", nargs="*", help="Symbols for directional, ETF, or event pipelines.")
    parser.add_argument("--sector-map", help="Path to JSON or CSV sector map.")
    parser.add_argument("--start", default="2018-01-01", help="Backtest start date (YYYY-MM-DD).")
    parser.add_argument("--end", default="2026-04-15", help="Backtest end date (YYYY-MM-DD).")
    parser.add_argument("--interval", default="1d", help="Price bar interval.")
    parser.add_argument("--experiment-name", help="Experiment label. Defaults to the selected pipeline name.")
    parser.add_argument("--train-bars", type=int, default=300, help="Training bars per walk-forward fold.")
    parser.add_argument("--test-bars", type=int, default=63, help="Test bars per walk-forward fold.")
    parser.add_argument("--step-bars", type=int, default=63, help="Walk-forward step size.")
    parser.add_argument("--bars-per-year", type=int, default=252, help="Bars per year for annualization.")
    parser.add_argument("--validation-purge-bars", type=int, default=5, help="Purge bars between train and test windows.")
    parser.add_argument("--validation-embargo-bars", type=int, default=0, help="Embargo bars after each test window.")
    parser.add_argument("--validation-pbo-partitions", type=int, default=8, help="Number of partitions for CSCV/PBO.")
    parser.add_argument(
        "--news-provider",
        nargs="+",
        choices=["local", "rss", "local_web", "web", "newsapi", "alphavantage", "benzinga"],
        help="One or more news sources to use before sentiment scoring.",
    )
    parser.add_argument("--news-file", nargs="*", help="One or more CSV/parquet files of raw news headlines.")
    parser.add_argument("--rss-feed", nargs="*", help="Optional RSS feed URLs. Use {ticker} in a URL for per-symbol feeds.")
    parser.add_argument("--local-web-search-feed", nargs="*", help="Optional RSS/Atom feed URLs for cached local web search. Use {ticker} for per-symbol feeds.")
    parser.add_argument("--local-web-refresh-minutes", type=int, default=60, help="Refresh interval for the cached local web-search index. Use 0 to refetch.")
    parser.add_argument("--local-web-max-pages-per-source", type=int, default=30, help="Maximum pages to crawl from each local web seed URL or domain.")
    parser.add_argument("--web-research-url", nargs="*", help="Optional direct web pages to fetch and summarize. Use {ticker} for per-symbol URLs.")
    parser.add_argument("--web-research-domain", nargs="*", help="Optional trusted domains for GDELT-backed web research, e.g. reuters.com cnbc.com.")
    parser.add_argument("--web-research-query-terms", default="", help="Optional extra web research query terms, e.g. earnings OR guidance.")
    parser.add_argument("--web-research-max-articles", type=int, default=4, help="Maximum discovered web articles per symbol.")
    parser.add_argument("--web-research-metadata-only", action="store_true", help="Do not fetch article pages; score discovered titles only.")
    parser.add_argument("--daily-sentiment-file", help="CSV or parquet of precomputed daily sentiment.")
    parser.add_argument("--news-api-key", help="API key for the selected remote news provider.")
    parser.add_argument("--alphavantage-api-key", help="API key for Alpha Vantage news.")
    parser.add_argument("--benzinga-api-key", help="API key for Benzinga news.")
    parser.add_argument("--newsapi-api-key", help="API key for NewsAPI.org.")
    parser.add_argument("--news-topics", nargs="*", help="Optional topic filters for providers that support them.")
    parser.add_argument("--use-finbert", action="store_true", help="Use FinBERT for headline sentiment.")
    parser.add_argument("--local-finbert-only", action="store_true", help="Require FinBERT to already exist locally.")
    parser.add_argument("--event-file", help="CSV or parquet of standardized event data.")
    parser.add_argument("--use-sec-companyfacts", action="store_true", help="Build EDGAR events from SEC company facts.")
    parser.add_argument("--include-sec-filings", action="store_true", help="Add official SEC filing events such as 8-K earnings releases, 10-Qs, and 10-Ks.")
    parser.add_argument("--sec-filing-forms", nargs="*", help="SEC filing forms to include for official event timestamps, e.g. 8-K 10-Q 10-K.")
    parser.add_argument("--edgar-user-agent", help="User-Agent string for SEC requests, e.g. 'YourName [email@example.com]'.")
    parser.add_argument("--pead-holding-period-bars", type=int, default=5, help="Post-event holding period for PEAD + sentiment.")
    parser.add_argument("--pead-entry-threshold", type=float, default=0.20, help="Combined event/sentiment threshold for PEAD + sentiment.")
    parser.add_argument("--pead-event-weight", type=float, default=0.45, help="Weight on event/fundamental proxy score in PEAD + sentiment.")
    parser.add_argument("--pead-sentiment-weight", type=float, default=0.55, help="Weight on daily sentiment score in PEAD + sentiment.")
    parser.add_argument("--pead-sentiment-window-days", type=int, default=2, help="Lookback window for sentiment around each PEAD event.")
    parser.add_argument("--pead-require-sentiment", action="store_true", help="Require sentiment coverage before PEAD + sentiment can trade an event.")
    parser.add_argument("--pead-allow-non-earnings-events", action="store_true", help="Allow PEAD + sentiment to trade non-earnings event types.")
    parser.add_argument("--cluster-correlation-floor", type=float, default=0.55, help="Minimum return correlation used to connect graph stat-arb clusters.")
    parser.add_argument("--cluster-min-size", type=int, default=3, help="Minimum graph stat-arb cluster size.")
    parser.add_argument("--cluster-max-size", type=int, default=8, help="Maximum graph stat-arb cluster size.")
    parser.add_argument("--cluster-min-history", type=int, default=180, help="Minimum train bars for graph stat-arb clustering.")
    parser.add_argument("--graph-residual-lookback", type=int, default=60, help="Rolling residual z-score window for graph stat-arb.")
    parser.add_argument("--graph-entry-z", type=float, default=1.25, help="Residual z-score entry threshold for graph stat-arb.")
    parser.add_argument("--graph-top-n-per-side", type=int, default=2, help="Max leaders and laggards traded per graph cluster.")
    parser.add_argument("--artifact-root", default="artifacts/experiments", help="Experiment artifact directory.")
    parser.add_argument("--price-cache-dir", default="data/cache", help="Price parquet cache directory.")
    parser.add_argument("--sentiment-cache-dir", default="data/sentiment_cache", help="Sentiment cache directory.")
    parser.add_argument("--event-cache-dir", default="data/event_cache", help="Event cache directory.")
    parser.add_argument("--paper-state-dir", default="artifacts/paper/state", help="State directory for shadow paper ledgers.")
    parser.add_argument("--paper-artifact-root", default="artifacts/paper/runs", help="Artifact directory for shadow paper runs.")
    parser.add_argument("--paper-asof-date", help="As-of date for a paper deployment run. Defaults to today in UTC.")
    parser.add_argument("--fast-window", type=int, default=20, help="Fast MA window for ma_cross.")
    parser.add_argument("--slow-window", type=int, default=80, help="Slow MA window for ma_cross.")
    parser.add_argument("--ema-fast-window", type=int, default=12, help="Fast EMA window for ema_cross.")
    parser.add_argument("--ema-slow-window", type=int, default=48, help="Slow EMA window for ema_cross.")
    parser.add_argument("--rsi-window", type=int, default=14, help="RSI window for rsi_mean_reversion.")
    parser.add_argument("--lower-entry", type=float, default=30.0, help="Long entry RSI threshold.")
    parser.add_argument("--upper-entry", type=float, default=70.0, help="Short entry RSI threshold.")
    parser.add_argument("--exit-level", type=float, default=50.0, help="RSI exit level.")
    parser.add_argument("--sma-window", type=int, default=40, help="SMA window for sma_deviation.")
    parser.add_argument("--z-entry", type=float, default=1.25, help="Z-score entry threshold for mean-reversion strategies.")
    parser.add_argument("--z-exit", type=float, default=0.25, help="Z-score exit threshold for mean-reversion strategies.")
    parser.add_argument("--stochastic-window", type=int, default=14, help="Lookback for stochastic_oscillator.")
    parser.add_argument("--stochastic-smooth-window", type=int, default=3, help="Smoothing window for stochastic_oscillator.")
    parser.add_argument("--stochastic-lower-entry", type=float, default=20.0, help="Long entry threshold for stochastic_oscillator.")
    parser.add_argument("--stochastic-upper-entry", type=float, default=80.0, help="Short entry threshold for stochastic_oscillator.")
    parser.add_argument("--bollinger-window", type=int, default=20, help="Rolling window for bollinger_mean_reversion.")
    parser.add_argument("--bollinger-num-std", type=float, default=2.0, help="Band width for bollinger_mean_reversion.")
    parser.add_argument("--macd-fast-window", type=int, default=12, help="Fast EMA window for macd_trend.")
    parser.add_argument("--macd-slow-window", type=int, default=26, help="Slow EMA window for macd_trend.")
    parser.add_argument("--macd-signal-window", type=int, default=9, help="Signal EMA window for macd_trend.")
    parser.add_argument("--breakout-window", type=int, default=55, help="Lookback for Donchian breakouts.")
    parser.add_argument("--breakout-exit-window", type=int, default=20, help="Exit lookback for Donchian breakouts.")
    parser.add_argument("--keltner-window", type=int, default=40, help="EMA/ATR window for keltner_breakout.")
    parser.add_argument("--keltner-atr-multiplier", type=float, default=1.5, help="ATR channel width for keltner_breakout.")
    parser.add_argument("--trend-window", type=int, default=120, help="Trend window for volatility_target_trend.")
    parser.add_argument("--volatility-window", type=int, default=20, help="Volatility window for volatility-aware directional strategies.")
    parser.add_argument("--target-volatility", type=float, default=0.15, help="Annualized target volatility for volatility_target_trend.")
    parser.add_argument("--max-position", type=float, default=1.5, help="Maximum single-strategy position multiplier.")
    parser.add_argument("--momentum-lookbacks", nargs="*", type=int, help="Lookbacks for time_series_momentum.")
    parser.add_argument("--momentum-min-agreement", type=float, default=0.25, help="Minimum horizon agreement for time_series_momentum.")
    parser.add_argument("--regime-fast-window", type=int, default=30, help="Fast MA window for adaptive_regime.")
    parser.add_argument("--regime-slow-window", type=int, default=120, help="Slow MA window for adaptive_regime.")
    parser.add_argument("--regime-mean-reversion-window", type=int, default=40, help="Mean-reversion window for adaptive_regime.")
    parser.add_argument("--regime-volatility-window", type=int, default=30, help="Volatility window for adaptive_regime.")
    parser.add_argument("--regime-volatility-quantile", type=float, default=0.70, help="Volatility quantile used by adaptive_regime.")
    parser.add_argument("--strategy-cost-bps", type=float, default=2.0, help="Internal strategy turnover cost in bps.")
    parser.add_argument("--max-position-pct", type=float, default=0.25, help="Maximum position size as fraction of equity for committee_signal_follower.")
    parser.add_argument("--confidence-threshold", type=int, default=30, help="Minimum confidence score (0-100) to act on a committee signal.")
    parser.add_argument("--scale-in-confidence-delta", type=int, default=10, help="Confidence increase needed to scale into a repeated signal.")
    parser.add_argument("--scale-out-on-opposite", action=argparse.BooleanOptionalAction, default=True, help="Flatten when an opposite signal arrives.")
    parser.add_argument("--flat-on-avoid", action=argparse.BooleanOptionalAction, default=True, help="Flatten position when AVOID signal is received.")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.deploy_paper_config:
        from ..operations.paper_trading import run_paper_batch

        paper_output = run_paper_batch(
            deployment_config_path=args.deploy_paper_config,
            asof_date=args.paper_asof_date,
            state_dir=args.paper_state_dir,
            artifact_root=args.paper_artifact_root,
            price_cache_dir=args.price_cache_dir,
            sentiment_cache_dir=args.sentiment_cache_dir,
            event_cache_dir=args.event_cache_dir,
        )
        print(json.dumps(json_ready(paper_output), indent=2))
        print(f"Paper artifacts saved to: {paper_output['artifact_dir']}")
        return

    experiment_name = args.experiment_name or args.pipeline

    if args.pipeline == "stat_arb":
        run_output = run_stat_arb_pipeline(
            sector_map_path=args.sector_map,
            start=args.start,
            end=args.end,
            interval=args.interval,
            experiment_name=experiment_name,
            price_cache_dir=args.price_cache_dir,
            sentiment_cache_dir=args.sentiment_cache_dir,
            artifact_root=args.artifact_root,
            news_provider_names=args.news_provider,
            news_files=args.news_file,
            daily_sentiment_file=args.daily_sentiment_file,
            use_finbert=args.use_finbert,
            local_finbert_only=args.local_finbert_only,
            news_api_key=args.news_api_key,
            alphavantage_api_key=args.alphavantage_api_key,
            benzinga_api_key=args.benzinga_api_key,
            newsapi_api_key=args.newsapi_api_key,
            news_topics=args.news_topics,
            rss_feed_urls=args.rss_feed,
            local_web_search_urls=args.local_web_search_feed,
            local_web_refresh_minutes=args.local_web_refresh_minutes,
            local_web_max_pages_per_source=args.local_web_max_pages_per_source,
            web_research_urls=args.web_research_url,
            web_research_domains=args.web_research_domain,
            web_research_query_terms=args.web_research_query_terms,
            web_research_max_articles=args.web_research_max_articles,
            web_research_fetch_article_text=not args.web_research_metadata_only,
            purge_bars=args.validation_purge_bars,
            embargo_bars=args.validation_embargo_bars,
            pbo_partitions=args.validation_pbo_partitions,
        )
    elif args.pipeline == "graph_stat_arb":
        run_output = run_graph_stat_arb_pipeline(
            sector_map_path=args.sector_map,
            start=args.start,
            end=args.end,
            interval=args.interval,
            experiment_name=experiment_name,
            price_cache_dir=args.price_cache_dir,
            artifact_root=args.artifact_root,
            cluster_correlation_floor=args.cluster_correlation_floor,
            cluster_min_size=args.cluster_min_size,
            cluster_max_size=args.cluster_max_size,
            cluster_min_history=args.cluster_min_history,
            residual_lookback=args.graph_residual_lookback,
            entry_z=args.graph_entry_z,
            top_n_per_side=args.graph_top_n_per_side,
            transaction_cost_bps=args.strategy_cost_bps,
            purge_bars=args.validation_purge_bars,
            embargo_bars=args.validation_embargo_bars,
            pbo_partitions=args.validation_pbo_partitions,
        )
    elif args.pipeline == "etf_trend":
        run_output = run_etf_trend_pipeline(
            symbols=args.symbols,
            start=args.start,
            end=args.end,
            interval=args.interval,
            experiment_name=experiment_name,
            price_cache_dir=args.price_cache_dir,
            artifact_root=args.artifact_root,
            purge_bars=args.validation_purge_bars,
            embargo_bars=args.validation_embargo_bars,
            pbo_partitions=args.validation_pbo_partitions,
        )
    elif args.pipeline == "pead_sentiment":
        run_output = run_pead_sentiment_pipeline(
            symbols=args.symbols,
            start=args.start,
            end=args.end,
            interval=args.interval,
            experiment_name=experiment_name,
            price_cache_dir=args.price_cache_dir,
            event_cache_dir=args.event_cache_dir,
            sentiment_cache_dir=args.sentiment_cache_dir,
            artifact_root=args.artifact_root,
            event_file=args.event_file,
            edgar_user_agent=args.edgar_user_agent,
            use_sec_companyfacts=args.use_sec_companyfacts,
            include_sec_filings=args.include_sec_filings,
            sec_filing_forms=args.sec_filing_forms,
            news_provider_names=args.news_provider,
            news_files=args.news_file,
            daily_sentiment_file=args.daily_sentiment_file,
            use_finbert=args.use_finbert,
            local_finbert_only=args.local_finbert_only,
            news_api_key=args.news_api_key,
            alphavantage_api_key=args.alphavantage_api_key,
            benzinga_api_key=args.benzinga_api_key,
            newsapi_api_key=args.newsapi_api_key,
            news_topics=args.news_topics,
            rss_feed_urls=args.rss_feed,
            local_web_search_urls=args.local_web_search_feed,
            local_web_refresh_minutes=args.local_web_refresh_minutes,
            local_web_max_pages_per_source=args.local_web_max_pages_per_source,
            web_research_urls=args.web_research_url,
            web_research_domains=args.web_research_domain,
            web_research_query_terms=args.web_research_query_terms,
            web_research_max_articles=args.web_research_max_articles,
            web_research_fetch_article_text=not args.web_research_metadata_only,
            holding_period_bars=args.pead_holding_period_bars,
            entry_threshold=args.pead_entry_threshold,
            event_weight=args.pead_event_weight,
            sentiment_weight=args.pead_sentiment_weight,
            sentiment_window_days=args.pead_sentiment_window_days,
            require_sentiment=args.pead_require_sentiment,
            require_earnings_event=not args.pead_allow_non_earnings_events,
            purge_bars=args.validation_purge_bars,
            embargo_bars=args.validation_embargo_bars,
            pbo_partitions=args.validation_pbo_partitions,
        )
    elif args.pipeline == "edgar_event":
        run_output = run_event_driven_pipeline(
            symbols=args.symbols,
            start=args.start,
            end=args.end,
            interval=args.interval,
            experiment_name=experiment_name,
            price_cache_dir=args.price_cache_dir,
            event_cache_dir=args.event_cache_dir,
            artifact_root=args.artifact_root,
            event_file=args.event_file,
            edgar_user_agent=args.edgar_user_agent,
            use_sec_companyfacts=args.use_sec_companyfacts,
            include_sec_filings=args.include_sec_filings,
            sec_filing_forms=args.sec_filing_forms,
            purge_bars=args.validation_purge_bars,
            embargo_bars=args.validation_embargo_bars,
            pbo_partitions=args.validation_pbo_partitions,
        )
    elif args.pipeline == "committee_signal_follower":
        run_output = run_committee_signal_follower_pipeline(
            symbols=args.symbols,
            decision_store=DecisionHistoryStore(),
            start=args.start,
            end=args.end,
            interval=args.interval,
            experiment_name=experiment_name,
            price_cache_dir=args.price_cache_dir,
            artifact_root=args.artifact_root,
            max_position_pct=args.max_position_pct,
            confidence_threshold=args.confidence_threshold,
            scale_in_confidence_delta=args.scale_in_confidence_delta,
            scale_out_on_opposite=args.scale_out_on_opposite,
            flat_on_avoid=args.flat_on_avoid,
            train_bars=args.train_bars,
            test_bars=args.test_bars,
            step_bars=args.step_bars,
            purge_bars=args.validation_purge_bars,
            embargo_bars=args.validation_embargo_bars,
            pbo_partitions=args.validation_pbo_partitions,
        )
    else:
        if not args.symbols:
            parser.error("--symbols is required for directional pipelines.")
        run_output = run_directional_pipeline(
            strategy_name=args.pipeline,
            symbols=args.symbols,
            start=args.start,
            end=args.end,
            interval=args.interval,
            experiment_name=experiment_name,
            price_cache_dir=args.price_cache_dir,
            artifact_root=args.artifact_root,
            train_bars=args.train_bars,
            test_bars=args.test_bars,
            step_bars=args.step_bars,
            bars_per_year=args.bars_per_year,
            fast_window=args.fast_window,
            slow_window=args.slow_window,
            ema_fast_window=args.ema_fast_window,
            ema_slow_window=args.ema_slow_window,
            rsi_window=args.rsi_window,
            lower_entry=args.lower_entry,
            upper_entry=args.upper_entry,
            exit_level=args.exit_level,
            sma_window=args.sma_window,
            z_entry=args.z_entry,
            z_exit=args.z_exit,
            stochastic_window=args.stochastic_window,
            stochastic_smooth_window=args.stochastic_smooth_window,
            stochastic_lower_entry=args.stochastic_lower_entry,
            stochastic_upper_entry=args.stochastic_upper_entry,
            bollinger_window=args.bollinger_window,
            bollinger_num_std=args.bollinger_num_std,
            macd_fast_window=args.macd_fast_window,
            macd_slow_window=args.macd_slow_window,
            macd_signal_window=args.macd_signal_window,
            breakout_window=args.breakout_window,
            breakout_exit_window=args.breakout_exit_window,
            keltner_window=args.keltner_window,
            keltner_atr_multiplier=args.keltner_atr_multiplier,
            trend_window=args.trend_window,
            volatility_window=args.volatility_window,
            target_volatility=args.target_volatility,
            max_position=args.max_position,
            momentum_lookbacks=args.momentum_lookbacks,
            momentum_min_agreement=args.momentum_min_agreement,
            regime_fast_window=args.regime_fast_window,
            regime_slow_window=args.regime_slow_window,
            regime_mean_reversion_window=args.regime_mean_reversion_window,
            regime_volatility_window=args.regime_volatility_window,
            regime_volatility_quantile=args.regime_volatility_quantile,
            strategy_cost_bps=args.strategy_cost_bps,
            purge_bars=args.validation_purge_bars,
            embargo_bars=args.validation_embargo_bars,
            pbo_partitions=args.validation_pbo_partitions,
        )

    result: ExperimentResult = run_output["result"]
    visuals = run_output["visuals"]

    print(json.dumps(run_output["summary"], indent=2))
    print(json.dumps(run_output["validation"], indent=2))
    print(f"Artifacts saved to: {result.artifact_dir}")
    for name, path in visuals.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
