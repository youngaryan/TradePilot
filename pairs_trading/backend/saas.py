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
from threading import Lock
from typing import Any
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import pandas as pd
from cryptography.fernet import Fernet, InvalidToken

from ..platform import build_metadata_store
from ..platform.persistence import IdempotencyConflictError
from .config import BackendSettings
from .email import EmailService
from .job_queue import JOB_KINDS
from .quotas import DEFAULT_QUOTAS
from .readiness import check_any_role_instance_from_settings
from .redaction import redact_paths
from .schemas import ApiKeyCreateRequest, BillingCheckoutRequest, SignupRequest
from .storage import ArtifactReference, build_artifact_storage


DEMO_EMAIL = "demo@quantops.local"
DEMO_PASSWORD = "quantops-demo"
SESSION_COOKIE_NAME = "quantops_session"
CSRF_COOKIE_NAME = "quantops_csrf"
MFA_COOKIE_NAME = "quantops_mfa"
PASSWORD_HASH_ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 12
_LOCAL_AUTH_ATTEMPTS: dict[str, tuple[int, float]] = {}
_LOCAL_AUTH_ATTEMPTS_LOCK = Lock()


class AuthRateLimitError(RuntimeError):
    pass


def validate_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > 256:
        raise ValueError("Password must be 256 characters or fewer.")


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
    validate_password_policy(password)
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PASSWORD_HASH_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${base64.b64encode(digest).decode('ascii')}"


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
    try:
        work_factor = int(iterations)
    except ValueError:
        return False
    if work_factor < 1 or work_factor > 10_000_000:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), work_factor)
    return hmac.compare_digest(base64.b64encode(digest).decode("ascii"), expected)


def password_hash_needs_rehash(stored_hash: str) -> bool:
    try:
        algorithm, iterations, _, _ = stored_hash.split("$", 3)
        return algorithm != "pbkdf2_sha256" or int(iterations) < PASSWORD_HASH_ITERATIONS
    except (TypeError, ValueError):
        return True


_DUMMY_PASSWORD_SALT = "quantops-nonexistent-account"
_DUMMY_PASSWORD_DIGEST = base64.b64encode(
    hashlib.pbkdf2_hmac(
        "sha256",
        b"nonexistent-account-password",
        _DUMMY_PASSWORD_SALT.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    )
).decode("ascii")
_DUMMY_PASSWORD_HASH = f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${_DUMMY_PASSWORD_SALT}${_DUMMY_PASSWORD_DIGEST}"


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


def matching_totp_counter(secret: str, code: str, *, window: int = 1, for_time: int | None = None) -> int | None:
    normalized = "".join(ch for ch in str(code) if ch.isdigit())
    if len(normalized) != 6:
        return None
    current_counter = int((for_time or time.time()) // 30)
    for offset in range(-window, window + 1):
        counter = current_counter + offset
        if hmac.compare_digest(_totp_digest(secret, counter), normalized):
            return counter
    return None


def verify_totp_code(secret: str, code: str, *, window: int = 1) -> bool:
    return matching_totp_counter(secret, code, window=window) is not None


class MfaSecretCipher:
    PREFIX = "enc:v1:"

    def __init__(self, settings: BackendSettings) -> None:
        source = settings.mfa_encryption_key
        if not source:
            if settings.is_production:
                raise RuntimeError("MFA encryption is not configured.")
            source = f"development-mfa:{settings.session_secret}"
        derived = hashlib.sha256(str(source).encode("utf-8")).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(derived))
        self.production = settings.is_production

    def encrypt(self, secret: str) -> str:
        return self.PREFIX + self.fernet.encrypt(secret.encode("utf-8")).decode("ascii")

    def decrypt(self, stored: str) -> tuple[str, bool]:
        if not stored.startswith(self.PREFIX):
            if self.production:
                raise RuntimeError("Stored MFA credentials are not encrypted.")
            return stored, True
        try:
            return self.fernet.decrypt(stored[len(self.PREFIX) :].encode("ascii")).decode("utf-8"), False
        except (InvalidToken, ValueError, UnicodeError):
            raise RuntimeError("Stored MFA credentials cannot be decrypted with the configured key.") from None


