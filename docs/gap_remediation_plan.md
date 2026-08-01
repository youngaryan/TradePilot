# TradePilot Gap Remediation Plan

Status: implementation-ready  
Baseline reviewed: 2026-08-01  
Scope: strategy builder, market research evidence, durable job dispatch, secret resolution, admin UI coverage, frontend dead code, marketplace persistence, and stale readiness documentation.

## 1. Purpose

This document is the execution plan for closing the verified product and infrastructure gaps without weakening TradePilot's existing safety boundaries. Each phase has explicit prerequisites, implementation steps, tests, rollout controls, and an exit gate. Complete phases in order unless a phase explicitly says it may run independently.

The plan assumes the current architecture remains authoritative:

- external providers belong in `pairs_trading/data/`;
- reusable read models belong in `pairs_trading/api/`;
- orchestration and application services belong in `pairs_trading/backend/`;
- FastAPI routers stay thin;
- durable metadata changes are implemented in both Alembic/Postgres migrations and the SQLite compatibility store in `pairs_trading/platform/persistence.py`;
- frontend API calls stay in `frontend/src/api/`, and screen logic stays in `frontend/src/features/`.

## 2. Confirmed baseline and corrections

The implementation agent must work from these facts:

1. `StrategyBuilderService.chat()` currently uses deterministic parsing. It does not call an LLM. Existing AI labels are inaccurate.
2. Marketplace publishing, strategy subscriptions, and creator accounting have no backend persistence. SaaS billing subscriptions are unrelated and must not be reused as marketplace subscriptions.
3. Local market-research defaults are deliberately `demo` data and a `mock` LLM. Production startup already rejects demo data, demo fallback, and non-hosted LLM modes.
4. The market-research collector lacks directly wired news and normalized fundamentals inputs. News adapters already exist in `pairs_trading/data/news.py`; do not build duplicate adapters.
5. SEC submissions and Company Facts support already exists in the financial-events path. Extract and reuse its fetch/cache foundation instead of calling private methods or implementing a second SEC client.
6. API job submission currently swallows the first Redis/RQ enqueue exception, but `apps/worker/job_control.py` already retries durable queued rows. Preserve this recovery design and add visibility; do not add a competing retry loop in API processes.
7. `secret-manager:` is not implemented, but direct environment variables and `*_FILE` mounted secrets are implemented and used by the production Compose topology.
8. The audit log, system-health, and quota admin endpoints exist but have no dedicated frontend consumers.
9. `StrategyLibrary.tsx`, `RiskMonitor.tsx`, and six exported frontend API helpers have no call sites. The helpers mostly point to real backend endpoints, so this is a frontend ownership/cleanup decision rather than evidence that the endpoints are missing.
10. `apps/web/` is an intentional future boundary. Paper-only trading and the absence of OHLCV-dependent partial fills are intentional non-goals for this plan.
11. Parts of `docs/saas_readiness_workflow.md` and `SaaSWorkspace.tsx` are stale: Stripe webhooks, external workers, Postgres/object storage configuration, and observability now exist.

## 3. Decisions and invariants

These rules apply to every phase:

### 3.1 Safety

- An LLM may produce only a candidate `StrategySpecModel`. It must never produce or execute Python, SQL, shell commands, URLs to fetch, or arbitrary expressions.
- The existing allowlisted indicator/rule validation, compatibility checks, dry run, explicit user approval, and admin disable path remain mandatory.
- Production research must never silently substitute demo prices, synthetic headlines, placeholder fundamentals, or a mock LLM.
- Provider failures must be represented by structured degraded-component metadata and safe user-facing errors. Never invent evidence to fill missing data.
- Provider credentials are server-side only. New request schemas must not accept API keys.
- Logs, audit records, metrics, error payloads, and persisted jobs must not contain provider secrets, raw authorization headers, or secret-manager values.
- Marketplace work remains fake-money research. Real-money trading, revenue sharing, and performance fees are prohibited until a separate legal and regulatory approval gate is completed.

### 3.2 Tenancy and authorization

- Every non-public marketplace mutation and every admin mutation requires the existing authentication, CSRF, role, and organization-scoping dependencies.
- A tenant must never obtain another tenant's private strategy record, draft, audit detail, subscription record, paper result, or unpublished listing.
- Public marketplace responses use dedicated safe read models. Do not serialize database rows directly.
- Published strategy versions are immutable. Subscriber behavior must not change when a publisher edits a draft or creates a later version.

