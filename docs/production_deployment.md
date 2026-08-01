# Production deployment

`docker-compose.production.yml` is a hardened application manifest, not an
all-in-one infrastructure stack. It deliberately contains no Postgres, Redis,
or object-storage service. Provision those as durable managed services (or as
separately operated clusters) before deploying TradePilot.

## Release inputs

Build and scan the API and web images in CI, push them to an immutable registry,
and deploy digest references rather than mutable tags:

```text
API_IMAGE_REPOSITORY=registry.example/tradepilot-api
API_IMAGE_DIGEST=sha256:<64 hex characters>
WEB_IMAGE_REPOSITORY=registry.example/tradepilot-web
WEB_IMAGE_DIGEST=sha256:<64 hex characters>
```

Never use `latest`, a floating release tag, or a locally rebuilt image during a
production rollout. Record both digests with the source revision and migration
revision in the release ticket.

The automated build, signing, SBOM, vulnerability, provenance, repository
controls, and release checklist are defined in
[`release_supply_chain.md`](release_supply_chain.md). A semantic-version tag is
for discovery only; production still consumes the recorded digest.

The production manifest requires non-secret routing, provider, Stripe price,
SMTP, and object-storage settings from the deployment environment. It requires
secret *file paths* such as `DATABASE_URL_SECRET_FILE` and
`SESSION_SECRET_FILE`; Compose mounts their contents under `/run/secrets`.
The hardened manifest also requires `SENTRY_DSN_SECRET_FILE` and
`OBSERVABILITY_METRICS_TOKEN_SECRET_FILE`; Sentry initialization is fail-closed
for every application role and the metrics token must contain at least 32
characters.
Create files with the minimum readable permissions supported by the deployment
host, keep them outside the repository, and never place values in Compose,
shell history, image layers, or `.env` files.

Application configuration supports either `NAME=value` or `NAME_FILE=/path`
for sensitive values, but rejects both together. Supported file-backed values
include:

- `DATABASE_URL`, `REDIS_URL`, `SESSION_SECRET`, `CSRF_SECRET`, and
  `MFA_ENCRYPTION_KEY`
- `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY`
- `SMTP_USERNAME` and `SMTP_PASSWORD`
- `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`
- `SENTRY_DSN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `NVIDIA_API_KEY`

Missing, unreadable, or empty files stop startup without printing the secret or
its filesystem path. Rotate secrets through the platform secret mechanism and
replace affected containers; do not edit a mounted secret in place.

Hosted-provider references may instead use `secret-manager:aws:<secret-id>` or
`secret-manager:aws:<secret-id>#<json-key>`. The application uses the AWS SDK
credential chain; do not configure static AWS credentials in TradePilot. Grant
the application role only `secretsmanager:GetSecretValue`, scoped to the exact
named application-secret ARNs it needs. Unqualified `secret-manager:`
references are rejected rather than guessing a cloud provider.

## Network and TLS boundary

Only the web container publishes a host port. Web-to-API traffic uses the
internal backend network; application roles additionally use a non-published
egress network for externally managed dependencies, and migration uses egress
only. Put a managed load balancer or reverse proxy in front of the web port and
terminate TLS there. The ingress must:

- redirect HTTP to HTTPS before requests reach the application;
- preserve the original `Host` and replace (not append untrusted client values
  to) `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Forwarded-Host`, and
  `X-Forwarded-Port`;
- restrict access to the configured web port to the ingress security group;
- use an explicit public hostname included in `TRUSTED_HOSTS`.

Set `API_HEALTH_HOST` to a hostname in `TRUSTED_HOSTS`; it is used only for the
container-local readiness request. Production startup rejects missing or
universal-wildcard trusted hosts. HSTS is mandatory with at least a one-year
max age. Enable `HSTS_PRELOAD` only after every subdomain is permanently HTTPS
and the domain satisfies browser preload requirements.

This Compose topology sets `FORWARDED_ALLOW_IPS=*` so Gunicorn/Uvicorn can
recover the real client address for Redis rate limiting. Nginx passes through
the ingress-supplied client/protocol headers (or uses its direct peer when they
are absent) and never appends an arbitrary client-provided chain. This is safe
here only because the web port is restricted to an ingress that replaces those
headers, the API publishes no port, and the isolated backend network is the
only web-to-API path. Do not copy this setting to a directly exposed API. If
the topology changes, restrict it to the actual proxy CIDRs and ensure the edge
overwrites all forwarding headers.

## Migrations and rollout

The `migrate` service is the single migration owner. It reads only the database
URL secret, runs `alembic upgrade head`, and exits. API and workers start only
after it succeeds. Before rollout:

1. Verify a recent Postgres backup and object-storage recovery point.
2. Review every migration for lock duration, backwards compatibility, and
   downgrade limitations.
3. Pull and verify the configured image digests.
4. Run `python scripts/validate-production-deployment.py`, then
   `docker compose -f docker-compose.production.yml config` in the secured
   deployment environment. The preflight rejects non-SHA256 image digests,
   unsafe public URLs/hosts, and missing secret files without reading or
   printing their values. Inspect the rendered non-secret configuration.
5. Start `migrate`; stop if it fails. Do not bypass the dependency condition.
6. Start API, worker, job-control, and web; wait for health checks.
7. Exercise login, a tenant-scoped read, queue dispatch, and artifact upload.

Use rolling replacement only when the new application is compatible with both
the pre- and post-migration schema. For rollback, restore the previous image
digests. Do not automatically downgrade the database. If a migration is not
backwards compatible, follow its reviewed restore/forward-fix procedure and
place the application in maintenance mode first.

## Filesystem and artifact durability

All runtime containers are non-root, drop Linux capabilities, enable
`no-new-privileges`, and use read-only root filesystems. `/tmp`, `/app/data`,
and `/app/artifacts` are bounded `tmpfs` working areas. They are intentionally
ephemeral and disappear when a container is replaced.

The API and workers also join a non-internal egress network so they can reach
externally operated Postgres, Redis, S3, SMTP, Stripe, telemetry, and approved
LLM endpoints. The isolated backend network remains the only web-to-API path.
Apply host firewall/egress policy or a platform network policy to restrict
outbound destinations; Compose bridge networks do not provide domain-level
egress controls.

Completed research and paper artifacts are durable only after publication to
the configured S3-compatible bucket. Configure bucket versioning, encryption,
retention, lifecycle policies, tenant-prefix access controls, and independent
backups. Size worker memory and tmpfs limits for the largest approved workload;
jobs that exceed those bounds should fail safely rather than consume the host.
Do not substitute a local bind mount as the production artifact system.

## Operations and rollback checks

Monitor API readiness, worker and controller role heartbeats, queue depth,
expired-lease recovery, Postgres saturation, Redis eviction, object-storage
errors, migration duration, and container restart/OOM counts. Keep the previous
known-good API and web digests available until the release observation window
closes. See `docs/production_operations.md` for backup and restore procedures.
