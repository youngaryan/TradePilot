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
from .schemas import ApiKeyCreateRequest, BillingCheckoutRequest


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

    def authenticate(self, *, token: str, organization_id: str | None = None) -> RequestContext:
        session = self.store.get_auth_session(token=token)
        if session is None:
            raise ValueError("Authentication required.")
        user = self.store.get_user_by_id(str(session["user_id"]))
        if user is None:
            raise ValueError("Authentication required.")
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
        return {"id": user["id"], "email": user["email"], "display_name": user["display_name"]}


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