### 3.3 Persistence

- Additive schema migrations ship before code that depends on them.
- Postgres/Alembic and local SQLite schemas must remain behaviorally equivalent.
- Persist timestamps as UTC using the repository's existing ISO format.
- Use integer basis points for percentages and integer micro-units for simulated credits. Do not use binary floating point for accounting.
- Accounting and subscription mutations require stable idempotency keys and database uniqueness constraints.

### 3.4 Compatibility and rollout

- Existing API fields remain backward compatible during the rollout. New response fields are additive until frontend adoption is complete.
- New externally visible features are server-controlled through capabilities included in the authenticated workspace payload. The frontend must not infer availability from local state.
- Database migrations, backend deployment, and frontend enablement are separate rollout steps.
- Every phase must leave the full backend test suite, frontend tests, type checking, and production configuration validation green.

## 4. Delivery sequence

The required sequence is:

1. Phase 0 — baseline, contracts, and truthful capability reporting.
2. Phase 1 — durable dispatch visibility and operations.
3. Phase 2 — secret resolver architecture and first concrete adapter.
4. Phase 3 — genuine structured-LLM strategy builder.
5. Phase 4 — real news and normalized fundamentals for market research.
6. Phase 5 — admin operations UI and frontend cleanup.
7. Phase 6 — marketplace sharing and subscriptions without accounting.
8. Phase 7 — simulated creator-credit ledger.
9. Phase 8 — integration hardening, documentation, and release gates.

Phases 1 and 2 may be implemented in parallel by separate contributors after Phase 0, but they must merge before Phases 3 and 4. Phases 6 and 7 must remain sequential.

## 5. Phase 0 — Baseline, contracts, and truthful capabilities

### Objective

Stop overstating deterministic behavior, establish server-owned capability flags, and create a reproducible baseline before functional changes.

### Implementation steps

1. Add a `capabilities` object to the authenticated workspace payload returned by `SaaSService.workspace_payload()` and represented by `WorkspacePayload` in `frontend/src/api/types.ts`.
2. Define these initial boolean/string capabilities from `BackendSettings`:
   - `strategy_builder_mode`: `rules` or `llm`;
   - `market_research_data_mode`: effective configured provider name;
   - `marketplace_enabled`: false by default;
   - `marketplace_creator_credits_enabled`: false by default;
   - `live_broker_trading_enabled`: always false in this plan.
3. Add validated settings and environment parsing for the capability switches. Invalid enum values must fail startup with a safe configuration error.
4. In `ApolloDashboard.tsx`, derive builder wording from `strategy_builder_mode`:
   - rules mode: `Design with rules`, `Parsing...`, and `rule-generated`;
   - LLM mode: `Design with AI`, `Thinking...`, and `AI-assisted`.
5. Add `generation_mode`, `provider`, `model`, and `prompt_version` fields to the strategy-builder chat response. In rules mode, use `generation_mode=rules`, `provider=deterministic`, and omit/null the model.
6. Replace the unconditional `(AI)` catalog suffix and `AI generated` difficulty string with mode-aware metadata persisted at strategy approval time. Existing records without generation metadata must display as `Rule-generated` rather than being guessed as AI.
7. Do not persist full chat messages in general audit metadata. Replace message bodies with message count, roles, bounded character counts, generation mode, prompt version, validation outcome, and a one-way content hash if correlation is required. Keep the candidate spec because it is the reviewed business artifact, but do not persist provider raw responses.
8. Correct stale statements in:
   - `docs/saas_readiness_workflow.md`;
   - `docs/backend_frontend_tutorial.md` where it says a distributed queue is still absent;
   - `frontend/src/features/SaaSWorkspace.tsx`.
9. Preserve the accurate statements that the app is paper-only, has no live broker integration, and requires legal review before live-money use.

### Tests

- Backend tests for capability defaults, environment overrides, and invalid configuration.
- Strategy-builder tests for rules-mode response metadata and audit redaction.
- Frontend tests for rules-mode and LLM-mode labels.
- Contract test ensuring the workspace payload and TypeScript type contain the same capability fields.
- Regression test that existing strategies without generation metadata are not labeled AI-generated.

### Exit gate

- No deterministic code path is labeled AI.
- The frontend obtains feature availability from the backend workspace payload.
- Stale readiness claims are corrected without claiming live-trading readiness.

