# Quant Research App

This repository is structured as an early-stage professional quant research app rather than a single strategy script.

The architecture follows a modular flow:
- `pairs_trading/core/`: shared strategy contracts, standardized outputs, and portfolio construction
- `pairs_trading/data/`: market, news, and event provider interfaces plus caching
- `pairs_trading/engines/`: backtesting, validation, execution, risk, broker simulation, and reconciliation
- `pairs_trading/features/`: alternative data transforms and overlays such as financial sentiment
- `pairs_trading/strategies/`: reusable alpha models for directional, stat-arb, and event-driven sleeves
- `pairs_trading/pipelines/`: orchestration layers that turn strategy outputs into portfolios
- `pairs_trading/operations/`: operational workflows such as shadow paper trading
- `pairs_trading/reporting/`: research dashboards and paper-trading dashboards
- `pairs_trading/api/`: frontend-facing read models that can later sit behind FastAPI or another web server
- `pairs_trading/backend/`: FastAPI HTTP backend for the frontend and future external clients
- `pairs_trading/platform/`: shared infrastructure primitives such as durable metadata storage
- `pairs_trading/apps/`: command-line entry points
- `apps/`: future-facing deployment facades for API, worker, and web apps
- `frontend/`: Vite React TypeScript operations console

More detail lives in [docs/architecture.md](docs/architecture.md).
The end-to-end backend/frontend tutorial lives in [docs/backend_frontend_tutorial.md](docs/backend_frontend_tutorial.md).
Backend/frontend instructions live in [docs/fullstack_workflows.md](docs/fullstack_workflows.md).
Strategy explanations live in [docs/strategy_catalog.md](docs/strategy_catalog.md) and are also exposed in the web dashboard.
Interactive backtest agent workflows live in [docs/backtest_agent_workbench.md](docs/backtest_agent_workbench.md).
The SaaS-readiness workflow lives in [docs/saas_readiness_workflow.md](docs/saas_readiness_workflow.md).
The UX/theme/refresh/telemetry audit lives in [docs/product_ux_observability_audit.md](docs/product_ux_observability_audit.md).

## Main Sleeves

The current codebase supports three research sleeves that can be matured independently:
- `etf_trend`: medium-frequency ETF trend and momentum with inverse-volatility sizing and rotation
- `stat_arb`: sector-neutral residual mean reversion plus classic pairs as a sub-sleeve
- `edgar_event`: EDGAR-style event drift using standardized event inputs or SEC company facts

The repo also keeps generic indicator pipelines for single-asset research:
- `buy_and_hold`
- `ma_cross`
- `ema_cross`
- `rsi_mean_reversion`
- `sma_deviation`
- `stochastic_oscillator`
- `bollinger_mean_reversion`
- `macd_trend`
- `donchian_breakout`
- `keltner_breakout`
- `volatility_target_trend`
- `time_series_momentum`
- `adaptive_regime`

## Run From Source

General help:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading --help
```

Step-by-step backtest examples live in [docs/backtest_workflows.md](docs/backtest_workflows.md).
Shadow paper-trading deployment examples live in [docs/paper_trading_workflows.md](docs/paper_trading_workflows.md).

### ETF Trend / Momentum

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline etf_trend `
  --symbols SPY QQQ IWM DIA TLT IEF GLD XLE XLF XLK XLV `
  --experiment-name etf_trend_core `
  --validation-purge-bars 5 `
  --validation-pbo-partitions 8
```

### Sector-Neutral Residual Stat-Arb

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline stat_arb `
  --sector-map examples\sector_map.sample.json `
  --experiment-name residual_stat_arb `
  --validation-purge-bars 5 `
  --validation-pbo-partitions 8
```

Stat-arb with local news sentiment:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline stat_arb `
  --sector-map examples\sector_map.sample.json `
  --news-provider local `
  --news-file data\news\headlines.csv `
  --use-finbert `
  --experiment-name stat_arb_finbert
```

### EDGAR Event Drift

Using a local standardized event file:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline edgar_event `
  --symbols AAPL MSFT NVDA AMZN GOOGL META JPM XOM `
  --event-file examples\events.sample.csv `
  --experiment-name edgar_event_local
