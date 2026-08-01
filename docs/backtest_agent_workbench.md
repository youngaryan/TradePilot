# Backtest Agent Workbench

The React dashboard now includes a `Backtests` page for launching research agents from the browser.

## What It Does

- Lets you choose a strategy or agent template.
- Lets you edit symbols, dates, validation settings, and strategy parameters without editing raw JSON.
- Submits the run to `POST /api/backtests/run`.
- Receives a job id immediately so the browser does not block.
- Persists job metadata under `artifacts/backtests/jobs/`.
- Polls `GET /api/backtests/jobs/{job_id}` until the run is completed, failed, or interrupted.
- Shows progress, stage messages, summary metrics, validation metrics, an equity/drawdown chart, promotion checks, visuals, and artifact paths.

## Start The Websites

Backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn pairs_trading.backend.app:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```powershell
cd frontend
npm.cmd run dev
```

Open:

```text
http://127.0.0.1:5173
```

## Backend Endpoints

```text
GET  /api/backtests/templates
POST /api/backtests/run
GET  /api/backtests/jobs
GET  /api/backtests/jobs/{job_id}
```

The current implementation uses an in-process job runner with persisted job metadata. That is good for local research and early experimentation because it is simple and easy to debug.

For production, move the same job contract to a durable queue such as Celery/RQ/Redis so jobs survive backend restarts, run on separate workers, and can be monitored independently.

## UI Workflow

1. Choose an agent template.
2. Review the selected strategy explanation.
3. Edit symbols. Use spaces or commas, for example `SPY QQQ TLT GLD`.
4. Set the start and end dates.
5. Set walk-forward controls.
6. Edit strategy-specific parameters in the parameter editor.
7. Launch the agent.
8. Watch progress and stage messages.
9. Review the equity curve and drawdown chart.
10. Review the decision panel before promoting anything to paper trading.

## Important Controls

- `Train bars`: how much history is used to fit each fold.
- `Test bars`: how much out-of-sample data is used for each fold.
- `Purge bars`: safety gap between train and test windows to reduce leakage.
- `PBO partitions`: partitions used to estimate probability of backtest overfitting.
- `Sector map path`: required when using a custom stat-arb universe.
- `Event file`: required for local EDGAR/event-driven tests unless SEC company facts are enabled.
- `Agent parameters`: strategy-specific knobs such as lookbacks, thresholds, volatility windows, and band widths.

## Result Panels

- `Agent Status`: job id, stage, progress, timestamps, and error messages.
- `Research Decision`: high-level promotion gate.
- `Metrics`: annual return, Sharpe, DSR, PBO, drawdown, and turnover.
- `Equity And Drawdown`: compounded net return path and drawdown path.
- `Validation Checklist`: individual pass/fail checks.
- `Artifacts`: saved output paths and raw summary/validation payload.

## Decision Logic

The decision panel checks:
- Sharpe above 1.0.
- DSR above 0.60.
- PBO below 0.30.
- Max drawdown better than -25%.
- Average turnover below 150%.
- At least three walk-forward folds.

Verdicts:
- `paper_candidate`: strong enough to consider shadow paper trading.
- `research_more`: promising, but needs more tests or parameter stability checks.
- `reject_or_redesign`: do not promote.

Passing the decision gate does not mean the strategy is ready for real money. It means it is ready for fake-money observation.

## Recommended Workflow

1. Use `Catalog` to understand the strategy.
2. Use `Backtests` to launch a research run.
3. Inspect the decision, chart, validation checks, and artifact paths.
4. Re-run nearby parameters to test stability.
5. Only promote promising strategies into `examples/paper_deployment.sample.json` or the `Live Trading` deployment builder.
6. Use `Live Trading` -> `Deploy Agents` for fake-money shadow trading after the backtest passes basic checks.

## Validation Checklist

Before trusting a result:
- Check `DSR`, not just raw Sharpe.
- Check `PBO` for backtest overfitting risk.
- Check turnover and max drawdown.
- Compare against `buy_and_hold`.
- Re-run with nearby parameters to see whether the result is stable.
- Shadow paper trade before any real broker integration.
