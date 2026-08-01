from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from ..research.llm_providers import (
    AnthropicStructuredLLMProvider,
    DeepInfraStructuredLLMProvider,
    LLMProviderUnavailable,
    MockStructuredLLMProvider,
    NvidiaStructuredLLMProvider,
    OllamaStructuredLLMProvider,
    OpenAIStructuredLLMProvider,
    StructuredLLMProvider,
)
from ..research.nvidia_model_catalog import nvidia_model_catalog_payload, resolve_nvidia_model
from .config import BackendSettings
from .secrets import CompositeSecretResolver, SecretResolver


@dataclass(frozen=True)
class StructuredProviderConfig:
    provider: str
    model: str
    timeout_seconds: float
    max_retries: int
    max_concurrency: int
    openai_api_key_ref: str | None = None
    anthropic_api_key_ref: str | None = None
    deepinfra_api_key_ref: str | None = None
    nvidia_api_key_ref: str | None = None
    ollama_base_url: str = "http://127.0.0.1:11434"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    deepinfra_base_url: str = "https://api.deepinfra.com/v1/openai"


class EnvSecretResolver:
    """Compatibility wrapper; new code should use CompositeSecretResolver."""

    def __init__(self, settings: BackendSettings) -> None:
        self.settings = settings
        self.provider = CompositeSecretResolver(settings)

    def resolve(self, secret_ref: str) -> str | None:
        return self.provider.resolve(secret_ref)


def _configured_secret(settings: BackendSettings, *, provider: str, resolver: SecretResolver) -> str | None:
    if provider == "openai":
        ref = settings.market_research_openai_api_key_ref or "env:OPENAI_API_KEY"
    elif provider == "anthropic":
        ref = settings.market_research_anthropic_api_key_ref or "env:ANTHROPIC_API_KEY"
    elif provider == "nvidia":
        ref = settings.market_research_nvidia_api_key_ref or "env:NVIDIA_API_KEY"
    else:
        return None
    return resolver.resolve(ref)


def create_structured_llm_provider(
    config: StructuredProviderConfig,
    *,
    settings: BackendSettings,
    resolver: SecretResolver | None = None,
    allow_mock: bool = False,
) -> StructuredLLMProvider:
    """Build a structured-output provider without coupling consumers' settings."""

    provider = config.provider.strip().lower()
    if provider in {"mock", "disabled"}:
        if not allow_mock:
            raise LLMProviderUnavailable("A hosted structured LLM provider is required.")
        mock = MockStructuredLLMProvider()
        if provider == "disabled":
            mock.provider_name = "disabled"  # type: ignore[misc]
            mock.model_name = "disabled"  # type: ignore[misc]
        return mock
    if provider == "ollama":
        return OllamaStructuredLLMProvider(
            model=config.model,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
            max_concurrency=config.max_concurrency,
            base_url=config.ollama_base_url,
        )

    references = {
        "openai": config.openai_api_key_ref or "env:OPENAI_API_KEY",
        "anthropic": config.anthropic_api_key_ref or "env:ANTHROPIC_API_KEY",
        "deepinfra": config.deepinfra_api_key_ref or "env:DEEPINFRA_API_KEY",
        "nvidia": config.nvidia_api_key_ref or "env:NVIDIA_API_KEY",
    }
    if provider not in references:
        raise LLMProviderUnavailable(f"Unsupported structured LLM provider: {provider}.")
    secret_resolver = resolver or CompositeSecretResolver(settings)
    try:
        api_key = secret_resolver.resolve(references[provider])
    except Exception as exc:
        raise LLMProviderUnavailable(
            f"{provider} structured LLM credentials could not be resolved."
        ) from exc
    if not api_key:
        raise LLMProviderUnavailable(f"{provider} API key is not configured.")
    common = {
        "api_key": api_key,
        "model": config.model,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
        "max_concurrency": config.max_concurrency,
    }
    if provider == "openai":
        return OpenAIStructuredLLMProvider(**common)
    if provider == "anthropic":
        return AnthropicStructuredLLMProvider(**common)
    if provider == "deepinfra":
        return DeepInfraStructuredLLMProvider(**common, base_url=config.deepinfra_base_url)
    return NvidiaStructuredLLMProvider(**common, base_url=config.nvidia_base_url)


def build_strategy_builder_llm_provider(
    settings: BackendSettings, *, resolver: SecretResolver | None = None
) -> StructuredLLMProvider:
    return create_structured_llm_provider(
        StructuredProviderConfig(
            provider=settings.strategy_builder_llm_provider,
            model=settings.strategy_builder_llm_model,
            timeout_seconds=settings.strategy_builder_llm_timeout_seconds,
            max_retries=settings.strategy_builder_llm_max_retries,
            max_concurrency=settings.strategy_builder_llm_max_concurrency,
            openai_api_key_ref=settings.strategy_builder_openai_api_key_ref,
            anthropic_api_key_ref=settings.strategy_builder_anthropic_api_key_ref,
            deepinfra_api_key_ref=settings.strategy_builder_deepinfra_api_key_ref,
            nvidia_api_key_ref=settings.strategy_builder_nvidia_api_key_ref,
            ollama_base_url=settings.strategy_builder_ollama_base_url,
            nvidia_base_url=settings.strategy_builder_nvidia_base_url,
            deepinfra_base_url=settings.strategy_builder_deepinfra_base_url,
        ),
        settings=settings,
        resolver=resolver,
        allow_mock=False,
    )


