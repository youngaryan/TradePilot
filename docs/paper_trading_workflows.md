# Paper Trading Workflows

This repo now supports multi-strategy shadow paper trading for experimental usage.

The safest first deployment shape is:
1. run all sleeves in local shadow paper ledgers
2. compare their fake-money equity and behavior separately
3. only after that, mirror the simplest sleeve to a broker paper account

## Why Shadow Paper First

Shadow paper trading solves two early problems:
- strategy attribution stays clean because each sleeve has its own ledger
- you can deploy strategies like residual stat-arb before you have broker-specific order routing for every leg

## Run A Manual Paper Batch

```powershell
.\.venv\Scripts\python.exe -m pairs_trading `
  --deploy-paper-config examples\paper_deployment.sample.json `
  --paper-asof-date 2026-04-24
```

## Run From The Website

Open the React dashboard and go to `Live Trading`.

Configure one or more fake-money agents, then click `Deploy Agents`. The page builds an inline deployment config and starts an asynchronous paper job:
- `loading_config`: reads the deployment config and execution settings
- `building_signals`: runs the strategies, news/sentiment overlays when configured, and builds target weights
- `simulating_orders`: applies risk controls, costs, slippage, and fake execution
- `saving_ledgers`: writes positions, cash, latest orders, dashboards, and API payloads

The live page supports:
- multiple agents in one deployment
- method selection from the catalog
- symbols, sector maps, and event files
- timeframes and lookback bars
- single-date execution
- business-day date range replay
- sentiment/news settings for news-aware pipelines

Backend endpoints:

```text
POST /api/paper/run-job
GET  /api/paper/jobs
GET  /api/paper/jobs/{job_id}
GET  /api/system/metadata
```

This is still fake-money shadow trading. It is called `live` because it runs against the current configured as-of date and updates persistent ledgers, not because it sends real broker orders.

## What The Sample Config Does

The sample config at `examples/paper_deployment.sample.json` runs:
- `etf_trend_core`
- `residual_stat_arb_shadow`
- `edgar_event_shadow`
- directional lab examples such as `vol_target_trend_shadow` and `bollinger_reversion_shadow`

The execution section defines:
- starting cash
- commissions
- slippage
- minimum trade size
- a weight tolerance to avoid noise rebalances

## Where Results Go

Persistent ledgers:
- `artifacts/paper/state/<strategy>.json`

Per-run artifacts:
- `artifacts/paper/runs/<timestamp>_paper_batch/paper_batch_summary.json`
- `artifacts/paper/runs/<timestamp>_paper_batch/paper_leaderboard.parquet`
- `artifacts/paper/runs/<timestamp>_paper_batch/paper_leaderboard.json`
- `artifacts/paper/runs/<timestamp>_paper_batch/visuals/index.html`

Stable latest dashboard:
- `artifacts/paper/live_dashboard/index.html`

Durable metadata index:
- `artifacts/metadata/app.sqlite3`

The JSON files remain easy to inspect manually, while SQLite gives the future worker/API layer a queryable index of jobs, deployment configs, and paper experiment runs.

## How To Read The Output

Per strategy, the paper summary includes:
- `equity_after`
- `daily_pnl`
- `net_return_since_inception`
- `gross_exposure_ratio`
- `trade_count`
- current `positions`

The dashboard is the easiest place to inspect the money:
- `Overview`: combined fake-money equity, PnL chart, exposure chart, capital allocation, leaderboard, and per-sleeve status
- `Live Trading`: multi-agent paper deployment, execution progress, job history, method charts, sentiment coverage, symbol coverage, risk footprint, order notional, and latest orders
- `Strategies`: current holdings, target weights, latest equity curve, and per-strategy capital
- `Orders`: simulated buy/sell orders, notional, commission, and execution price
- `Diagnostics`: raw model diagnostics and backend payloads
- `Catalog`: explanations, risk notes, CLI examples, and paper config snippets for every strategy
- `Tutorials`: website usage guide for the dashboard and FastAPI docs site

Important clarification:
- this is `live` at the cadence of the paper run, not tick-by-tick streaming
- if your automation runs nightly, the dashboard refreshes nightly
- the stable `artifacts/paper/live_dashboard/index.html` path always points to the latest saved paper state

## Recommended Next Step

Use the shadow paper layer every market day for a few weeks before adding any real broker paper routing.

That will tell you:
- which sleeves are stable enough to keep
- whether turnover is manageable
- whether event inputs are arriving on time
- whether stat-arb selection is too unstable day to day
