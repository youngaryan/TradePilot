# Secure v1 Production Notes

This project now defaults to a safe local-development mode and fails closed when `APP_ENV=production`.

## Local production-like stack

```bash
cp .env.example .env
docker compose up --build
```

Services:
- API: `http://localhost:8000`
- Frontend: `http://localhost:5173`
- Postgres: `localhost:5432`
- Redis: `localhost:6379`
- MinIO: `http://localhost:9001`

## Production requirements

Set `APP_ENV=production` only after configuring:
- `DATABASE_URL`
- `REDIS_URL`
- `SESSION_SECRET`
- `CSRF_SECRET`
- `CORS_ORIGINS`
- `APP_BASE_URL`
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_PRICE_PRO_MONTHLY`
- `S3_ENDPOINT_URL`
- `S3_BUCKET`
- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`
- `SMTP_HOST`
- `SMTP_PORT`
- `EMAIL_FROM`

Production also requires:
- `ENABLE_DEMO_ACCOUNTS=false`
- `ENABLE_IN_PROCESS_JOBS=false`
- HTTPS cookies through `COOKIE_SECURE=true`

## Security model

Browser auth uses HttpOnly `quantops_session` cookies. Mutating browser requests must include `X-CSRF-Token`, sourced from the non-HttpOnly `quantops_csrf` cookie. Bearer tokens are still accepted for CLI/test compatibility but should not be stored by the frontend.

Artifact-style endpoints require authentication. Normal users cannot access admin routes, and production admin APIs require an MFA verification cookie.

## Current migration boundary

Alembic/Postgres migrations are included for the secure-v1 schema and the backend selects `PostgresMetadataStore` whenever `DATABASE_URL` uses `postgresql://` or `postgresql+psycopg://`. SQLite remains available only for local development and tests.

The API container runs `alembic upgrade head` before starting Gunicorn/Uvicorn workers. Backtest, paper, and sentiment jobs still use local temporary working directories while the job is running, then publish completed artifacts to tenant-scoped S3/MinIO keys such as `organizations/{organization_id}/backtests/{experiment_id}/...`.

## Auth lifecycle and admin MFA

Signup sends an email-verification token through SMTP in production and a local JSON outbox in development. Password reset uses single-use, expiring tokens. Admin MFA uses TOTP and the admin API checks a session-bound HttpOnly MFA cookie in production.

## Quotas and abuse controls

Premium launch endpoints enforce server-side daily quotas before backtest, sentiment, and paper jobs are accepted. Production rate limiting uses Redis when `REDIS_URL` is configured; local development falls back to a single-process limiter.