class AuthAttemptLimiter:
    _ADMIT_SCRIPT = """
    local blocked = 0
    for i, key in ipairs(KEYS) do
      local value = redis.call('INCR', key)
      if value == 1 then redis.call('EXPIRE', key, ARGV[1]) end
      if value > tonumber(ARGV[2]) then blocked = 1 end
    end
    return blocked
    """
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings

    def _keys(self, *, action: str, identity: str, client_id: str | None) -> tuple[str, str]:
        normalized = identity.casefold().strip()
        digest = lambda value: hmac.new(
            self.settings.session_secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return (
            f"quantops:auth:{action}:account:{digest(normalized)}",
            f"quantops:auth:{action}:client:{digest(normalized + ':' + str(client_id or 'unknown'))}",
        )

    def _redis(self):
        if not self.settings.redis_url:
            return None
        try:
            from redis import Redis

            return Redis.from_url(self.settings.redis_url, decode_responses=True, socket_timeout=2.0)
        except Exception:
            if self.settings.is_production:
                raise AuthRateLimitError("Authentication service is temporarily unavailable.") from None
            return None

    def admit(self, *, action: str, identity: str, client_id: str | None = None) -> None:
        keys = self._keys(action=action, identity=identity, client_id=client_id)
        redis_client = self._redis()
        if redis_client is not None:
            try:
                blocked = redis_client.eval(
                    self._ADMIT_SCRIPT,
                    len(keys),
                    *keys,
                    self.settings.auth_attempt_window_seconds,
                    self.settings.auth_attempt_max_failures,
                )
                if int(blocked or 0) != 0:
                    raise AuthRateLimitError("Too many authentication attempts. Try again later.")
                return
            except AuthRateLimitError:
                raise
            except Exception:
                if self.settings.is_production:
                    raise AuthRateLimitError("Authentication service is temporarily unavailable.") from None
        if self.settings.is_production:
            raise AuthRateLimitError("Authentication service is temporarily unavailable.")
        now = time.monotonic()
        with _LOCAL_AUTH_ATTEMPTS_LOCK:
            for key in keys:
                count, expires = _LOCAL_AUTH_ATTEMPTS.get(key, (0, 0.0))
                next_count = (count if expires > now else 0) + 1
                _LOCAL_AUTH_ATTEMPTS[key] = (next_count, now + self.settings.auth_attempt_window_seconds)
                if next_count > self.settings.auth_attempt_max_failures:
                    raise AuthRateLimitError("Too many authentication attempts. Try again later.")

    def success(self, *, action: str, identity: str, client_id: str | None = None) -> None:
        keys = self._keys(action=action, identity=identity, client_id=client_id)
        redis_client = self._redis()
        if redis_client is not None:
            try:
                redis_client.delete(*keys)
            except Exception:
                if self.settings.is_production:
                    raise AuthRateLimitError("Authentication service is temporarily unavailable.") from None
        with _LOCAL_AUTH_ATTEMPTS_LOCK:
            for key in keys:
                _LOCAL_AUTH_ATTEMPTS.pop(key, None)


@dataclass(frozen=True)
class RequestContext:
    user: dict[str, Any]
    organization_id: str


class AuthService:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.store = build_metadata_store(settings)
        self.email = EmailService(settings)
        self.mfa_cipher = MfaSecretCipher(settings)
        self.attempts = AuthAttemptLimiter(settings)

    def _audit(self, *, user_id: str, action: str, metadata: dict[str, Any] | None = None) -> None:
        organization_id = self.store.get_default_organization_id(user_id=user_id)
        if organization_id is None:
            return
        self.store.record_audit_log(
            organization_id=str(organization_id),
            actor_user_id=user_id,
            action=action,
            target_type="user",
            target_id=user_id,
            metadata=metadata or {},
        )

    def _mfa_secret(self, *, user: dict[str, Any], field: str) -> str:
        stored = str(user.get(field) or "")
        if not stored:
            return ""
        secret, needs_upgrade = self.mfa_cipher.decrypt(stored)
        if needs_upgrade:
            encrypted = self.mfa_cipher.encrypt(secret)
            if field == "mfa_pending_secret":
                self.store.set_user_mfa_pending_secret(user_id=str(user["id"]), secret=encrypted)
            else:
                self.store.set_user_mfa_secret(
                    user_id=str(user["id"]),
                    secret=encrypted,
                    enabled=bool(user.get("mfa_enabled")),
                )
        return secret

    def login(self, *, email: str, password: str, client_id: str | None = None) -> dict[str, Any]:
        self.attempts.admit(action="login", identity=email, client_id=client_id)
        user = self.store.get_user_by_email(email)
        password_valid = verify_password(
            password,
            str((user or {}).get("password_hash") or _DUMMY_PASSWORD_HASH),
            allow_demo_passwords=self.settings.enable_demo_accounts and not self.settings.is_production,
        )
        if user is None or not password_valid:
            raise ValueError("Invalid email or password.")
        if str(user.get("status") or "active").lower() != "active":
            raise ValueError("Invalid email or password.")
        if self.settings.is_production and not user.get("email_verified_at_utc"):
            raise ValueError("Invalid email or password.")
        if password_hash_needs_rehash(str(user.get("password_hash") or "")):
            upgraded = self.store.update_user_password(user_id=str(user["id"]), password_hash=hash_password(password))
            user = upgraded or user
        self.attempts.success(action="login", identity=email, client_id=client_id)
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(UTC) + timedelta(hours=self.settings.session_ttl_hours)).isoformat().replace("+00:00", "Z")
        self.store.create_auth_session(user_id=str(user["id"]), token=token, expires_at_utc=expires_at)
        self._audit(user_id=str(user["id"]), action="auth.login")
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
        validate_password_policy(request.password)
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
        session = self.store.get_auth_session(token=token)
        self.store.delete_auth_session(token=token)
        if session is not None:
            self._audit(user_id=str(session["user_id"]), action="auth.logout")

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

    def request_email_verification(self, *, email: str, client_id: str | None = None) -> dict[str, Any]:
        self.attempts.admit(action="email_verification", identity=email, client_id=client_id)
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
        del delivery
        return {"status": "accepted", "message": "If the account exists, verification instructions will be sent."}

    def verify_email(self, *, token: str) -> dict[str, Any]:
        consumed = self.store.consume_auth_token(purpose="email_verification", token=token)
        if consumed is None:
            raise ValueError("Verification link is invalid or expired.")
        user = self.store.mark_email_verified(user_id=str(consumed["user_id"]))
        self._audit(user_id=str(consumed["user_id"]), action="auth.email_verified")
        return {"status": "verified", "user": self.public_user(user)} if user else {"status": "verified"}

    def request_password_reset(self, *, email: str, client_id: str | None = None) -> dict[str, str]:
        self.attempts.admit(action="password_reset", identity=email, client_id=client_id)
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
        validate_password_policy(new_password)
        consumed = self.store.consume_auth_token(purpose="password_reset", token=token)
        if consumed is None:
            raise ValueError("Password reset link is invalid or expired.")
        self.store.update_user_password(user_id=str(consumed["user_id"]), password_hash=hash_password(new_password))
        self._audit(user_id=str(consumed["user_id"]), action="auth.password_reset_completed")
        return {"status": "updated", "message": "Password updated. Please log in again."}

    def setup_mfa(
        self,
        *,
        user_id: str,
        password: str,
        rotate: bool = False,
        current_code: str | None = None,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        self.attempts.admit(action="mfa_reauthentication", identity=user_id, client_id=client_id)
        user = self.store.get_user_by_id(user_id)
        if user is None:
            raise ValueError("User not found.")
        if not verify_password(
            password,
            str(user.get("password_hash") or ""),
            allow_demo_passwords=self.settings.enable_demo_accounts and not self.settings.is_production,
        ):
            raise PermissionError("Password reauthentication failed.")
        enabled = bool(user.get("mfa_enabled"))
        if enabled and not rotate:
            raise PermissionError("MFA is already enabled. Explicit rotation is required.")
        if rotate:
            if not enabled or not current_code:
                raise PermissionError("Current MFA verification is required for rotation.")
            current_secret = self._mfa_secret(user=user, field="mfa_secret")
            counter = matching_totp_counter(current_secret, current_code)
            if counter is None or self.store.advance_user_mfa_counter(user_id=user_id, counter=counter) is None:
                raise PermissionError("Current MFA verification failed or was already used.")
        secret = generate_totp_secret()
        self.store.set_user_mfa_pending_secret(user_id=user_id, secret=self.mfa_cipher.encrypt(secret))
        self.attempts.success(action="mfa_reauthentication", identity=user_id, client_id=client_id)
        self._audit(user_id=user_id, action="auth.mfa_rotation_started" if enabled else "auth.mfa_enrollment_started")
        issuer = "QuantOps"
        account = str(user.get("email") or user_id)
        otpauth_url = f"otpauth://totp/{issuer}:{account}?secret={secret}&issuer={issuer}&digits=6&period=30"
        return {
            "status": "ready",
            "method": "totp",
            "secret": secret,
            "otpauth_url": otpauth_url,
            "enabled": enabled,
            "rotation": enabled,
        }

    def verify_mfa_code(self, *, user_id: str, code: str, client_id: str | None = None) -> dict[str, Any]:
        self.attempts.admit(action="mfa_code", identity=user_id, client_id=client_id)
        user = self.store.get_user_by_id(user_id)
        if not user:
            raise PermissionError("Invalid MFA code.")
        pending_stored = str(user.get("mfa_pending_secret") or "")
        if pending_stored:
            pending_secret = self._mfa_secret(user=user, field="mfa_pending_secret")
            refreshed = self.store.get_user_by_id(user_id) or user
            pending_stored = str(refreshed.get("mfa_pending_secret") or pending_stored)
            counter = matching_totp_counter(pending_secret, code)
            if counter is not None:
                promoted = self.store.advance_user_mfa_counter(
                    user_id=user_id,
                    counter=counter,
                    pending_secret=pending_stored,
                    promote_pending=True,
                )
                if promoted is None:
                    raise PermissionError("Invalid or previously used MFA code.")
                self.attempts.success(action="mfa_code", identity=user_id, client_id=client_id)
                self._audit(user_id=user_id, action="auth.mfa_enabled_or_rotated")
                return {"status": "verified", "method": "totp", "sessions_revoked": True}
        secret = self._mfa_secret(user=user, field="mfa_secret")
        counter = matching_totp_counter(secret, code) if secret and bool(user.get("mfa_enabled")) else None
        if counter is None or self.store.advance_user_mfa_counter(user_id=user_id, counter=counter) is None:
            raise PermissionError("Invalid or previously used MFA code.")
        self.attempts.success(action="mfa_code", identity=user_id, client_id=client_id)
        self._audit(user_id=user_id, action="auth.mfa_verified")
        return {"status": "verified", "method": "totp", "sessions_revoked": False}

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

    def workspace_payload(self, *, organization_id: str, user_id: str | None = None) -> dict[str, Any]:
        self.sync_default_datasets(organization_id=organization_id)
        datasets = self.store.list_datasets(organization_id=organization_id)
        if self.settings.is_production:
            datasets = [{**dataset, "path": None} for dataset in datasets]
        reports = (
            self.store.list_market_research_reports(organization_id=organization_id, user_id=user_id, limit=10)
            if user_id
            else []
        )
        return {
            "organization_id": organization_id,
            "capabilities": self.settings.capabilities,
            "projects": self.store.list_projects(organization_id=organization_id),
            "subscription": self.store.get_subscription(organization_id=organization_id),
            "datasets": datasets,
            "api_keys": self.store.list_api_keys(organization_id=organization_id),
            "experiments": self.store.list_experiments(organization_id=organization_id, limit=20),
            "paper_agents": self.store.list_paper_agents(organization_id=organization_id),
            "market_research_reports": reports,
            "onboarding": self.onboarding_state(organization_id=organization_id),
        }

    def onboarding_state(self, *, organization_id: str) -> dict[str, Any]:
        projects = self.store.list_projects(organization_id=organization_id)
        datasets = self.store.list_datasets(organization_id=organization_id)
        experiments = self.store.list_experiments(organization_id=organization_id, limit=1)
        paper_agents = self.store.list_paper_agents(organization_id=organization_id)
        subscription = self.store.get_subscription(organization_id=organization_id) or {}
        paid_statuses = {"active"} | ({"trialing"} if self.settings.allow_trial_entitlements else set())
        billing_complete = (
            str(subscription.get("plan") or "free").lower() in {"pro", "team", "enterprise", "pro_trial"}
            and str(subscription.get("status") or "").lower() in paid_statuses
        )
        steps = [
            {"id": "project", "label": "Create or use a research project", "complete": bool(projects)},
            {"id": "dataset", "label": "Connect market/news data or use local cache", "complete": bool(datasets)},
            {"id": "backtest", "label": "Run a validated backtest", "complete": bool(experiments)},
            {"id": "paper", "label": "Deploy a fake-money paper agent", "complete": bool(paper_agents)},
            {"id": "billing", "label": "Review billing and usage limits", "complete": billing_complete},
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
            "workspace": self.workspace_payload(organization_id=context.organization_id, user_id=str(context.user["id"])),
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

    def list_market_research_reports(
        self,
        *,
        organization_id: str,
        user_id: str,
        search: str | None = None,
        ticker: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        reports = self.store.list_market_research_reports(
            organization_id=organization_id,
            user_id=user_id,
            search=search,
            ticker=ticker,
            status=status,
            limit=limit,
            offset=offset,
        )
        return [self._public_market_research_report(report, detail=False) for report in reports]

    def get_market_research_report(self, *, organization_id: str, user_id: str, report_id: str) -> dict[str, Any] | None:
        report = self.store.get_market_research_report(
            organization_id=organization_id,
            user_id=user_id,
            report_id=report_id,
        )
        return None if report is None else self._public_market_research_report(report, detail=True)

    def delete_market_research_report(self, *, organization_id: str, user_id: str, report_id: str) -> dict[str, Any] | None:
        deleted = self.store.soft_delete_market_research_report(
            organization_id=organization_id,
            user_id=user_id,
            report_id=report_id,
        )
        if deleted is None:
            return None
        self.store.record_audit_log(
            action="market_research_report.deleted",
            organization_id=organization_id,
            actor_user_id=user_id,
            target_type="market_research_report",
            target_id=report_id,
            metadata={"ticker": deleted.get("ticker"), "job_id": deleted.get("job_id")},
        )
        return self._public_market_research_report(deleted, detail=False)

    @staticmethod
    def _public_market_research_report(report: dict[str, Any], *, detail: bool) -> dict[str, Any]:
        provider_metadata = dict(report.get("provider_metadata") or {})
        for key in list(provider_metadata):
            lowered = key.lower()
            if any(fragment in lowered for fragment in ("prompt", "secret", "token", "api_key", "apikey", "password")) and key not in {
                "prompt_version",
                "agent_prompt_hashes",
            }:
                provider_metadata.pop(key, None)
        public = {
            "id": report.get("id"),
            "report_id": report.get("id"),
            "organization_id": report.get("organization_id"),
            "user_id": report.get("user_id"),
            "job_id": report.get("job_id"),
            "parent_report_id": report.get("parent_report_id"),
            "version": report.get("version"),
            "ticker": report.get("ticker"),
            "analysis_date": report.get("analysis_date"),
            "horizon": report.get("horizon"),
            "report_type": report.get("report_type"),
            "title": report.get("title"),
            "status": report.get("status"),
            "decision": report.get("decision"),
            "confidence": report.get("confidence"),
            "summary": report.get("summary"),
            "disclaimer": report.get("disclaimer"),
            "source_references": report.get("source_references", []),
            "provider_metadata": provider_metadata,
            "warnings": report.get("warnings", []),
            "artifact_id": report.get("artifact_id"),
            "error": report.get("error"),
            "created_at_utc": report.get("created_at_utc"),
            "updated_at_utc": report.get("updated_at_utc"),
            "completed_at_utc": report.get("completed_at_utc"),
            "deleted_at_utc": report.get("deleted_at_utc"),
        }
        if detail:
            public["context"] = report.get("context", {})
            public["report"] = report.get("report", {})
        return public

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

    def sync_paper_agents_from_dashboard(
        self,
        *,
        organization_id: str,
        deployment_id: str,
        payload: dict[str, Any],
        project_id: str | None = None,
    ) -> None:
        for strategy in payload.get("strategies", []) or []:
            name = str(strategy.get("name") or "paper_agent")
            warnings = warning_snapshot(strategy)
            self.store.upsert_paper_agent(
                organization_id=organization_id,
                payload={
                    "id": self.store.stable_id("agt", f"{organization_id}:{deployment_id}:{name}"),
                    "deployment_id": deployment_id,
                    "project_id": project_id,
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

    def list_paper_agents(self, *, organization_id: str, deployment_id: str | None = None) -> list[dict[str, Any]]:
        agents = self.store.list_paper_agents(organization_id=organization_id, deployment_id=deployment_id)
        return redact_paths(agents) if self.settings.is_production else agents

    def get_paper_agent(self, *, organization_id: str, agent_id: str, deployment_id: str | None = None) -> dict[str, Any] | None:
        agent = self.store.get_paper_agent(
            organization_id=organization_id,
            agent_id=agent_id,
            deployment_id=deployment_id,
        )
        return redact_paths(agent) if agent is not None and self.settings.is_production else agent


class BillingInputError(ValueError):
    pass


class BillingProviderError(RuntimeError):
    pass


class BillingWebhookProcessingError(RuntimeError):
    pass


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
        if request.price_id:
            raise BillingInputError("Client-supplied Stripe price IDs are not accepted.")
        plan = str(request.plan or "pro").lower()
        price_id = self.settings.stripe_plan_price_ids.get(plan)
        if plan not in {"pro", "team"}:
            raise BillingInputError("Checkout supports only server-owned paid plans.")
        if not self.settings.stripe_secret_key or not price_id:
            if self.settings.is_production:
                raise BillingProviderError("Billing checkout is not configured for the requested plan.")
            subscription = self.store.get_subscription(organization_id=organization_id) or {}
            self.store.upsert_subscription(
                organization_id=organization_id,
                payload={**subscription, "plan": plan, "status": "trialing"},
            )
            return {
                "mode": "demo",
                "checkout_url": f"{self.settings.app_base_url}?billing=demo-checkout",
            }
        subscription = self.store.get_subscription(organization_id=organization_id) or {}
        success_url = self._safe_return_url(self.settings.stripe_success_url or f"{self.settings.app_base_url}?billing=success")
        cancel_url = self._safe_return_url(self.settings.stripe_cancel_url or f"{self.settings.app_base_url}?billing=cancelled")
        data = {
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "metadata[organization_id]": organization_id,
            "metadata[plan]": plan,
        }
        customer_id = str(subscription.get("stripe_customer_id") or "")
        if customer_id:
            if not customer_id.startswith("cus_"):
                raise BillingProviderError("Stored billing customer reference is invalid.")
            data["customer"] = customer_id
        request_id = str(getattr(request, "request_id", None) or "")
        if not request_id:
            request_id = str(int(datetime.now(UTC).timestamp()) // 600)
        idempotency_key = "checkout_" + hashlib.sha256(
            f"{organization_id}:{plan}:{request_id}".encode("utf-8")
        ).hexdigest()
        response = self._stripe_post(
            "https://api.stripe.com/v1/checkout/sessions",
            data,
            idempotency_key=idempotency_key,
        )
        return self._allowlisted_stripe_session(response, url_key="checkout_url", id_key="stripe_session_id", id_prefix="cs_")

    def portal(self, *, organization_id: str, return_url: str | None = None) -> dict[str, Any]:
        subscription = self.store.get_subscription(organization_id=organization_id) or {}
        customer_id = subscription.get("stripe_customer_id")
        if not self.settings.stripe_secret_key or not customer_id:
            if self.settings.is_production:
                raise BillingProviderError("Billing portal is unavailable for this workspace.")
            return {
                "mode": "demo",
                "portal_url": f"{self.settings.app_base_url}?billing=demo-portal",
            }
        customer_id = str(customer_id)
        if not customer_id.startswith("cus_"):
            raise BillingProviderError("Stored billing customer reference is invalid.")
        data = {"customer": customer_id, "return_url": self._safe_return_url(return_url or self.settings.app_base_url)}
        response = self._stripe_post("https://api.stripe.com/v1/billing_portal/sessions", data)
        return self._allowlisted_stripe_session(response, url_key="portal_url", id_key="stripe_session_id", id_prefix="bps_")

    def sync_subscription(self, *, organization_id: str) -> dict[str, Any]:
        subscription = self.store.get_subscription(organization_id=organization_id) or {}
        stripe_subscription_id = str(subscription.get("stripe_subscription_id") or "")
        if not self.settings.stripe_secret_key or not stripe_subscription_id:
            if self.settings.is_production:
                raise BillingProviderError("Billing subscription sync is unavailable for this workspace.")
            return {"status": "skipped", "reason": "stripe_not_configured"}
        remote = self._stripe_get(f"https://api.stripe.com/v1/subscriptions/{stripe_subscription_id}")
        sync_created_at = int(datetime.now(UTC).timestamp())
        return self._upsert_subscription_from_stripe_object(
            organization_id=organization_id,
            data=remote,
            source="stripe_subscription_sync",
            event_created_at=sync_created_at,
            event_id=f"sync_{sync_created_at}",
        )

    def _safe_return_url(self, value: str) -> str:
        candidate = urlsplit(str(value))
        base = urlsplit(str(self.settings.app_base_url))
        if (
            candidate.scheme not in ({"https"} if self.settings.is_production else {"http", "https"})
            or not candidate.hostname
            or candidate.username is not None
            or candidate.password is not None
            or (candidate.scheme, candidate.netloc) != (base.scheme, base.netloc)
        ):
            raise BillingInputError("Billing return URL is not an approved application URL.")
        return str(value)

    @staticmethod
    def _allowlisted_stripe_session(
        response: dict[str, Any],
        *,
        url_key: str,
        id_key: str,
        id_prefix: str,
    ) -> dict[str, Any]:
        session_id = str(response.get("id") or "")
        session_url = str(response.get("url") or "")
        parsed = urlsplit(session_url)
        host = str(parsed.hostname or "").lower()
        if (
            not session_id.startswith(id_prefix)
            or parsed.scheme != "https"
            or not (host == "stripe.com" or host.endswith(".stripe.com"))
        ):
            raise BillingProviderError("Billing provider returned an invalid session response.")
        return {"mode": "stripe", url_key: session_url, id_key: session_id}

    def webhook(self, *, payload: bytes, signature_header: str | None) -> dict[str, Any]:
        if not self.settings.stripe_webhook_secret:
            raise BillingProviderError("Billing webhook processing is not configured.")
        if not self._verify_stripe_signature(payload=payload, signature_header=signature_header):
            raise PermissionError("Billing webhook signature is invalid.")
        try:
            event = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise BillingInputError("Billing webhook payload is invalid.") from None
        if not isinstance(event, dict):
            raise BillingInputError("Billing webhook payload is invalid.")
        event_id = str(event.get("id") or "")
        event_type = str(event.get("type") or "")
        created = event.get("created")
        if (
            not event_id.startswith("evt_")
            or not event_type
            or len(event_type) > 128
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_." for character in event_type.lower())
            or isinstance(created, bool)
            or not isinstance(created, int)
            or created <= 0
        ):
            raise BillingInputError("Billing webhook identity is invalid.")
        event_data = event.get("data")
        data = event_data.get("object") if isinstance(event_data, dict) else None
        if not isinstance(data, dict):
            raise BillingInputError("Billing webhook object is invalid.")
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in metadata.items()):
            raise BillingInputError("Billing webhook metadata is invalid.")
        organization_id = str(metadata.get("organization_id") or "").strip() or None
        if organization_id and (len(organization_id) > 128 or any(character.isspace() for character in organization_id)):
            raise BillingInputError("Billing webhook organization metadata is invalid.")
        object_id = str(data.get("id") or "") or None
        subscription_id = str(data.get("subscription") or "") or None
        customer_id = str(data.get("customer") or "") or None
        summary = {
            "event_id": event_id,
            "event_type": event_type,
            "event_created_at": created,
            "object_id": object_id,
            "organization_id": organization_id,
            "subscription_id": subscription_id,
            "customer_id": customer_id,
        }
        try:
            claim = self.store.claim_stripe_event(
                event_id=event_id,
                event_type=event_type,
                event_created_at=created,
                payload_hash=hashlib.sha256(payload).hexdigest(),
                payload=summary,
            )
        except IdempotencyConflictError:
            raise BillingInputError("Billing webhook event identity conflicts with an existing event.") from None
        if claim["duplicate"]:
            return {"received": True, "updated": False, "duplicate": True, "event_type": event_type}
        if claim["exhausted"]:
            return {"received": True, "updated": False, "retry_exhausted": True, "event_type": event_type}
        if not claim["claimed"]:
            return {"received": True, "updated": False, "in_progress": True, "event_type": event_type}
        claim_token = str(claim["claim_token"])
        try:
            updated = self._process_claimed_stripe_event(
                event_id=event_id,
                event_type=event_type,
                event_created_at=created,
                data=data,
                metadata=metadata,
                organization_id=organization_id,
            )
        except Exception:
            self.store.fail_stripe_event(
                event_id=event_id,
                claim_token=claim_token,
                error_code="subscription_mutation_failed",
            )
            raise BillingWebhookProcessingError("Billing webhook processing failed and may be retried.") from None
        if not self.store.complete_stripe_event(event_id=event_id, claim_token=claim_token):
            raise BillingWebhookProcessingError("Billing webhook completion could not be persisted.")
        return {"received": True, "updated": updated, "event_type": event_type}

    def _process_claimed_stripe_event(
        self,
        *,
        event_id: str,
        event_type: str,
        event_created_at: int,
        data: dict[str, Any],
        metadata: dict[str, str],
        organization_id: str | None,
    ) -> bool:
        if event_type == "checkout.session.completed":
            if not str(data.get("id") or "").startswith("cs_") or not organization_id:
                raise BillingInputError("Checkout webhook object is incomplete.")
            plan = str(metadata.get("plan") or "").lower()
            if plan not in {"pro", "team"} or not self.settings.stripe_plan_price_ids.get(plan):
                raise BillingInputError("Checkout webhook plan is not configured.")
            customer_id = str(data.get("customer") or "")
            subscription_id = str(data.get("subscription") or "")
            if not customer_id.startswith("cus_") or not subscription_id.startswith("sub_"):
                raise BillingInputError("Checkout webhook references are invalid.")
            result = self.store.apply_subscription_event(
                organization_id=organization_id,
                event_created_at=event_created_at,
                event_id=event_id,
                payload={
                    "plan": plan,
                    "status": "active",
                    "stripe_customer_id": customer_id,
                    "stripe_subscription_id": subscription_id,
                    "usage": {"source": "stripe_checkout"},
                },
            )
            return bool(result["applied"])
        if event_type.startswith("customer.subscription."):
            subscription_id = str(data.get("id") or "")
            if not subscription_id.startswith("sub_"):
                raise BillingInputError("Subscription webhook reference is invalid.")
            organization_id = organization_id or self._organization_id_for_stripe_subscription(subscription_id)
            if not organization_id:
                raise BillingInputError("Subscription webhook is not mapped to a workspace.")
            if event_type == "customer.subscription.deleted":
                data = {**data, "status": "canceled"}
            result = self._upsert_subscription_from_stripe_object(
                organization_id=organization_id,
                data=data,
                source="stripe_subscription_webhook",
                event_created_at=event_created_at,
                event_id=event_id,
            )
            return bool(result.get("applied"))
        if event_type in {"invoice.payment_failed", "invoice.paid"}:
            subscription_id = str(data.get("subscription") or "")
            if not subscription_id.startswith("sub_"):
                raise BillingInputError("Invoice webhook subscription reference is invalid.")
            organization_id = organization_id or self._organization_id_for_stripe_subscription(subscription_id)
            current = self.store.get_subscription(organization_id=organization_id) if organization_id else None
            if current is None:
                raise BillingInputError("Invoice webhook is not mapped to a workspace.")
            customer_id = str(data.get("customer") or current.get("stripe_customer_id") or "")
            if customer_id and not customer_id.startswith("cus_"):
                raise BillingInputError("Invoice webhook customer reference is invalid.")
            result = self.store.apply_subscription_event(
                organization_id=str(organization_id),
                event_created_at=event_created_at,
                event_id=event_id,
                payload={
                    "plan": str(current["plan"]),
                    "status": "active" if event_type == "invoice.paid" else "past_due",
                    "stripe_customer_id": customer_id or None,
                    "stripe_subscription_id": subscription_id,
                    "current_period_end_utc": current.get("current_period_end_utc"),
                    "usage": {"source": event_type},
                },
            )
            return bool(result["applied"])
        return False

    def _plan_from_subscription_object(self, data: dict[str, Any]) -> str:
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        metadata_plan = str(metadata.get("plan") or "").lower()
        price_id = None
        items = data.get("items") if isinstance(data.get("items"), dict) else {}
        rows = items.get("data") if isinstance(items.get("data"), list) else []
        if rows:
            price = rows[0].get("price") if isinstance(rows[0], dict) else {}
            price_id = price.get("id") if isinstance(price, dict) else None
        for configured_plan, configured_price in self.settings.stripe_plan_price_ids.items():
            if configured_price and configured_price == price_id:
                if metadata_plan and metadata_plan != configured_plan:
                    raise BillingInputError("Subscription metadata does not match its configured price.")
                return configured_plan
        raise BillingInputError("Subscription price is not mapped to a configured plan.")

    def _upsert_subscription_from_stripe_object(self, *, organization_id: str, data: dict[str, Any], source: str, event_created_at: int, event_id: str) -> dict[str, Any]:
        period_end = data.get("current_period_end")
        current_period_end_utc = None
        if isinstance(period_end, (int, float)):
            current_period_end_utc = datetime.fromtimestamp(float(period_end), tz=UTC).isoformat().replace("+00:00", "Z")
        elif period_end:
            current_period_end_utc = str(period_end)
        status = str(data.get("status") or "")
        if status not in {"active", "trialing", "past_due", "canceled", "unpaid", "incomplete", "incomplete_expired", "paused"}:
            raise BillingInputError("Subscription status is not recognized.")
        if status in {"canceled", "unpaid", "incomplete_expired"}:
            status = "canceled"
        customer_id = str(data.get("customer") or "")
        subscription_id = str(data.get("id") or "")
        if not customer_id.startswith("cus_") or not subscription_id.startswith("sub_"):
            raise BillingInputError("Subscription references are invalid.")
        return self.store.apply_subscription_event(
            organization_id=organization_id,
            event_created_at=event_created_at,
            event_id=event_id,
            payload={
                "plan": self._plan_from_subscription_object(data),
                "status": status,
                "stripe_customer_id": customer_id,
                "stripe_subscription_id": subscription_id,
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

    def _stripe_post(self, url: str, data: dict[str, str], *, idempotency_key: str | None = None) -> dict[str, Any]:
        encoded = urlencode(data).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.settings.stripe_secret_key}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = Request(
            url,
            data=encoded,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=20) as response:
                result = json.loads(response.read().decode("utf-8"))
        except Exception:
            raise BillingProviderError("Billing provider request failed.") from None
        if not isinstance(result, dict):
            raise BillingProviderError("Billing provider returned an invalid response.")
        return result

    def _stripe_get(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={"Authorization": f"Bearer {self.settings.stripe_secret_key}"},
        )
        try:
            with urlopen(request, timeout=20) as response:
                result = json.loads(response.read().decode("utf-8"))
        except Exception:
            raise BillingProviderError("Billing provider request failed.") from None
        if not isinstance(result, dict):
            raise BillingProviderError("Billing provider returned an invalid response.")
        return result


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

    def audit_log(
        self,
        *,
        organization_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        from .observability import scrub

        rows = self.store.list_audit_log(
            organization_id=organization_id,
            action=action,
            limit=limit,
            offset=offset,
        )
        return [{**row, "metadata": scrub(row.get("metadata", {}))} for row in rows]

    def system_health(self) -> dict[str, Any]:
        counts = self.store.counts()
        queued_by_kind: dict[str, int] = {}
        queued_rows: list[dict[str, Any]] = []
        for kind in JOB_KINDS:
            rows = self.store.list_jobs(kind=kind, status="queued", limit=200)
            queued_by_kind[kind] = len(rows)
            queued_rows.extend(rows)
        now = datetime.now(UTC)
        queued_ages: list[float] = []
        pending_ages: list[float] = []
        for row in queued_rows:
            try:
                created = datetime.fromisoformat(str(row.get("created_at_utc") or "").replace("Z", "+00:00"))
                age = max(0.0, (now - created.astimezone(UTC)).total_seconds())
            except (TypeError, ValueError):
                continue
            queued_ages.append(age)
            if row.get("stage") == "dispatch_pending" or row.get("dispatch_state") == "pending":
                pending_ages.append(age)
        return {
            "status": "ok",
            "app_env": self.settings.app_env,
            "database": {"configured": bool(self.settings.database_url), "metadata_store": "sqlite" if not self.settings.database_url else "postgres"},
            "queue": {
                "redis_configured": bool(self.settings.redis_url),
                "in_process_jobs": self.settings.enable_in_process_jobs,
                "controller": check_any_role_instance_from_settings(self.settings, role="controller"),
                "queued_count_by_kind": queued_by_kind,
                "oldest_queued_age_seconds": round(max(queued_ages), 3) if queued_ages else None,
                "dispatch_pending_count": len(pending_ages),
                "oldest_dispatch_pending_age_seconds": round(max(pending_ages), 3) if pending_ages else None,
            },
            "storage": {"s3_configured": bool(self.settings.s3_bucket and self.settings.s3_endpoint_url)},
            "stripe": {"configured": bool(self.settings.stripe_secret_key and self.settings.stripe_webhook_secret)},
            "counts": counts.__dict__,
        }

    def quotas(self) -> dict[str, Any]:
        organizations: dict[str, dict[str, Any]] = {}
        for user in self.store.list_users_with_default_org():
            organization_id = str(user.get("organization_id") or "")
            if not organization_id or organization_id in organizations:
                continue
            overrides = self.store.get_organization_quotas(organization_id=organization_id) or {}
            organizations[organization_id] = {
                "organization_id": organization_id,
                "organization_name": user.get("organization_name") or organization_id,
                "overrides": overrides,
                "effective": {**DEFAULT_QUOTAS, **overrides},
            }
        return {
            "defaults": DEFAULT_QUOTAS,
            "source": "secure_v1_defaults",
            "organizations": list(organizations.values()),
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
