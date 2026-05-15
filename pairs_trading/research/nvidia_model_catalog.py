from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NvidiaFreeEndpointModel:
    id: str
    display_name: str
    provider: str
    category: str
    endpoint: str
    recommendation: str
    recommended_for: tuple[str, ...]
    limitations: tuple[str, ...]
    market_research_compatible: bool = False
    preview: bool = True
    production_ready: bool = False
    notes: tuple[str, ...] = ()

    def to_runtime_payload(self) -> dict[str, Any]:
        return {
            "provider": "nvidia",
            "model": self.id,
            "display_name": self.display_name,
            "model_provider": self.provider,
            "category": self.category,
            "endpoint": self.endpoint,
            "recommendation": self.recommendation,
            "recommended_for": list(self.recommended_for),
            "limitations": list(self.limitations),
            "market_research_compatible": self.market_research_compatible,
            "preview": self.preview,
            "production_ready": self.production_ready,
            "notes": list(self.notes),
        }


NVIDIA_FREE_ENDPOINT_MODELS: tuple[NvidiaFreeEndpointModel, ...] = (
    NvidiaFreeEndpointModel(
        id="mistralai/mistral-large-3-675b-instruct-2512",
        display_name="Mistral Large 3 675B Instruct",
        provider="Mistral AI",
        category="multimodal_llm",
        endpoint="chat_completions",
        recommendation="conditionally_recommended",
        recommended_for=(
            "financial_news_analysis",
            "summarisation",
            "structured_extraction",
            "agentic_research",
            "vision_language_analysis",
        ),
        limitations=(
            "NVIDIA free endpoint is for evaluation/prototyping, not production.",
            "Not finance-specialized; validate every extracted ticker, entity, and risk signal.",
        ),
        market_research_compatible=True,
    ),
    NvidiaFreeEndpointModel(
        id="mistralai/mistral-nemotron",
        display_name="Mistral-Nemotron",
        provider="Mistral AI / NVIDIA",
        category="llm",
        endpoint="chat_completions",
        recommendation="conditionally_recommended",
        recommended_for=(
            "agentic_research",
            "financial_news_analysis",
            "function_calling",
            "summarisation",
            "structured_extraction",
        ),
        limitations=(
            "NVIDIA free endpoint is for evaluation/prototyping, not production.",
            "Commercial self-hosting/custom deployment requires separate model-license review.",
        ),
        market_research_compatible=True,
    ),
    NvidiaFreeEndpointModel(
        id="qwen/qwen3-coder-480b-a35b-instruct",
        display_name="Qwen3-Coder 480B A35B Instruct",
        provider="Qwen",
        category="code_llm",
        endpoint="chat_completions",
        recommendation="conditionally_recommended",
        recommended_for=(
            "coding_automation",
            "agentic_workflows",
            "browser_use",
            "tool_calling",
            "research_code_generation",
        ),
        limitations=(
            "Best suited for coding/automation; do not treat as the primary finance analysis model without benchmarks.",
            "NVIDIA free endpoint is for evaluation/prototyping, not production.",
        ),
        market_research_compatible=True,
    ),
    NvidiaFreeEndpointModel(
        id="stepfun-ai/step-3.5-flash",
        display_name="Step 3.5 Flash",
        provider="Stepfun AI",
        category="llm",
        endpoint="chat_completions",
        recommendation="needs_further_testing",
        recommended_for=(
            "agentic_research",
            "coding_automation",
            "reasoning",
            "structured_extraction",
        ),
        limitations=(
            "Newer model; benchmark claims need local validation.",
            "NVIDIA free endpoint is for evaluation/prototyping, not production.",
        ),
        market_research_compatible=True,
    ),
    NvidiaFreeEndpointModel(
        id="minimaxai/minimax-m2.7",
        display_name="MiniMax M2.7",
        provider="MiniMax AI",
        category="llm",
        endpoint="chat_completions",
        recommendation="needs_further_testing",
        recommended_for=(
            "long_context_research",
            "coding_automation",
            "summarisation",
            "structured_extraction",
        ),
        limitations=(
            "License terms need legal review before commercial use.",
            "NVIDIA free endpoint is for evaluation/prototyping, not production.",
        ),
        market_research_compatible=True,
    ),
    NvidiaFreeEndpointModel(
        id="meta/llama-4-maverick-17b-128e-instruct",
        display_name="Llama 4 Maverick 17B 128E Instruct",
        provider="Meta",
        category="multimodal_llm",
        endpoint="chat_completions",
        recommendation="needs_further_testing",
        recommended_for=(
            "vision_language_analysis",
            "summarisation",
            "financial_news_analysis",
            "structured_extraction",
        ),
        limitations=(
            "Llama license requires review before commercial deployment.",
            "NVIDIA free endpoint is for evaluation/prototyping, not production.",
        ),
        market_research_compatible=True,
    ),
    NvidiaFreeEndpointModel(
        id="microsoft/phi-4-multimodal-instruct",
        display_name="Phi-4 Multimodal Instruct",
        provider="Microsoft",
        category="multimodal_llm",
        endpoint="chat_completions",
        recommendation="needs_further_testing",
        recommended_for=(
            "chart_image_analysis",
            "audio_news_experiments",
            "lightweight_extraction",
        ),
        limitations=(
            "Small model; not a primary financial reasoning model.",
            "NVIDIA free endpoint is for evaluation/prototyping, not production.",
        ),
        market_research_compatible=True,
    ),
    NvidiaFreeEndpointModel(
        id="google/gemma-3n-e4b-it",
        display_name="Gemma 3n E4B IT",
        provider="Google",
        category="multimodal_edge_llm",
        endpoint="chat_completions",
        recommendation="needs_further_testing",
        recommended_for=(
            "lightweight_multimodal_experiments",
            "quick_extraction_smoke_tests",
        ),
        limitations=(
            "Small model with a lower quality ceiling for nuanced financial analysis.",
            "NVIDIA free endpoint is for evaluation/prototyping, not production.",
        ),
        market_research_compatible=True,
    ),
    NvidiaFreeEndpointModel(
        id="google/gemma-3n-e2b-it",
        display_name="Gemma 3n E2B IT",
        provider="Google",
        category="multimodal_edge_llm",
        endpoint="chat_completions",
        recommendation="needs_further_testing",
        recommended_for=(
            "low_cost_smoke_tests",
            "lightweight_multimodal_experiments",
        ),
        limitations=(
            "Very small model; expect weak financial/news nuance.",
            "NVIDIA free endpoint is for evaluation/prototyping, not production.",
        ),
        market_research_compatible=True,
    ),
    NvidiaFreeEndpointModel(
        id="bytedance/seed-oss-36b-instruct",
        display_name="Seed OSS 36B Instruct",
        provider="ByteDance",
        category="llm",
        endpoint="chat_completions",
        recommendation="needs_further_testing",
        recommended_for=(
            "reasoning_experiments",
            "agentic_research",
            "structured_extraction",
        ),
        limitations=(
            "Quality, latency, and structured-output reliability need validation.",
            "NVIDIA free endpoint is for evaluation/prototyping, not production.",
        ),
        market_research_compatible=True,
    ),
    NvidiaFreeEndpointModel(
        id="abacusai/dracarys-llama-3.1-70b-instruct",
        display_name="Dracarys Llama 3.1 70B Instruct",
        provider="Abacus.AI",
        category="llm",
        endpoint="chat_completions",
        recommendation="needs_further_testing",
        recommended_for=(
            "summarisation",
            "coding_automation",
            "general_research",
        ),
        limitations=(
            "Older Llama derivative; benchmark against newer alternatives before adoption.",
            "NVIDIA free endpoint is for evaluation/prototyping, not production.",
        ),
        market_research_compatible=True,
        preview=False,
    ),
    NvidiaFreeEndpointModel(
        id="nvidia/nemotron-mini-4b-instruct",
        display_name="Nemotron Mini 4B Instruct",
        provider="NVIDIA",
        category="small_llm",
        endpoint="chat_completions",
        recommendation="needs_further_testing",
        recommended_for=(
            "fast_local_style_smoke_tests",
            "lightweight_rag_experiments",
            "function_calling_experiments",
        ),
        limitations=(
            "Too small for final financial-news analysis without strong retrieval and validation.",
            "NVIDIA free endpoint is for evaluation/prototyping, not production.",
        ),
        market_research_compatible=True,
        preview=False,
    ),
    NvidiaFreeEndpointModel(
        id="nvidia/rerank-qa-mistral-4b",
        display_name="Rerank QA Mistral 4B",
        provider="NVIDIA",
        category="reranker",
        endpoint="retrieval_reranking",
        recommendation="conditionally_recommended",
        recommended_for=("rag_reranking", "qa_retrieval"),
        limitations=(
            "Not a generative market-research model.",
            "Call path differs from chat completions and should be benchmarked separately.",
        ),
        preview=False,
    ),
    NvidiaFreeEndpointModel(
        id="nvidia/nv-embedcode-7b-v1",
        display_name="NV-EmbedCode 7B v1",
        provider="NVIDIA",
        category="embedding",
        endpoint="embeddings",
        recommendation="conditionally_recommended",
        recommended_for=("code_retrieval", "automation_context_search"),
        limitations=(
            "Optimized for code retrieval, not financial-news semantic search.",
            "Embedding dimensionality/storage costs must be planned before large indexing.",
        ),
        preview=False,
    ),
    NvidiaFreeEndpointModel(
        id="nvidia/gliner-pii",
        display_name="GLiNER PII",
        provider="NVIDIA",
        category="pii_detection",
        endpoint="token_classification",
        recommendation="conditionally_recommended",
        recommended_for=("pii_prefiltering", "privacy_review"),
        limitations=(
            "Detects PII spans; it is not a market entity/ticker extractor.",
            "Should supplement, not replace, policy and compliance review.",
        ),
        preview=False,
    ),
    NvidiaFreeEndpointModel(
        id="meta/llama-guard-4-12b",
        display_name="Llama Guard 4 12B",
        provider="Meta",
        category="safety",
        endpoint="safety_classification",
        recommendation="conditionally_recommended",
        recommended_for=("input_output_safety", "agent_guardrails"),
        limitations=(
            "Not a financial/news analysis model.",
            "Safety taxonomies need project-specific calibration.",
        ),
    ),
    NvidiaFreeEndpointModel(
        id="nvidia/nemotron-content-safety-reasoning-4b",
        display_name="Nemotron Content Safety Reasoning 4B",
        provider="NVIDIA",
        category="safety",
        endpoint="safety_classification",
        recommendation="conditionally_recommended",
        recommended_for=("domain_policy_checks", "agent_guardrails"),
        limitations=(
            "Not a financial/news analysis model.",
            "Policy prompts and false-positive rates need local testing.",
        ),
        preview=False,
    ),
    NvidiaFreeEndpointModel(
        id="nvidia/riva-translate-4b-instruct-v1.1",
        display_name="Riva Translate 4B Instruct v1.1",
        provider="NVIDIA",
        category="translation",
        endpoint="translation",
        recommendation="needs_further_testing",
        recommended_for=("multilingual_news_ingestion",),
        limitations=(
            "Translation only; pair with a separate analysis model.",
            "Language coverage and terminology fidelity need validation on finance/news text.",
        ),
        preview=False,
    ),
)

