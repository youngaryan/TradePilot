# Fullstack Workflows

The project now has a Python backend and a React TypeScript frontend.

For the bigger architecture walkthrough, read [backend_frontend_tutorial.md](backend_frontend_tutorial.md).

## Backend

Install backend dependencies:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements/test-py312.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
```

`requirements/test-py312.lock` pins the Python 3.12 backend/test resolution. The
`test` extra includes NLTK for the unconditional VADER tests without installing
the heavy FinBERT/Torch stack. API-only development can instead use
`requirements/backend-py312.lock` with `.[backend]`.

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
npm.cmd ci
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

- `apps/api/` exposes the API deployment facade.
- `apps/worker/rq_worker.py` runs the active Redis/RQ job worker used when
  `ENABLE_IN_PROCESS_JOBS=false`; `apps/worker/main.py` remains an operational
  inspection and one-shot refresh/sentiment CLI.
- `apps/web/` documents the future frontend app boundary while the React app still lives in `frontend/`.
- `pairs_trading/platform/` owns shared infrastructure primitives including
  local SQLite and production PostgreSQL metadata persistence.
- `pairs_trading/api/` produces frontend-ready read models.
- `pairs_trading/backend/` exposes those read models over HTTP.
- `frontend/src/api/` owns typed fetch clients and TypeScript API types.
- `frontend/src/features/` owns domain screens.
- `frontend/src/components/` owns shared UI primitives.

This keeps quant logic out of the UI and keeps route handlers thin enough to scale into auth, persistence, and live brokerage adapters later.

## Production-like local services

Docker Compose requires a root `.env` file even when values match development
defaults:

```powershell
Copy-Item .env.example .env
docker compose config
docker compose up --build
```

This starts PostgreSQL, Redis, MinIO, the API, the current RQ worker, and the web
application. Set `ENABLE_IN_PROCESS_JOBS=false` to exercise queue-backed jobs.
The standard image intentionally excludes FinBERT; install `.[sentiment]` in a
dedicated ML worker image if transformer-backed local inference is required.

The interactive backtest workflow is documented in [backtest_agent_workbench.md](backtest_agent_workbench.md).
