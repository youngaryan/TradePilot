# SaaS Readiness Workflow

The app now has a local-first SaaS shell around the quant research engine. This is not a fully hosted production SaaS yet, but it gives the product the same core objects a subscription product needs: users, organizations, projects, experiments, paper agents, datasets, API-key metadata, and subscriptions.

## Demo Login

Run the backend and frontend as usual:

```powershell
.\.venv\Scripts\python.exe -m uvicorn pairs_trading.backend.app:app --reload --host 127.0.0.1 --port 8000
```

```powershell
cd frontend
npm.cmd run dev
```

Open:

```text
http://127.0.0.1:5173
```

Use the seeded local demo account:

```text
Email: demo@quantops.local
Password: quantops-demo
```

The backend creates a demo organization, default project, and trial subscription in the SQLite metadata database.

## Workspace Model

The current SaaS data model lives in `artifacts/metadata/app.sqlite3` by default and includes:

- `users`: login identities.
- `auth_sessions`: bearer sessions used by the frontend.
- `organizations`: tenant/workspace boundary.
- `organization_members`: user-to-organization roles.
- `projects`: research containers inside an organization.
- `experiments`: durable backtest records with summary, validation, lineage, readiness, trades, and sentiment metadata.
- `paper_agents`: durable fake-money deployment records with latest payload, warnings, config, and fake cash.
- `datasets`: indexed local data dependencies such as sentiment, price cache, and event cache.
- `api_keys`: masked API-key metadata or environment/vault references.
- `subscriptions`: local billing state and Stripe ids when configured.

## Billing

The frontend Workspace page includes Stripe Checkout and Customer Portal buttons.

In local mode, if Stripe environment variables are not set, the backend returns demo URLs and keeps the subscription in `trialing` mode.

To create real Checkout sessions, set:

```powershell
$env:STRIPE_SECRET_KEY="sk_live_or_test_..."
$env:STRIPE_PRO_PRICE_ID="price_..."
$env:PAIRS_TRADING_APP_BASE_URL="http://127.0.0.1:5173"
```

Optional:

```powershell
$env:STRIPE_SUCCESS_URL="http://127.0.0.1:5173?billing=success"
$env:STRIPE_CANCEL_URL="http://127.0.0.1:5173?billing=cancelled"
```

Production still needs Stripe webhooks to sync subscription status after checkout, renewal, cancellation, failed payment, and plan changes.

## Experiment Detail

Backtest jobs still run through the existing Backtest page. Completed jobs now also create durable SaaS experiment records.

The Workspace page can show:

- Data lineage: symbols, dates, parameters, event files, sector maps, sentiment files, and artifact directory.
- Validation: DSR, PBO, Sharpe, drawdown, turnover, and fold count.
- Readiness score: a practical promotion gate for deciding whether a strategy is ready for fake-money paper testing.
- Sentiment overlay metadata: which sentiment file/providers were attached.
- Artifact trail: saved backtest folder and available report files.

## Paper Agent Detail

Paper runs still execute from the Run Paper page. The SaaS layer now syncs the latest paper state into paper-agent records.

The Workspace page can show:

- Fake equity and cash.
- Latest daily PnL.
- Gross exposure.
- Trade count.
- Target-weight concentration.
- Diagnostics and warnings.

## Production Gap

This implementation deliberately stays local-first. Before charging real users, the app still needs:

- Real hosted identity provider or hardened auth lifecycle.
- Secure secret vaulting instead of local masked metadata.
- Stripe webhooks and entitlement enforcement.
- Postgres/object storage in production.
- Background workers outside the web process.
- Tenant-level object authorization on every paid resource.
- Monitoring, logging, rate limits, backups, and admin tooling.
- Legal review for investment-advice, hypothetical-performance, and marketing language.
