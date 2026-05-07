# Production Operations Runbook

This runbook is the minimum operating model before a hosted SaaS launch. It assumes Postgres for metadata, Redis/RQ for jobs, and S3-compatible storage for artifacts.

## Backup And Restore

Back up both metadata and artifacts. A database-only backup is not enough because experiment, paper-agent, and sentiment records point to S3/MinIO artifacts.

Postgres backup:

```bash
DATABASE_URL="postgresql+psycopg://..." BACKUP_DIR="backups/postgres" ./scripts/backup_postgres.sh
```

Postgres restore:

```bash
DATABASE_URL="postgresql+psycopg://..." BACKUP_FILE="backups/postgres/quantops_YYYYMMDDTHHMMSSZ.dump" ALLOW_RESTORE=1 ./scripts/restore_postgres.sh
```

Artifact backup:

```bash
S3_ENDPOINT_URL="https://..." S3_BUCKET="quantops-artifacts" S3_ACCESS_KEY_ID="..." S3_SECRET_ACCESS_KEY="..." ./scripts/backup_object_storage.sh
```

Production policy:
- Run Postgres backups daily and before every migration.
- Run object-storage backups daily or enable provider-native bucket versioning and lifecycle retention.
- Restore-test into a temporary environment at least monthly.
- Store backups encrypted outside the primary deployment account.
- Retain daily backups for 30 days and monthly backups for 12 months unless legal requirements differ.

## Monitoring And Alerting

Required telemetry integrations:
- Set `SENTRY_DSN` for backend exceptions.
- Set `OTEL_EXPORTER_OTLP_ENDPOINT` for traces if using OpenTelemetry-compatible infrastructure.
- Ship structured container logs to the deployment platform log sink.

Health checks:
- `GET /api/health` for API liveness.
- `GET /api/admin/system-health` for admin-only dependency health.
- Redis queue depth and failed-job count from RQ.
- Postgres connection and migration revision.
- S3/MinIO bucket write/read probe.
- Stripe webhook delivery and idempotency failures.

Minimum alert thresholds:
- API 5xx rate above 2% for 5 minutes.
- p95 API latency above 2 seconds for 10 minutes.
- RQ queue age above 15 minutes.
- Any job stuck running without heartbeat for more than 30 minutes.
- Stripe webhook failures above 0 in production.
- Refresh job failure rate above 10% in a 24-hour window.
- Postgres storage above 80% or connection usage above 80%.
- Object-storage backup older than 26 hours.

## Incident Process

1. Freeze deploys unless the deploy is the fix.
2. Check `/api/admin/system-health`, logs, RQ failed jobs, Stripe webhook logs, and database connectivity.
3. If tenant data exposure is suspected, disable affected endpoints or put the app behind maintenance mode immediately.
4. Preserve logs and audit records before manual database changes.
5. Write a short incident note with impact, timeline, root cause, fix, and follow-up owners.

## Launch Gate

Do not launch unless:
- `APP_ENV=production` starts cleanly with `ENABLE_DEMO_ACCOUNTS=false` and `ENABLE_IN_PROCESS_JOBS=false`.
- `alembic upgrade head` has run against production Postgres.
- Backups complete and a restore smoke test has passed.
- Sentry/logging/alerts are verified with a synthetic error.
- Stripe webhooks have been tested from the Stripe CLI or dashboard.
- Legal pages, telemetry consent, account export, and account deletion flows are reachable.
