from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class StrategyCatalogItem:
    id: str
    name: str
    family: str
    difficulty: str
    pipeline: str
    summary: str
    how_it_works: str
    best_for: str
    watch_out: str
    key_parameters: tuple[str, ...]
    example_cli: str
    paper_config_example: dict[str, Any]


def _directional_item(
    *,
    strategy_id: str,
    name: str,
    difficulty: str,
    summary: str,
    how_it_works: str,
    best_for: str,
    watch_out: str,
    key_parameters: tuple[str, ...],
    cli_args: str = "",
    params: dict[str, Any] | None = None,
) -> StrategyCatalogItem:
    command = (
        ".\\.venv\\Scripts\\python.exe -m pairs_trading.apps.cli "
        f"--pipeline {strategy_id} --symbols SPY QQQ TLT --start 2018-01-01 --end 2026-04-15"
    )
    if cli_args:
        command = f"{command} {cli_args}"
    return StrategyCatalogItem(
        id=strategy_id,
        name=name,
        family="Directional",
        difficulty=difficulty,
        pipeline=strategy_id,
        summary=summary,
        how_it_works=how_it_works,
        best_for=best_for,
        watch_out=watch_out,
        key_parameters=key_parameters,
        example_cli=command,
        paper_config_example={
            "name": f"{strategy_id}_shadow",
            "pipeline": strategy_id,
            "symbols": ["SPY", "QQQ", "TLT"],
            "lookback_bars": 360,
            "params": params or {},
        },
    )


