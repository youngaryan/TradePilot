from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import base64
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from ..platform import SQLiteMetadataStore
from .config import BackendSettings
from .schemas import ApiKeyCreateRequest, BillingCheckoutRequest, SignupRequest


DEMO_EMAIL = "demo@quantops.local"
DEMO_PASSWORD = "quantops-demo"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    return value


def hash_password(password: str, *, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return f"pbkdf2_sha256$200000${salt}${base64.b64encode(digest).decode('ascii')}"


def verify_password(password: str, stored_hash: str) -> bool:
    if stored_hash == "demo-password-hash":
        return password == DEMO_PASSWORD
    if stored_hash == "demo-user-password-hash":
        return password == "quantops-user"
    try:
        algorithm, iterations, salt, expected = stored_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations))
    return hmac.compare_digest(base64.b64encode(digest).decode("ascii"), expected)


@dataclass(frozen=True)
class RequestContext:
    user: dict[str, Any]
    organization_id: str


class AuthService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.store = SQLiteMetadataStore(settings.metadata_db_path)

    def login(self, *, email: str, password: str) -> dict[str, Any]:
        user = self.store.get_user_by_email(email)
        if user is None or not verify_password(password, str(user.get("password_hash", ""))):
            raise ValueError("Invalid email or password.")
        if str(user.get("status") or "active").lower() != "active":
            raise PermissionError("This account has been deactivated. Contact an administrator.")
        token = secrets.token_urlsafe(32)
        self.store.create_auth_session(user_id=str(user["id"]), token=token)
        organizations = self.store.list_organizations_for_user(user_id=str(user["id"]))
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": self.public_user(user),
            "organizations": organizations,
            "active_organization_id": organizations[0]["id"] if organizations else None,
        }

    def signup(self, request: SignupRequest) -> dict[str, Any]:
        email = request.email.casefold().strip()
        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            raise ValueError("Enter a valid email address.")
        self.store.create_user_workspace(
            email=email,
            display_name=request.display_name.strip(),
            password_hash=hash_password(request.password),
            organization_name=request.organization_name.strip(),
            role="user",
            plan="free",
            subscription_status="free",
        )
        return self.login(email=email, password=request.password)

    def authenticate(self, *, token: str, organization_id: str | None = None) -> RequestContext:
        session = self.store.get_auth_session(token=token)
        if session is None:
            raise ValueError("Authentication required.")
        user = self.store.get_user_by_id(str(session["user_id"]))
        if user is None:
            raise ValueError("Authentication required.")
        if str(user.get("status") or "active").lower() != "active":
            raise PermissionError("This account has been deactivated. Contact an administrator.")
        active_org = organization_id or self.store.get_default_organization_id(user_id=str(user["id"]))
        if active_org is None or not self.store.user_has_organization_access(user_id=str(user["id"]), organization_id=active_org):
            raise PermissionError("You do not have access to this workspace.")
        return RequestContext(user=self.public_user(user), organization_id=active_org)

    def me(self, *, token: str, organization_id: str | None = None) -> dict[str, Any]:
        context = self.authenticate(token=token, organization_id=organization_id)
        organizations = self.store.list_organizations_for_user(user_id=str(context.user["id"]))
        return {
            "user": context.user,
            "organizations": organizations,
            "active_organization_id": context.organization_id,
        }

    def logout(self, *, token: str) -> None:
        self.store.delete_auth_session(token=token)

    @staticmethod
    def public_user(user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "role": user.get("role", "user"),
            "status": user.get("status", "active"),
        }