## 6. Phase 1 — Durable job dispatch visibility

### Objective

Keep the existing durable reconciliation design while making initial enqueue failures observable and understandable.

### Implementation steps

1. Create one shared helper in the backend job-dispatch layer for initial dispatch. It accepts settings, kind, job ID, metadata store, and the enqueue function. Replace the four duplicated `try/except/pass` blocks for paper, backtest, sentiment, and market research.
2. On successful initial enqueue:
   - leave status `queued` until a worker claims it;
   - set stage/message to `queued`/accepted by Redis-RQ;
   - persist sanitized `dispatch_state=accepted`, `dispatch_attempted_at_utc`, and returned RQ job ID where available.
3. On initial enqueue exception:
   - do not mark the job failed, because the controller owns retry;
   - persist status `queued`, stage `dispatch_pending`, and a safe message explaining that durable reconciliation will retry;
   - persist only a bounded error classification such as `redis_unavailable` or `dispatch_error`, never `str(exception)`;
   - emit a sanitized structured warning through the existing observability helpers;
   - increment a low-cardinality dispatch-failure metric labeled only by job kind.
4. Update `reconcile_queued_jobs()` so successful redispatch changes `dispatch_state` to `accepted`. A failed pass leaves the job retryable and increments the existing controller error metrics.
5. Keep deterministic RQ IDs and the controller as the single recovery owner. Do not retry in HTTP request threads and do not start an API-local background timer.
6. Extend system health/admin health output with:
   - controller heartbeat status and age;
   - queued count by bounded job kind;
   - oldest queued age;
   - count/staleness of `dispatch_pending` jobs.
7. Update frontend job status rendering to distinguish `dispatch_pending`, `queued`, `running`, terminal failure, and completed states.
8. Add an alert threshold for stale dispatch-pending jobs to `docs/prometheus_alerts.yml` or the repository's current Prometheus rules file, using bounded labels.

### Tests

- Parameterized unit tests for all four job kinds on enqueue success and failure.
- Assert the raw Redis exception and credentials never appear in job JSON, logs, metrics, or API responses.
- Controller test proving a `dispatch_pending` row is later enqueued and remains idempotent across repeated reconciliation passes.
- Race test where a worker completes before the initial enqueue call returns.
- Health/readiness tests for missing controller heartbeat and stale queue age.
- Existing durable visibility, fencing, claims, Redis transport, and observability tests must remain green.

### Rollback

The new payload fields are additive and live in existing job payload JSON. No schema migration is required. The old frontend can ignore them. Roll back application code without data conversion.

### Exit gate

- No initial enqueue exception is silently discarded.
- A transient Redis outage remains retryable by exactly one controller path.
- Operators and users can distinguish pending dispatch from accepted queue work.

## 7. Phase 2 — Secret resolver architecture

### Objective

Replace the unimplemented generic secret-manager branch with a testable resolver registry while retaining environment and mounted-file support.

### Supported first implementation

Implement AWS Secrets Manager first because `boto3` is already part of the backend dependency profile. Keep the interface provider-neutral so Azure or GCP adapters can be added without changing consumers.

Use explicit references:

- `env:OPENAI_API_KEY` — resolved through `secret_env_value()`, including `OPENAI_API_KEY_FILE`;
- `secret-manager:aws:<secret-id>` — entire AWS SecretString value;
- `secret-manager:aws:<secret-id>#<json-key>` — one key from a JSON SecretString.

Unqualified `secret-manager:<value>` must fail startup with an actionable configuration error. Do not guess a cloud provider.

### Implementation steps

1. Define a single `SecretResolver` protocol in `pairs_trading/backend/secrets.py` and remove/consolidate the duplicate protocol in `llm_config.py`.
2. Implement:
   - `EnvironmentSecretResolver` using the existing direct/`*_FILE` behavior;
   - `AwsSecretsManagerResolver`, with an injected client for tests;
   - `CompositeSecretResolver`, which routes by explicit scheme;
   - `SecretProvider` as a backwards-compatible façade if existing callers require it.
