# Rate Limit And Abuse Policy

Rate limits protect compute, paid data providers, web crawling targets, and login surfaces. They are enforced server-side and should be treated as part of the entitlement system, not a frontend UX feature.

## Default Policy

Global API limit:
- `RATE_LIMIT_ENABLED=true`
- `RATE_LIMIT_WINDOW_SECONDS=60`
- `RATE_LIMIT_MAX_REQUESTS=120`
- In production, Redis-backed limits must be active through `REDIS_URL`.

Premium compute limits:
- Backtests: 20 per day by default.
- Sentiment jobs: 20 per day by default.
- Paper jobs: 20 per day by default.
- News crawling pages: separately metered so a user cannot bypass sentiment-job limits by requesting huge crawls.

Authentication limits:
- Login and signup endpoints should remain behind the global limiter and deployment-provider bot protection.
- Add CAPTCHA or proof-of-work only after real abuse appears; do not add it to the first user validation funnel unless needed.

## Operational Rules

- Return stable `429` error codes: `rate_limited` for request rates and `quota_exceeded` for paid plan quotas.
- Never let the frontend decide whether a user has quota remaining; the backend must check and record usage atomically.
- Do not exempt admin users from abuse limits in production, except for explicit break-glass maintenance windows.
- Treat local-web scraping and GDELT discovery as high-abuse-risk features and keep approved domains tightly scoped.

## Review Cadence

Review rate-limit logs weekly during beta. Tune quotas from real usage and cost data, not assumptions.