def validate_market_research_llm_settings(settings: BackendSettings) -> None:
    provider = settings.market_research_llm_provider.strip().lower()
    if not settings.is_production:
        return
    if provider in {"mock", "disabled", "ollama", "nvidia", ""}:
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
    data_provider = settings.market_research_data_provider.strip().lower() or "demo"
    warnings: list[str] = []
    if provider == "mock":
        warnings.append("Mock LLM provider is active; no local or hosted model will be called.")
    if provider == "disabled":
        warnings.append("Market research LLM generation is disabled; deterministic agent outputs will be used.")
    if data_provider == "demo":
        warnings.append("Demo market data provider is active; report warnings about demo data are expected.")
    if settings.market_research_allow_demo_fallback:
        warnings.append("Development demo fallback is enabled for market-data provider outages.")
    if data_provider == "cached_yahoo":
        warnings.append("Direct news and fundamentals providers are not configured; research context is degraded.")
    if provider == "ollama" and (
        settings.market_research_agent_timeout_seconds < 120 or settings.market_research_llm_timeout_seconds < 120
    ):
        warnings.append("Ollama local models can be slow; use at least 120s for agent and LLM timeouts.")
    if provider == "nvidia":
        warnings.append("NVIDIA Build free endpoints are enabled for research only; production startup rejects this provider.")
        warnings.append(
            "NVIDIA free-endpoint calls are capped and fail-fast in this application; use production providers for SLA-backed runs."
        )
        if resolve_nvidia_model(settings.market_research_llm_model) is None:
            warnings.append("Configured NVIDIA model is not in the vetted free-endpoint research catalog.")
    nvidia_payload = nvidia_model_catalog_payload()
    try:
        nvidia_payload["api_key_configured"] = bool(_configured_secret(settings, provider="nvidia", resolver=EnvSecretResolver(settings)))
    except Exception:
        nvidia_payload["api_key_configured"] = False

    diagnostics: dict[str, object] = {
        "llm_provider": provider,
        "llm_model": settings.market_research_llm_model,
        "data_provider": data_provider,
        "data_provider_policy": {
            "allow_demo_fallback": settings.market_research_allow_demo_fallback,
            "synthetic_data_configured": data_provider == "demo",
            "production_compatible": data_provider == "cached_yahoo" and not settings.market_research_allow_demo_fallback,
        },
        "agent_timeout_seconds": settings.market_research_agent_timeout_seconds,
        "llm_timeout_seconds": settings.market_research_llm_timeout_seconds,
        "llm_max_retries": settings.market_research_llm_max_retries,
        "llm_max_concurrency": settings.market_research_llm_max_concurrency,
        "free_endpoint_timeout_cap_seconds": settings.market_research_free_endpoint_timeout_cap_seconds,
        "llm_fail_fast_after_failures": settings.market_research_llm_fail_fast_after_failures,
        "model_override_enabled": settings.market_research_allow_request_model_override and not settings.is_production,
        "nvidia": nvidia_payload,
        "warnings": warnings,
    }
    if provider == "ollama":
        diagnostics["ollama"] = probe_ollama_runtime(settings)
    return diagnostics


def preflight_market_research_llm(settings: BackendSettings) -> None:
    provider = settings.market_research_llm_provider.strip().lower() or "mock"
    if provider == "nvidia":
        spec = resolve_nvidia_model(settings.market_research_llm_model)
        if spec is None:
            raise LLMProviderUnavailable(
                f"NVIDIA model '{settings.market_research_llm_model}' is not in the vetted research catalog."
            )
        if not spec.market_research_compatible:
            raise LLMProviderUnavailable(
                f"NVIDIA model '{spec.id}' is a {spec.category} endpoint and cannot be used as a market-research chat LLM."
            )
        resolver = EnvSecretResolver(settings)
        ref = settings.market_research_nvidia_api_key_ref or "env:NVIDIA_API_KEY"
        try:
            secret = resolver.resolve(ref)
        except Exception as exc:
            raise LLMProviderUnavailable("NVIDIA market research secret could not be resolved.") from exc
        if not secret:
            raise LLMProviderUnavailable("Configure NVIDIA_API_KEY or PAIRS_TRADING_MARKET_RESEARCH_NVIDIA_API_KEY_REF.")
        return
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
    return create_structured_llm_provider(
        StructuredProviderConfig(
            provider=settings.market_research_llm_provider or "mock",
            model=settings.market_research_llm_model,
            timeout_seconds=settings.market_research_llm_timeout_seconds,
            max_retries=settings.market_research_llm_max_retries,
            max_concurrency=settings.market_research_llm_max_concurrency,
            openai_api_key_ref=settings.market_research_openai_api_key_ref,
            anthropic_api_key_ref=settings.market_research_anthropic_api_key_ref,
            nvidia_api_key_ref=settings.market_research_nvidia_api_key_ref,
            ollama_base_url=settings.market_research_ollama_base_url,
            nvidia_base_url=settings.market_research_nvidia_base_url,
        ),
        settings=settings,
        resolver=resolver,
        allow_mock=True,
    )
