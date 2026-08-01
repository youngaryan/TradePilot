from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib

from fastapi.testclient import TestClient
import pytest

from pairs_trading.backend.app import create_app
from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.saas import (
    AuthRateLimitError,
    AuthService,
    MfaSecretCipher,
    hash_password,
    totp_code,
)
from pairs_trading.platform import SQLiteMetadataStore
from tests.common import fresh_test_dir


def _settings(name: str, **updates) -> BackendSettings:
    root = fresh_test_dir(f"artifacts/{name}")
    settings = BackendSettings(
        metadata_db_path=root / "metadata.sqlite3",
        paper_job_state_dir=root / "paper-jobs",
        backtest_job_state_dir=root / "backtest-jobs",
        sentiment_job_state_dir=root / "sentiment-jobs",
        market_research_job_state_dir=root / "market-jobs",
        session_secret="test-session-secret-at-least-thirty-two-characters",
        csrf_secret="test-csrf-secret-at-least-thirty-two-characters",
        mfa_encryption_key="test-mfa-key-at-least-thirty-two-characters",
        enable_in_process_jobs=False,
        auth_attempt_max_failures=5,
    )
    return replace(settings, **updates)


def _user(store: SQLiteMetadataStore, *, email: str = "secure@example.com", password: str = "correct-password-123") -> dict:
    store.create_user_workspace(
        email=email,
        display_name="Secure User",
        password_hash=hash_password(password),
        organization_name="Secure Workspace",
    )
    user = store.get_user_by_email(email)
    assert user is not None
    return user


def test_mfa_enrollment_requires_password_encrypts_secret_rejects_replay_and_revokes_sessions() -> None:
    settings = _settings("test_auth_mfa_enrollment")
    store = SQLiteMetadataStore(settings.metadata_db_path, enable_demo_accounts=False)
    user = _user(store)
    auth = AuthService(settings)
    store.create_auth_session(user_id=user["id"], token="stolen-session")

    with pytest.raises(PermissionError, match="reauthentication"):
        auth.setup_mfa(user_id=user["id"], password="wrong-password")
    setup = auth.setup_mfa(user_id=user["id"], password="correct-password-123")
    pending = store.get_user_by_id(user["id"])
    assert pending is not None
    assert pending["mfa_pending_secret"].startswith("enc:v1:")
    assert setup["secret"] not in pending["mfa_pending_secret"]
    assert not pending["mfa_enabled"]

    verified = auth.verify_mfa_code(user_id=user["id"], code=totp_code(setup["secret"]))
    assert verified["sessions_revoked"] is True
    assert store.get_auth_session(token="stolen-session") is None
    enabled = store.get_user_by_id(user["id"])
    assert enabled is not None
    assert enabled["mfa_enabled"] == 1
    assert enabled["mfa_secret"].startswith("enc:v1:")
    assert enabled["mfa_pending_secret"] is None

    with pytest.raises(PermissionError, match="previously used"):
        auth.verify_mfa_code(user_id=user["id"], code=totp_code(setup["secret"]))
    with pytest.raises(PermissionError, match="already enabled"):
        auth.setup_mfa(user_id=user["id"], password="correct-password-123")


def test_mfa_rotation_is_explicit_and_existing_secret_is_never_revealed() -> None:
    settings = _settings("test_auth_mfa_rotation")
    store = SQLiteMetadataStore(settings.metadata_db_path, enable_demo_accounts=False)
    user = _user(store)
    auth = AuthService(settings)
    first = auth.setup_mfa(user_id=user["id"], password="correct-password-123")
    auth.verify_mfa_code(user_id=user["id"], code=totp_code(first["secret"]))

    with pytest.raises(PermissionError, match="Explicit rotation"):
        auth.setup_mfa(user_id=user["id"], password="correct-password-123")
    with pytest.raises(PermissionError, match="Current MFA"):
        auth.setup_mfa(
            user_id=user["id"],
            password="correct-password-123",
            rotate=True,
            current_code="000000",
        )
    rotated = auth.setup_mfa(
        user_id=user["id"],
        password="correct-password-123",
        rotate=True,
        current_code=totp_code(first["secret"], for_time=int(__import__("time").time()) + 30),
    )
    assert rotated["rotation"] is True
    assert rotated["secret"] != first["secret"]


def test_mfa_cipher_fails_closed_for_missing_wrong_key_and_plaintext_production_data() -> None:
    base = _settings("test_auth_mfa_cipher")
    with pytest.raises(RuntimeError, match="not configured"):
        MfaSecretCipher(replace(base, app_env="production", mfa_encryption_key=None))
    ciphertext = MfaSecretCipher(base).encrypt("JBSWY3DPEHPK3PXP")
    wrong = MfaSecretCipher(replace(base, mfa_encryption_key="different-mfa-key-at-least-thirty-two-characters"))
    with pytest.raises(RuntimeError, match="cannot be decrypted"):
        wrong.decrypt(ciphertext)
    production = MfaSecretCipher(replace(base, app_env="production"))
    with pytest.raises(RuntimeError, match="not encrypted"):
        production.decrypt("JBSWY3DPEHPK3PXP")


