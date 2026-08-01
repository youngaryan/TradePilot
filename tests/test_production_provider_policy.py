from __future__ import annotations

import json
import os
from unittest.mock import patch

import pandas as pd
import pytest

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.market_research_services import (
    BackendMarketResearchDataProvider,
    ProviderUnavailable,
)
from pairs_trading.research.market_research_agents import MarketResearchInput, ResearchHorizon


def _request() -> MarketResearchInput:
    return MarketResearchInput(
        ticker="AAPL",
        analysis_date="2026-05-01",
        horizon=ResearchHorizon.SWING,
        include_sentiment=False,
        include_financial_events=False,
    )


def _provider(settings: BackendSettings, market_data_provider: object | None = None) -> BackendMarketResearchDataProvider:
    with (
        patch("pairs_trading.backend.market_research_services.SentimentService"),
        patch("pairs_trading.backend.market_research_services.FinancialEventsService"),
    ):
        return BackendMarketResearchDataProvider(settings, market_data_provider=market_data_provider)  # type: ignore[arg-type]


def test_unknown_data_provider_is_rejected_in_every_environment() -> None:
    settings = BackendSettings(market_research_data_provider="cached_yaho_typo")

    with pytest.raises(RuntimeError, match="Unsupported PAIRS_TRADING_MARKET_RESEARCH_DATA_PROVIDER"):
        settings.validate_for_startup()
    with pytest.raises(RuntimeError, match="cached_yaho_typo"):
        _provider(settings)


@pytest.mark.parametrize(
    "provider, fallback, expected",
    [
        ("demo", False, "DATA_PROVIDER=cached_yahoo"),
        ("cached_yahoo", True, "ALLOW_DEMO_FALLBACK=false"),
    ],
)
def test_production_rejects_demo_or_demo_fallback(provider: str, fallback: bool, expected: str) -> None:
    settings = BackendSettings(
        app_env="production",
        market_research_data_provider=provider,
        market_research_allow_demo_fallback=fallback,
    )

    with pytest.raises(RuntimeError, match=expected):
        settings.validate_for_startup()


def test_environment_defaults_allow_fallback_only_outside_production() -> None:
    names = {
        "APP_ENV": "production",
        "PAIRS_TRADING_MARKET_RESEARCH_DATA_PROVIDER": "cached_yahoo",
        "PAIRS_TRADING_MARKET_RESEARCH_ALLOW_DEMO_FALLBACK": "",
    }
    with patch.dict(os.environ, names, clear=False), patch(
        "pairs_trading.backend.config.dotenv_value",
        return_value=None,
    ):
        os.environ.pop("PAIRS_TRADING_MARKET_RESEARCH_ALLOW_DEMO_FALLBACK", None)
        production = BackendSettings.from_env()
    with patch.dict(
        os.environ,
        {
            "APP_ENV": "development",
            "PAIRS_TRADING_MARKET_RESEARCH_DATA_PROVIDER": "cached_yahoo",
        },
        clear=False,
    ), patch("pairs_trading.backend.config.dotenv_value", return_value=None):
        os.environ.pop("PAIRS_TRADING_MARKET_RESEARCH_ALLOW_DEMO_FALLBACK", None)
        development = BackendSettings.from_env()

    assert production.market_research_allow_demo_fallback is False
    assert development.market_research_allow_demo_fallback is True


def test_production_provider_outage_never_falls_back_or_exposes_provider_error() -> None:
    secret_error = "https://user:provider-password@prices.invalid failed"

    class FailingProvider:
        def get_close_prices(self, *_args: object, **_kwargs: object) -> pd.DataFrame:
            raise RuntimeError(secret_error)

    provider = _provider(
        BackendSettings(
            app_env="production",
            market_research_data_provider="cached_yahoo",
            market_research_allow_demo_fallback=False,
        ),
        FailingProvider(),
    )

    with pytest.raises(ProviderUnavailable) as raised:
        provider.collect(_request())

    assert str(raised.value) == "The configured market research data provider is temporarily unavailable."
    assert "provider-password" not in str(raised.value)


def test_development_explicit_fallback_is_synthetic_and_fully_labelled() -> None:
    class FailingProvider:
        def get_close_prices(self, *_args: object, **_kwargs: object) -> pd.DataFrame:
            raise RuntimeError("offline")

    provider = _provider(
        BackendSettings(
            market_research_data_provider="cached_yahoo",
            market_research_allow_demo_fallback=True,
        ),
        FailingProvider(),
    )

    context = provider.collect(_request())

    assert context.provider_metadata["backend_data_provider"] == "demo_fallback"
    assert context.provider_metadata["configured_data_provider"] == "cached_yahoo"
    assert context.provider_metadata["effective_data_provider"] == "demo"
    assert context.provider_metadata["synthetic_data_used"] is True
    assert context.provider_metadata["demo_fallback_used"] is True
    assert context.provider_metadata["degraded"] is True
    assert context.provider_metadata["fallback_reason"] == "provider_unavailable"
    assert any("explicit development demo fallback" in warning for warning in context.warnings)
    assert any(item.provider == "demo" for item in context.provenance)


def test_development_can_disable_fallback() -> None:
    class FailingProvider:
        def get_close_prices(self, *_args: object, **_kwargs: object) -> pd.DataFrame:
            raise RuntimeError("offline")

    provider = _provider(
        BackendSettings(
            market_research_data_provider="cached_yahoo",
            market_research_allow_demo_fallback=False,
        ),
        FailingProvider(),
    )

    with pytest.raises(ProviderUnavailable):
        provider.collect(_request())


def test_production_real_context_has_no_demo_payload_and_marks_missing_components_degraded() -> None:
    class PriceProvider:
        def get_close_prices(self, *_args: object, **_kwargs: object) -> pd.DataFrame:
            date_index = pd.date_range("2026-01-01", periods=90, freq="D")
            return pd.DataFrame(
                {"AAPL": [150.0 + offset / 10 for offset in range(90)]},
                index=date_index,
            )

    provider = _provider(
        BackendSettings(
            app_env="production",
            market_research_data_provider="cached_yahoo",
            market_research_allow_demo_fallback=False,
        ),
        PriceProvider(),
    )

    context = provider.collect(_request())
    serialized = json.dumps(context.model_dump(mode="json"), sort_keys=True).lower()

    assert context.provider_metadata["effective_data_provider"] == "cached_yahoo"
    assert context.provider_metadata["synthetic_data_used"] is False
    assert context.provider_metadata["degraded_components"] == ["direct_news", "fundamentals"]
    assert context.missing_data_indicators == ["direct_news_provider", "fundamentals_provider"]
    assert context.news == []
    assert "demo_fallback" not in serialized
    assert '"effective_data_provider": "demo"' not in serialized
    assert any("direct news and fundamentals" in warning for warning in context.warnings)
