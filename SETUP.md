# TradePilot local setup

TradePilot is a research-first paper-trading platform. It validates strategies
with walk-forward backtests and overfitting checks, then runs them with simulated
capital. It does not connect to a broker or place real orders.

## Requirements

- Python 3.12 is recommended. The package supports Python 3.11 and newer, but
  the checked-in reproducibility constraints target Python 3.12.
- Node.js 22.22.0 or newer.
- Docker Desktop or another Compose-compatible Docker installation for the
  production-like local stack.

## Local source workflow

From the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -c requirements/test-py312.lock -e ".[backend,test]"
Copy-Item .env.example .env
python -m uvicorn pairs_trading.backend.app:app --reload --host 127.0.0.1 --port 8000
```

The `test` extra includes NLTK because the unconditional Python suite exercises
the VADER implementation. It does not install PyTorch, Transformers, or the
FinBERT model stack.

In a second PowerShell terminal:

```powershell
Set-Location frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`.

The Vite development server proxies `/api` to the backend. Demo accounts are
created when `ENABLE_DEMO_ACCOUNTS=true`:

- Full access: `demo@quantops.local` / `quantops-demo`
- Free tier: `user@quantops.local` / `quantops-user`

## Docker Compose workflow

Compose reads `.env` unconditionally, so create it before validating or starting
the stack:

```powershell
Copy-Item .env.example .env
docker compose config
docker compose up --build
```

The stack starts PostgreSQL, Redis, MinIO, the FastAPI service, an RQ worker, and
the web application. `ENABLE_IN_PROCESS_JOBS=true` is convenient for a single
local API process. Set it to `false` when exercising Redis/RQ dispatch. Production
must use the external worker path rather than in-process thread pools.

## Sentiment installation profiles

- Rule-based sentiment is included in the core package.
- VADER only: `python -m pip install -e ".[sentiment-vader]"`
- Full local FinBERT/NLP stack: `python -m pip install -e ".[sentiment]"`

The full sentiment extra is intentionally excluded from the standard API/RQ
image because Torch, Transformers, spaCy, and model artifacts are large. Build a
dedicated ML worker image if production jobs must execute FinBERT. FinBERT also
requires an available model snapshot or permission to download one. VADER may
download the `vader_lexicon` NLTK resource on first use; for an offline runtime,
preload it during environment provisioning:

```powershell
python -m nltk.downloader vader_lexicon
```

## Verification

```powershell
python -m pip check
python -m pytest
```

## Updating Python constraints

The lock files are generated artifacts from `pyproject.toml`, not hand-maintained
dependency lists. After an intentional dependency update, install `uv`, regenerate
both profiles, review the version changes, and repeat the clean-environment tests:

```powershell
uv pip compile pyproject.toml --extra backend --python-version 3.12 --universal --no-annotate --output-file requirements/backend-py312.lock
uv pip compile pyproject.toml --extra backend --extra test --python-version 3.12 --universal --no-annotate --output-file requirements/test-py312.lock
```

These files make dependency resolution repeatable across supported platforms.
They intentionally do not contain package hashes, so package integrity still
depends on the configured package index and TLS. Add reviewed hashes or an
internal package mirror before treating the public index as a hermetic supply
chain.

Market-price and remote research providers require network access and, where
applicable, server-side credentials. Nothing produced by this application is
financial advice.
