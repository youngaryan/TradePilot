# Backtest Workflows

This guide shows how to run the three main sleeves in a consistent research process:
- `ETF trend / momentum`
- `Sector-neutral residual stat-arb`
- `EDGAR event drift`

It also supports single-asset directional strategy research. Those strategies are useful for education, feature testing, and building blocks, but they should be promoted slowly through the same validation process as the main sleeves.

## 1. Verify The Environment

Run the test suite first:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Check the CLI:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --help
```

## 2. Prepare Data

Price data is loaded through `pairs_trading/data/market.py`.

By default the CLI uses:
- `CachedParquetProvider`
- `YahooFinanceProvider` as the upstream source

That means:
- the first run will fetch data and save parquet cache files under `data/cache/`
- later runs will reuse the cache for the same symbol/date/interval request

For professional deployment, replace `YahooFinanceProvider` with a production data vendor behind the same interface.

### Optional Inputs

Sector map for stat-arb:

```json
{
  "KO": "Beverages",
  "PEP": "Beverages",
  "KDP": "Beverages",
  "XOM": "Energy",
  "CVX": "Energy",
  "COP": "Energy",
  "JPM": "Banks",
  "BAC": "Banks",
  "WFC": "Banks",
  "C": "Banks"
}
```

Event file schema for local event backtests:

```csv
timestamp,ticker,event_score,confidence,event_type
2024-02-01,AAPL,0.45,0.90,company_facts
2024-02-02,MSFT,0.35,0.80,company_facts
2024-02-05,NVDA,-0.30,0.75,company_facts
```

## 3. Run The ETF Trend Sleeve

This is the cleanest first sleeve because it avoids short borrow and unstable pair selection.

Recommended command:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline etf_trend `
  --symbols SPY QQQ IWM DIA TLT IEF GLD SLV XLE XLF XLK XLV `
  --start 2010-01-01 `
  --end 2026-04-15 `
  --experiment-name etf_trend_core `
  --validation-purge-bars 5 `
  --validation-pbo-partitions 8
```

What it does:
- builds a walk-forward trend/momentum portfolio across liquid ETFs
- uses multi-horizon momentum and a trend filter
- applies inverse-volatility sizing
- runs a trial grid to compare a few nearby variants for `PBO` / `DSR`

What to inspect afterward:
- `summary.json`
- `validation.json`
- `visuals/report.html`

## 4. Run The Sector-Neutral Residual Stat-Arb Sleeve

This pipeline combines:
- residual mean reversion across same-sector names
- classic pairs as a sub-sleeve
- optional sentiment overlay for ranking and scaling

Recommended command:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline stat_arb `
  --sector-map examples\sector_map.sample.json `
  --start 2018-01-01 `
  --end 2026-04-15 `
  --experiment-name residual_stat_arb `
  --validation-purge-bars 5 `
  --validation-pbo-partitions 8
```

With local sentiment:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline stat_arb `
  --sector-map examples\sector_map.sample.json `
  --news-provider local `
  --news-file data\news\headlines.csv `
  --use-finbert `
  --experiment-name residual_stat_arb_sentiment
```

What to inspect afterward:
- `diagnostics.json` for `selected_pairs`, `residual_symbols`, and sleeve settings
- `validation_trial_metrics.parquet` for neighboring variants
- `visuals/pair_weights.png`
- `visuals/sentiment_overlay.png` when sentiment is used

## 5. Run The EDGAR Event Sleeve

There are two ways to do this.

### Option A: Local standardized events

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline edgar_event `
  --symbols AAPL MSFT NVDA AMZN GOOGL META JPM XOM `
  --event-file examples\events.sample.csv `
  --start 2018-01-01 `
  --end 2026-04-15 `
  --experiment-name edgar_event_local `
  --validation-purge-bars 5 `
  --validation-pbo-partitions 8
```

### Option B: SEC company facts

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline edgar_event `
  --symbols AAPL MSFT NVDA AMZN `
  --use-sec-companyfacts `
  --edgar-user-agent "Your Name your.email@example.com" `
  --start 2018-01-01 `
  --end 2026-04-15 `
  --experiment-name edgar_event_sec `
  --validation-purge-bars 5 `
  --validation-pbo-partitions 8
```

The SEC path builds event scores from company facts growth data and caches them under `data/event_cache/`.

## 6. Run Directional Strategy Labs

The directional lab now includes thirteen strategies from basic to advanced:
- `buy_and_hold`: passive long-only benchmark.
- `ma_cross`: simple moving-average trend.
- `ema_cross`: faster exponential moving-average trend.
- `rsi_mean_reversion`: oscillator-based overbought/oversold mean reversion.
- `sma_deviation`: z-score deviation from a moving average.
- `stochastic_oscillator`: channel-location oscillator mean reversion.
- `bollinger_mean_reversion`: volatility-band mean reversion.
- `macd_trend`: MACD histogram trend continuation.
- `donchian_breakout`: prior high/low breakout.
- `keltner_breakout`: volatility-adjusted channel breakout.
- `volatility_target_trend`: trend following with realized-volatility scaling.
- `time_series_momentum`: multi-horizon momentum vote.
- `adaptive_regime`: trend in stronger regimes, mean reversion in calmer regimes.

Example advanced lab:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline time_series_momentum `
  --symbols SPY QQQ TLT GLD `
  --momentum-lookbacks 21 63 126 252 `
  --momentum-min-agreement 0.25 `
  --start 2010-01-01 `
  --end 2026-04-15 `
  --experiment-name tsmom_macro_lab
```

Use the web dashboard `Catalog` page or [strategy_catalog.md](strategy_catalog.md) for full explanations and parameter examples.

## 7. Read The Validation Outputs

Each sleeve now reports:
- `psr`
- `dsr`
- `pbo`

Rules of thumb:
- higher `dsr` is better than higher raw Sharpe alone
- lower `pbo` is better
- review `avg_turnover` together with `net_return`
- drawdown and turnover matter as much as raw CAGR

## 8. Inspect Execution And Risk Assumptions

The broker simulation now separates:
- `pairs_trading/engines/risk.py`: leverage and turnover constraints
- `pairs_trading/engines/execution.py`: commission, spread, slippage, impact, funding, borrow, and delay assumptions
- `pairs_trading/engines/broker.py`: orchestration of risk and execution
- `pairs_trading/engines/reconciliation.py`: post-run execution summaries

Columns worth checking in `equity_curve.parquet`:
- `execution_cost`
- `impact_cost`
- `latency_cost`
- `borrow_cost`
- `total_cost`
- `net_return`
- `risk_scale`
- `participation_rate`

## 9. Compare Sleeves

After running the three main sleeves, compare:
- return quality: `dsr`, `pbo`, `max_drawdown`
- implementation burden: turnover, borrow, event freshness
- live feasibility: universe breadth, rebalancing cadence, data dependencies

Recommended order for serious capital:
1. `etf_trend`
2. `stat_arb`
3. `edgar_event`

## 10. Next Iteration Checklist

Before putting more capital behind any sleeve:
- replace the default price provider with a production vendor
- add point-in-time corporate actions and symbol master support
- calibrate execution assumptions from real fills
- run paper and shadow trading
- compare live slippage against the simulated broker outputs