3. Use the AWS SDK credential chain/IAM role. Do not add static AWS credentials to application settings.
4. Configure bounded SDK connection/read timeouts and retries. Convert SDK failures into safe resolver exceptions that contain the reference scheme but not the secret ID if it may be sensitive.
5. Support `SecretString` and base64-decoded `SecretBinary`. For `#json-key`, require a JSON object and an existing scalar key.
6. Add a small in-process TTL cache keyed by the full reference. Never expose cache contents, and provide a cache-clear method for tests/controlled rotation.
7. Resolve required production secrets during startup/preflight so bad references fail before accepting traffic. Optional integrations may remain lazily resolved but must expose unavailable status safely.
8. Route all hosted LLM API-key resolution through the consolidated resolver. Do not regress mounted-file secrets in production Compose.
9. Document IAM permissions narrowly: `secretsmanager:GetSecretValue` only for named application secrets.

### Tests

- Environment direct value, mounted file, mutual-exclusion, and missing value tests.
- AWS SecretString, SecretBinary, JSON-key, missing-key, malformed JSON, timeout, access-denied, and cache-expiry tests using an injected fake client.
- Production startup tests for invalid/unqualified schemes.
- Redaction tests proving secret values and secret payloads never reach logs or exception messages.
- Existing LLM provider preflight tests must continue to pass with fake resolvers.

### Exit gate

- No supported reference reaches `NotImplementedError`.
- Mounted secrets continue to work unchanged.
- Invalid production references fail closed during startup.

## 8. Phase 3 — Genuine structured-LLM strategy builder

### Objective

Make LLM mode genuinely model-assisted while preserving deterministic validation and explicit approval as the security boundary.

### Architecture

Add a focused generation service, for example `pairs_trading/backend/strategy_builder_generation.py`. `StrategyBuilderService` remains responsible for validation, approval, persistence, and catalog behavior. The generation service is responsible only for turning a bounded conversation into a candidate structured spec plus clarification questions.

### Configuration

Add strategy-specific settings rather than reusing fields named for market research:

- `PAIRS_TRADING_STRATEGY_BUILDER_MODE=rules|llm`;
- `PAIRS_TRADING_STRATEGY_BUILDER_LLM_PROVIDER`;
- `PAIRS_TRADING_STRATEGY_BUILDER_LLM_MODEL`;
- timeout, retry, and concurrency settings;
- provider API-key reference fields.

Refactor `llm_config.py` to expose a provider-agnostic structured-provider factory and retain a market-research wrapper so existing behavior stays compatible.

### Implementation steps

1. Define a dedicated Pydantic output model containing:
   - candidate `StrategySpecModel` or null;
   - state: `needs_clarification`, `ready_for_validation`, or `rejected`;
   - bounded clarification questions;
   - safe assistant summary;
   - no arbitrary metadata dictionary from the model.
2. Build a versioned prompt that contains:
   - the exact `StrategySpecModel` JSON schema;
   - allowlisted indicator and rule kinds;
   - supported timeframes and long-only limitation;
   - instruction to state missing requirements as questions;
   - instruction that it cannot access files, tools, databases, secrets, URLs, or execute code.
3. Bound input before the provider call: maximum message count, per-message characters, total characters, allowed roles, and no binary/attachment content.
4. Keep the current deterministic prompt-injection and unsupported-capability pre-check before any hosted provider call. Rejection must not consume a provider request.
5. Inject `StructuredLLMProvider` into the generation service for tests. Do not instantiate providers inside each chat request.
6. Call `generate_structured(prompt, StrategyBuilderGenerationResult, options)` exactly once per chat turn, subject to the provider's bounded retry behavior.
7. Treat model output as untrusted:
   - validate the generation envelope;
   - pass the candidate through `validate_strategy_spec()`;
   - run `dry_run_strategy_spec()` only after validation;
   - never persist or return a candidate that fails validation as ready for approval.
8. On provider timeout, unavailability, or schema failure:
   - return a safe `needs_clarification`/temporarily unavailable response;
   - do not silently run the regex parser while displaying AI mode;
   - record sanitized failure metadata and metrics.
9. Keep rules mode as an explicit local/development mode. Do not call the current research mock provider for strategy generation because its payload is research-agent-specific.
10. Persist generation provenance with the approved strategy: mode, provider, model, prompt version/hash, latency, and sanitized usage totals. Do not persist raw prompts or raw provider responses.
11. Update the UI to show the active mode and a review warning that the structured result is a hypothesis, not generated executable code or investment advice.

### Tests

