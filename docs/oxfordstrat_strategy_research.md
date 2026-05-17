# Oxford Capital Strategies Public Strategy Review

Research date: 2026-05-16

## Scope

Reviewed the public Oxford Capital Strategies resources index, strategy reviews, indicator reviews, volatility-clustering data posts, product pages, research-services page, sitemap, performance access page, and risk disclosure.

Primary public source groups:

- Resources index: https://oxfordstrat.com/resources/
- Products: https://oxfordstrat.com/products/
- ALPHA20: https://oxfordstrat.com/products/alpha20tm-trading-system/
- ALPHA20 concept: https://oxfordstrat.com/products/alpha20tm-trading-system/concept/
- DELTA20: https://oxfordstrat.com/products/delta20tm-trading-system/
- Research services: https://oxfordstrat.com/products/proprietary-research/
- Risk disclosure: https://oxfordstrat.com/risk-disclosure/

Oxford's strategy pages are mostly public-domain strategy reviews tested on futures portfolios. The site repeatedly states that the performance shown is hypothetical or simulated. No future-performance claim is made here.

## Selection Criteria

Strategies were ranked higher when they had a clear market rationale, simple rules, broad-market applicability, lower expected turnover, robustness indications in Oxford's own summaries, and compatibility with this project's close-price walk-forward architecture. Strategies were ranked lower or rejected when Oxford's own summary said the filter did not add value, the strategy stopped working after realistic costs, or implementation would require open/high/low/intraday data that this project does not currently ingest.

ALPHA20 and DELTA20 are proprietary. Public ALPHA20 pages describe fractal filters, self-adaptation, same parameters across markets/timeframes, and risk-neutral/risk-seeking/risk-avoiding exits, but do not disclose exact rules. Public DELTA20 pages describe volatility price patterns, signal-to-noise patterns, volatility clustering, momentum, failed momentum, and patterns-within-patterns. The implemented `oxford_volatility_clustering` strategy is a public DELTA20-adjacent approximation; no proprietary ALPHA20 or DELTA20 rules were invented.

## Ranked Implemented Strategies

### 1. Oxford Combined Donchian

- Source: https://oxfordstrat.com/trading-strategies/combined-donchian-channels/
- Logic: Enter on a breakout above/below a prior Donchian channel and exit on a shorter opposite channel.
- Hypothesis: Persistent trends follow major price-channel breakouts; a shorter trailing channel can capture trends while cutting reversals.
- Entry rules: Long when close exceeds the prior entry-channel high; short when close breaks the prior entry-channel low.
- Exit rules: Long exits when close breaks the prior exit-channel low; short exits when close breaks the prior exit-channel high.
- Position sizing: Single-symbol target position is -1, 0, or +1; portfolio sizing is handled by `PortfolioManager`.
- Risk management: Channel exit plus platform-level costs, leverage, and portfolio risk controls.
- Inputs: Close prices, entry window, exit window.
- Strengths: Classic cross-market trend-following rationale, simple, low parameter count, relatively cost tolerant.
- Weaknesses: Whipsaws in range-bound markets; source uses stop orders on OHLC channels, while this implementation is close-only.
- Complexity: Low to medium.
- Selected over: ADX/volume-filtered Donchian variants because Oxford summaries say several added filters do not add value.
- Assumptions: Close-only channels approximate high/low stop channels.

### 2. Oxford Price Momentum

- Source: https://oxfordstrat.com/trading-strategies/price-momentum-model/
- Logic: Require fast and slow price momentum to agree; exit when fast momentum flips.
- Hypothesis: Trends persist across multiple horizons when short and longer lookbacks agree.
- Entry rules: Long when both fast and slow price differences are positive; short when both are negative.
- Exit rules: Long exits when fast momentum turns negative; short exits when fast momentum turns positive.
- Position sizing: -1, 0, or +1 per symbol before portfolio allocation.
- Risk management: Momentum exit, cost model, and portfolio controls.
- Inputs: Close prices, slow lookback, fast-lookback index.
- Strengths: Very simple, close-compatible, low turnover when slower defaults are used.
- Weaknesses: Lags reversals and may overstay late-stage trends.
- Complexity: Low.
- Selected over: MACD signal-line and directional-movement variants because Oxford judged those weaker or average.
- Assumptions: Close-to-close execution stands in for next-open orders.

### 3. Oxford Dual Momentum ROC

