from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.quotas import QuotaExceeded, QuotaService
from pairs_trading.backend.saas import (
    BillingInputError,
    BillingProviderError,
    BillingService,
    BillingWebhookProcessingError,
)
from pairs_trading.backend.schemas import BillingCheckoutRequest
from pairs_trading.platform.persistence import SQLiteMetadataStore


def _settings(tmp_path: Path, **overrides: Any) -> BackendSettings:
    values: dict[str, Any] = {
        "metadata_db_path": tmp_path / "metadata.sqlite3",
        "paper_state_dir": tmp_path / "paper_state",
        "paper_artifact_root": tmp_path / "paper_runs",
        "paper_job_state_dir": tmp_path / "paper_jobs",
        "backtest_job_state_dir": tmp_path / "backtest_jobs",
        "sentiment_job_state_dir": tmp_path / "sentiment_jobs",
        "default_paper_config": tmp_path / "missing.json",
        "app_base_url": "http://127.0.0.1:5173",
        "stripe_secret_key": "sk_test_unit_secret",
        "stripe_webhook_secret": "whsec_unit_secret",
        "stripe_price_pro_monthly": "price_pro_unit",
        "stripe_price_team_monthly": "price_team_unit",
    }
    values.update(overrides)
    return BackendSettings(**values)


def _workspace(store: SQLiteMetadataStore, label: str) -> dict[str, Any]:
    return store.ensure_demo_workspace(
        email=f"{label}@example.test",
        organization_name=f"{label} workspace",
        organization_slug=f"{label}-workspace",
        role="user",
        plan="free",
        subscription_status="active",
    )


def _signed_webhook(service: BillingService, event: dict[str, Any]) -> tuple[bytes, str]:
    payload = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")
    timestamp = str(int(time.time()))
    digest = hmac.new(
        str(service.settings.stripe_webhook_secret).encode("utf-8"),
        timestamp.encode("utf-8") + b"." + payload,
        hashlib.sha256,
    ).hexdigest()
    return payload, f"t={timestamp},v1={digest}"


def _subscription_event(
    *,
    event_id: str,
    created: int,
    organization_id: str,
    plan: str,
    status: str = "active",
    subscription_id: str = "sub_unit",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": "customer.subscription.updated",
        "created": created,
        "data": {
            "object": {
                "id": subscription_id,
                "customer": "cus_unit",
                "status": status,
                "metadata": {"organization_id": organization_id, "plan": plan},
                "items": {"data": [{"price": {"id": f"price_{plan}_unit"}}]},
            }
        },
    }


