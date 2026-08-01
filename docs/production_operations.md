# Production Operations Runbook

This runbook is the minimum operating model before a hosted SaaS launch. It assumes Postgres for metadata, Redis/RQ for jobs, and S3-compatible storage for artifacts.

## Backup And Restore

Back up both metadata and artifacts. A database-only backup is not enough because experiment, paper-agent, and sentiment records point to S3/MinIO artifacts.

Postgres backup:

```bash
DATABASE_URL="postgresql+psycopg://..." BACKUP_DIR="backups/postgres" ./scripts/backup_postgres.sh
```

The result is a custom-format archive plus mandatory `.manifest.json` and
`.sha256` sidecars. The checksum covers both the archive and its manifest. The
script validates that `pg_restore` can read the archive and records the exact
database and Alembic revision. Upload all three files together to encrypted,
access-controlled storage in a different failure domain.

Postgres restore is destructive and requires both an exact database-name match
and an exact typed confirmation. Restore into a newly created isolated database
for routine drills; never point a drill at production:

```bash
DATABASE_URL="postgresql+psycopg://restore_user:...@db/isolated_restore" \
BACKUP_FILE="backups/postgres/quantops_YYYYMMDDTHHMMSSZ.dump" \
RESTORE_TARGET_DATABASE="isolated_restore" \
RESTORE_CONFIRMATION="RESTORE:isolated_restore" \
ALLOW_RESTORE=1 \
./scripts/restore_postgres.sh
```

The restore verifies the archive and both sidecar hashes before connecting to
the target. It verifies `current_database()`, creates and validates a rollback
archive of the pre-restore target, restores in one transaction, then checks the
recorded Alembic revision and sentinel tables. It intentionally does not run
`alembic upgrade`; migration is a separate reviewed change. Preserve the JSON
pre/post reports and rollback archive. A failed post-restore sentinel check is
an incident: keep traffic disabled and recover with the retained rollback dump
or a forward repair after peer review.

Artifact backup:

```bash
S3_ENDPOINT_URL="https://..." S3_BUCKET="quantops-artifacts" S3_ACCESS_KEY_ID="..." S3_SECRET_ACCESS_KEY="..." ./scripts/backup_object_storage.sh
```

The completed directory contains `objects/`, a per-object SHA-256 inventory, a
manifest, and `checksums.sha256`. Use short-lived, read-only source credentials
for backup. Restore with distinct, short-lived write credentials:

```bash
OBJECT_BACKUP_DIR="backups/object-storage/quantops-artifacts_YYYYMMDDTHHMMSSZ" \
S3_ENDPOINT_URL="https://..." \
S3_BUCKET="quantops-artifacts-restore" \
S3_ACCESS_KEY_ID="..." \
S3_SECRET_ACCESS_KEY="..." \
ALLOW_OBJECT_RESTORE=1 \
RESTORE_CONFIRMATION="RESTORE-BUCKET:quantops-artifacts-restore" \
./scripts/restore_object_storage.sh
```

Restore validates the manifest, inventory, every local object, and the absence
of undeclared local files before uploading, then streams every restored object
back and verifies its digest. Remote-only objects are preserved by default.
Deleting them additionally requires `OBJECT_RESTORE_DELETE_EXTRA=true` and
`DELETE_EXTRA_CONFIRMATION="DELETE-EXTRA:<bucket>"`. Bucket version history is
not captured by this mirror; retain provider-native versioning separately.

Production policy:
- Run Postgres backups daily and before every migration.
- Run object-storage backups daily or enable provider-native bucket versioning and lifecycle retention.
- Restore-test into a temporary environment at least monthly.
- Store backups encrypted outside the primary deployment account.
- Retain daily backups for 30 days and monthly backups for 12 months unless legal requirements differ.
- Use separate least-privilege backup and restore identities, rotate them after
  every drill, and never reuse application runtime credentials.
- Define and approve the actual RPO/RTO, retention, legal-hold, encryption-key,
  and data-residency policies before launch; the example schedule is not a
  business-approved disaster-recovery policy.

## Monitoring And Alerting

Required telemetry integrations:
- Set `SENTRY_DSN` for backend exceptions.
- Set `OTEL_EXPORTER_OTLP_ENDPOINT` for traces if using OpenTelemetry-compatible infrastructure.
- Ship structured container logs to the deployment platform log sink.
- Scrape the API container's backend-only `GET /internal/metrics` endpoint with
  `Authorization: Bearer <OBSERVABILITY_METRICS_TOKEN>`. Nginx only proxies
  `/api/`, so this endpoint is intentionally unreachable from the public web
  service. Store the token in `OBSERVABILITY_METRICS_TOKEN_FILE`.
- Scrape the worker and job-control containers on ports 9101 and 9102 at the
  same path and with the same bearer token. These ports exist only on the
  internal backend network and are not Compose host ports.