- Source: https://oxfordstrat.com/trading-strategies/dual-momentum-rate-of-change/
- Logic: Slow ROC is the filter, fast ROC is the setup, and trades use a time exit.
- Hypothesis: Momentum is more robust when multiple speeds agree and trades are not over-managed.
- Entry rules: Long when fast and slow ROC are both positive; short when both are negative.
- Exit rules: Exit when the opposite agreement appears or after the configured time exit.
- Position sizing: -1, 0, or +1 per symbol before portfolio allocation.
- Risk management: Time exit, opposite-signal exit, transaction-cost estimates.
- Inputs: Close prices, fast lookback, slow lookback, time-exit bars.
- Strengths: Directly implements Oxford's dual-momentum concept and longer-holding-period observation.
- Weaknesses: Time exits can be arbitrary and need walk-forward validation.
- Complexity: Low.
- Selected over: Single Vortex Indicator because Oxford's summary says dual momentum improved on single momentum.
- Assumptions: ROC signs are computed from adjusted closes.

### 4. Oxford Bollinger Momentum

- Source: https://oxfordstrat.com/trading-strategies/bollinger-band/
- Logic: Three-phase trend model: long above upper band, short below lower band, neutral after crossing the middle band.
- Hypothesis: A volatility-adjusted breakout identifies directional expansion better than a raw moving-average cross.
- Entry rules: Long when close is above the upper Bollinger band; short when below the lower band.
- Exit rules: Long exits below the middle band; short exits above the middle band.
- Position sizing: -1, 0, or +1 per symbol before portfolio allocation.
- Risk management: Middle-band exit and platform risk controls.
- Inputs: Close prices, Bollinger window, standard-deviation multiplier.
- Strengths: Close-compatible, volatility adjusted, Oxford summary favors slower frequencies.
- Weaknesses: Can whipsaw during volatility expansion without trend follow-through.
- Complexity: Low.
- Selected over: Volatility squeeze because Oxford's summary says the squeeze filter did not improve the base model.
- Assumptions: Uses rolling close standard deviation rather than full OHLC volatility.

### 5. Oxford Keltner Three Phase

- Source: https://oxfordstrat.com/trading-strategies/keltner-channels-2/
- Logic: Three-phase Keltner channel trend system with centerline exits.
- Hypothesis: Breakouts beyond an ATR-style envelope are more informative when volatility is normalized.
- Entry rules: Long above the upper Keltner line; short below the lower line.
- Exit rules: Long exits below the EMA centerline; short exits above it.
- Position sizing: -1, 0, or +1 per symbol before portfolio allocation.
- Risk management: Centerline exit and portfolio controls.
- Inputs: Close prices, EMA window, ATR multiplier.
- Strengths: Volatility-adjusted and simple.
- Weaknesses: This implementation uses a close-to-close ATR proxy because the data layer stores close prices only.
- Complexity: Low to medium.
- Selected over: Opening-range breakout variants because many ORB variants need open/high/low intraday stop behavior.
- Assumptions: Close-to-close absolute movement approximates ATR.

### 6. Oxford Normalized Regression Slope

- Source: https://oxfordstrat.com/trading-strategies/normalized-linear-regression/
- Logic: Trade when normalized rolling regression slope exceeds a positive or negative growth threshold.
- Hypothesis: A fitted slope can estimate trend direction with less point-to-point noise than raw momentum.
- Entry rules: Long above the positive growth threshold; short below the negative threshold.
- Exit rules: Long exits when normalized slope drops below zero; short exits when it rises above zero.
- Position sizing: -1, 0, or +1 per symbol before portfolio allocation.
- Risk management: Zero-slope exit and cost model.
- Inputs: Close prices, regression window, growth threshold.
- Strengths: Smooth trend signal, simple close-only data requirements.
- Weaknesses: Fitted slopes lag abrupt reversals; normalization is approximated.
- Complexity: Medium.
- Selected over: Simple moving average because it is comparably simple but less tied to arbitrary crossovers.
- Assumptions: Normalized slope is implemented as fitted slope times lookback divided by price.

### 7. Oxford Bollinger %b Reversal

- Source: https://oxfordstrat.com/trading-strategies/bollinger-bands-reversal/
- Logic: Buy repeated low %b readings only when price is above a trend average; short repeated high %b readings only below the trend average.
- Hypothesis: Pullbacks within an established trend mean-revert more reliably than unconditional band fades.
- Entry rules: Long after the configured count of %b readings below the lower threshold in an uptrend; short after repeated high %b readings in a downtrend.
- Exit rules: Long exits after %b rises above the upper threshold; short exits after %b falls below the lower threshold.
- Position sizing: -1, 0, or +1 per symbol before portfolio allocation.
- Risk management: Opposite-band exit and platform risk controls.
- Inputs: Close prices, band window, trend window, %b thresholds, confirmation count.
- Strengths: Mean-reversion logic constrained by trend regime.
- Weaknesses: Can lose during real trend breaks when a pullback becomes a reversal.
- Complexity: Medium.
- Selected over: Pure Bollinger mean reversion because Oxford's public setup explicitly adds trend context.
- Assumptions: Uses close-only %b and next-bar return convention.

### 8. Oxford RSI2 Pullback