def test_atomic_quota_reservation_prevents_concurrent_overspend(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    seed_store = SQLiteMetadataStore(settings.metadata_db_path)
    organization_id = _workspace(seed_store, "quota-race")["organization_id"]
    seed_store.upsert_organization_quotas(organization_id=organization_id, quotas={"backtest_job": 3})
    barrier = threading.Barrier(10)

    def reserve_once(index: int) -> bool:
        service = QuotaService(settings)
        barrier.wait(timeout=10)
        try:
            service.check_and_record(
                organization_id=organization_id,
                feature="backtest_job",
                user_id=f"worker-{index}",
                occurred_at_utc="2026-08-01T12:00:00Z",
            )
            return True
        except QuotaExceeded:
            return False

    with ThreadPoolExecutor(max_workers=10) as executor:
        outcomes = list(executor.map(reserve_once, range(10)))

    assert outcomes.count(True) == 3
    assert outcomes.count(False) == 7
    assert seed_store.usage_count_window(
        organization_id=organization_id,
        feature="backtest_job",
        window_start_utc="2026-08-01T00:00:00Z",
        window_end_utc="2026-08-02T00:00:00Z",
    ) == 3


def test_quota_reservations_are_tenant_isolated_and_use_half_open_utc_days(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = SQLiteMetadataStore(settings.metadata_db_path)
    organization_a = _workspace(store, "quota-a")["organization_id"]
    organization_b = _workspace(store, "quota-b")["organization_id"]
    for organization_id in (organization_a, organization_b):
        store.upsert_organization_quotas(organization_id=organization_id, quotas={"backtest_job": 1})
    service = QuotaService(settings)

    service.check_and_record(
        organization_id=organization_a,
        feature="backtest_job",
        occurred_at_utc="2026-08-01T23:59:59.999999Z",
    )
    service.check_and_record(
        organization_id=organization_a,
        feature="backtest_job",
        occurred_at_utc="2026-08-02T00:00:00Z",
    )
    service.check_and_record(
        organization_id=organization_b,
        feature="backtest_job",
        occurred_at_utc="2026-08-01T23:59:59.999999Z",
    )

    assert store.usage_count_window(
        organization_id=organization_a,
        feature="backtest_job",
        window_start_utc="2026-08-01T00:00:00Z",
        window_end_utc="2026-08-02T00:00:00Z",
    ) == 1
    assert store.usage_count_window(
        organization_id=organization_a,
        feature="backtest_job",
        window_start_utc="2026-08-02T00:00:00Z",
        window_end_utc="2026-08-03T00:00:00Z",
    ) == 1
    assert store.usage_count_window(
        organization_id=organization_b,
        feature="backtest_job",
        window_start_utc="2026-08-01T00:00:00Z",
        window_end_utc="2026-08-02T00:00:00Z",
    ) == 1


def test_quota_batch_rolls_back_and_invalid_values_fail_closed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = SQLiteMetadataStore(settings.metadata_db_path)
    organization_id = _workspace(store, "quota-batch")["organization_id"]
    store.upsert_organization_quotas(
        organization_id=organization_id,
        quotas={"news_pages": 5, "sentiment_job": 1},
    )
    service = QuotaService(settings)
    service.check_and_record(
        organization_id=organization_id,
        feature="sentiment_job",
        occurred_at_utc="2026-08-01T10:00:00Z",
    )
    with pytest.raises(QuotaExceeded):
        service.check_and_record_many(
            organization_id=organization_id,
            occurred_at_utc="2026-08-01T11:00:00Z",
            reservations=[
                {"feature": "news_pages", "quantity": 2},
                {"feature": "sentiment_job", "quantity": 1},
            ],
        )
    assert store.usage_count_window(
        organization_id=organization_id,
        feature="news_pages",
        window_start_utc="2026-08-01T00:00:00Z",
        window_end_utc="2026-08-02T00:00:00Z",
    ) == 0

    store.upsert_organization_quotas(organization_id=organization_id, quotas={"news_pages": 0})
    assert store.get_organization_quotas(organization_id=organization_id)["news_pages"] == 0

    for invalid in (-1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            service.check_and_record(
                organization_id=organization_id,
                feature="news_pages",
                quantity=invalid,
            )
        with pytest.raises(ValueError):
            store.upsert_organization_quotas(organization_id=organization_id, quotas={"news_pages": invalid})

    bypass = service.check_and_record(
        organization_id=organization_id,
        feature="news_pages",
        role="admin",
        quantity=5,
    )
    assert bypass["allowance"]["bypassed"] is True
    assert bypass["usage"] is None


def test_stripe_webhook_failure_is_retryable_then_duplicates_are_noops(tmp_path: Path) -> None:
    service = BillingService(_settings(tmp_path))
    organization_id = _workspace(service.store, "billing-retry")["organization_id"]
    event = _subscription_event(
        event_id="evt_retry_unit",
        created=100,
        organization_id=organization_id,
        plan="pro",
    )
    payload, signature = _signed_webhook(service, event)
    original = service.store.apply_subscription_event
    attempts = 0

    def fail_once(**kwargs: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient database failure containing sk_test_unit_secret")
        return original(**kwargs)

    service.store.apply_subscription_event = fail_once  # type: ignore[method-assign]
    with pytest.raises(BillingWebhookProcessingError, match="may be retried"):
        service.webhook(payload=payload, signature_header=signature)
    failed = service.store.get_stripe_event(event_id="evt_retry_unit")
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["attempt_count"] == 1
    assert "secret" not in str(failed).lower()

    retried = service.webhook(payload=payload, signature_header=signature)
    assert retried["updated"] is True
    processed = service.store.get_stripe_event(event_id="evt_retry_unit")
    assert processed is not None
    assert processed["status"] == "processed"
    assert processed["attempt_count"] == 2
    duplicate = service.webhook(payload=payload, signature_header=signature)
    assert duplicate["duplicate"] is True
    assert service.store.get_stripe_event(event_id="evt_retry_unit")["attempt_count"] == 2
    conflicting = {**event, "created": 101}
    conflict_payload, conflict_signature = _signed_webhook(service, conflicting)
    with pytest.raises(BillingInputError, match="conflicts"):
        service.webhook(payload=conflict_payload, signature_header=conflict_signature)


def test_stripe_events_are_ordered_unknown_prices_fail_and_invoice_preserves_team(tmp_path: Path) -> None:
    service = BillingService(_settings(tmp_path))
    organization_id = _workspace(service.store, "billing-order")["organization_id"]
    newer = _subscription_event(
        event_id="evt_newer_team",
        created=200,
        organization_id=organization_id,
        plan="team",
    )
    older = _subscription_event(
        event_id="evt_older_pro",
        created=100,
        organization_id=organization_id,
        plan="pro",
    )
    for event in (newer, older):
        payload, signature = _signed_webhook(service, event)
        service.webhook(payload=payload, signature_header=signature)
    subscription = service.store.get_subscription(organization_id=organization_id)
    assert subscription is not None
    assert subscription["plan"] == "team"
    assert subscription["stripe_event_id"] == "evt_newer_team"

    invoice = {
        "id": "evt_invoice_paid_team",
        "type": "invoice.paid",
        "created": 201,
        "data": {"object": {"id": "in_unit", "subscription": "sub_unit", "customer": "cus_unit"}},
    }
    payload, signature = _signed_webhook(service, invoice)
    assert service.webhook(payload=payload, signature_header=signature)["updated"] is True
    assert service.store.get_subscription(organization_id=organization_id)["plan"] == "team"

    unknown = _subscription_event(
        event_id="evt_unknown_price",
        created=202,
        organization_id=organization_id,
        plan="pro",
    )
    unknown["data"]["object"]["items"]["data"][0]["price"]["id"] = "price_attacker_controlled"
    payload, signature = _signed_webhook(service, unknown)
    with pytest.raises(BillingWebhookProcessingError):
        service.webhook(payload=payload, signature_header=signature)
    assert service.store.get_stripe_event(event_id="evt_unknown_price")["status"] == "failed"
    assert service.store.get_subscription(organization_id=organization_id)["plan"] == "team"


def test_stripe_event_storage_and_checkout_responses_are_minimized(tmp_path: Path) -> None:
    service = BillingService(_settings(tmp_path))
    organization_id = _workspace(service.store, "billing-minimize")["organization_id"]
    event = _subscription_event(
        event_id="evt_minimized",
        created=300,
        organization_id=organization_id,
        plan="pro",
    )
    event["data"]["object"].update(
        {
            "customer_email": "private@example.test",
            "customer_details": {"name": "Private Person", "address": {"line1": "Secret Street"}},
        }
    )
    payload, signature = _signed_webhook(service, event)
    service.webhook(payload=payload, signature_header=signature)
    stored = json.dumps(service.store.get_stripe_event(event_id="evt_minimized"), sort_keys=True)
    assert "private@example.test" not in stored
    assert "Private Person" not in stored
    assert "Secret Street" not in stored

    captured: dict[str, Any] = {}

    def fake_post(url: str, data: dict[str, str], *, idempotency_key: str | None = None) -> dict[str, Any]:
        captured.update({"url": url, "data": data, "idempotency_key": idempotency_key})
        return {
            "id": "cs_unit_checkout",
            "url": "https://checkout.stripe.com/c/pay/unit",
            "customer_email": "leak@example.test",
            "metadata": {"private": "value"},
        }

    service._stripe_post = fake_post  # type: ignore[method-assign]
    result = service.checkout(
        organization_id=organization_id,
        request=BillingCheckoutRequest(plan="pro", request_id="request-unit-123"),
    )
    assert result == {
        "mode": "stripe",
        "checkout_url": "https://checkout.stripe.com/c/pay/unit",
        "stripe_session_id": "cs_unit_checkout",
    }
    assert captured["data"]["line_items[0][price]"] == "price_pro_unit"
    assert str(captured["idempotency_key"]).startswith("checkout_")


def test_billing_rejects_untrusted_urls_client_prices_and_secret_bearing_provider_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = BillingService(_settings(tmp_path, stripe_success_url="https://evil.example/capture"))
    organization_id = _workspace(service.store, "billing-input")["organization_id"]
    with pytest.raises(BillingInputError, match="price IDs"):
        service.checkout(
            organization_id=organization_id,
            request=BillingCheckoutRequest(plan="pro", price_id="price_attacker", request_id="request-price-123"),
        )
    with pytest.raises(BillingInputError, match="approved"):
        service.checkout(
            organization_id=organization_id,
            request=BillingCheckoutRequest(plan="pro", request_id="request-url-123"),
        )
    with pytest.raises(BillingInputError):
        service._safe_return_url("http://127.0.0.1:5173@evil.example/")

    def provider_failure(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("request failed with Authorization: Bearer sk_test_unit_secret")

    monkeypatch.setattr("pairs_trading.backend.saas.urlopen", provider_failure)
    with pytest.raises(BillingProviderError) as exc_info:
        service._stripe_post("https://api.stripe.com/v1/test", {"unit": "value"})
    assert str(exc_info.value) == "Billing provider request failed."
    assert "sk_test_unit_secret" not in str(exc_info.value)
