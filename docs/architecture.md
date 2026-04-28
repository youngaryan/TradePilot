# Architecture

This repo is organized as an early professional quant app. The package layout separates research, data, execution, operations, reporting, and future frontend/API concerns.

For the full backend/frontend walkthrough, read [backend_frontend_tutorial.md](backend_frontend_tutorial.md).

## Package Layout

- `pairs_trading/core/`: stable contracts and portfolio primitives. Put shared dataclasses, strategy output contracts, and allocation utilities here.
- `pairs_trading/data/`: external and local data providers. Put market data, news, EDGAR/event providers, caches, and provider interfaces here.
- `pairs_trading/engines/`: reusable engines. Put backtesting, validation, risk controls, execution modeling, broker simulation, and reconciliation here.
- `pairs_trading/features/`: alternative data transforms and overlays such as financial sentiment.
- `pairs_trading/strategies/`: alpha models. Put individual signal generators here.
- `pairs_trading/pipelines/`: strategy orchestration. Put multi-asset or multi-sleeve pipelines here.
- `pairs_trading/operations/`: production-like workflows. Put paper trading, future live trading jobs, and scheduled operational flows here.
- `pairs_trading/reporting/`: charts, reports, dashboards, and visualization output.
- `pairs_trading/api/`: frontend-facing read models. This is where future FastAPI or dashboard endpoints should import from.
- `pairs_trading/backend/`: FastAPI transport layer. Keep it thin; it should call service/read-model modules instead of owning quant logic.
- `pairs_trading/platform/`: shared infrastructure primitives such as SQLite metadata storage for jobs, deployments, and experiments.
- `pairs_trading/apps/`: command-line applications and other runnable entry points.
- `apps/`: future-facing deployment facades for API, worker, and web app boundaries.
- `frontend/`: React TypeScript UI. Keep API calls in `frontend/src/api/` and domain UI in `frontend/src/features/`.

## Frontend Boundary

The frontend should not read raw ledger files directly. Backend routes should use read-model helpers such as:

```python
from pairs_trading.api import build_paper_dashboard_payload

payload = build_paper_dashboard_payload(
    state_dir="artifacts/paper/state",
    batch_summary_path="artifacts/paper/runs/<run_id>/paper_batch_summary.json",
)
```

That payload is intentionally shaped around dashboard concepts:

- `totals`: combined fake-money equity, cash, exposure, PnL, and activity
- `leaderboard`: one row per strategy sleeve
- `strategies`: detailed per-sleeve positions, target weights, orders, diagnostics, and history
- `visuals`: generated dashboard links when a paper batch summary is available

The HTTP backend exposes the same read model at:

```text
GET /api/paper/summary
GET /api/paper/strategies
GET /api/paper/strategies/{strategy_name}
POST /api/paper/run
POST /api/paper/run-job
GET /api/paper/jobs
GET /api/paper/jobs/{job_id}
GET /api/system/metadata
```

Keep the backend route layer thin. New frontend screens should usually be backed by a read model in `pairs_trading/api/`, a service in `pairs_trading/backend/services.py` or a nearby service module, and a route adapter in `pairs_trading/backend/routers/`.

## Preferred Imports

Prefer domain imports like:

```python
from pairs_trading.engines.backtesting import WalkForwardBacktester
from pairs_trading.data.market import CachedParquetProvider
from pairs_trading.reporting.paper import PaperDashboardVisualizer
```