def test_development_plaintext_mfa_is_upgraded_on_successful_use() -> None:
    settings = _settings("test_auth_mfa_plaintext_upgrade")
    store = SQLiteMetadataStore(settings.metadata_db_path, enable_demo_accounts=False)
    user = _user(store)
    secret = "JBSWY3DPEHPK3PXP"
    store.set_user_mfa_secret(user_id=user["id"], secret=secret, enabled=True)
    AuthService(settings).verify_mfa_code(user_id=user["id"], code=totp_code(secret))
    upgraded = store.get_user_by_id(user["id"])
    assert upgraded is not None
    assert upgraded["mfa_secret"].startswith("enc:v1:")
    assert secret not in upgraded["mfa_secret"]


def test_legacy_password_hash_is_opportunistically_rehashed() -> None:
    settings = _settings("test_auth_password_rehash")
    store = SQLiteMetadataStore(settings.metadata_db_path, enable_demo_accounts=False)
    password = "correct-password-123"
    salt = "legacy-salt"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    legacy_hash = f"pbkdf2_sha256$200000${salt}${base64.b64encode(digest).decode()}"
    store.create_user_workspace(
        email="legacy@example.com",
        display_name="Legacy User",
        password_hash=legacy_hash,
        organization_name="Legacy Workspace",
    )
    AuthService(settings).login(email="legacy@example.com", password=password)
    upgraded = store.get_user_by_email("legacy@example.com")
    assert upgraded is not None
    assert upgraded["password_hash"].startswith("pbkdf2_sha256$600000$")


def test_auth_attempt_admission_is_atomic_under_concurrency() -> None:
    settings = _settings("test_auth_atomic_throttle", auth_attempt_max_failures=3)
    store = SQLiteMetadataStore(settings.metadata_db_path, enable_demo_accounts=False)
    _user(store, email="limited@example.com")
    auth = AuthService(settings)

    def attempt(_: int) -> str:
        try:
            auth.login(email="limited@example.com", password="wrong-password", client_id="same-client")
        except AuthRateLimitError:
            return "limited"
        except ValueError:
            return "admitted"
        return "unexpected"

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(executor.map(attempt, range(12)))
    assert outcomes.count("admitted") <= 3
    assert outcomes.count("limited") >= 9
    assert "unexpected" not in outcomes


def test_arbitrary_bearer_cannot_bypass_csrf_and_machine_key_cannot_mutate_browser_workspace() -> None:
    settings = _settings("test_auth_bearer_boundaries", enable_demo_accounts=True)
    client = TestClient(create_app(settings))
    login = client.post("/api/auth/login", json={"email": "demo@quantops.local", "password": "quantops-demo"})
    assert login.status_code == 200
    csrf = client.cookies.get("quantops_csrf")
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post("/api/workspaces/projects", headers={"Authorization": "Bearer arbitrary-session"}, json={"name": "No"}).status_code == 401

    store = SQLiteMetadataStore(settings.metadata_db_path)
    token = "qops_machine_browser_boundary"
    store.create_api_key_metadata(
        organization_id=login.json()["active_organization_id"],
        name="Machine",
        provider="quantops",
        token_hash=store.hash_token(token),
        scopes=["paper:read"],
    )
    denied = client.post(
        "/api/workspaces/api-keys",
        headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf or ""},
        json={"name": "Escalate", "provider": "quantops"},
    )
    assert denied.status_code == 403


def test_machine_job_polling_requires_matching_explicit_read_scope() -> None:
    settings = _settings("test_auth_machine_read_scope", enable_demo_accounts=True)
    client = TestClient(create_app(settings))
    login = client.post("/api/auth/login", json={"email": "demo@quantops.local", "password": "quantops-demo"})
    organization_id = login.json()["active_organization_id"]
    store = SQLiteMetadataStore(settings.metadata_db_path)
    allowed_token = "qops_paper_reader"
    wrong_token = "qops_backtest_reader"
    store.create_api_key_metadata(
        organization_id=organization_id,
        name="Paper reader",
        provider="quantops",
        token_hash=store.hash_token(allowed_token),
        scopes=["paper:read"],
    )
    store.create_api_key_metadata(
        organization_id=organization_id,
        name="Wrong reader",
        provider="quantops",
        token_hash=store.hash_token(wrong_token),
        scopes=["backtests:read"],
    )
    allowed = client.get("/api/paper/jobs", headers={"Authorization": f"Bearer {allowed_token}"})
    wrong = client.get("/api/paper/jobs", headers={"Authorization": f"Bearer {wrong_token}"})
    cross_tenant = client.get(
        "/api/paper/jobs",
        headers={"Authorization": f"Bearer {allowed_token}", "X-Organization-Id": "another-tenant"},
    )
    assert allowed.status_code == 200
    assert wrong.status_code == 403
    assert wrong.json()["detail"]["code"] == "api_key_scope_required"
    assert cross_tenant.status_code == 403
