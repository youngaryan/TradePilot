# Frontend access model — subscription segment × organizational role

The Meridian interface treats **what a workspace pays for** and **what authority an
account holds** as two independent dimensions. Nothing in the frontend is a
security boundary: the API remains authoritative (`require_auth_context`,
`require_admin_context`, `require_paid_context`, tenant scoping). This document
records the contracts the UI reads, and the gaps it deliberately does not paper
over.

Implementation: [`frontend/src/access/model.ts`](../frontend/src/access/model.ts).
Tests: `frontend/src/access/__tests__/model.test.ts`,
`frontend/src/app/__tests__/RestrictedRoutes.test.tsx`,
`frontend/src/shell/__tests__/navigation.test.ts`.

## Dimension 1 — subscription segment (per workspace)

| Source | Field | Used for |
| --- | --- | --- |
| `GET /api/billing/status` | `premium` | Authoritative entitlement. Preferred over any local derivation. |
| `GET /api/billing/status` | `access` (`"admin"` / `"subscription"`) | Why premium is granted. |
| `GET /api/billing/status` / `GET /api/workspaces` | `subscription.plan`, `subscription.status`, `current_period_end_utc` | Plan label, lifecycle state, renewal date. |

Lifecycle states rendered distinctly: `none`, `free`, `trialing`, `active`,
`past_due`, `canceled`, `incomplete`, `unpaid`, `paused`, `unknown`.

A **paid plan with a blocking status** (`past_due`, `canceled`, `incomplete`,
`unpaid`, `paused`) is reported as *billing attention required* — never as "free"
and never as "upgrade to unlock". The distinction matters: one is a purchase
decision, the other is a payment problem.

`access: "admin"` is returned by the API for **every** administrator, including
administrators whose workspace also holds a valid paid plan. The UI therefore
attributes premium to the subscription whenever the subscription alone would
grant it, and only calls out the administrator override when the override is
actually doing the work.

## Dimension 2 — organizational role

| Source | Field | Values | Meaning |
| --- | --- | --- | --- |
| `GET /api/auth/me` → `organizations[]` | `role` | `owner`, `admin`, `member` | Authority **inside that workspace**. Read from `organization_members.role`. |
| `GET /api/auth/me` → `user` | `role` | `admin`, `user` | Authority **across the whole deployment** (`users.role`). |

`owner` and `admin` on the active workspace are treated as **workspace manager**.
This is a real, server-provided value — no frontend-only role was invented.

Platform administration is a separate flag on a different table. A workspace
manager is never granted platform administration, and a platform administrator is
not automatically a manager of the workspace they are looking at (the Management
screen says so explicitly when that happens).

## Capability matrix

| Capability | Gate | Notes |
| --- | --- | --- |
| Browse strategy library, read saved experiments / agents / reports / datasets | authentication | Free on every plan. |
| Natural-language strategy builder (`/builder/chat`, `/builder/approve`) | authentication | **Not** plan-gated by the API — only quota-limited in LLM mode. Must never be presented as premium. |
| Marketplace publish / subscribe | `capabilities.marketplace_enabled` | Member-accessible; only moderation is admin-gated. |
| Start a backtest, sentiment build, research committee, paper deployment, data refresh | active paid plan **or** platform admin | Server returns `402 payment_required` otherwise. |
| Billing checkout / portal / sync | authentication (any workspace member) | See "Missing contracts" below. |
| Workspace management area | workspace `owner`/`admin`, or platform admin | Presentation gate over data the API already scopes to the tenant. |
| Platform administration | `users.role == "admin"` | Server-enforced by `require_admin_context` (plus MFA in production). |

## Audience segments in platform administration

`GET /api/admin/users` returns `role`, `status`, `organization_role`, `plan` and
`subscription_status`. Segments are computed from exactly those fields:

- **Free** — not entitled and not in a billing-attention state.
- **Paying** — paid plan with `active` or `trialing` status.
- **Managers** — `organization_role` of `owner` or `admin`.
- **Administrators** — `users.role == "admin"`.
- **Billing attention** — paid plan with a blocking status.
- **Restricted** — `status != "active"`.

Nothing is inferred from unrelated fields, and an account with no plan
information is never classified as paying, managing, or administering.

## Missing backend contracts (documented, not invented)

1. **No workspace member directory.** There is no `GET /api/workspaces/members`.
   Membership rows exist in `organization_members` with a `role` column, but only
   the *caller's own* membership is surfaced (via `/api/auth/me`). The Management
   screen therefore shows a "Members & roles" tab containing the caller's own
   roles per workspace plus an explicit statement that the directory is not
   exposed — rather than a table populated from guesses.
2. **No invitation or workspace-role assignment endpoint.** Platform
   administrators can change a user's *platform* role via
   `PATCH /api/admin/users/{id}`, which is a different dimension and does not
   touch workspace membership. No frontend control claims otherwise.
3. **No separate billing permission.** `POST /api/billing/checkout`,
   `/portal` and `/sync` depend only on `context` (any authenticated member of the
   active workspace). The UI therefore exposes billing to members and states that
   billing is a workspace-level setting, instead of inventing a
   "manager-only billing" rule the server would not enforce.
4. **No per-plan quota exposure to the client.** `GET /api/admin/quotas` is
   admin-only and there is no member-visible usage endpoint, so the UI shows no
   usage meters or "N of M runs used" figures anywhere.

The information architecture and the reusable `AccessNotice` / `Gate` primitives
are built so each of these can be introduced without restructuring: add the
endpoint, add a capability to `buildAccessContext`, and populate the tab that
already exists.

## Rules the UI follows

- Hiding a navigation item is presentation only; every route re-checks the gate
  before rendering, and the API rejects the request regardless.
- A restricted surface always explains: what the feature does, why it is
  unavailable, what unlocks it, and what the user can do instead.
- A control that the server would refuse is disabled with a reason, never shown
  as if it would work.
- Plan state and role state are displayed separately wherever either is shown.
- Changing a plan never changes a role in the UI's model, and changing a role
  never changes a plan.
