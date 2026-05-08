from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import base64
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import struct
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from ..platform import build_metadata_store
from .config import BackendSettings
from .email import EmailService
from .quotas import DEFAULT_QUOTAS
from .redaction import redact_paths
from .schemas import ApiKeyCreateRequest, BillingCheckoutRequest, SignupRequest
from .storage import ArtifactReference, build_artifact_storage


DEMO_EMAIL = "demo@quantops.local"
DEMO_PASSWORD = "quantops-demo"
SESSION_COOKIE_NAME = "quantops_session"
CSRF_COOKIE_NAME = "quantops_csrf"
MFA_COOKIE_NAME = "quantops_mfa"


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


def verify_password(password: str, stored_hash: str, *, allow_demo_passwords: bool = True) -> bool:
    if allow_demo_passwords and stored_hash == "demo-password-hash":
        return password == DEMO_PASSWORD
    if allow_demo_passwords and stored_hash == "demo-user-password-hash":
        return password == "quantops-user"
    try:
        algorithm, iterations, salt, expected = stored_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations))
    return hmac.compare_digest(base64.b64encode(digest).decode("ascii"), expected)


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _totp_digest(secret: str, counter: int, *, digits: int = 6) -> str:
    padded = secret.upper() + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    message = struct.pack(">Q", counter)
    digest = hmac.new(key, message, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def totp_code(secret: str, *, for_time: int | None = None, step_seconds: int = 30) -> str:
    return _totp_digest(secret, int((for_time or time.time()) // step_seconds))


def verify_totp_code(secret: str, code: str, *, window: int = 1) -> bool:
    normalized = "".join(ch for ch in str(code) if ch.isdigit())
    if len(normalized) != 6:
        return False
    current_counter = int(time.time() // 30)
    for offset in range(-window, window + 1):
        if hmac.compare_digest(_totp_digest(secret, current_counter + offset), normalized):
            return True
    return False


@dataclass(frozen=True)
class RequestContext:
    user: dict[str, Any]
    organization_id: str


class AuthService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.store = build_metadata_store(settings)
        self.email = EmailService(settings)

    def login(self, *, email: str, password: str) -> dict[str, Any]:
        user = self.store.get_user_by_email(email)
        if user is None or not verify_password(
            password,
            str(user.get("password_hash", "")),
            allow_demo_passwords=self.settings.enable_demo_accounts and not self.settings.is_production,
        ):
            raise ValueError("Invalid email or password.")
        if str(user.get("status") or "active").lower() != "active":
            raise PermissionError("This account has been deactivated. Contact an administrator.")
        if self.settings.is_production and not user.get("email_verified_at_utc"):
            self.request_email_verification(email=str(user["email"]))
            raise PermissionError("Email verification is required before login. We sent a fresh verification link.")
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(UTC) + timedelta(hours=self.settings.session_ttl_hours)).isoformat().replace("+00:00", "Z")
        self.store.create_auth_session(user_id=str(user["id"]), token=token, expires_at_utc=expires_at)
        organizations = self.store.list_organizations_for_user(user_id=str(user["id"]))
        return {
            "session_token": token,
            "csrf_token": self.csrf_token_for_session(token),
            "expires_at_utc": expires_at,
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
        self.request_email_verification(email=email)
        if self.settings.is_production:
            user = self.store.get_user_by_email(email) or {}
            return {
                "status": "email_verification_required",
                "message": "Check your email to verify your account before logging in.",
                "user": self.public_user(user) if user else None,
                "organizations": [],
                "active_organization_id": None,
            }
        return self.login(email=email, password=request.password)

    def authenticate(self, *, token: str, organization_id: str | None = None) -> RequestContext:
        if token.startswith("qops_"):
            return self.authenticate_machine_key(token=token, organization_id=organization_id)
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

    def authenticate_machine_key(self, *, token: str, organization_id: str | None = None) -> RequestContext:
        api_key = self.store.get_api_key_by_token_hash(token_hash=self.store.hash_token(token))
        if api_key is None:
            raise ValueError("Authentication required.")
        key_org = str(api_key.get("organization_id") or "")
        if not key_org:
            raise PermissionError("API key is not scoped to a workspace.")
        if organization_id and organization_id != key_org:
            raise PermissionError("API key cannot access the requested workspace.")
        machine_user = {
            "id": f"api_key:{api_key.get('id')}",
            "email": f"{api_key.get('provider', 'machine')}@machine.quantops.local",
            "display_name": str(api_key.get("name") or "Machine API key"),
            "role": "user",
            "status": "active",
            "machine": True,
            "scopes": api_key.get("scopes", []),
        }
        return RequestContext(user=machine_user, organization_id=key_org)

    def me(self, *, token: str, organization_id: str | None = None) -> dict[str, Any]:
        context = self.authenticate(token=token, organization_id=organization_id)
        organizations = self.store.list_organizations_for_user(user_id=str(context.user["id"]))
        return {
            "user": context.user,
            "organizations": organizations,
            "active_organization_id": context.organization_id,
            "csrf_token": self.csrf_token_for_session(token),
        }

    def logout(self, *, token: str) -> None:
        self.store.delete_auth_session(token=token)

    def csrf_token_for_session(self, token: str) -> str:
        return hmac.new(
            self.settings.csrf_secret.encode("utf-8"),
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_csrf_token(self, *, session_token: str, csrf_token: str | None) -> bool:
        if not csrf_token:
            return False
        return hmac.compare_digest(self.csrf_token_for_session(session_token), csrf_token)

    def mfa_cookie_for_session(self, *, session_token: str, user_id: str) -> str:
        payload = f"{session_token}:{user_id}:admin-mfa"
        return hmac.new(
            self.settings.session_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_mfa_cookie(self, *, session_token: str, user_id: str, cookie_value: str | None) -> bool:
        if not cookie_value:
            return False
        return hmac.compare_digest(self.mfa_cookie_for_session(session_token=session_token, user_id=user_id), cookie_value)

    def request_email_verification(self, *, email: str) -> dict[str, Any]:
        user = self.store.get_user_by_email(email)
        if user is None:
            return {"status": "accepted"}
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(UTC) + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
        self.store.create_auth_token(user_id=str(user["id"]), purpose="email_verification", token=token, expires_at_utc=expires_at)
        verification_url = f"{self.settings.app_base_url.rstrip('/')}/verify-email?token={token}"
        delivery = self.email.send(
            to_email=str(user["email"]),
            subject="Verify your QuantOps account",
            text=(
                "Welcome to QuantOps.\n\n"
                "Verify your email address to activate your account:\n"
                f"{verification_url}\n\n"
                "If you did not create this account, you can ignore this email."
            ),
            metadata={"purpose": "email_verification", "user_id": user["id"], "expires_at_utc": expires_at},
        )
        return {"status": "accepted", "delivery": delivery.__dict__}

    def verify_email(self, *, token: str) -> dict[str, Any]:
        consumed = self.store.consume_auth_token(purpose="email_verification", token=token)
        if consumed is None:
            raise ValueError("Verification link is invalid or expired.")
        user = self.store.mark_email_verified(user_id=str(consumed["user_id"]))
        return {"status": "verified", "user": self.public_user(user)} if user else {"status": "verified"}

    def request_password_reset(self, *, email: str) -> dict[str, str]:
        user = self.store.get_user_by_email(email)
        if user is not None and str(user.get("status") or "active").lower() == "active":
            token = secrets.token_urlsafe(32)
            expires_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            self.store.create_auth_token(user_id=str(user["id"]), purpose="password_reset", token=token, expires_at_utc=expires_at)
            reset_url = f"{self.settings.app_base_url.rstrip('/')}/password-reset?token={token}"
            self.email.send(
                to_email=str(user["email"]),
                subject="Reset your QuantOps password",
                text=(
                    "Use the link below to reset your QuantOps password:\n"
                    f"{reset_url}\n\n"
                    "This link expires in 1 hour. If you did not request it, you can ignore this email."
                ),
                metadata={"purpose": "password_reset", "user_id": user["id"], "expires_at_utc": expires_at},
            )
        return {"status": "accepted", "message": "If the account exists, reset instructions will be sent."}

    def confirm_password_reset(self, *, token: str, new_password: str) -> dict[str, str]:
        if len(new_password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        consumed = self.store.consume_auth_token(purpose="password_reset", token=token)
        if consumed is None:
            raise ValueError("Password reset link is invalid or expired.")
        self.store.update_user_password(user_id=str(consumed["user_id"]), password_hash=hash_password(new_password))
        return {"status": "updated", "message": "Password updated. Please log in again."}

    def setup_mfa(self, *, user_id: str) -> dict[str, Any]:
        user = self.store.get_user_by_id(user_id)
        if user is None:
            raise ValueError("User not found.")
        secret = str(user.get("mfa_secret") or generate_totp_secret())
        self.store.set_user_mfa_secret(user_id=user_id, secret=secret, enabled=bool(user.get("mfa_enabled")))
        issuer = "QuantOps"
        account = str(user.get("email") or user_id)
        otpauth_url = f"otpauth://totp/{issuer}:{account}?secret={secret}&issuer={issuer}&digits=6&period=30"
        return {
            "status": "ready",
            "method": "totp",
            "secret": secret,
            "otpauth_url": otpauth_url,
            "enabled": bool(user.get("mfa_enabled")),
        }

    def verify_mfa_code(self, *, user_id: str, code: str) -> dict[str, Any]:
        user = self.store.get_user_by_id(user_id)
        secret = str((user or {}).get("mfa_secret") or "")
        if not user or not secret or not verify_totp_code(secret, code):
            raise PermissionError("Invalid MFA code.")
        self.store.set_user_mfa_secret(user_id=user_id, secret=secret, enabled=True)
        return {"status": "verified", "method": "totp"}

    @staticmethod
    def public_user(user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "role": user.get("role", "user"),
            "status": user.get("status", "active"),
            "email_verified_at_utc": user.get("email_verified_at_utc"),
            "mfa_enabled": bool(user.get("mfa_enabled")),
        }


class SaaSService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.store = build_metadata_store(settings)
        self.artifact_storage = build_artifact_storage(settings)

    def workspace_payload(self, *, organization_id: str) -> dict[str, Any]:
        self.sync_default_datasets(organization_id=organization_id)
        datasets = self.store.list_datasets(organization_id=organization_id)
        if self.settings.is_production:
            datasets = [{**dataset, "path": None} for dataset in datasets]
        return {
            "organization_id": organization_id,
            "projects": self.store.list_projects(organization_id=organization_id),
            "subscription": self.store.get_subscription(organization_id=organization_id),
            "datasets": datasets,
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
        if request.secret and self.settings.is_production:
            raise ValueError("Production rejects raw API secrets. Use a secret_ref or generate a scoped machine API key.")
        scopes = sorted({str(scope).strip().lower() for scope in request.scopes if str(scope).strip()}) or ["read"]
        generated_token: str | None = None
        token_hash: str | None = None
        secret_for_masking = request.secret
        if not request.secret and not request.secret_ref:
            generated_token = f"qops_{secrets.token_urlsafe(32)}"
            token_hash = self.store.hash_token(generated_token)
            secret_for_masking = generated_token
        record = self.store.create_api_key_metadata(
            organization_id=organization_id,
            name=request.name,
            provider=request.provider,
            secret=secret_for_masking,
            secret_ref=request.secret_ref,
            token_hash=token_hash,
            scopes=scopes,
        )
        if generated_token:
            record["token"] = generated_token
            record["message"] = "Store this machine API key now. It will not be shown again."
        return record

    def export_account(self, *, context: RequestContext) -> dict[str, Any]:
        organizations = self.store.list_organizations_for_user(user_id=str(context.user["id"]))
        return {
            "exported_at_utc": utc_now_iso(),
            "user": context.user,
            "organizations": organizations,
            "active_organization_id": context.organization_id,
            "workspace": self.workspace_payload(organization_id=context.organization_id),
            "audit_note": "Export includes metadata visible to this workspace. Heavy artifacts remain available through tenant artifact IDs.",
        }

    def delete_account(self, *, context: RequestContext) -> dict[str, Any]:
        user_id = str(context.user["id"])
        if str(context.user.get("role") or "user") == "admin" and self.store.count_active_admins() <= 1:
            raise PermissionError("At least one active admin must remain before this account can be deleted.")
        updated = self.store.update_user_status(user_id=user_id, status="inactive")
        self.store.record_audit_log(
            action="account.deleted",
            organization_id=context.organization_id,
            actor_user_id=user_id,
            target_type="user",
            target_id=user_id,
            metadata={"method": "self_service_soft_delete"},
        )
        return {"status": "deactivated", "user": AuthService.public_user(updated or context.user)}

    def sync_default_datasets(self, *, organization_id: str) -> None:
        if self.settings.is_production:
            # Production datasets are registered by the worker after publishing
            # tenant-scoped artifacts. Request-time filesystem scans would break
            # isolation and fail when API/worker containers do not share disks.
            return
        sentiment_path = (
            self.settings.sentiment_cache_dir / "shadow" / "daily_sentiment.parquet"
        )
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
        experiments = self.store.list_experiments(organization_id=organization_id, limit=50)
        return redact_paths(experiments) if self.settings.is_production else experiments

    def get_dataset(self, *, organization_id: str, dataset_id: str) -> dict[str, Any] | None:
        self.sync_default_datasets(organization_id=organization_id)
        dataset = self.store.get_dataset(organization_id=organization_id, dataset_id=dataset_id)
        if dataset is not None and self.settings.is_production:
            dataset = {**dataset, "path": None}
        return dataset

    def get_artifact(self, *, organization_id: str, artifact_id: str) -> dict[str, Any] | None:
        artifact = self.store.get_artifact(organization_id=organization_id, artifact_id=artifact_id)
        if artifact is not None and self.settings.is_production:
            artifact = {**artifact, "uri": None, "storage_key": None, "key": None}
        return artifact

    def get_experiment(self, *, organization_id: str, experiment_id: str) -> dict[str, Any] | None:
        self.sync_experiment_runs(organization_id=organization_id)
        experiment = self.store.get_experiment(organization_id=organization_id, experiment_id=experiment_id)
        if experiment is not None:
            detail = self.enrich_experiment_detail(experiment, organization_id=organization_id)
            return redact_paths(detail) if self.settings.is_production else detail
        return None

    def sync_experiment_runs(self, *, organization_id: str) -> None:
        if self.settings.is_production:
            return
        for run in self.store.list_experiment_runs(kind="backtest", organization_id=organization_id):
            artifact_dir = Path(str(run.get("artifact_dir") or ""))
            summary = dict(run.get("summary") or {})
            validation = _json_file(artifact_dir / "validation.json") if artifact_dir.exists() else {}
            experiment_id = str(summary.get("experiment_id") or run["id"])
            if self.store.get_experiment(organization_id=organization_id, experiment_id=experiment_id):
                continue
            matched_job: dict[str, Any] | None = None
            for job in self.store.list_jobs(kind="backtest"):
                result = job.get("result") or {}
                if result.get("artifact_dir") == str(artifact_dir):
                    matched_job = job
                    break
            if matched_job is None or str(matched_job.get("organization_id") or "") != organization_id:
                continue
            request = dict(matched_job.get("request") or {})
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

    def _materialize_experiment_artifact(self, *, organization_id: str, experiment: dict[str, Any]) -> Path | None:
        summary = experiment.get("summary") if isinstance(experiment.get("summary"), dict) else {}
        artifact_id = str(summary.get("artifact_id") or "")
        if not artifact_id:
            return None
        artifact = self.store.get_artifact(organization_id=organization_id, artifact_id=artifact_id)
        if not artifact:
            return None
        reference = ArtifactReference(
            provider=str(artifact.get("provider") or "local"),
            key=str(artifact.get("storage_key") or artifact.get("key") or ""),
            uri=str(artifact.get("uri") or ""),
            file_count=int(artifact.get("file_count", 0) or 0),
            byte_count=int(artifact.get("byte_count", 0) or 0),
        )
        target = self.settings.backtest_artifact_root / "materialized" / organization_id / str(experiment.get("id") or artifact_id)
        return self.artifact_storage.materialize_directory(reference, target)

    def enrich_experiment_detail(self, experiment: dict[str, Any], *, organization_id: str) -> dict[str, Any]:
        if self.settings.is_production:
            materialized = self._materialize_experiment_artifact(organization_id=organization_id, experiment=experiment)
            artifact_dir = materialized if materialized is not None else Path("")
        else:
            artifact_dir = Path(str(experiment.get("artifact_dir") or ""))
        if artifact_dir.exists():
            files = sorted(path for path in artifact_dir.rglob("*") if path.is_file() and path.name != ".DS_Store")
            experiment["artifact_files"] = sorted(
                str(path.relative_to(artifact_dir)) if self.settings.is_production else str(path)
                for path in files
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
        agents = self.store.list_paper_agents(organization_id=organization_id)
        return redact_paths(agents) if self.settings.is_production else agents

    def get_paper_agent(self, *, organization_id: str, agent_id: str) -> dict[str, Any] | None:
        agent = self.store.get_paper_agent(organization_id=organization_id, agent_id=agent_id)
        return redact_paths(agent) if agent is not None and self.settings.is_production else agent


class BillingService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.store = build_metadata_store(settings)

    def pricing(self, *, organization_id: str | None = None) -> dict[str, Any]:
        subscription = self.store.get_subscription(organization_id=organization_id) if organization_id else None
        configured_prices = self.settings.stripe_plan_price_ids
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
                "stripe_configured": bool(configured_prices.get("pro")),
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
                "stripe_configured": bool(configured_prices.get("team")),
                "cta": "Contact sales workflow",
            },
        ]
        return {"plans": plans, "subscription": subscription}

    def status(self, *, organization_id: str, role: object = "user") -> dict[str, Any]:
        subscription = self.store.get_subscription(organization_id=organization_id)
        allowed_statuses = {"active"} | ({"trialing"} if self.settings.allow_trial_entitlements else set())
        subscription_premium = (
            subscription is not None
            and str(subscription.get("plan")) in {"pro", "team", "enterprise", "pro_trial"}
            and str(subscription.get("status")) in allowed_statuses
        )
        admin_override = str(role or "user").lower() == "admin"
        return {
            "subscription": subscription,
            "premium": admin_override or subscription_premium,
            "access": "admin" if admin_override else "subscription",
            "pricing": self.pricing(organization_id=organization_id)["plans"],
        }

    def checkout(self, *, organization_id: str, request: BillingCheckoutRequest) -> dict[str, Any]:
        if request.price_id and self.settings.is_production:
            raise ValueError("Client-supplied Stripe price IDs are not accepted in production.")
        plan = str(request.plan or "pro").lower()
        price_id = self.settings.stripe_plan_price_ids.get(plan)
        if plan not in {"pro", "team"}:
            raise ValueError("Checkout supports only server-owned paid plan ids: pro or team.")
        if not self.settings.stripe_secret_key or not price_id:
            if self.settings.is_production:
                raise RuntimeError(f"Stripe checkout is not configured for production plan '{plan}'.")
            subscription = self.store.get_subscription(organization_id=organization_id) or {}
            self.store.upsert_subscription(
                organization_id=organization_id,
                payload={**subscription, "plan": plan, "status": "trialing"},
            )
            return {
                "mode": "demo",
                "checkout_url": f"{self.settings.app_base_url}?billing=demo-checkout",
                "message": "Stripe is not configured. Set STRIPE_SECRET_KEY and plan-specific Stripe Price IDs to create real Checkout sessions.",
            }
        subscription = self.store.get_subscription(organization_id=organization_id) or {}
        success_url = self.settings.stripe_success_url or f"{self.settings.app_base_url}?billing=success"
        cancel_url = self.settings.stripe_cancel_url or f"{self.settings.app_base_url}?billing=cancelled"
        data = {
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "metadata[organization_id]": organization_id,
            "metadata[plan]": plan,
        }
        response = self._stripe_post("https://api.stripe.com/v1/checkout/sessions", data)
        return {"mode": "stripe", "checkout_url": response.get("url"), "stripe_session": response}

    def portal(self, *, organization_id: str, return_url: str | None = None) -> dict[str, Any]:
        subscription = self.store.get_subscription(organization_id=organization_id) or {}
        customer_id = subscription.get("stripe_customer_id")
        if not self.settings.stripe_secret_key or not customer_id:
            if self.settings.is_production:
                raise RuntimeError("Stripe Customer Portal is not available until the workspace has a synced Stripe customer.")
            return {
                "mode": "demo",
                "portal_url": f"{self.settings.app_base_url}?billing=demo-portal",
                "message": "Stripe customer portal needs STRIPE_SECRET_KEY and a synced stripe_customer_id.",
            }
        data = {"customer": customer_id, "return_url": return_url or self.settings.app_base_url}
        response = self._stripe_post("https://api.stripe.com/v1/billing_portal/sessions", data)
        return {"mode": "stripe", "portal_url": response.get("url"), "stripe_session": response}

    def sync_subscription(self, *, organization_id: str) -> dict[str, Any]:
        subscription = self.store.get_subscription(organization_id=organization_id) or {}
        stripe_subscription_id = str(subscription.get("stripe_subscription_id") or "")
        if not self.settings.stripe_secret_key or not stripe_subscription_id:
            if self.settings.is_production:
                raise RuntimeError("Stripe subscription sync requires STRIPE_SECRET_KEY and a synced subscription id.")
            return {"status": "skipped", "reason": "stripe_not_configured"}
        remote = self._stripe_get(f"https://api.stripe.com/v1/subscriptions/{stripe_subscription_id}")
        return self._upsert_subscription_from_stripe_object(
            organization_id=organization_id,
            data=remote,
            source="stripe_subscription_sync",
        )

    def webhook(self, *, payload: bytes, signature_header: str | None) -> dict[str, Any]:
        if not self.settings.stripe_webhook_secret:
            raise ValueError("STRIPE_WEBHOOK_SECRET is required before accepting Stripe webhooks.")
        if not self._verify_stripe_signature(payload=payload, signature_header=signature_header):
            raise PermissionError("Invalid Stripe webhook signature.")
        event = json.loads(payload.decode("utf-8"))
        event_id = str(event.get("id") or "")
        event_type = str(event.get("type") or "")
        if event_id:
            first_seen = self.store.record_stripe_event(event_id=event_id, event_type=event_type, payload=event)
            if not first_seen:
                return {"received": True, "updated": False, "duplicate": True, "event_type": event_type}
        data = event.get("data", {}).get("object", {}) if isinstance(event.get("data"), dict) else {}
        organization_id = (
            data.get("metadata", {}).get("organization_id")
            if isinstance(data.get("metadata"), dict)
            else None
        )
        if event_type == "checkout.session.completed" and organization_id:
            plan = str((data.get("metadata") or {}).get("plan") or "pro")
            self.store.upsert_subscription(
                organization_id=str(organization_id),
                payload={
                    "plan": plan,
                    "status": "active",
                    "stripe_customer_id": data.get("customer"),
                    "stripe_subscription_id": data.get("subscription"),
                    "usage": {"source": "stripe_checkout"},
                },
            )
            return {"received": True, "updated": True, "event_type": event_type}
        if event_type.startswith("customer.subscription."):
            subscription_id = data.get("id")
            organization_id = organization_id or self._organization_id_for_stripe_subscription(str(subscription_id or ""))
            if organization_id:
                if event_type == "customer.subscription.deleted":
                    data = {**data, "status": "canceled"}
                self._upsert_subscription_from_stripe_object(
                    organization_id=str(organization_id),
                    data=data,
                    source="stripe_subscription_webhook",
                )
                return {"received": True, "updated": True, "event_type": event_type}
        if event_type in {"invoice.payment_failed", "charge.refunded", "payment_intent.payment_failed"}:
            subscription_id = data.get("subscription")
            organization_id = organization_id or self._organization_id_for_stripe_subscription(str(subscription_id or ""))
            if organization_id:
                self.store.upsert_subscription(
                    organization_id=str(organization_id),
                    payload={
                        "plan": "pro",
                        "status": "past_due",
                        "stripe_customer_id": data.get("customer"),
                        "stripe_subscription_id": subscription_id,
                        "usage": {"source": event_type},
                    },
                )
                return {"received": True, "updated": True, "event_type": event_type}
        if event_type == "invoice.paid":
            subscription_id = data.get("subscription")
            organization_id = organization_id or self._organization_id_for_stripe_subscription(str(subscription_id or ""))
            if organization_id:
                self.store.upsert_subscription(
                    organization_id=str(organization_id),
                    payload={
                        "plan": "pro",
                        "status": "active",
                        "stripe_customer_id": data.get("customer"),
                        "stripe_subscription_id": subscription_id,
                        "usage": {"source": event_type},
                    },
                )
                return {"received": True, "updated": True, "event_type": event_type}
        return {"received": True, "updated": False, "event_type": event_type}

    def _plan_from_subscription_object(self, data: dict[str, Any]) -> str:
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        plan = str(metadata.get("plan") or "").lower()
        if plan in {"pro", "team", "enterprise"}:
            return plan
        price_id = None
        items = data.get("items") if isinstance(data.get("items"), dict) else {}
        rows = items.get("data") if isinstance(items.get("data"), list) else []
        if rows:
            price = rows[0].get("price") if isinstance(rows[0], dict) else {}
            price_id = price.get("id") if isinstance(price, dict) else None
        for configured_plan, configured_price in self.settings.stripe_plan_price_ids.items():
            if configured_price and configured_price == price_id:
                return configured_plan
        return "pro"

    def _upsert_subscription_from_stripe_object(self, *, organization_id: str, data: dict[str, Any], source: str) -> dict[str, Any]:
        period_end = data.get("current_period_end")
        current_period_end_utc = None
        if isinstance(period_end, (int, float)):
            current_period_end_utc = datetime.fromtimestamp(float(period_end), tz=UTC).isoformat().replace("+00:00", "Z")
        elif period_end:
            current_period_end_utc = str(period_end)
        status = str(data.get("status") or "active")
        if status in {"canceled", "unpaid", "incomplete_expired"}:
            status = "canceled"
        return self.store.upsert_subscription(
            organization_id=organization_id,
            payload={
                "plan": self._plan_from_subscription_object(data),
                "status": status,
                "stripe_customer_id": data.get("customer"),
                "stripe_subscription_id": data.get("id"),
                "current_period_end_utc": current_period_end_utc,
                "usage": {"source": source},
            },
        )

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

    def _stripe_get(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={"Authorization": f"Bearer {self.settings.stripe_secret_key}"},
        )
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))


class AdminService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.store = build_metadata_store(settings)

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
        self.store.record_audit_log(
            action="admin.user_updated",
            actor_user_id=actor_user_id,
            target_type="user",
            target_id=user_id,
            metadata={"role": next_role, "status": next_status},
        )
        return AuthService.public_user(updated)

    def audit_log(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list_audit_log(limit=limit)

    def system_health(self) -> dict[str, Any]:
        counts = self.store.counts()
        return {
            "status": "ok",
            "app_env": self.settings.app_env,
            "database": {"configured": bool(self.settings.database_url), "metadata_store": "sqlite" if not self.settings.database_url else "postgres"},
            "queue": {"redis_configured": bool(self.settings.redis_url), "in_process_jobs": self.settings.enable_in_process_jobs},
            "storage": {"s3_configured": bool(self.settings.s3_bucket and self.settings.s3_endpoint_url)},
            "stripe": {"configured": bool(self.settings.stripe_secret_key and self.settings.stripe_webhook_secret)},
            "counts": counts.__dict__,
        }

    def quotas(self) -> dict[str, Any]:
        return {
            "defaults": DEFAULT_QUOTAS,
            "source": "secure_v1_defaults",
        }

    def update_quotas(self, *, organization_id: str, payload: dict[str, Any], actor_user_id: str) -> dict[str, Any]:
        stored = self.store.upsert_organization_quotas(organization_id=organization_id, quotas=payload)
        self.store.record_audit_log(
            action="admin.quotas_updated",
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            target_type="organization",
            target_id=organization_id,
            metadata={"quotas": payload},
        )
        return {"organization_id": organization_id, "quotas": stored["quotas"], "status": "updated"}


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