- Fake provider returns a valid spec and reaches ready-for-approval only after validation/dry run.
- Malformed JSON, schema mismatch, timeout, provider outage, excessive questions, unknown rule kind, unsupported timeframe, short/long-short request, and prompt-injection tests.
- Assert rejected requests never call the provider.
- Assert provider output cannot inject executable code or unknown rule blocks.
- Assert approval persists generation provenance and remains tenant scoped.
- Assert rules mode makes zero provider calls and uses truthful labels.
- Router tests for response contracts and safe status codes.
- Frontend tests for loading, clarification, provider failure, review, approval, and rules-mode fallback states.

### Rollout

1. Deploy backend with mode `rules`.
2. Verify provider preflight in a non-production environment.
3. Enable `llm` for internal/test tenants through server configuration.
4. Compare validation rejection rates, latency, and provider failures.
5. Enable production only after prompt/version evidence is recorded and rollback to `rules` is tested.

### Exit gate

- LLM-branded requests call a configured real structured provider.
- No model response bypasses deterministic validation, dry run, or user approval.
- Provider failure cannot produce an executable/approvable strategy.

## 9. Phase 4 — Real market-research news and fundamentals

### Objective

Give the committee direct, point-in-time evidence for news and fundamentals while preserving provenance and component-level degradation.

### Data contracts

Create provider-neutral protocols in `pairs_trading/data/`:

- `MarketResearchNewsProvider.get_news(ticker, start, end) -> list[normalized headline]`;
- `FundamentalsProvider.get_snapshot(ticker, as_of_date) -> FundamentalsSnapshot`.

`FundamentalsSnapshot` must use typed fields and reporting-period metadata. At minimum: revenue, net income, diluted EPS, cash, total assets, total liabilities, debt, shares outstanding when available, filing/report dates, fiscal period, currency/unit, source URL, provider, and freshness. Missing values remain null with explicit missing indicators.

### News implementation steps

1. Extract the provider-construction logic currently embedded in `SentimentService._headline_provider()` into a reusable server-side factory.
2. Preserve the existing RSS, NewsAPI, Alpha Vantage, Benzinga, and composite/dedup implementations. Do not copy their network or parsing logic.
3. Add server-side market-research news settings: ordered provider list, API-key references, maximum articles, lookback, timeout, and cache/freshness policy.
4. Resolve keys through the Phase 2 secret resolver. Market-research requests must not contain provider keys.
5. Convert provider rows into committee `NewsItem` and `SourceReference` records with ticker, timestamp, headline, source/provider, URL, sentiment if available, deduplication metadata, and confidence.
6. Enforce point-in-time filtering: no article timestamp after the requested analysis date.
7. Record source-specific warnings without discarding successful sources. If all news providers fail, mark only news degraded.

### Fundamentals implementation steps

1. Extract the SEC fetch/cache behavior shared by `SecCompanyFactsEventProvider` into a reusable SEC Company Facts client; update the event provider to use it.
2. Implement a `SecCompanyFactsFundamentalsProvider` on top of that client. Do not call the event provider's private methods.
3. Normalize XBRL concepts conservatively and retain the selected concept, form, filing date, reporting period, unit, accession/source URL, and selection rationale.
4. Enforce as-of correctness using filing availability, not merely fiscal period end. A filing published after the requested analysis date must not be used.
5. Treat guidance and forward estimates as unavailable unless a separately licensed provider is later added. Do not infer guidance from price or headlines.
6. Add typed `fundamentals` to `MarketResearchContext` and update `FundamentalAnalyst` to consume it. Financial events remain an additional signal and must not masquerade as a complete snapshot.

### Collector/orchestrator changes

1. Refactor `_collect_cached_yahoo()` into component collectors for prices, news, fundamentals, sentiment datasets, and financial events.
2. Run independent I/O collectors concurrently only with bounded concurrency and timeouts; merge results deterministically.
3. Calculate degradation and missing indicators from actual component results rather than hardcoded `news=[]` and fixed warnings.
4. Keep production policy fail-closed for the required price component. Decide explicitly whether news/fundamentals are required or degradable through validated configuration; default them to required for a report advertised as complete.
5. Persist per-component provider, source references, freshness, row counts, warnings, and synthetic/fallback flags.
6. Update runtime diagnostics and the frontend report view so users can see exactly which components are real, missing, stale, or synthetic.

### Tests