```

Using SEC company facts directly:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline edgar_event `
  --symbols AAPL MSFT NVDA AMZN `
  --use-sec-companyfacts `
  --edgar-user-agent "Your Name your.email@example.com" `
  --experiment-name edgar_event_sec
```

### Generic Indicator Research

Buy-and-hold benchmark:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline buy_and_hold `
  --symbols SPY QQQ TLT `
  --experiment-name passive_baseline
```

Moving-average crossover:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline ma_cross `
  --symbols AAPL MSFT NVDA `
  --fast-window 20 `
  --slow-window 80 `
  --experiment-name ma_cross_equities
```

RSI mean reversion:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline rsi_mean_reversion `
  --symbols SPY QQQ IWM `
  --rsi-window 14 `
  --lower-entry 30 `
  --upper-entry 70 `
  --exit-level 50 `
  --experiment-name rsi_reversion
```

Donchian breakout:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline donchian_breakout `
  --symbols GLD TLT XLE `
  --breakout-window 55 `
  --breakout-exit-window 20 `
  --experiment-name donchian_macro
```

Advanced directional examples:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline volatility_target_trend `
  --symbols SPY QQQ TLT GLD `
  --trend-window 120 `
  --volatility-window 20 `
  --target-volatility 0.15 `
  --experiment-name vol_target_trend
```

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --pipeline adaptive_regime `
  --symbols SPY QQQ IWM `
  --regime-fast-window 30 `
  --regime-slow-window 120 `
  --regime-volatility-quantile 0.70 `
  --experiment-name adaptive_regime_lab
```

The root launcher still works too:

```powershell
.\.venv\Scripts\python.exe run_pipeline.py --pipeline etf_trend
```

## Output

Each run writes an experiment folder under `artifacts/experiments/<timestamp>_<name>/` with:
- `summary.json`
- `validation.json`
- `diagnostics.json`
- `fold_metrics.parquet`
- `equity_curve.parquet`
- `validation_trial_metrics.parquet` when a trial grid is used
- a `visuals/` folder with charts and an HTML report

Key metrics:
- `psr`: probabilistic Sharpe ratio
- `dsr`: deflated Sharpe ratio
- `pbo`: probability of backtest overfitting
- `avg_turnover`, `max_drawdown`, `annualized_return`, `annualized_vol`

## Shadow Paper Trading

The repo now includes a multi-strategy shadow paper deployment layer for experimental use.

Use the sample config:

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --deploy-paper-config examples\paper_deployment.sample.json `
  --paper-asof-date 2026-04-24
```

That mode:
- keeps a separate fake-money ledger per strategy
- writes state to `artifacts/paper/state/`
- writes each run report to `artifacts/paper/runs/<timestamp>_paper_batch/`
- produces a cross-strategy leaderboard so you can see which sleeve is making or losing money
- generates a run-specific visual dashboard at `artifacts/paper/runs/<timestamp>_paper_batch/visuals/index.html`
- updates a stable latest dashboard at `artifacts/paper/live_dashboard/index.html`

This is intentionally a `shadow` paper layer rather than a live broker adapter by default:
- ETF, directional, and event sleeves trade underlying symbols in the fake ledger
- stat-arb runs as a synthetic component book so PnL stays attributable even before broker routing is added

The paper dashboard is the easiest way to understand the money:
- `Overview` shows total fake capital, the sleeve leaderboard, and which books currently hold positions
- `Capital Flow` shows how the latest move split between market PnL and rebalance cost
- each sleeve page shows current positions, target weights, latest orders, and diagnostics
- `Glossary` explains what fields like `equity_before`, `daily_pnl`, and `rebalance_cost_pnl` mean

For a future frontend, use `pairs_trading.api.build_paper_dashboard_payload(...)` to get a stable JSON-style read model from the paper ledgers.

The repo now includes that frontend boundary:
- backend entrypoint: `pairs_trading.backend.app:app`
- local frontend: `frontend/`
- primary API payload: `GET /api/paper/summary`
- interactive backtest launch: `POST /api/backtests/run`

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Packaging

This repo includes a `pyproject.toml` so it can grow into an installable internal research package. Once installed in editable mode, you can also run:

```powershell
pairs-trading --pipeline etf_trend
```
