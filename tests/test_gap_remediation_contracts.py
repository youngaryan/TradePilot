from __future__ import annotations

import base64
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pytest

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.secrets import AwsSecretsManagerResolver, CompositeSecretResolver, SecretResolutionError
from pairs_trading.data.fundamentals import SecCompanyFactsFundamentalsProvider
from pairs_trading.data.research_news import HeadlineMarketResearchNewsProvider
from pairs_trading.data.sec import SecCompanyFactsClient


class _SecretsClient:
    def __init__(self, response: dict | None = None, error: Exception | None = None) -> None:
        self.response = response or {}
        self.error = error
        self.calls = 0

    def get_secret_value(self, **_: object) -> dict:
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


def test_capability_defaults_and_invalid_modes() -> None:
    settings = BackendSettings()
    assert settings.capabilities == {
        "strategy_builder_mode": "rules",
        "strategy_builder_provider": "deterministic",
        "strategy_builder_model": "",
        "market_research_data_mode": "demo",
        "marketplace_enabled": False,
        "marketplace_creator_credits_enabled": False,
        "live_broker_trading_enabled": False,
    }
    with pytest.raises(RuntimeError, match="STRATEGY_BUILDER_MODE"):
        BackendSettings(strategy_builder_mode="fictional").validate_capabilities()
    with pytest.raises(RuntimeError, match="STRATEGY_BUILDER_LLM_PROVIDER"):
        BackendSettings(strategy_builder_llm_provider="fictional").validate_capabilities()
    with pytest.raises(RuntimeError, match="requires"):
        BackendSettings(marketplace_creator_credits_enabled=True).validate_capabilities()


def test_aws_secret_string_json_binary_cache_and_safe_errors() -> None:
    clock = [10.0]
    client = _SecretsClient({"SecretString": '{"api_key":"top-secret"}'})
    resolver = AwsSecretsManagerResolver(client=client, ttl_seconds=5, clock=lambda: clock[0])
    reference = "secret-manager:aws:opaque-id#api_key"
    assert resolver.resolve(reference) == "top-secret"
    assert resolver.resolve(reference) == "top-secret"
    assert client.calls == 1
    clock[0] = 16.0
    assert resolver.resolve(reference) == "top-secret"
    assert client.calls == 2

    binary = AwsSecretsManagerResolver(client=_SecretsClient({"SecretBinary": base64.b64encode(b"binary-value").decode()}))
    assert binary.resolve("secret-manager:aws:opaque") == "binary-value"

    denied = AwsSecretsManagerResolver(client=_SecretsClient(error=RuntimeError("denied top-secret opaque-id")))
    with pytest.raises(SecretResolutionError) as failure:
        denied.resolve("secret-manager:aws:opaque-id")
    assert "top-secret" not in str(failure.value)
    assert "opaque-id" not in str(failure.value)
    with pytest.raises(SecretResolutionError, match="must name a provider"):
        CompositeSecretResolver(BackendSettings()).resolve("secret-manager:opaque-id")


def test_sec_fundamentals_enforce_filing_cutoff_and_concept_priority() -> None:
    payload = {
        "facts": {"us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                {"val": 100, "form": "10-Q", "filed": "2024-04-15", "start": "2024-01-01", "end": "2024-03-31", "fy": 2024, "fp": "Q1", "accn": "1"},
                {"val": 999, "form": "10-Q", "filed": "2024-08-15", "start": "2024-04-01", "end": "2024-06-30", "fy": 2024, "fp": "Q2", "accn": "2"},
            ]}},
            "Revenues": {"units": {"USD": [{"val": 90, "form": "10-Q/A", "filed": "2024-04-15", "end": "2024-03-31"}]}},
        }}
    }
    with TemporaryDirectory(prefix="tradepilot-sec-") as temp:
        def fetch(url: str) -> dict:
            return {"0": {"ticker": "ACME", "cik_str": 1234}} if "company_tickers" in url else payload

        provider = SecCompanyFactsFundamentalsProvider(SecCompanyFactsClient(cache_dir=Path(temp), fetch_json=fetch))
        snapshot = provider.get_snapshot("acme", date(2024, 6, 1))
    assert snapshot.revenue is not None
    assert snapshot.revenue.value == 100
    assert snapshot.revenue.concept == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert snapshot.freshness_days == 47
    assert snapshot.guidance_available is False
    assert "net_income" in snapshot.missing_fields


def test_market_research_news_filters_time_and_deduplicates() -> None:
    class Provider:
        last_errors = ["one source degraded"]

        def get_headlines(self, *_: object) -> pd.DataFrame:
            return pd.DataFrame([
                {"ticker": "ACME", "timestamp": "2024-01-15T12:00:00Z", "headline": "Quarterly results improve", "source": "wire"},
                {"ticker": "ACME", "timestamp": "2024-01-15T13:00:00Z", "headline": "  Quarterly   results improve ", "source": "wire"},
                {"ticker": "ACME", "timestamp": "2023-12-31T23:00:00Z", "headline": "Too early", "source": "wire"},
                {"ticker": "ACME", "timestamp": "2024-02-01T00:00:00Z", "headline": "Look-ahead", "source": "wire"},
            ])

    adapter = HeadlineMarketResearchNewsProvider(Provider())
    rows = adapter.get_news("ACME", date(2024, 1, 1), date(2024, 1, 31))
    assert len(rows) == 1
    assert rows[0].headline.strip() == "Quarterly   results improve"
    assert rows[0].deduplication_key
    assert adapter.last_warnings == ["one source degraded"]
