# Worker applications

This directory contains two distinct worker-facing entry points.

## Redis/RQ worker

`apps.worker.rq_worker` is the active external job worker. It consumes the queue
configured by `REDIS_URL` and executes backend job contracts outside the API
process:

```powershell
$env:REDIS_URL="redis://127.0.0.1:6379/0"
.\.venv\Scripts\python.exe -m apps.worker.rq_worker
```

Use `ENABLE_IN_PROCESS_JOBS=false` on the API when validating this path. The
Docker Compose stack enforces external execution for the API and starts the
worker plus the single lease recovery/queued-dispatch controller:

```powershell
docker compose up
```

The stack first runs a one-shot `migrate` service. The API, worker, and
controller start only after that service completes successfully; none of those
long-running services runs migrations itself. Redis is intentionally available
only on the Compose network, and the queue uses RQ's JSON serializer rather
than accepting pickle payloads.

For lightweight local source development without Docker, the default settings
keep in-process jobs enabled; start only the API/uvicorn process and do not run
the external controller. Never run the controller against an API that is
creating in-process jobs, because every durable queued row is treated as
external dispatch intent. The worker uses the same
`.[backend]` dependency profile and container image as the API, so the standard
image supports RQ but intentionally omits the heavyweight FinBERT/Torch stack.

The controller runs `python -m apps.worker.job_control`. It requeues expired
worker leases and repairs the database/Redis crash window by reconciling queued
rows with deterministic per-attempt RQ job IDs. Configure it with
`JOB_LEASE_SECONDS`, `JOB_HEARTBEAT_SECONDS`, `JOB_RECOVERY_POLL_SECONDS`,
`JOB_MAX_ATTEMPTS`, and `JOB_RECOVERY_BATCH_SIZE`.

`JOB_RECOVERY_BATCH_SIZE` also bounds queued reconciliation work per pass. The
controller distributes that budget across job kinds and rotates the first kind
between passes. Transient metadata-store failures use bounded exponential
backoff; container restart policies cover process-level failures.

Non-paper workers compute at least once into deterministic, attempt-scoped
directories or object keys. Before publishing authoritative artifacts,
datasets, experiments, reports, or committee decisions they lock the durable
job row and re-check its live owner in the same database transaction. A stale
attempt can therefore leave only an unreferenced immutable blob; it cannot
replace tenant-visible domain rows. Configure object-storage lifecycle cleanup
for unreferenced `.attempts` objects after the retry and incident-retention window.

Production deployments outside Compose should use the same topology: run
`alembic upgrade head` once as a release step, set `RUN_DB_MIGRATIONS=false`,
then start API, workers, and exactly one controller. Worker/controller startup
requires `ENABLE_IN_PROCESS_JOBS=false`, `REDIS_URL`, and `DATABASE_URL`.

## Operational CLI

`apps/worker/main.py` inspects persisted jobs and runs one-shot maintenance or
sentiment accumulation tasks. It is not the long-running queue consumer.

Inspect local metadata:

```powershell
.\.venv\Scripts\python.exe apps\worker\main.py
```

List paper or backtest jobs:

```powershell
.\.venv\Scripts\python.exe apps\worker\main.py --kind paper
.\.venv\Scripts\python.exe apps\worker\main.py --kind backtest
```

Build a local sentiment dataset:

```powershell
.\.venv\Scripts\python.exe apps\worker\main.py --accumulate-sentiment --symbols AAPL MSFT NVDA --sentiment-provider rss --start 2026-04-01 --end 2026-04-29
```

This writes raw, scored, and daily Parquet data under
`data/sentiment_cache/shadow/`. Rule-based sentiment needs no optional package;
VADER uses `.[sentiment-vader]`; FinBERT requires the much larger `.[sentiment]`
extra and a cached or downloadable model.

Optional NewsAPI supplement:

```powershell
$env:NEWSAPI_API_KEY="your_key"
.\.venv\Scripts\python.exe apps\worker\main.py --accumulate-sentiment --symbols AAPL MSFT NVDA --sentiment-provider rss newsapi
```

Offline sample smoke test:

```powershell
.\.venv\Scripts\python.exe apps\worker\main.py --accumulate-sentiment --symbols AAPL MSFT NVDA --sentiment-provider local --news-file examples/news_headlines.sample.csv --start 2024-01-01 --end 2024-02-10 --sentiment-output-dir artifacts/smoke_tests/sentiment_shadow
```
