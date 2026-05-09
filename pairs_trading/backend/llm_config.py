from __future__ import annotations

from typing import Protocol
from urllib.parse import urljoin

from ..research.llm_providers import (
    AnthropicStructuredLLMProvider,
    LLMProviderUnavailable,
    MockStructuredLLMProvider,
    OllamaStructuredLLMProvider,
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
    if provider in {"mock", "disabled", "ollama", ""}:
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


def probe_ollama_runtime(settings: BackendSettings, *, timeout_seconds: float = 3.0) -> dict[str, object]:
    """Return safe local Ollama diagnostics without prompts, responses, or secrets."""

    base_url = settings.market_research_ollama_base_url.rstrip("/")
    payload: dict[str, object] = {
        "base_url": base_url,
        "reachable": False,
        "model_available": False,
        "configured_model": settings.market_research_llm_model,
        "models": [],
        "error": None,
    }
    try:
        import httpx

        response = httpx.get(urljoin(f"{base_url}/", "api/tags"), timeout=timeout_seconds)
        if response.status_code >= 400:
            payload["error"] = f"Ollama /api/tags returned HTTP {response.status_code}."
            return payload
        body = response.json()
        models = [
            str(item.get("model") or item.get("name"))
            for item in body.get("models", [])
            if isinstance(item, dict) and (item.get("model") or item.get("name"))
        ]
        payload["reachable"] = True
        payload["models"] = models
        payload["model_available"] = settings.market_research_llm_model in set(models)
        if not payload["model_available"]:
            payload["error"] = f"Ollama model '{settings.market_research_llm_model}' is not pulled."
        return payload
    except Exception as exc:
        payload["error"] = f"Ollama is not reachable: {exc}"
        return payload


def market_research_runtime_diagnostics(settings: BackendSettings) -> dict[str, object]:
    provider = settings.market_research_llm_provider.strip().lower() or "mock"
    warnings: list[str] = []
    if provider == "mock":
        warnings.append("Mock LLM provider is active; no local or hosted model will be called.")
    if provider == "disabled":
        warnings.append("Market research LLM generation is disabled; deterministic agent outputs will be used.")
    if settings.market_research_data_provider == "demo":
        warnings.append("Demo market data provider is active; report warnings about demo data are expected.")
    if provider == "ollama" and (
        settings.market_research_agent_timeout_seconds < 120 or settings.market_research_llm_timeout_seconds < 120
    ):
        warnings.append("Ollama local models can be slow; use at least 120s for agent and LLM timeouts.")

    diagnostics: dict[str, object] = {
        "llm_provider": provider,
        "llm_model": settings.market_research_llm_model,
        "data_provider": settings.market_research_data_provider,
        "agent_timeout_seconds": settings.market_research_agent_timeout_seconds,
        "llm_timeout_seconds": settings.market_research_llm_timeout_seconds,
        "llm_max_retries": settings.market_research_llm_max_retries,
        "llm_max_concurrency": settings.market_research_llm_max_concurrency,
        "warnings": warnings,
    }
    if provider == "ollama":
        diagnostics["ollama"] = probe_ollama_runtime(settings)
    return diagnostics


def preflight_market_research_llm(settings: BackendSettings) -> None:
    provider = settings.market_research_llm_provider.strip().lower() or "mock"
    if provider != "ollama":
        return
    diagnostics = probe_ollama_runtime(settings)
    if not diagnostics.get("reachable"):
        raise LLMProviderUnavailable(str(diagnostics.get("error") or "Ollama is not reachable."))
    if not diagnostics.get("model_available"):
        models = diagnostics.get("models")
        available = ", ".join(str(model) for model in models) if isinstance(models, list) and models else "none"
        raise LLMProviderUnavailable(
            f"Ollama model '{settings.market_research_llm_model}' is not available. "
            f"Run `ollama pull {settings.market_research_llm_model}` and restart the backend. "
            f"Available local models: {available}."
        )


def build_structured_llm_provider(settings: BackendSettings, *, resolver: SecretResolver | None = None) -> StructuredLLMProvider:
    provider = settings.market_research_llm_provider.strip().lower() or "mock"
    if provider in {"mock", "disabled"}:
        mock = MockStructuredLLMProvider()
        if provider == "disabled":
            mock.provider_name = "disabled"  # type: ignore[misc]
            mock.model_name = "disabled"  # type: ignore[misc]
        return mock
    if provider == "ollama":
        return OllamaStructuredLLMProvider(
            model=settings.market_research_llm_model,
            timeout_seconds=settings.market_research_llm_timeout_seconds,
            max_retries=settings.market_research_llm_max_retries,
            max_concurrency=settings.market_research_llm_max_concurrency,
            base_url=settings.market_research_ollama_base_url,
        )

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