def build_strategy_catalog() -> list[dict[str, Any]]:
    """Return frontend-ready strategy documentation.

    The catalog intentionally explains behavior and operational caveats next to
    runnable examples so the UI can teach without depending on Python internals.
    """

    items: list[StrategyCatalogItem] = [
        _directional_item(
            strategy_id="buy_and_hold",
            name="Buy And Hold Benchmark",
            difficulty="Basic",
            summary="Long-only baseline that stays invested whenever prices are available.",
            how_it_works="Uses a constant positive position. It is not meant to be clever; it tells you what passive exposure would have done.",
            best_for="Benchmarking every active strategy against a simple invest-and-wait baseline.",
            watch_out="It can have large drawdowns and does not control downside risk by itself.",
            key_parameters=("strategy_cost_bps",),
            params={"strategy_cost_bps": 0.25},
        ),
        _directional_item(
            strategy_id="ma_cross",
            name="Simple Moving Average Cross",
            difficulty="Basic",
            summary="Trend model that goes long when a fast average is above a slow average and short when below.",
            how_it_works="Compares rolling arithmetic means to detect persistent price direction.",
            best_for="Easy-to-explain trend following on liquid ETFs or large caps.",
            watch_out="Can whipsaw in sideways markets because moving averages lag.",
            key_parameters=("fast_window", "slow_window", "strategy_cost_bps"),
            cli_args="--fast-window 20 --slow-window 80",
            params={"fast_window": 20, "slow_window": 80},
        ),
        _directional_item(
            strategy_id="ema_cross",
            name="Exponential Moving Average Cross",
            difficulty="Basic",
            summary="Faster trend model that reacts more to recent prices than the simple moving-average version.",
            how_it_works="Compares fast and slow exponential averages and converts the spread into a forecast.",
            best_for="Markets where recent momentum matters more than older history.",
            watch_out="More responsive also means more turnover and potentially more false signals.",
            key_parameters=("ema_fast_window", "ema_slow_window", "strategy_cost_bps"),
            cli_args="--ema-fast-window 12 --ema-slow-window 48",
            params={"ema_fast_window": 12, "ema_slow_window": 48},
        ),
        _directional_item(
            strategy_id="rsi_mean_reversion",
            name="RSI Mean Reversion",
            difficulty="Basic",
            summary="Oscillator strategy that buys oversold readings and sells overbought readings.",
            how_it_works="Computes RSI from smoothed gains and losses, then uses entry and exit thresholds to avoid constant flipping.",
            best_for="Range-bound assets with repeated short-term overreaction.",
            watch_out="Strong trends can stay overbought or oversold for a long time.",
            key_parameters=("rsi_window", "lower_entry", "upper_entry", "exit_level"),
            cli_args="--rsi-window 14 --lower-entry 30 --upper-entry 70",
            params={"rsi_window": 14, "lower_entry": 30, "upper_entry": 70},
        ),
        _directional_item(
            strategy_id="sma_deviation",
            name="SMA Deviation Reversion",
            difficulty="Intermediate",
            summary="Mean-reversion strategy that fades statistically large deviations from a moving average.",
            how_it_works="Builds a z-score of price minus its moving average and enters only when the deviation is large enough.",
            best_for="Assets that oscillate around a stable local mean.",
            watch_out="A true regime break can make a stretched price become the new normal.",
            key_parameters=("sma_window", "z_entry", "z_exit"),
            cli_args="--sma-window 40 --z-entry 1.25 --z-exit 0.25",
            params={"sma_window": 40, "z_entry": 1.25, "z_exit": 0.25},
        ),
        _directional_item(
            strategy_id="stochastic_oscillator",
            name="Stochastic Oscillator Reversion",
            difficulty="Intermediate",
            summary="Range oscillator that looks at where price sits inside its recent high-low channel.",
            how_it_works="Computes a smoothed stochastic percent K/D reading and buys low-channel exhaustion or sells high-channel exhaustion.",
            best_for="Shorter-term range trading where highs and lows define useful boundaries.",
            watch_out="Breakouts can punish it when a channel boundary stops being resistance or support.",
            key_parameters=("stochastic_window", "stochastic_smooth_window", "stochastic_lower_entry", "stochastic_upper_entry"),
            cli_args="--stochastic-window 14 --stochastic-smooth-window 3",
            params={"stochastic_window": 14, "stochastic_smooth_window": 3},
        ),
        _directional_item(
            strategy_id="bollinger_mean_reversion",
            name="Bollinger Band Mean Reversion",
            difficulty="Intermediate",
            summary="Classic band strategy that fades moves outside rolling volatility bands.",
            how_it_works="Uses a rolling mean and standard deviation to define upper and lower bands, then reverts toward the middle.",
            best_for="Liquid instruments that regularly snap back after volatility-adjusted extremes.",
            watch_out="Needs realistic costs because repeated band touches can create high turnover.",
            key_parameters=("bollinger_window", "bollinger_num_std", "z_exit"),
            cli_args="--bollinger-window 20 --bollinger-num-std 2.0",
            params={"bollinger_window": 20, "bollinger_num_std": 2.0},
        ),
        _directional_item(
            strategy_id="macd_trend",
            name="MACD Trend",
            difficulty="Intermediate",
            summary="Momentum strategy based on MACD histogram direction after signal-line confirmation.",
            how_it_works="Subtracts slow EMA from fast EMA, smooths it with a signal line, and trades the histogram sign.",
            best_for="Trend continuation signals with a familiar technical-analysis interpretation.",
            watch_out="MACD can lag sharp reversals and may underperform during noisy transitions.",
            key_parameters=("macd_fast_window", "macd_slow_window", "macd_signal_window"),
            cli_args="--macd-fast-window 12 --macd-slow-window 26 --macd-signal-window 9",
            params={"macd_fast_window": 12, "macd_slow_window": 26, "macd_signal_window": 9},
        ),
        _directional_item(
            strategy_id="donchian_breakout",
            name="Donchian Breakout",
            difficulty="Intermediate",
            summary="Breakout strategy that enters when price clears a prior high or low channel.",
            how_it_works="Uses prior rolling highs and lows for entry, and a shorter opposite channel for exits.",
            best_for="Markets with occasional strong directional moves.",
            watch_out="Can sit through many small false breakouts before a real trend arrives.",
            key_parameters=("breakout_window", "breakout_exit_window"),
            cli_args="--breakout-window 55 --breakout-exit-window 20",
            params={"breakout_window": 55, "breakout_exit_window": 20},
        ),
        _directional_item(
            strategy_id="keltner_breakout",
            name="Keltner Channel Breakout",
            difficulty="Advanced",
            summary="Volatility-adjusted breakout that uses an EMA centerline and ATR-style channel width.",
            how_it_works="Builds bands around an EMA using a close-to-close true-range proxy and holds until price crosses the centerline.",
            best_for="Breakout sleeves where channel width should expand in volatile markets.",
            watch_out="This close-only implementation is robust for cached close data, but true high-low-close ATR would be better with OHLC feeds.",
            key_parameters=("keltner_window", "keltner_atr_multiplier"),
            cli_args="--keltner-window 40 --keltner-atr-multiplier 1.5",
            params={"keltner_window": 40, "keltner_atr_multiplier": 1.5},
        ),
        _directional_item(
            strategy_id="volatility_target_trend",
            name="Volatility Target Trend",
            difficulty="Advanced",
            summary="Trend-following strategy that scales exposure by realized volatility.",
            how_it_works="Trades price versus a trend moving average, then sizes positions toward a target annualized volatility.",
            best_for="Professional research where position size should respond to changing risk.",
            watch_out="Volatility estimates can jump after losses, causing de-risking after the move has already happened.",
            key_parameters=("trend_window", "volatility_window", "target_volatility", "max_position"),
            cli_args="--trend-window 120 --volatility-window 20 --target-volatility 0.15",
            params={"trend_window": 120, "volatility_window": 20, "target_volatility": 0.15},
        ),
        _directional_item(
            strategy_id="time_series_momentum",
            name="Multi-Horizon Time-Series Momentum",
            difficulty="Advanced",
            summary="Trend model that requires multiple momentum horizons to agree before taking risk.",
            how_it_works="Votes across one-month, three-month, six-month, and twelve-month lookbacks by default.",
            best_for="ETF trend sleeves and slower institutional-style trend research.",
            watch_out="Slow horizons can react late, so it should be evaluated through full market cycles.",
            key_parameters=("momentum_lookbacks", "momentum_min_agreement"),
            cli_args="--momentum-lookbacks 21 63 126 252 --momentum-min-agreement 0.25",
            params={"momentum_lookbacks": [21, 63, 126, 252], "momentum_min_agreement": 0.25},
        ),
        _directional_item(
            strategy_id="adaptive_regime",
            name="Adaptive Regime Switcher",
            difficulty="Advanced",
            summary="Hybrid strategy that follows trends in stronger regimes and fades deviations in calmer regimes.",
            how_it_works="Uses volatility and trend-strength thresholds from the training window to decide between trend and mean reversion.",
            best_for="Researching whether one asset behaves differently across volatility regimes.",
            watch_out="Regime models are easy to overfit, so use purged validation and PBO/DSR reports before trusting results.",
            key_parameters=("regime_fast_window", "regime_slow_window", "regime_volatility_quantile", "z_entry"),
            cli_args="--regime-fast-window 30 --regime-slow-window 120 --regime-volatility-quantile 0.70",
            params={"regime_fast_window": 30, "regime_slow_window": 120, "regime_volatility_quantile": 0.70},
        ),
        StrategyCatalogItem(
            id="etf_trend",
            name="ETF Trend Momentum Sleeve",
            family="Portfolio Sleeve",
            difficulty="Advanced",
            pipeline="etf_trend",
            summary="Cross-asset ETF momentum sleeve that ranks ETFs and allocates to the strongest trend candidates.",
            how_it_works="Combines multiple momentum lookbacks, applies a trend filter, and allocates through the portfolio manager.",
            best_for="A first serious production candidate because ETF liquidity, borrow, and operational complexity are manageable.",
            watch_out="Needs live borrow/corporate-action checks, realistic ETF close/open execution assumptions, and out-of-sample monitoring.",
            key_parameters=("top_n", "trend_window", "rebalance_bars", "lookbacks"),
            example_cli=".\\.venv\\Scripts\\python.exe -m pairs_trading.apps.cli --pipeline etf_trend --symbols SPY QQQ TLT GLD XLE XLK --start 2010-01-01 --end 2026-04-15",
            paper_config_example={
                "name": "etf_trend_core",
                "pipeline": "etf_trend",
                "symbols": ["SPY", "QQQ", "TLT", "GLD", "XLE", "XLK"],
                "lookback_bars": 800,
                "params": {"top_n": 3, "trend_window": 200, "rebalance_bars": 21},
            },
        ),
        StrategyCatalogItem(
            id="stat_arb",
            name="Sector-Neutral Residual Stat-Arb",
            family="Portfolio Sleeve",
            difficulty="Advanced",
            pipeline="stat_arb",
            summary="Sector-constrained residual mean-reversion book with classic pairs as a sub-sleeve.",
            how_it_works="Screens within sectors, builds residual signals, applies break detection, ranks candidates, and allocates capital.",
            best_for="Market-neutral research where sector exposure should be controlled explicitly.",
            watch_out="Requires high-quality prices, survivorship-aware universes, borrow checks, and very careful cost modeling.",
            key_parameters=("sector_map_path", "residual_lookback", "residual_entry_z", "top_n_pairs"),
            example_cli=".\\.venv\\Scripts\\python.exe -m pairs_trading.apps.cli --pipeline stat_arb --sector-map examples/sector_map.sample.json --start 2018-01-01 --end 2026-04-15",
            paper_config_example={
                "name": "residual_stat_arb_shadow",
                "pipeline": "stat_arb",
                "sector_map_path": "examples/sector_map.sample.json",
                "lookback_bars": 620,
                "params": {"include_residual_book": True, "include_classic_pairs": True, "top_n_pairs": 3},
            },
        ),
        StrategyCatalogItem(
            id="graph_stat_arb",
            name="Graph Cluster Stat-Arb",
            family="Portfolio Sleeve",
            difficulty="Advanced",
            pipeline="graph_stat_arb",
            summary="Cluster-based market-neutral sleeve that trades laggards and leaders inside correlated stock communities.",
            how_it_works="Builds minimum-spanning-tree style clusters from return correlations, computes cluster residual z-scores, then goes long laggards and short leaders.",
            best_for="Moving beyond one pair at a time while still keeping sector and cluster exposure explicit.",
            watch_out="Correlation clusters are unstable in crises; use purged validation, turnover checks, and borrow/liquidity filters before trusting it.",
            key_parameters=("sector_map_path", "cluster_correlation_floor", "cluster_min_size", "residual_lookback", "entry_z"),
            example_cli=".\\.venv\\Scripts\\python.exe -m pairs_trading.apps.cli --pipeline graph_stat_arb --sector-map examples/sector_map.sample.json --start 2018-01-01 --end 2026-04-15",
            paper_config_example={
                "name": "graph_stat_arb_shadow",
                "pipeline": "graph_stat_arb",
                "sector_map_path": "examples/sector_map.sample.json",
                "lookback_bars": 620,
                "params": {
                    "cluster_correlation_floor": 0.55,
                    "cluster_min_size": 3,
                    "residual_lookback": 60,
                    "entry_z": 1.25,
                    "top_n_per_side": 2,
                },
            },
        ),
        StrategyCatalogItem(
            id="pead_sentiment",
            name="PEAD + Sentiment",
            family="Event Driven",
            difficulty="Advanced",
            pipeline="pead_sentiment",
            summary="Post-earnings announcement drift sleeve that combines official events, fundamental/event scores, and news sentiment.",
            how_it_works="Detects earnings-like events, combines event_score with daily sentiment, waits until the next bar, then holds for a short post-event drift window.",
            best_for="Researching whether official filings plus bullish/bearish news create measurable post-event continuation.",
            watch_out="This v1 uses event_score as a surprise proxy unless analyst-estimate surprise data is supplied upstream.",
            key_parameters=("event_file", "include_sec_filings", "daily_sentiment_file", "holding_period_bars", "entry_threshold"),
            example_cli=".\\.venv\\Scripts\\python.exe -m pairs_trading.apps.cli --pipeline pead_sentiment --symbols AAPL MSFT NVDA --event-file examples/events.sample.csv --daily-sentiment-file examples/daily_sentiment.sample.csv --start 2018-01-01 --end 2026-04-15",
            paper_config_example={
                "name": "pead_sentiment_shadow",
                "pipeline": "pead_sentiment",
                "symbols": ["AAPL", "MSFT", "NVDA"],
                "event_file": "examples/events.sample.csv",
                "lookback_bars": 520,
                "params": {
                    "holding_period_bars": 5,
                    "entry_threshold": 0.20,
                    "event_weight": 0.45,
                    "sentiment_weight": 0.55,
                    "daily_sentiment_file": "examples/daily_sentiment.sample.csv",
                    "require_earnings_event": True,
                },
            },
        ),
        StrategyCatalogItem(
            id="edgar_event",
            name="EDGAR Event Drift",
            family="Event Driven",
            difficulty="Advanced",
            pipeline="edgar_event",
            summary="Event-driven sleeve that turns standardized filing or company-facts events into short holding-period signals.",
            how_it_works="Loads events, maps them to tickers and dates, converts event scores to positions, then applies portfolio allocation.",
            best_for="Testing whether filings, fundamentals, or event scores create measurable post-event drift.",
            watch_out="Event timestamp quality matters. Avoid look-ahead bias by using only data available at the event time.",
            key_parameters=("event_file", "holding_period_bars", "entry_threshold"),
            example_cli=".\\.venv\\Scripts\\python.exe -m pairs_trading.apps.cli --pipeline edgar_event --symbols AAPL MSFT NVDA --event-file examples/events.sample.csv --start 2018-01-01 --end 2026-04-15",
            paper_config_example={
                "name": "edgar_event_shadow",
                "pipeline": "edgar_event",
                "symbols": ["AAPL", "MSFT", "NVDA"],
                "event_file": "examples/events.sample.csv",
                "lookback_bars": 520,
                "params": {"holding_period_bars": 5, "entry_threshold": 0.15},
            },
        ),
        StrategyCatalogItem(
            id="committee_signal_follower",
            name="Committee Signal Follower",
            family="Evaluation",
            difficulty="Intermediate",
            pipeline="committee_signal_follower",
            summary="Simulates trading every historical committee research decision — BUY goes long, SELL goes short, AVOID flattens — to measure the real P&L of following the AI research agents.",
            how_it_works="Loads past committee decisions from the DecisionHistoryStore, then on each decision date sets a target weight proportional to the committee's confidence. Repeated BUY signals can scale in if confidence increases. The walk-forward backtester handles entry delays, costs, and benchmarks against buy-and-hold.",
            best_for="Validating whether the market research committee's past advice would have been profitable. Not for live trading without human review.",
            watch_out="Decisions are based on LLM analysis of historical data — they are not forward-looking. The backtest assumes you trade every signal; skipping trades changes the outcome. Past performance does not guarantee future results.",
            key_parameters=("max_position_pct", "confidence_threshold", "scale_in_confidence_delta", "scale_out_on_opposite"),
            example_cli=".\\.venv\\Scripts\\python.exe -m pairs_trading.apps.cli --pipeline committee_signal_follower --symbols AAPL MSFT --start 2024-01-01 --end 2025-01-01",
            paper_config_example={
                "name": "committee_signal_follower_shadow",
                "pipeline": "committee_signal_follower",
                "symbols": ["AAPL", "MSFT"],
                "lookback_bars": 520,
                "params": {
                    "max_position_pct": 0.25,
                    "confidence_threshold": 30,
                    "scale_in_confidence_delta": 10,
                    "scale_out_on_opposite": True,
                    "flat_on_avoid": True,
                },
            },
        ),
    ]
    return [asdict(item) for item in items]