_MODEL_BY_ID = {model.id.lower(): model for model in NVIDIA_FREE_ENDPOINT_MODELS}
_MODEL_BY_SHORT_NAME = {model.id.rsplit("/", 1)[-1].lower(): model for model in NVIDIA_FREE_ENDPOINT_MODELS}
_MODEL_BY_DISPLAY_NAME = {model.display_name.lower(): model for model in NVIDIA_FREE_ENDPOINT_MODELS}


def resolve_nvidia_model(value: str) -> NvidiaFreeEndpointModel | None:
    key = str(value or "").strip().lower()
    if not key:
        return None
    return _MODEL_BY_ID.get(key) or _MODEL_BY_SHORT_NAME.get(key) or _MODEL_BY_DISPLAY_NAME.get(key)


def nvidia_market_research_models() -> tuple[NvidiaFreeEndpointModel, ...]:
    return tuple(model for model in NVIDIA_FREE_ENDPOINT_MODELS if model.market_research_compatible)


def nvidia_utility_models() -> tuple[NvidiaFreeEndpointModel, ...]:
    return tuple(model for model in NVIDIA_FREE_ENDPOINT_MODELS if not model.market_research_compatible)


def nvidia_model_catalog_payload() -> dict[str, Any]:
    return {
        "models": [model.to_runtime_payload() for model in NVIDIA_FREE_ENDPOINT_MODELS],
        "market_research_models": [model.to_runtime_payload() for model in nvidia_market_research_models()],
        "utility_models": [model.to_runtime_payload() for model in nvidia_utility_models()],
        "caveats": [
            "NVIDIA Build free endpoints are treated as research/prototype endpoints in this application.",
            "Production startup intentionally rejects NVIDIA free-endpoint configuration unless the production policy is changed after legal, privacy, quota, and SLA review.",
            "Models marked needs_further_testing require task-specific validation before use in automated research workflows.",
        ],
    }
