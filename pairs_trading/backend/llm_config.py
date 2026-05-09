from __future__ import annotations

from typing import Protocol

from ..research.llm_providers import (
    AnthropicStructuredLLMProvider,
    LLMProviderUnavailable,
    MockStructuredLLMProvider,
    OpenAIStructuredLLMProvider,
    StructuredLLMProvider,
)
from .config import BackendSettings
from .secrets import SecretProvider


class SecretResolver(Protocol):
    def resolve(self, secret_ref: str) -> str | None:
        ...


class EnvSecretResolver:
    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.provider = SecretProvider(settings)

    def resolve(self, secret_ref: str) -> str | None:
        return self.provider.resolve(secret_ref)


def _configured_secret(settings: BackendSettings, *, provider: str, resolver: SecretResolver) -> str | None:
    if provider == "openai":
        ref = settings.market_research_openai_api_key_ref or "env:OPENAI_API_KEY"
    elif provider == "anthropic":
        ref = settings.market_research_anthropic_api_key_ref or "env:ANTHROPIC_API_KEY"
    else:
        return None
    return resolver.resolve(ref)


def validate_market_research_llm_settings(settings: BackendSettings) -> None:
    provider = settings.market_research_llm_provider.strip().lower()
    if not settings.is_production:
        return
    if provider in {"mock", "disabled", ""}:
        raise RuntimeError("Production startup blocked. Configure PAIRS_TRADING_MARKET_RESEARCH_LLM_PROVIDER=openai or anthropic.")
    if provider not in {"openai", "anthropic"}:
        raise RuntimeError(f"Production startup blocked. Unsupported market research LLM provider: {provider}.")

    resolver = EnvSecretResolver(settings)
    if provider == "openai":
        ref = settings.market_research_openai_api_key_ref or "env:OPENAI_API_KEY"
        try:
            secret = resolver.resolve(ref)
        except Exception as exc:
            raise RuntimeError("Production startup blocked. OpenAI market research secret could not be resolved.") from exc
        if not secret:
            raise RuntimeError("Production startup blocked. Configure OPENAI_API_KEY or PAIRS_TRADING_MARKET_RESEARCH_OPENAI_API_KEY_REF.")
    if provider == "anthropic":
        ref = settings.market_research_anthropic_api_key_ref or "env:ANTHROPIC_API_KEY"
        try:
            secret = resolver.resolve(ref)
        except Exception as exc:
            raise RuntimeError("Production startup blocked. Anthropic market research secret could not be resolved.") from exc
        if not secret:
            raise RuntimeError("Production startup blocked. Configure ANTHROPIC_API_KEY or PAIRS_TRADING_MARKET_RESEARCH_ANTHROPIC_API_KEY_REF.")


def build_structured_llm_provider(settings: BackendSettings, *, resolver: SecretResolver | None = None) -> StructuredLLMProvider:
    provider = settings.market_research_llm_provider.strip().lower() or "mock"
    if provider in {"mock", "disabled"}:
        mock = MockStructuredLLMProvider()
        if provider == "disabled":
            mock.provider_name = "disabled"  # type: ignore[misc]
            mock.model_name = "disabled"  # type: ignore[misc]
        return mock

    secret_resolver = resolver or EnvSecretResolver(settings)
    api_key = _configured_secret(settings, provider=provider, resolver=secret_resolver)
    if not api_key:
        raise LLMProviderUnavailable(f"{provider} API key is not configured.")

    common = {
        "api_key": api_key,
        "model": settings.market_research_llm_model,
        "timeout_seconds": settings.market_research_llm_timeout_seconds,
        "max_retries": settings.market_research_llm_max_retries,
        "max_concurrency": settings.market_research_llm_max_concurrency,
    }
    if provider == "openai":
        return OpenAIStructuredLLMProvider(**common)
    if provider == "anthropic":
        return AnthropicStructuredLLMProvider(**common)
    raise LLMProviderUnavailable(f"Unsupported market research LLM provider: {provider}.")