- Load [prometheus_alerts.yml](prometheus_alerts.yml) into the alert manager and
  replace every `OWNER_*` placeholder with an actual escalation route.

The production Compose config enables the Prometheus Python multiprocess
directory so its two Gunicorn API workers are merged correctly. Other deployment
platforms must set and lifecycle-manage `PROMETHEUS_MULTIPROC_DIR` or scrape each
API process separately. Durable queue/backlog/readiness gauges are recomputed
from shared state. Aggregate every API replica in Prometheus or OpenTelemetry;
never interpret one container as the global total.

Health checks:
- `GET /api/health` for API liveness.
- `GET /api/admin/system-health` for admin-only dependency health.
- Redis queue depth and failed-job count from RQ.
- Postgres connection and migration revision.
- S3/MinIO bucket write/read probe.
- Stripe webhook delivery and idempotency failures.

Market-research provider checks:
- Production configuration must use `PAIRS_TRADING_MARKET_RESEARCH_DATA_PROVIDER=cached_yahoo`.
- `PAIRS_TRADING_MARKET_RESEARCH_ALLOW_DEMO_FALLBACK` must be `false`.
- Treat `ProviderUnavailable` as a retryable provider outage; do not manually replace failed production jobs with demo output.
- Monitor report provenance and degraded components. Cached Yahoo currently supplies prices, while direct news and fundamentals remain explicitly unconfigured.

Minimum alert thresholds:
- API 5xx rate above 2% for 5 minutes.
- p95 API latency above 2 seconds for 10 minutes.
- RQ queue age above 15 minutes.
- Any job stuck running without heartbeat for more than 30 minutes.
- Stripe webhook failures above 0 in production.
- Refresh job failure rate above 10% in a 24-hour window.
- Postgres storage above 80% or connection usage above 80%.
- Object-storage backup older than 26 hours.

### API errors or latency

1. Correlate the alert window with `event=api_request_failed`, the templated
   `route`, and `correlation_id`. Do not search by request bodies or user email.
2. Compare dependency readiness and queue gauges. If one route is isolated,
   roll back its release or disable that feature; otherwise inspect database and
   Redis saturation before scaling API replicas.
3. Confirm p95 recovery for ten minutes before closing. Owner: `OWNER_PLATFORM`.

### Queue backlog, failed, or stuck jobs

1. Inspect backlog and oldest age by the bounded `kind` label, then check worker
   heartbeat and `event=job_execution_failed` logs using the logged job ID.
2. Do not manually mark jobs completed. Restore the dependency or worker, allow
   the controller to recover expired leases, and verify redispatch metrics.
3. Escalate repeated deterministic failures to `OWNER_JOBS`; preserve the job's
   durable error and trace before retrying.

### Worker or controller heartbeat loss

Check container health, Redis reachability, OOM/restart events, and the role's
last structured log. Restart one instance only after confirming its lease has
expired. Verify `tradepilot_role_heartbeat_healthy` remains 1 for three minutes.
Owner: `OWNER_PLATFORM`.

### Database, Redis, or object-storage readiness

Use `/api/health/ready` inside the backend network and inspect the named
`tradepilot_dependency_ready` component. Fail over or restore that dependency;
do not weaken readiness requirements. Exercise an S3 read/write probe and a
database transaction before returning traffic. If
`tradepilot_metrics_collection_success` is zero, repair collector access first;
dependency gauges deliberately fail closed when readiness collection fails.
Owner: `OWNER_PLATFORM`.

### Stripe webhook failures

Alert from the centralized log/Sentry event for any production webhook failure
within five minutes. Compare Stripe's delivery ID with the persisted idempotency
record, retry from Stripe only after resolving signature/availability failures,
and never replay an altered payload. Owner: `OWNER_BILLING`.

## Incident Process

1. Freeze deploys unless the deploy is the fix.
2. Check `/api/admin/system-health`, logs, RQ failed jobs, Stripe webhook logs, and database connectivity.
3. If tenant data exposure is suspected, disable affected endpoints or put the app behind maintenance mode immediately.
4. Preserve logs and audit records before manual database changes.
5. Write a short incident note with impact, timeline, root cause, fix, and follow-up owners.

## Launch Gate

Do not launch unless:
- `APP_ENV=production` starts cleanly with `ENABLE_DEMO_ACCOUNTS=false` and `ENABLE_IN_PROCESS_JOBS=false`.
- Production startup rejects demo market data and demo fallback, and a provider-outage drill fails without creating synthetic report output.
- `alembic upgrade head` has run against production Postgres.
- Backups complete and a restore smoke test has passed.
- The release evidence contains both image digests, SPDX SBOMs, passing Trivy
  reports, GitHub provenance attestations, and valid keyless cosign signatures.
- Sentry/logging/alerts are verified with a synthetic error.
- Stripe webhooks have been tested from the Stripe CLI or dashboard.
- Legal pages, telemetry consent, account export, and account deletion flows are reachable.