- Unit tests for provider factory configuration and secret resolution.
- News normalization, deduplication, timestamp/as-of filtering, partial-provider failure, and all-provider failure tests.
- SEC concept normalization, units, amendments, duplicate periods, as-of filing cutoff, missing facts, cache, and source URL tests.
- Collector tests for price success/news failure, news success/fundamentals failure, and complete success.
- Production tests proving demo data/mock evidence cannot appear.
- Agent tests proving the fundamental and news analysts use real inputs and reduce confidence when inputs are absent/stale.
- Integration tests remain opt-in for external services; normal CI uses fixtures/fakes and performs no live provider calls.

### Rollout

1. Deploy with collection configured but reports marked preview/internal.
2. Backtest known historical as-of dates to detect look-ahead leakage.
3. Compare provider row counts, freshness, and failure rates.
4. Enable complete-report status only after provenance and as-of tests pass.

### Exit gate

- Configured reports contain direct news and normalized fundamentals with source-level provenance.
- Missing components are explicit and never replaced by fabricated evidence in production.
- Historical analysis cannot consume data filed or published after the analysis date.

## 10. Phase 5 — Admin UI coverage and frontend cleanup

### Objective

Expose existing operational endpoints to authorized admins and remove or deliberately adopt orphaned frontend code.

### Admin implementation steps

1. Add explicit TypeScript response types and client functions for:
   - `GET /api/admin/audit-log`;
   - `GET /api/admin/system-health`;
   - `GET /api/admin/quotas`;
   - `PATCH /api/admin/quotas/{organization_id}`.
2. Replace the untyped quota PATCH body in the backend with a bounded Pydantic request model. Validate non-negative integer limits, recognized quota names, and maximum allowed values.
3. Add three focused panels/tabs to `AdminDashboard`:
   - health summary with manual refresh and bounded polling only while visible;
   - paginated/filterable audit entries with safe metadata rendering;
   - organization quotas with edit confirmation, pending state, success/error state, and server reload after mutation.
4. Never expose secret-bearing health details or raw exception strings. The frontend displays safe dependency state and timestamps only.
5. Add empty, loading, partial failure, forbidden, and stale-data states.

### Dead-code decision

The primary `/` route uses `ApolloApp`/`ApolloDashboard`, which already owns strategy browsing and risk presentation. Therefore the default action is:

1. Delete `StrategyLibrary.tsx` and `RiskMonitor.tsx` after confirming no route/import in a clean build.
2. Remove their now-unused CSS selectors, imports, chart helpers, or types only when reference searches and tests prove they are unused.
3. Classify the six unused frontend API helpers:
   - remove wrappers whose endpoint is already covered through another payload/path;
   - retain and wire a wrapper only when a named screen in this phase needs it;
   - do not remove the backend endpoint merely because the frontend does not use it.
4. Add an unused-export/static analysis check appropriate for TypeScript exports, plus existing TypeScript no-unused checks. Configure explicit ignore entries for intentionally public API modules rather than blanket exclusions.

### Tests

- Backend validation/auth/CSRF/tenant tests for quota mutation.
- Admin API client tests and component tests for all panel states.
- Assert non-admin users cannot load or mutate admin data.
- Frontend build, typecheck, tests, and unused-export check pass after deletion.
- Backend endpoint contract tests remain green for helpers removed only from the frontend.

### Exit gate

- Audit, health, and quota capabilities are usable from the admin UI.
- Every retained frontend feature component and exported API helper has an intentional consumer or documented public reason.

## 11. Phase 6 — Marketplace sharing and subscriptions

### Objective

Implement durable, tenant-safe strategy sharing without money or performance accounting.

### Feature controls

- Add `MARKETPLACE_ENABLED=false` by default.
- Keep all marketplace mutations unavailable when false.
- The workspace capability controls frontend visibility; do not depend on a frontend build-time flag alone.

### Schema

Create the next Alembic migration and matching SQLite schema for:

1. `strategy_listings`
   - ID;
   - publisher organization/user IDs;
   - source user-strategy ID;
   - title, slug, summary;
   - visibility (`public`, `unlisted`);
   - status (`draft`, `published`, `suspended`, `archived`);
   - current version ID;
   - created, updated, published, archived timestamps.
2. `strategy_listing_versions`
   - ID and listing ID;
   - monotonically increasing version number;
   - immutable StrategySpec snapshot JSON;
   - safe catalog/read-model snapshot JSON;
   - validation snapshot JSON and risk level;
   - source strategy version;
   - content hash;
   - creator and timestamp;
   - unique `(listing_id, version)` and content-hash constraints as appropriate.
