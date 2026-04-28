# Fullstack Workflows

The project now has a Python backend and a React TypeScript frontend.

For the bigger architecture walkthrough, read [backend_frontend_tutorial.md](backend_frontend_tutorial.md).

## Backend

Install backend dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[backend]"
```

Run the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn pairs_trading.backend.app:app --reload --host 127.0.0.1 --port 8000
```

Useful endpoints:

```text
GET  http://127.0.0.1:8000/api/health
GET  http://127.0.0.1:8000/api/paper/summary
GET  http://127.0.0.1:8000/api/paper/strategies
GET  http://127.0.0.1:8000/api/paper/strategies/{strategy_name}
POST http://127.0.0.1:8000/api/paper/run
POST http://127.0.0.1:8000/api/paper/run-job
GET  http://127.0.0.1:8000/api/paper/jobs
GET  http://127.0.0.1:8000/api/paper/jobs/{job_id}
GET  http://127.0.0.1:8000/api/strategies/catalog
GET  http://127.0.0.1:8000/api/strategies/catalog/{strategy_id}
GET  http://127.0.0.1:8000/api/backtests/templates
POST http://127.0.0.1:8000/api/backtests/run
GET  http://127.0.0.1:8000/api/backtests/jobs
GET  http://127.0.0.1:8000/api/backtests/jobs/{job_id}
GET  http://127.0.0.1:8000/api/system/metadata
```

FastAPI also serves interactive OpenAPI docs:

```text
http://127.0.0.1:8000/docs
```

## Frontend

Install frontend dependencies:

```powershell
cd frontend
npm.cmd install
```

Run the frontend:

```powershell
npm.cmd run dev
```

Open:

```text
http://127.0.0.1:5173
```

## Website Tutorial

The dashboard has eight pages:
- `Overview`: total fake capital, daily PnL, leaderboard, and a selected strategy chart.
- `Live Trading`: configure multiple fake-money agents, deploy paper jobs, watch progress, inspect stage messages, and review live equity/order charts.
- `Strategies`: one-strategy drilldown with target weights, positions, and equity history.
- `Orders`: latest simulated broker orders with notional, commission, and execution price.
- `Diagnostics`: raw model metadata for debugging.
- `Backtests`: launch strategy agents, poll job status, and inspect validation outputs.
- `Catalog`: explanations, caveats, CLI commands, and paper config snippets for every strategy.
- `Tutorials`: step-by-step usage guide for the backend website, frontend website, and paper workflow.

Use the FastAPI docs website at `http://127.0.0.1:8000/docs` when you want to test endpoints directly. Use the React dashboard at `http://127.0.0.1:5173` for day-to-day paper-trading observation.

The Vite dev server proxies `/api` to `http://127.0.0.1:8000`. For another backend URL, set:

```powershell
$env:VITE_API_BASE_URL="http://127.0.0.1:8000"
```

## Design Boundary

- `apps/api/` exposes a future-facing API deployment facade.
- `apps/worker/` exposes a future-facing worker facade for durable job processing.
- `apps/web/` documents the future frontend app boundary while the React app still lives in `frontend/`.
- `pairs_trading/platform/` owns shared infrastructure primitives such as SQLite metadata persistence.
- `pairs_trading/api/` produces frontend-ready read models.
- `pairs_trading/backend/` exposes those read models over HTTP.
- `frontend/src/api/` owns typed fetch clients and TypeScript API types.
- `frontend/src/features/` owns domain screens.
- `frontend/src/components/` owns shared UI primitives.

This keeps quant logic out of the UI and keeps route handlers thin enough to scale into auth, persistence, and live brokerage adapters later.

The interactive backtest workflow is documented in [backtest_agent_workbench.md](backtest_agent_workbench.md).