class SaaSService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.store = SQLiteMetadataStore(settings.metadata_db_path)

    def workspace_payload(self, *, organization_id: str) -> dict[str, Any]:
        self.sync_default_datasets(organization_id=organization_id)
        return {
            "organization_id": organization_id,
            "projects": self.store.list_projects(organization_id=organization_id),
            "subscription": self.store.get_subscription(organization_id=organization_id),
            "datasets": self.store.list_datasets(organization_id=organization_id),
            "api_keys": self.store.list_api_keys(organization_id=organization_id),
            "experiments": self.store.list_experiments(organization_id=organization_id, limit=20),
            "paper_agents": self.store.list_paper_agents(organization_id=organization_id),
            "onboarding": self.onboarding_state(organization_id=organization_id),
        }

    def onboarding_state(self, *, organization_id: str) -> dict[str, Any]:
        projects = self.store.list_projects(organization_id=organization_id)
        datasets = self.store.list_datasets(organization_id=organization_id)
        experiments = self.store.list_experiments(organization_id=organization_id, limit=1)
        paper_agents = self.store.list_paper_agents(organization_id=organization_id)
        api_keys = self.store.list_api_keys(organization_id=organization_id)
        steps = [
            {"id": "project", "label": "Create or use a research project", "complete": bool(projects)},
            {"id": "dataset", "label": "Connect market/news data or use local cache", "complete": bool(datasets)},
            {"id": "backtest", "label": "Run a validated backtest", "complete": bool(experiments)},
            {"id": "paper", "label": "Deploy a fake-money paper agent", "complete": bool(paper_agents)},
            {"id": "billing", "label": "Review billing and usage limits", "complete": bool(api_keys) or True},
        ]
        return {
            "steps": steps,
            "complete_count": sum(1 for step in steps if step["complete"]),
            "total_count": len(steps),
        }

    def create_project(self, *, organization_id: str, name: str, description: str | None = None) -> dict[str, Any]:
        return self.store.create_project(organization_id=organization_id, name=name, description=description)

    def create_api_key_metadata(self, *, organization_id: str, request: ApiKeyCreateRequest) -> dict[str, Any]:
        if not request.secret and not request.secret_ref:
            raise ValueError("Provide either a secret value to mask or a secret_ref such as NEWSAPI_API_KEY.")
        return self.store.create_api_key_metadata(
            organization_id=organization_id,
            name=request.name,
            provider=request.provider,
            secret=request.secret,
            secret_ref=request.secret_ref,
        )

    def sync_default_datasets(self, *, organization_id: str) -> None:
        sentiment_path = self.settings.sentiment_cache_dir / "shadow" / "daily_sentiment.parquet"
        if sentiment_path.exists():
            try:
                daily = pd.read_parquet(sentiment_path)
                schema = {"columns": list(daily.columns)}
                row_count = len(daily)
            except Exception:
                schema = {}
                row_count = 0
            self.store.upsert_dataset(
                organization_id=organization_id,
                payload={
                    "name": "Shadow Daily Sentiment",
                    "kind": "sentiment_daily",
                    "path": str(sentiment_path),
                    "provider": {"source": "sentiment_accumulator"},
                    "schema": schema,
                    "row_count": row_count,
                },
            )
        for cache_dir, kind in (
            (self.settings.price_cache_dir, "price_cache"),
            (self.settings.event_cache_dir, "event_cache"),
        ):
            if cache_dir.exists():
                files = list(cache_dir.rglob("*.parquet"))
                self.store.upsert_dataset(
                    organization_id=organization_id,
                    payload={
                        "name": f"{kind.replace('_', ' ').title()}",
                        "kind": kind,
                        "path": str(cache_dir),
                        "provider": {"source": "local_cache"},
                        "schema": {"file_count": len(files)},
                        "row_count": len(files),
                    },
                )

    def list_experiments(self, *, organization_id: str) -> list[dict[str, Any]]:
        self.sync_experiment_runs(organization_id=organization_id)
        return self.store.list_experiments(organization_id=organization_id, limit=50)

    def get_experiment(self, *, organization_id: str, experiment_id: str) -> dict[str, Any] | None:
        self.sync_experiment_runs(organization_id=organization_id)
        experiment = self.store.get_experiment(organization_id=organization_id, experiment_id=experiment_id)
        if experiment is not None:
            return self.enrich_experiment_detail(experiment)
        return None

    def sync_experiment_runs(self, *, organization_id: str) -> None:
        for run in self.store.list_experiment_runs(kind="backtest"):
            artifact_dir = Path(str(run.get("artifact_dir") or ""))
            summary = dict(run.get("summary") or {})
            validation = _json_file(artifact_dir / "validation.json") if artifact_dir.exists() else {}
            experiment_id = str(summary.get("experiment_id") or run["id"])
            if self.store.get_experiment(organization_id=organization_id, experiment_id=experiment_id):
                continue
            request = {}
            for job in self.store.list_jobs(kind="backtest"):
                result = job.get("result") or {}
                if result.get("artifact_dir") == str(artifact_dir):
                    request = dict(job.get("request") or {})
                    break
            readiness = build_readiness(summary=summary, validation=validation)
            self.store.upsert_experiment(
                organization_id=organization_id,
                payload={
                    "id": experiment_id,
                    "name": str(summary.get("strategy") or experiment_id),
                    "pipeline": str(request.get("pipeline") or summary.get("strategy") or "backtest"),
                    "status": "completed",
                    "artifact_dir": str(artifact_dir) if str(artifact_dir) else None,
                    "summary": summary,
                    "validation": validation,
                    "lineage": build_lineage(request=request, artifact_dir=str(artifact_dir), settings=self.settings),
                    "readiness": readiness,
                    "trades": [],
                    "sentiment": sentiment_snapshot(request=request, artifact_dir=artifact_dir),
                    "created_at_utc": run.get("created_at_utc"),
                },
            )

    def enrich_experiment_detail(self, experiment: dict[str, Any]) -> dict[str, Any]:
        artifact_dir = Path(str(experiment.get("artifact_dir") or ""))
        if artifact_dir.exists():
            experiment["artifact_files"] = sorted(
                str(path) for path in artifact_dir.rglob("*") if path.is_file() and path.name != ".DS_Store"
            )
            equity_path = artifact_dir / "equity_curve.parquet"
            if equity_path.exists():
                try:
                    equity = pd.read_parquet(equity_path)
                    experiment["equity_curve_points"] = frame_points(equity.tail(500))
                except Exception:
                    experiment["equity_curve_points"] = []
            fold_path = artifact_dir / "fold_metrics.parquet"
            if fold_path.exists():
                try:
                    experiment["fold_metrics"] = _json_ready(pd.read_parquet(fold_path).tail(25).to_dict(orient="records"))
                except Exception:
                    experiment["fold_metrics"] = []
            diagnostics = _json_file(artifact_dir / "diagnostics.json")
            experiment["diagnostics"] = diagnostics[:10] if isinstance(diagnostics, list) else diagnostics
        return experiment

    def sync_paper_agents_from_dashboard(self, *, organization_id: str, payload: dict[str, Any]) -> None:
        for strategy in payload.get("strategies", []) or []:
            name = str(strategy.get("name") or "paper_agent")
            warnings = warning_snapshot(strategy)
            self.store.upsert_paper_agent(
                organization_id=organization_id,
                payload={
                    "id": self.store.stable_id("agt", f"{organization_id}:{name}"),
                    "name": name,
                    "pipeline": str(strategy.get("pipeline") or strategy.get("diagnostics", {}).get("pipeline") or "unknown"),
                    "status": "running" if strategy.get("equity") is not None else "idle",
                    "fake_cash": float(strategy.get("cash") or 0.0),
                    "config": {
                        "mode": strategy.get("mode"),
                        "target_weights": strategy.get("target_weights", {}),
                    },
                    "latest_payload": strategy,
                    "warnings": warnings,
                },
            )

    def list_paper_agents(self, *, organization_id: str) -> list[dict[str, Any]]:
        return self.store.list_paper_agents(organization_id=organization_id)

    def get_paper_agent(self, *, organization_id: str, agent_id: str) -> dict[str, Any] | None:
        return self.store.get_paper_agent(organization_id=organization_id, agent_id=agent_id)


