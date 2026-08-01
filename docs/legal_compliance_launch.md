# Legal And Compliance Launch Notes

QuantOps is currently a research and paper-trading product. Keep that boundary visible in product copy, pricing, onboarding, and support responses.

## Required Pages

The frontend includes:
- `/privacy`
- `/terms`
- `/risk-disclaimer`
- `/compliance`

Before launch, have counsel review these pages and replace the current operational wording with jurisdiction-specific legal text.

## Product Claim Rules

Allowed:
- "Backtest a strategy idea."
- "Run fake-money paper agents."
- "Inspect validation, sentiment, and artifact lineage."
- "This result is not financial advice."

Not allowed without legal review:
- "Guaranteed profit."
- "Beat the market."
- "Recommended buy/sell signals."
- "Autonomous trading for real capital."
- "Verified performance" unless independently audited and methodology is disclosed.

## Data Provider Compliance

Free RSS, scraped pages, NewsAPI, Alpha Vantage, Benzinga, SEC EDGAR, and GDELT each have separate terms. For commercial SaaS:
- Confirm redistribution rights before showing article text or summaries to paying users.
- Keep source attribution and links where required.
- Cache only when allowed.
- Respect robots.txt and rate limits.
- Store provider credentials in deployment secrets, not tenant-visible metadata.

## Privacy Operations

Required user rights workflows:
- Account export from the Account page.
- Account deletion from the Account page.
- Telemetry consent UI.
- Redaction of API keys, passwords, tokens, and emails from telemetry.

Operational requirements:
- Define retention windows for telemetry and job artifacts.
- Document subprocessors before taking paid users.
- Keep audit logs for admin changes, billing overrides, quota changes, and destructive actions.