3. `strategy_marketplace_subscriptions`
   - ID;
   - subscriber organization/user IDs;
   - listing and pinned listing-version IDs;
   - status (`active`, `cancelled`);
   - created, updated, cancelled timestamps;
   - unique subscriber-organization/listing relationship.

Do not overload the existing SaaS `subscriptions` table.

### Service/API design

1. Add `MarketplaceService` in a dedicated backend module and a thin `routers/marketplace.py` router.
2. Add typed Pydantic request/response models and safe read models.
3. Provide:
   - paginated/filterable public listing search;
   - listing detail by ID/slug;
   - publisher create/update-draft/create-version/publish/archive operations;
   - subscribe, unsubscribe, and list-my-subscriptions operations;
   - list-my-publications operation;
   - admin suspend/reinstate operations with audit entries.
4. Publishing creates or selects an immutable version only after the same strategy validation and dry run used by approval.
5. Subscribers receive access to the pinned immutable version. A new publisher version requires explicit subscriber upgrade; never mutate an active subscription silently.
6. A publisher may not subscribe its own organization to its listing.
7. Archiving prevents new subscriptions but does not delete historical versions or break existing audit records. Suspension prevents execution by subscribers according to a documented safety policy.
8. All mutations are idempotent and audited. List endpoints use bounded pagination and deterministic ordering.
9. Build a dedicated marketplace catalog read model. Do not expose approval notes, owner email, raw audit metadata, private validation internals, or organization secrets.

### Frontend

1. Replace `marketPub` and `marketSubs` local maps with server data.
2. Add publish/version/subscribe/unsubscribe flows with confirmation and error states.
3. Continue showing the unavailable banner when the capability is false.
4. Remove all royalty/earnings displays in this phase. Use `Creator credits coming later` only if Phase 7 is approved.
5. Display listing version and risk/validation summary before subscription.

### Tests

- Migration upgrade/downgrade and SQLite parity tests.
- Publisher authorization, cross-tenant isolation, private draft non-disclosure, admin suspension, archive behavior, and self-subscription rejection.
- Immutable-version and explicit-upgrade tests.
- Idempotent subscribe/unsubscribe and concurrent subscription tests.
- Pagination/filter/order and safe serialization tests.
- Frontend tests for disabled capability, browse, publish, subscribe, upgrade, archive, and errors.

### Rollout

1. Apply schema with feature disabled.
2. Deploy backend and run migration/parity tests.
3. Enable for internal organizations and seed reviewed listings.
4. Audit tenant isolation and moderation paths.
5. Enable public browsing/subscription only after security review.

### Exit gate

- Marketplace state survives restart and is database authoritative.
- Subscribers are pinned to immutable reviewed versions.
- No client-only publish/subscription state remains.

## 12. Phase 7 — Simulated creator-credit ledger

### Objective

Replace client-side royalty arithmetic with deterministic, append-only fake-money creator credits. This is not a payment system.

### Legal/product gate

Before implementation, approve terminology and policy stating:

- credits have no cash value;
- credits cannot be withdrawn, transferred, purchased, or redeemed;
- results are hypothetical paper performance;
- negative performance never creates a creator charge;
- this phase does not authorize live trading or performance fees.

If this policy is not approved, leave `marketplace_creator_credits_enabled=false` and omit the feature.

### Schema

Add an append-only `strategy_creator_credit_ledger` with:

- entry ID and unique idempotency key;
- listing, listing version, and marketplace subscription IDs;
- source paper deployment/run ID;
- beneficiary organization/user IDs;
- event type (`accrual`, `reversal`, `adjustment`);
- gross eligible gain in integer micro-units;
- creator rate in integer basis points;
- calculated credit amount in integer micro-units;
- unit/currency label explicitly marked simulated;
- calculation-policy version;
- period start/end and created timestamp;
- reversal-of entry ID where applicable;
- immutable source metadata hash.

Do not update ledger entries in place. Corrections use reversing entries.

### Calculation service

1. Define one versioned policy based only on completed, authoritative paper-run records tied to the subscriber and pinned listing version.
2. Calculate from a documented eligible-gain measure. Do not reuse the current client-side `positive gains * percent` calculation without specifying baseline, period, and duplicate-run behavior.
3. Perform the calculation server-side with decimal/integer arithmetic.
4. Insert with a deterministic idempotency key derived from policy version, source run, subscription, and ledger event type.
5. Run accrual as a worker job after paper-run publication or as a bounded reconciliation job. It must be safe under at-least-once execution.
6. Provide creator statement and admin audit endpoints with pagination and totals calculated from ledger entries.
7. Keep all feature flags and UI wording explicitly simulated.