class BillingService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.store = SQLiteMetadataStore(settings.metadata_db_path)

    def pricing(self, *, organization_id: str | None = None) -> dict[str, Any]:
        subscription = self.store.get_subscription(organization_id=organization_id) if organization_id else None
        plans = [
            {
                "id": "free",
                "name": "Free",
                "price_monthly": 0,
                "currency": "usd",
                "description": "Explore the workspace, catalog, guides, and saved results without launching premium compute.",
                "features": [
                    "Read-only cockpit and strategy catalog",
                    "Workspace setup and billing status",
                    "View existing experiments and paper-agent records",
                ],
                "premium": False,
                "cta": "Current free access" if (subscription or {}).get("plan") == "free" else "Start free",
            },
            {
                "id": "pro",
                "name": "Pro",
                "price_monthly": 49,
                "currency": "usd",
                "description": "For active researchers who need backtests, sentiment collection, refresh jobs, and paper agents.",
                "features": [
                    "Launch backtest and sentiment jobs",
                    "Deploy fake-money paper agents",
                    "24-hour data refresh and telemetry",
                    "Experiment artifacts, lineage, and readiness reports",
                ],
                "premium": True,
                "recommended": True,
                "cta": "Upgrade to Pro",
            },
            {
                "id": "team",
                "name": "Team",
                "price_monthly": 149,
                "currency": "usd",
                "description": "For teams that need admin controls, shared workspaces, and stronger operating visibility.",
                "features": [
                    "Everything in Pro",
                    "Admin dashboard and user management",
                    "Usage telemetry and activity monitoring",
                    "Priority path for future broker/data integrations",
                ],
                "premium": True,
                "cta": "Contact sales workflow",
            },
        ]
        return {"plans": plans, "subscription": subscription}

    def status(self, *, organization_id: str) -> dict[str, Any]:
        subscription = self.store.get_subscription(organization_id=organization_id)
        premium = subscription is not None and str(subscription.get("plan")) in {"pro", "team", "enterprise", "pro_trial"} and str(subscription.get("status")) in {"active", "trialing"}
        return {"subscription": subscription, "premium": premium, "pricing": self.pricing(organization_id=organization_id)["plans"]}

    def checkout(self, *, organization_id: str, request: BillingCheckoutRequest) -> dict[str, Any]:
        subscription = self.store.get_subscription(organization_id=organization_id) or {}
        price_id = request.price_id or self.settings.stripe_pro_price_id
        if not self.settings.stripe_secret_key or not price_id:
            self.store.upsert_subscription(
                organization_id=organization_id,
                payload={**subscription, "plan": request.plan, "status": "trialing"},
            )
            return {
                "mode": "demo",
                "checkout_url": f"{self.settings.app_base_url}?billing=demo-checkout",
                "message": "Stripe is not configured. Set STRIPE_SECRET_KEY and STRIPE_PRO_PRICE_ID to create real Checkout sessions.",
            }
        success_url = self.settings.stripe_success_url or f"{self.settings.app_base_url}?billing=success"
        cancel_url = self.settings.stripe_cancel_url or f"{self.settings.app_base_url}?billing=cancelled"
        data = {
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "metadata[organization_id]": organization_id,
        }
        response = self._stripe_post("https://api.stripe.com/v1/checkout/sessions", data)
        return {"mode": "stripe", "checkout_url": response.get("url"), "stripe_session": response}

    def portal(self, *, organization_id: str, return_url: str | None = None) -> dict[str, Any]:
        subscription = self.store.get_subscription(organization_id=organization_id) or {}
        customer_id = subscription.get("stripe_customer_id")
        if not self.settings.stripe_secret_key or not customer_id:
            return {
                "mode": "demo",
                "portal_url": f"{self.settings.app_base_url}?billing=demo-portal",
                "message": "Stripe customer portal needs STRIPE_SECRET_KEY and a synced stripe_customer_id.",
            }
        data = {"customer": customer_id, "return_url": return_url or self.settings.app_base_url}
        response = self._stripe_post("https://api.stripe.com/v1/billing_portal/sessions", data)
        return {"mode": "stripe", "portal_url": response.get("url"), "stripe_session": response}

    def webhook(self, *, payload: bytes, signature_header: str | None) -> dict[str, Any]:
        if not self.settings.stripe_webhook_secret:
            raise ValueError("STRIPE_WEBHOOK_SECRET is required before accepting Stripe webhooks.")
        if not self._verify_stripe_signature(payload=payload, signature_header=signature_header):
            raise PermissionError("Invalid Stripe webhook signature.")
        event = json.loads(payload.decode("utf-8"))
        event_type = str(event.get("type") or "")
        data = event.get("data", {}).get("object", {}) if isinstance(event.get("data"), dict) else {}
        organization_id = (
            data.get("metadata", {}).get("organization_id")
            if isinstance(data.get("metadata"), dict)
            else None
        )
        if event_type == "checkout.session.completed" and organization_id:
            self.store.upsert_subscription(
                organization_id=str(organization_id),
                payload={
                    "plan": "pro",
                    "status": "active",
                    "stripe_customer_id": data.get("customer"),
                    "stripe_subscription_id": data.get("subscription"),
                    "usage": {"source": "stripe_checkout"},
                },
            )
            return {"received": True, "updated": True, "event_type": event_type}
        if event_type.startswith("customer.subscription."):
            subscription_id = data.get("id")
            status = data.get("status") or ("canceled" if event_type.endswith(".deleted") else "active")
            current_period_end = data.get("current_period_end")
            organization_id = organization_id or self._organization_id_for_stripe_subscription(str(subscription_id or ""))
            if organization_id:
                self.store.upsert_subscription(
                    organization_id=str(organization_id),
                    payload={
                        "plan": "pro",
                        "status": str(status),
                        "stripe_customer_id": data.get("customer"),
                        "stripe_subscription_id": subscription_id,
                        "current_period_end_utc": current_period_end,
                        "usage": {"source": "stripe_subscription_webhook"},
                    },
                )
                return {"received": True, "updated": True, "event_type": event_type}
        return {"received": True, "updated": False, "event_type": event_type}

    def _organization_id_for_stripe_subscription(self, subscription_id: str) -> str | None:
        if not subscription_id:
            return None
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT organization_id FROM subscriptions WHERE stripe_subscription_id = ?",
                (subscription_id,),
            ).fetchone()
        return None if row is None else str(row["organization_id"])

    def _verify_stripe_signature(self, *, payload: bytes, signature_header: str | None, tolerance_seconds: int = 300) -> bool:
        if not signature_header:
            return False
        parts: dict[str, list[str]] = {}
        for item in signature_header.split(","):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            parts.setdefault(key.strip(), []).append(value.strip())
        timestamp = (parts.get("t") or [None])[0]
        signatures = parts.get("v1") or []
        if not timestamp or not signatures:
            return False
        try:
            ts = int(timestamp)
        except ValueError:
            return False
        now_ts = int(datetime.now(UTC).timestamp())
        if abs(now_ts - ts) > tolerance_seconds:
            return False
        signed_payload = timestamp.encode("utf-8") + b"." + payload
        expected = hmac.new(
            str(self.settings.stripe_webhook_secret).encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()
        return any(hmac.compare_digest(expected, candidate) for candidate in signatures)

    def _stripe_post(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        encoded = urlencode(data).encode("utf-8")
        request = Request(
            url,
            data=encoded,
            headers={
                "Authorization": f"Bearer {self.settings.stripe_secret_key}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))


class AdminService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.store = SQLiteMetadataStore(settings.metadata_db_path)

    def overview(self) -> dict[str, Any]:
        counts = self.store.counts()
        metrics = self.store.admin_metric_snapshot()
        telemetry_events = self.store.list_telemetry_events(limit=1000)
        return {
            "counts": counts.__dict__,
            "metrics": metrics,
            "telemetry": telemetry_events[:100],
            "landing_analytics": build_landing_analytics(telemetry_events),
            "refresh_statuses": self.store.list_refresh_statuses(),
            "recent_refresh_runs": self.store.list_refresh_runs(limit=25),
            "recent_jobs": {
                "backtests": self.store.list_jobs(kind="backtest")[:25],
                "paper": self.store.list_jobs(kind="paper")[:25],
                "sentiment": self.store.list_jobs(kind="sentiment")[:25],
            },
        }

    def list_users(
        self,
        *,
        search: str | None = None,
        role: str | None = None,
        status: str | None = None,
        sort_by: str = "created_at_utc",
        sort_dir: str = "desc",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return self.store.list_admin_users(
            search=search,
            role=role,
            status=status,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=limit,
        )

    def update_user(self, *, user_id: str, role: str | None, status: str | None, actor_user_id: str) -> dict[str, Any]:
        user = self.store.get_user_by_id(user_id)
        if user is None:
            raise KeyError(f"User not found: {user_id}")
        next_role = role.lower() if role else None
        next_status = status.lower() if status else None
        if next_role and next_role not in {"admin", "user"}:
            raise ValueError("Role must be admin or user.")
        if next_status and next_status not in {"active", "inactive"}:
            raise ValueError("Status must be active or inactive.")
        if user_id == actor_user_id and (next_status == "inactive" or next_role == "user"):
            raise PermissionError("You cannot remove your own admin access from the admin dashboard.")
        if str(user.get("role")) == "admin" and self.store.count_active_admins() <= 1:
            if next_role == "user" or next_status == "inactive":
                raise PermissionError("At least one active admin must remain.")
        updated = user
        if next_role:
            updated = self.store.update_user_role(user_id=user_id, role=next_role) or updated
        if next_status:
            updated = self.store.update_user_status(user_id=user_id, status=next_status) or updated
        return AuthService.public_user(updated)


def build_landing_analytics(events: list[dict[str, Any]]) -> dict[str, Any]:
    landing_events = [event for event in events if str(event.get("name", "")).startswith(("landing_", "auth_signup", "auth_login", "pricing_viewed"))]
    by_country: dict[str, int] = {}
    section_views: dict[str, int] = {}
    cta_clicks: dict[str, int] = {}
    trend: dict[str, int] = {}
    totals = {
        "landing_page_visits": 0,
        "pricing_views": 0,
        "features_views": 0,
        "examples_views": 0,
        "faq_views": 0,
        "login_views": 0,
        "signup_views": 0,
        "cta_clicks": 0,
        "login_starts": 0,
        "login_completions": 0,
        "signup_starts": 0,
        "signup_completions": 0,
    }
    for event in landing_events:
        name = str(event.get("name") or "")
        properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}
        context = event.get("context") if isinstance(event.get("context"), dict) else {}
        country = str(context.get("visitor_country") or properties.get("country") or "Unknown")[:24]
        by_country[country] = by_country.get(country, 0) + 1
        occurred = str(event.get("occurred_at_utc") or "")[:10] or "unknown"
        trend[occurred] = trend.get(occurred, 0) + 1
        section = str(properties.get("section") or properties.get("target_section") or "")
        cta = str(properties.get("cta") or properties.get("target") or "")
        if name == "landing_page_view":
            totals["landing_page_visits"] += 1
        is_pricing_interest = name == "pricing_viewed" or section == "pricing"
        if is_pricing_interest:
            totals["pricing_views"] += 1
        if section:
            section_views[section] = section_views.get(section, 0) + 1
            key = f"{section}_views"
            if key in totals and not (section == "pricing" and is_pricing_interest):
                totals[key] += 1
        if name == "landing_cta_clicked":
            totals["cta_clicks"] += 1
            label = cta or section or "unknown"
            cta_clicks[label] = cta_clicks.get(label, 0) + 1
        if name == "auth_login_started":
            totals["login_starts"] += 1
        if name == "auth_login_completed":
            totals["login_completions"] += 1
        if name == "auth_signup_started":
            totals["signup_starts"] += 1
        if name == "auth_signup_completed":
            totals["signup_completions"] += 1
    signup_rate = totals["signup_completions"] / totals["signup_starts"] if totals["signup_starts"] else 0.0
    login_rate = totals["login_completions"] / totals["login_starts"] if totals["login_starts"] else 0.0
    return {
        "totals": totals,
        "conversion_rates": {"signup": signup_rate, "login": login_rate},
        "visitors_by_country": by_country,
        "section_views": section_views,
        "cta_clicks": cta_clicks,
        "traffic_trend": [{"date": date, "visits": visits} for date, visits in sorted(trend.items())],
        "recent_events": landing_events[:50],
    }


def build_lineage(*, request: dict[str, Any], artifact_dir: str | None, settings: BackendSettings) -> dict[str, Any]:
    parameters = request.get("parameters") if isinstance(request.get("parameters"), dict) else {}
    return {
        "pipeline": request.get("pipeline"),
        "symbols": request.get("symbols", []),
        "date_range": {"start": request.get("start"), "end": request.get("end"), "interval": request.get("interval")},
        "validation": {
            "train_bars": request.get("train_bars"),
            "test_bars": request.get("test_bars"),
            "purge_bars": request.get("purge_bars"),
            "pbo_partitions": request.get("pbo_partitions"),
        },
        "parameters": parameters,
        "datasets": {
            "price_cache_dir": str(settings.price_cache_dir),
            "sentiment_file": parameters.get("daily_sentiment_file"),
            "event_file": request.get("event_file"),
            "sector_map_path": request.get("sector_map_path"),
        },
        "artifact_dir": artifact_dir,
    }


def build_readiness(*, summary: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, value: Any, passed: bool, target: str) -> None:
        checks.append({"name": name, "value": value, "passed": bool(passed), "target": target})

    sharpe = as_float(summary.get("sharpe"))
    dsr = as_float(validation.get("dsr") or summary.get("dsr"))
    pbo = as_float(validation.get("pbo") or summary.get("pbo"))
    drawdown = as_float(summary.get("max_drawdown"))
    turnover = as_float(summary.get("avg_turnover"))
    folds = as_float(summary.get("folds"))
    add("Sharpe after costs", sharpe, sharpe is not None and sharpe >= 1.0, ">= 1.0")
    add("Deflated Sharpe", dsr, dsr is not None and dsr >= 0.60, ">= 0.60")
    add("PBO", pbo, pbo is not None and pbo <= 0.30, "<= 0.30")
    add("Max drawdown", drawdown, drawdown is not None and drawdown >= -0.25, ">= -25%")
    add("Turnover", turnover, turnover is not None and turnover <= 1.50, "<= 150%")
    add("Walk-forward folds", folds, folds is not None and folds >= 3, ">= 3")
    passed = sum(1 for check in checks if check["passed"])
    score = round(100 * passed / max(len(checks), 1))
    if score >= 80:
        verdict = "paper_candidate"
    elif score >= 50:
        verdict = "research_more"
    else:
        verdict = "reject_or_redesign"
    return {"score": score, "verdict": verdict, "passed_checks": passed, "total_checks": len(checks), "checks": checks}


def as_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def frame_points(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    if "net_return" in frame.columns:
        equity = (1.0 + frame["net_return"].fillna(0.0)).cumprod()
        drawdown = equity / equity.cummax() - 1.0
        working = frame.assign(equity=equity, drawdown=drawdown)
    else:
        working = frame
    points = []
    for timestamp, row in working.iterrows():
        item = row.to_dict()
        item["timestamp"] = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
        points.append(_json_ready(item))
    return points


def sentiment_snapshot(*, request: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    parameters = request.get("parameters") if isinstance(request.get("parameters"), dict) else {}
    sentiment_file = parameters.get("daily_sentiment_file")
    visuals = []
    if artifact_dir.exists():
        visuals = [str(path) for path in (artifact_dir / "visuals").glob("*sentiment*") if path.exists()]
    return {
        "daily_sentiment_file": sentiment_file,
        "news_providers": parameters.get("news_provider_names", []),
        "visuals": visuals,
        "explanation": "Sentiment is recorded as an overlay dataset, not a guarantee of future returns.",
    }


def warning_snapshot(strategy: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if float(strategy.get("gross_exposure_ratio") or 0.0) > 1.2:
        warnings.append("Gross exposure is above 120%; verify leverage and margin assumptions.")
    if float(strategy.get("daily_pnl") or 0.0) < 0:
        warnings.append("Latest fake-money PnL is negative; compare against the benchmark and expected drawdown.")
    if int(strategy.get("trade_count") or 0) == 0:
        warnings.append("No latest trades were generated; confirm signal availability and thresholds.")
    diagnostics = strategy.get("diagnostics") if isinstance(strategy.get("diagnostics"), dict) else {}
    if diagnostics.get("status"):
        warnings.append(f"Strategy diagnostic status: {diagnostics['status']}")
    return warnings
