# Product UX, Theme, Refresh, and Telemetry Audit

This document records the product hardening pass for the local-first SaaS prototype.

## Fixes Found

- The frontend was still oriented around engineering terms such as "Command Center" and "Run Paper" without enough user-facing guidance.
- Theme handling was missing. The UI assumed a light background, and many panels, forms, tables, charts, alerts, and code blocks used light-only colors.
- There was no durable 24-hour per-user refresh status. Data sync could happen manually, but the product could not answer "when was my data last refreshed?"
- There was no telemetry layer for product analytics, errors, refresh outcomes, latency, or funnel events.
- Privacy handling for telemetry was undefined.

## UX Improvements

- Navigation language is clearer:
  - `Today`: portfolio health and current state.
  - `Setup`: workspace, onboarding, billing, data refresh, and records.
  - `Paper Trading`: fake-money agent deployment.
  - `News & Sentiment`: inspect what the model read.
  - `Strategy Tests`: validate before trusting a strategy.
  - `Learn`: explanations and architecture.
- The top bar now includes explicit theme and analytics controls.
- The workspace now includes an `Operations` page for data-refresh health and telemetry debugging.
- Error/success messages are written in product language rather than backend-only language.
- Empty states explain what to do next instead of simply saying there is no data.

## Dark Mode

The frontend now supports:

- `Light`
- `Dark`
- `System`

Theme state is stored in local storage and applied through `document.documentElement.dataset.theme`.

Dark mode covers:

- Body/app backgrounds.
- Navigation.
- Cards and panels.
- Forms and selects.
- Tables.
- Badges.
- Alerts.
- Code blocks.
- Empty states.
- Chart axes, labels, and lines.

## Scheduled Refresh Architecture

The app now has a local-first scheduled-refresh coordinator:

- `refresh_statuses`: per-user status, last success, last attempt, next due time, latest run, and last error.
- `refresh_runs`: durable run records with idempotency key, attempt count, max attempts, lock time, summary, status, and error.
- `/api/refresh/status`: inspect current refresh health.
- `/api/refresh/run`: run the authenticated user's refresh if due, or force it for debugging.
- `/api/refresh/tick`: worker/scheduler endpoint to run due users in batches.
- `quant-worker --run-daily-refresh`: CLI worker entry point for cron, Windows Task Scheduler, GitHub Actions, serverless cron, or a real queue worker.

The local FastAPI scheduler is disabled by default and can be enabled with:

```powershell
$env:PAIRS_TRADING_REFRESH_SCHEDULER_ENABLED="true"
```

Recommended production path:

- Use a managed scheduler or worker queue instead of the web process.
- Run `quant-worker --run-daily-refresh --refresh-limit 100`.
- Scale horizontally by sharding users or moving due-user claims into Postgres row locks.

## Refresh Safety

The refresh implementation includes:

- Idempotency key: `daily_refresh:{user_id}:{YYYY-MM-DD}`.
- Duplicate-run protection through a unique database constraint.
- Retry attempts with bounded backoff.
- Failure logging in `refresh_runs` and `refresh_statuses`.
- Per-user next-due tracking.
- Refresh telemetry events for started, succeeded, failed attempt, and deduplicated runs.

## Telemetry Schema

Telemetry is stored in `telemetry_events`.

Core fields:

```json
{
  "id": "event id",
  "organization_id": "tenant id or null",
  "user_id": "internal user id or null",
  "name": "snake_case_event_name",
  "category": "product | engineering | refresh | billing | error | security",
  "properties": {},
  "context": {},
  "consent": "granted | denied | system | unknown",
  "occurred_at_utc": "2026-05-02T12:00:00Z"
}
```

Examples:

```json
{
  "name": "view_opened",
  "category": "product",
  "properties": { "view": "backtests" },
  "context": { "theme_mode": "system", "resolved_theme": "dark" },
  "consent": "granted"
}
```

```json
{
  "name": "data_refresh_succeeded",
  "category": "refresh",
  "properties": {
    "run_id": "refresh id",
    "attempt": 1,
    "dataset_count": 3,
    "experiment_count": 12,
    "paper_agent_count": 4
  },
  "consent": "system"
}
```

## Privacy And Security

- Product telemetry is skipped when analytics consent is `denied`.
- System/security/error telemetry can still be stored for operational safety.
- The backend redacts sensitive-looking keys before storage, including password, secret, token, API key, authorization, cookie, email, phone, and address.
- String payloads are capped and log-injection characters are normalized.
- The frontend offers an analytics on/off control.

## Key Edge Cases

- Duplicate refresh request: returns the existing run instead of creating another one.
- User not due: returns `skipped_not_due`.
- Refresh fails repeatedly: marks run/status as failed and schedules a shorter retry window.
- Telemetry disabled by environment: returns `stored: false`.
- Analytics consent off: skips product telemetry.
- Sensitive telemetry fields: redacted before persistence.
- Dark/system theme changes: system mode listens for OS preference changes.

## Remaining Production Work

- Move metadata from SQLite to Postgres for real multi-tenant SaaS.
- Use row-level locks or queue leases for refresh claims.
- Add external observability export through OpenTelemetry Collector.
- Add retention policies for telemetry and refresh logs.
- Add data-provider-specific refresh plans per organization/user.
- Add legal/compliance review before marketing any trading-performance claims.