### Tests

- Exact arithmetic, rounding, zero/negative gain, duplicate delivery, reversal, policy-version, concurrent insert, and cross-tenant isolation tests.
- Assert a source paper run cannot accrue twice.
- Assert unpublished/unsubscribed/wrong-version runs are ineligible.
- Migration and SQLite/Postgres constraint parity tests.
- Frontend statement tests that always display the simulated/no-cash-value disclosure.

### Exit gate

- Creator-credit totals are derived exclusively from an append-only, idempotent server ledger.
- The frontend performs no authoritative accounting.
- No UI or API suggests cash value or regulatory approval.

## 13. Phase 8 — Integration hardening and release

### Objective

Verify that the combined changes operate safely under production topology and that documentation matches reality.

### Required verification

1. Run the entire Python unit suite and frontend suite, not only targeted tests.
2. Run Postgres migration tests, atomicity/concurrency tests, and Redis/RQ integration tests with configured test services.
3. Run production configuration validation and Compose configuration rendering.
4. Run security checks already used by the repository: Bandit, dependency audit, secret scan, and supply-chain checks where available.
5. Run frontend accessibility checks for new admin/marketplace flows.
6. Exercise these failure drills:
   - Redis unavailable during submit and later recovery;
   - controller unavailable/stale heartbeat;
   - LLM timeout and invalid schema;
   - all news providers fail while prices remain available;
   - SEC unavailable/stale cache;
   - secret-manager access denied and rotation;
   - marketplace duplicate/concurrent subscribe;
   - credit accrual delivered twice.
7. Update operational runbooks, environment-variable references, API documentation, and user-facing disclosures.
8. Remove obsolete marketplace-unavailable copy only when the corresponding server capability is enabled.

### Mandatory commands

Run the repository-equivalent commands from the workspace root:

```powershell
python -m pytest -q
python -m pytest -q -m integration
python scripts/validate-production-deployment.py
```

Run from `frontend/`:

```powershell
npm run typecheck
npm test -- --run
npm run build
```

Integration tests may be skipped only when their required external test URLs are unavailable, and that omission must be recorded in the handoff.

### Final exit gate

- All enabled capabilities have durable backend support and accurate UI wording.
- Production cannot silently use mock/synthetic research evidence.
- Queue, provider, and controller failures are observable and recoverable.
- Secrets resolve through supported explicit schemes without leakage.
- Admin operational endpoints have UI coverage.
- No known orphaned frontend code remains without an explicit documented reason.
- Marketplace subscriptions and simulated credits are tenant-safe, immutable/versioned, and idempotent.
- Paper-only and no-cash-value boundaries remain prominent.

## 14. Pull-request breakdown

Keep changes reviewable. The recommended PR sequence is:

1. Capability contract, truthful builder labels, audit redaction, and documentation corrections.
2. Shared initial-dispatch helper, dispatch metadata, metrics, health, and UI status.
3. Secret resolver consolidation and AWS adapter.
4. Generic structured-provider factory refactor with no behavior change.
5. Strategy-builder generation service, tests, and LLM-mode UI.
6. Reusable headline-provider factory and committee news integration.
7. Reusable SEC client, fundamentals snapshot provider, and fundamental analyst integration.
8. Admin typed contracts and health/audit/quota panels.
9. Orphaned frontend cleanup and unused-export CI check.
10. Marketplace schema/persistence/service/API behind a disabled flag.
11. Marketplace frontend behind server capability.
12. Simulated creator-credit schema/service/worker behind a second disabled flag.
13. Creator statement UI, operational documentation, and final production drills.

Do not combine schema introduction, marketplace enablement, and creator accounting into one deployment.

## 15. Handoff checklist for each phase

Every implementing agent must include the following in its handoff:

- files changed and why;
- migrations added and downgrade/rollback behavior;
- API contract changes;
- new settings and safe defaults;
- security/tenancy decisions;
- tests added and exact commands/results;
- integration tests not run and why;
- feature flags and rollout state;
- known limitations that remain;
- confirmation that no unrelated user changes were overwritten.