- Source: https://oxfordstrat.com/trading-strategies/relative-strength-index-1/
- Logic: Long-only equity pullback system using 2-period RSI under a long moving-average bull-market filter.
- Hypothesis: In equity-index uptrends, sharp short-term selloffs can mean-revert.
- Entry rules: Long when close is above the setup moving average and RSI2 is below the entry threshold.
- Exit rules: Exit when price recovers above a short moving average.
- Position sizing: Long or flat only before portfolio allocation.
- Risk management: Short moving-average exit; no short exposure.
- Inputs: Close prices, setup MA window, RSI window, RSI entry threshold, exit MA window.
- Strengths: Diversifies slower trend systems and is easy to test.
- Weaknesses: Oxford's own summary says it underperformed alternative momentum models; not suitable as a core strategy without validation.
- Complexity: Low.
- Selected over: More elaborate reversal/candlestick systems because it is close-compatible and easier to validate.
- Assumptions: Applied to any symbol but intended mainly for equity-index-like markets.

### 9. Oxford Wyckoff Range Reversion

- Source: https://oxfordstrat.com/trading-strategies/richard-wyckoff-mean-reversion-3/
- Logic: Approximate bear-trap and bull-trap re-entry into a rolling trading range.
- Hypothesis: Failed breakouts outside a well-defined range can revert toward the range midpoint.
- Entry rules: Long when price was below the prior range low and closes back inside; short when price was above the prior range high and closes back inside.
- Exit rules: Exit at the range midpoint or after a time exit.
- Position sizing: -1, 0, or +1 per symbol before portfolio allocation.
- Risk management: Midpoint target, time exit, and portfolio controls.
- Inputs: Close prices, range window, exit window.
- Strengths: Clear mean-reversion hypothesis and lower complexity than hand-labeled swing structures.
- Weaknesses: Public Oxford rules use richer swing-point geometry and stop levels; close-only rolling ranges are a simplification.
- Complexity: Medium.
- Selected over: Turtle Soup and false-breakout variants because this version is easier to express with close data and explicit range logic.
- Assumptions: Rolling close range approximates Wyckoff trading range.

### 10. Oxford Volatility Clustering

- Sources: https://oxfordstrat.com/data/volatility-clustering-1/ and https://oxfordstrat.com/data/volatility-clustering-2/
- Logic: Trade in the same direction after unusually large recent moves, then exit after a short hold.
- Hypothesis: Large moves tend to cluster; Oxford's public volatility-clustering tests found continuation scenarios stronger than reversal scenarios.
- Entry rules: If the current close-to-close move is the largest in the recent range window, go with the move's direction.
- Exit rules: Exit after the configured time exit.
- Position sizing: -1, 0, or +1 per symbol before portfolio allocation.
- Risk management: Short holding period and platform-level risk controls.
- Inputs: Close prices, move window, range window, time-exit bars.
- Strengths: Implements a public DELTA20-adjacent concept without using proprietary details.
- Weaknesses: True Oxford wide-range rules use high/low/open data; close-only movement is an approximation.
- Complexity: Low to medium.
- Selected over: Volatility Clustering Part 3 ORB because Oxford's summary says ORB did not improve the original model.
- Assumptions: Close-to-close large moves approximate OHLC wide-range events.

## Explicitly Rejected Or Deferred

- Direct ALPHA20 implementation: proprietary rules are not public. The public fractal concept was reviewed, but implementing exact fractal filters would require inventing rules.
- Direct DELTA20 implementation: proprietary rules are not public. Only public volatility-clustering concepts were implemented.
- FRAMA: Oxford's public summary says it did not significantly outperform alternatives and uses an inaccurate fractal-dimension approximation.
- ADX and Directional Movement filters: Oxford summaries say they did not add value or reduced performance.
- MACD signal-line model: Oxford summary says it performed worse than the earlier MACD model and default MACD parameters should be avoided.
- Friday Momentum / weekly ORB: Oxford summaries say realistic costs degraded or ended the edge.
- NR7 and price-breakout NR7: Oxford summaries say the pattern was not currently tradeable after costs without additional rules.
- Volatility Squeeze: Oxford summary says it did not improve the simple Bollinger momentum model.
- Volume and POIV filters: Oxford summaries say improvements were limited to shorter lookbacks and did not help longer lookbacks.
- Candlestick/gap/hook/smash-day patterns: many require open/high/low and stop execution, making them poor fits for this close-only architecture.

## Implementation Notes

All implemented strategies live in `pairs_trading/strategies/oxford.py`. They return the standard `StrategyOutput` used by the walk-forward engine. Signals are computed from data available at the signal bar, and strategy returns are calculated using the prior position, matching the existing no-look-ahead convention in `pairs_trading/strategies/directional.py`.

The project currently stores close-price matrices, not OHLCV bars. Where Oxford's source rules require open, high, low, volume, open interest, or stop-order execution, the implementation uses conservative close-only approximations and records the assumption in `output.diagnostics["implementation_assumption"]`.
