from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from threading import BoundedSemaphore
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, ValidationError


T = TypeVar("T", bound=BaseModel)


class LLMProviderError(RuntimeError):
    """Base error for hosted structured-output providers."""


class LLMSchemaValidationError(LLMProviderError):
    """Raised when a provider response cannot be parsed into the requested schema."""


class LLMProviderUnavailable(LLMProviderError):
    """Raised when provider credentials, dependencies, or endpoints are unavailable."""


@dataclass(frozen=True)
class LLMCallResult(Generic[T]):
    value: T
    provider: str
    model: str
    latency_ms: int
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class StructuredLLMProvider(Protocol):
    provider_name: str
    model_name: str

    def generate_structured(self, prompt: str, schema: type[T], options: dict[str, Any] | None = None) -> LLMCallResult[T]:
        ...


def _schema_name(schema: type[BaseModel]) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in schema.__name__)[:64] or "StructuredOutput"


def _schema_payload(schema: type[BaseModel]) -> dict[str, Any]:
    return schema.model_json_schema()


def _validate_payload(payload: Any, schema: type[T], *, provider: str) -> T:
    try:
        if isinstance(payload, str):
            payload = json.loads(payload)
        return schema.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise LLMSchemaValidationError(f"{provider} returned output that did not match {schema.__name__}: {exc}") from exc


def _extract_json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("output_text"), str):
            return str(value["output_text"])
        pieces: list[str] = []
        for output in value.get("output", []) if isinstance(value.get("output"), list) else []:
            if not isinstance(output, dict):
                continue
            for content in output.get("content", []) if isinstance(output.get("content"), list) else []:
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str):
                    pieces.append(text)
        if pieces:
            return "".join(pieces)
    raise LLMSchemaValidationError("Provider response did not contain structured output text.")


def _extract_json_document(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


class MockStructuredLLMProvider:
    """Deterministic provider used for tests and local demo mode."""

    provider_name = "mock"
    model_name = "mock-research-v1"

    def generate_structured(self, prompt: str, schema: type[T], options: dict[str, Any] | None = None) -> LLMCallResult[T]:
        del options
        started = time.perf_counter()
        if hasattr(schema, "model_validate"):
            from .market_research_prompts import PROMPT_VERSION

            payload = {
                "agent_name": "mock_llm",
                "display_name": "Mock LLM",
                "version": "mock",
                "prompt_version": PROMPT_VERSION,
                "summary": "Mock structured output; no hosted model was called.",
                "signals": [],
                "confidence": 50,
                "warnings": ["Mock LLM provider used; no hosted model was called."],
                "details": {"prompt_preview": prompt[:120]},
            }
            return LLMCallResult(
                value=_validate_payload(payload, schema, provider=self.provider_name),
                provider=self.provider_name,
                model=self.model_name,
                latency_ms=int((time.perf_counter() - started) * 1000),
                metadata={"mock": True},
                warnings=["Mock LLM provider used; no hosted model was called."],
            )
        raise LLMSchemaValidationError(f"Mock provider cannot build {schema!r}.")


class OpenAIStructuredLLMProvider:
    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 1,
        max_concurrency: int = 2,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        if not api_key:
            raise LLMProviderUnavailable("OpenAI API key is not configured.")
        self.api_key = api_key
        self.model_name = model
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max(0, int(max_retries))
        self.base_url = base_url.rstrip("/")
        self._semaphore = BoundedSemaphore(max(1, int(max_concurrency)))

    def generate_structured(self, prompt: str, schema: type[T], options: dict[str, Any] | None = None) -> LLMCallResult[T]:
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - covered by packaging tests
            raise LLMProviderUnavailable("Install backend dependencies with httpx to use OpenAI structured output.") from exc

        opts = options or {}
        last_error: Exception | None = None
        prompt_text = prompt
        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            try:
                with self._semaphore:
                    response = httpx.post(
                        f"{self.base_url}/responses",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self.model_name,
                            "input": [
                                {"role": "system", "content": opts.get("system") or "Return only schema-valid structured research output."},
                                {"role": "user", "content": prompt_text},
                            ],
                            "text": {
                                "format": {
                                    "type": "json_schema",
                                    "name": _schema_name(schema),
                                    "schema": _schema_payload(schema),
                                    "strict": bool(opts.get("strict", True)),
                                }
                            },
                            "temperature": float(opts.get("temperature", 0.2)),
                            "max_output_tokens": int(opts.get("max_output_tokens", 1800)),
                        },
                        timeout=self.timeout_seconds,
                    )
                if response.status_code >= 400:
                    raise LLMProviderError(f"OpenAI structured output failed with HTTP {response.status_code}.")
                payload = response.json()
                value = _validate_payload(_extract_json_text(payload), schema, provider=self.provider_name)
                return LLMCallResult(
                    value=value,
                    provider=self.provider_name,
                    model=self.model_name,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    usage=dict(payload.get("usage") or {}),
                    metadata={"response_id": payload.get("id"), "attempt": attempt + 1},
                )
            except Exception as exc:
                last_error = exc
                prompt_text = f"{prompt}\n\nThe previous structured output failed validation: {exc}\nReturn valid JSON only."
        raise LLMProviderError(f"OpenAI structured output failed after retries: {last_error}") from last_error


class DeepInfraStructuredLLMProvider:
    """DeepInfra OpenAI-compatible chat provider with structured-output validation."""

    provider_name = "deepinfra"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 1,
        max_concurrency: int = 2,
        base_url: str = "https://api.deepinfra.com/v1/openai",
    ) -> None:
        if not api_key:
            raise LLMProviderUnavailable("DeepInfra API key is not configured.")
        self.api_key = api_key
        self.model_name = model
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max(0, int(max_retries))
        self.base_url = base_url.rstrip("/")
        self._semaphore = BoundedSemaphore(max(1, int(max_concurrency)))

    def generate_structured(self, prompt: str, schema: type[T], options: dict[str, Any] | None = None) -> LLMCallResult[T]:
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - covered by packaging tests
            raise LLMProviderUnavailable("Install backend dependencies with httpx to use DeepInfra structured output.") from exc

        opts = options or {}
        last_error: Exception | None = None
        prompt_text = prompt
        system = str(opts.get("system") or "Return only schema-valid structured output.")
        schema_payload = _schema_payload(schema)
        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            for response_format in (
                {
                    "type": "json_schema",
                    "json_schema": {
                        "name": _schema_name(schema),
                        "strict": bool(opts.get("strict", True)),
                        "schema": schema_payload,
                    },
                },
                {"type": "json_object"},
            ):
                try:
                    user_content = prompt_text
                    if response_format["type"] == "json_object":
                        user_content = (
                            f"{prompt_text}\n\nReturn a JSON object matching this JSON Schema exactly:\n"
                            f"{json.dumps(schema_payload, separators=(',', ':'), sort_keys=True)}"
                        )
                    with self._semaphore:
                        response = httpx.post(
                            f"{self.base_url}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {self.api_key}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": self.model_name,
                                "messages": [
                                    {"role": "system", "content": system},
                                    {"role": "user", "content": user_content},
                                ],
                                "response_format": response_format,
                                "temperature": float(opts.get("temperature", 0.2)),
                                "max_tokens": int(opts.get("max_output_tokens", 1800)),
                            },
                            timeout=self.timeout_seconds,
                        )
                    if response.status_code >= 400:
                        if response_format["type"] == "json_schema" and response.status_code in {400, 422}:
                            continue
                        raise LLMProviderError(
                            f"DeepInfra structured output failed with HTTP {response.status_code}."
                        )
                    payload = response.json()
                    content = self._extract_message_content(payload)
                    value = _validate_payload(_extract_json_document(content), schema, provider=self.provider_name)
                    warnings = []
                    if response_format["type"] == "json_object":
                        warnings.append(
                            "DeepInfra JSON Schema mode was unavailable; JSON-object mode with local schema validation was used."
                        )
                    return LLMCallResult(
                        value=value,
                        provider=self.provider_name,
                        model=self.model_name,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        usage=dict(payload.get("usage") or {}),
                        metadata={
                            "response_id": payload.get("id"),
                            "attempt": attempt + 1,
                            "response_format": response_format["type"],
                        },
                        warnings=warnings,
                    )
                except httpx.TimeoutException as exc:
                    raise LLMProviderError(
                        f"DeepInfra structured output timed out after {self.timeout_seconds:.1f}s."
                    ) from exc
                except Exception as exc:
                    last_error = exc
                    if response_format["type"] == "json_schema":
                        continue
                    prompt_text = (
                        f"{prompt}\n\nThe previous DeepInfra output failed validation: {exc}\nReturn valid JSON only."
                    )
        raise LLMProviderError(f"DeepInfra structured output failed after retries: {last_error}") from last_error

    @staticmethod
    def _extract_message_content(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return str(message["content"])
        raise LLMSchemaValidationError("DeepInfra response did not include choices[0].message.content.")


class NvidiaStructuredLLMProvider:
    """NVIDIA Build / NIM OpenAI-compatible chat provider for research-stage use."""

    provider_name = "nvidia"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 120.0,
        max_retries: int = 1,
        max_concurrency: int = 1,
        base_url: str = "https://integrate.api.nvidia.com/v1",
    ) -> None:
        if not api_key:
            raise LLMProviderUnavailable("NVIDIA API key is not configured.")
        from .nvidia_model_catalog import resolve_nvidia_model

        spec = resolve_nvidia_model(model)
        if spec is None:
            raise LLMProviderUnavailable(
                f"NVIDIA model '{model}' is not in the vetted research catalog. "
                "Use one of the configured NVIDIA free endpoint model ids."
            )
        if not spec.market_research_compatible:
            raise LLMProviderUnavailable(
                f"NVIDIA model '{spec.id}' is a {spec.category} endpoint and cannot be used as a market-research chat LLM."
            )
        self.api_key = api_key
        self.model_name = spec.id
        self.model_spec = spec
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max(0, int(max_retries))
        self.base_url = base_url.rstrip("/")
        self._semaphore = BoundedSemaphore(max(1, int(max_concurrency)))

    def generate_structured(self, prompt: str, schema: type[T], options: dict[str, Any] | None = None) -> LLMCallResult[T]:
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - covered by packaging tests
            raise LLMProviderUnavailable("Install backend dependencies with httpx to use NVIDIA structured output.") from exc

        opts = options or {}
        last_error: Exception | None = None
        prompt_text = prompt
        system = str(
            opts.get("system")
            or "Return only JSON that matches the requested schema. Do not include markdown, commentary, or extra keys."
        )
        schema_hint = json.dumps(_schema_payload(schema), separators=(",", ":"), sort_keys=True)
        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            for include_response_format in (True, False):
                try:
                    request_body: dict[str, Any] = {
                        "model": self.model_name,
                        "messages": [
                            {"role": "system", "content": system},
                            {
                                "role": "user",
                                "content": (
                                    f"{prompt_text}\n\nReturn a JSON object matching this JSON Schema exactly:\n"
                                    f"{schema_hint}"
                                ),
                            },
                        ],
                        "temperature": float(opts.get("temperature", 0.1)),
                        "max_tokens": int(opts.get("max_output_tokens", 1800)),
                    }
                    if include_response_format:
                        request_body["response_format"] = {"type": "json_object"}
                    with self._semaphore:
                        response = httpx.post(
                            f"{self.base_url}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {self.api_key}",
                                "Content-Type": "application/json",
                            },
                            json=request_body,
                            timeout=self.timeout_seconds,
                        )
                    if response.status_code >= 400:
                        if include_response_format and response.status_code in {400, 422}:
                            continue
                        raise LLMProviderError(f"NVIDIA structured output failed with HTTP {response.status_code}.")
                    payload = response.json()
                    content = self._extract_message_content(payload)
                    value = _validate_payload(_extract_json_document(content), schema, provider=self.provider_name)
                    warnings = [
                        "NVIDIA Build free endpoint used for research only; do not treat this as production-grade inference."
                    ]
                    if not include_response_format:
                        warnings.append("NVIDIA response_format=json_object was unavailable; prompt-only JSON fallback was used.")
                    return LLMCallResult(
                        value=value,
                        provider=self.provider_name,
                        model=self.model_name,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        usage=dict(payload.get("usage") or {}),
                        metadata={
                            "response_id": payload.get("id"),
                            "attempt": attempt + 1,
                            "response_format": "json_object" if include_response_format else "prompt_only_json",
                            "model_recommendation": self.model_spec.recommendation,
                        },
                        warnings=warnings,
                    )
                except httpx.TimeoutException as exc:
                    raise LLMProviderError(
                        f"NVIDIA structured output timed out after {self.timeout_seconds:.1f}s."
                    ) from exc
                except Exception as exc:
                    last_error = exc
                    if include_response_format:
                        continue
                    prompt_text = f"{prompt}\n\nThe previous NVIDIA model output failed validation: {exc}\nReturn valid JSON only."
        raise LLMProviderError(f"NVIDIA structured output failed after retries: {last_error}") from last_error

    @staticmethod
    def _extract_message_content(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str):
                        return content
                    if isinstance(content, list):
                        pieces = [
                            str(item.get("text"))
                            for item in content
                            if isinstance(item, dict) and isinstance(item.get("text"), str)
                        ]
                        if pieces:
                            return "".join(pieces)
                if isinstance(first.get("text"), str):
                    return str(first["text"])
        raise LLMSchemaValidationError("NVIDIA response did not include choices[0].message.content.")


class OllamaStructuredLLMProvider:
    """Local development provider for free/open-weight models served by Ollama."""

    provider_name = "ollama"

    def __init__(
        self,
        *,
        model: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 1,
        max_concurrency: int = 1,
        base_url: str = "http://127.0.0.1:11434",
    ) -> None:
        if not model:
            raise LLMProviderUnavailable("Ollama model is not configured.")
        self.model_name = model
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max(0, int(max_retries))
        self.base_url = base_url.rstrip("/")
        self._semaphore = BoundedSemaphore(max(1, int(max_concurrency)))

    def generate_structured(self, prompt: str, schema: type[T], options: dict[str, Any] | None = None) -> LLMCallResult[T]:
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - covered by packaging tests
            raise LLMProviderUnavailable("Install backend dependencies with httpx to use Ollama structured output.") from exc

        opts = options or {}
        last_error: Exception | None = None
        prompt_text = prompt
        system = opts.get("system") or (
            "Return only JSON that matches the requested schema. Do not include markdown, commentary, or extra keys."
        )
        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            for format_payload in (_schema_payload(schema), "json"):
                try:
                    with self._semaphore:
                        response = httpx.post(
                            f"{self.base_url}/api/chat",
                            json={
                                "model": self.model_name,
                                "stream": False,
                                "messages": [
                                    {"role": "system", "content": str(system)},
                                    {"role": "user", "content": prompt_text},
                                ],
                                "format": format_payload,
                                "options": {
                                    "temperature": float(opts.get("temperature", 0.1)),
                                    "num_predict": int(opts.get("max_output_tokens", 1800)),
                                },
                            },
                            timeout=self.timeout_seconds,
                        )
                    if response.status_code >= 400:
                        raise LLMProviderError(
                            f"Ollama structured output failed with HTTP {response.status_code}. "
                            f"Ensure Ollama is running and model '{self.model_name}' is pulled."
                        )
                    payload = response.json()
                    content = self._extract_message_content(payload)
                    value = _validate_payload(content, schema, provider=self.provider_name)
                    metadata = {
                        "attempt": attempt + 1,
                        "format": "json_schema" if isinstance(format_payload, dict) else "json",
                        "total_duration": payload.get("total_duration"),
                        "load_duration": payload.get("load_duration"),
                    }
                    usage = {
                        "prompt_eval_count": payload.get("prompt_eval_count"),
                        "eval_count": payload.get("eval_count"),
                    }
                    warnings = [] if isinstance(format_payload, dict) else ["Ollama JSON-schema mode was unavailable; JSON mode fallback was used."]
                    return LLMCallResult(
                        value=value,
                        provider=self.provider_name,
                        model=self.model_name,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        usage={key: value for key, value in usage.items() if value is not None},
                        metadata={key: value for key, value in metadata.items() if value is not None},
                        warnings=warnings,
                    )
                except Exception as exc:
                    last_error = exc
                    # Older Ollama builds accept format="json" but not a JSON schema object.
                    if isinstance(format_payload, dict):
                        continue
                    prompt_text = f"{prompt}\n\nThe previous local model output failed validation: {exc}\nReturn valid JSON only."
        raise LLMProviderError(f"Ollama structured output failed after retries: {last_error}") from last_error

    @staticmethod
    def _extract_message_content(payload: dict[str, Any]) -> str:
        message = payload.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return str(message["content"])
        if isinstance(payload.get("response"), str):
            return str(payload["response"])
        raise LLMSchemaValidationError("Ollama response did not include message.content.")


class AnthropicStructuredLLMProvider:
    provider_name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 1,
        max_concurrency: int = 2,
        base_url: str = "https://api.anthropic.com/v1",
    ) -> None:
        if not api_key:
            raise LLMProviderUnavailable("Anthropic API key is not configured.")
        self.api_key = api_key
        self.model_name = model
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max(0, int(max_retries))
        self.base_url = base_url.rstrip("/")
        self._semaphore = BoundedSemaphore(max(1, int(max_concurrency)))

    def generate_structured(self, prompt: str, schema: type[T], options: dict[str, Any] | None = None) -> LLMCallResult[T]:
        try:
            import httpx
        except Exception as exc:  # pragma: no cover - covered by packaging tests
            raise LLMProviderUnavailable("Install backend dependencies with httpx to use Anthropic structured output.") from exc

        opts = options or {}
        last_error: Exception | None = None
        prompt_text = prompt
        tool_name = "emit_structured_output"
        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            try:
                with self._semaphore:
                    response = httpx.post(
                        f"{self.base_url}/messages",
                        headers={
                            "x-api-key": self.api_key,
                            "anthropic-version": str(opts.get("anthropic_version") or "2023-06-01"),
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self.model_name,
                            "max_tokens": int(opts.get("max_output_tokens", 1800)),
                            "temperature": float(opts.get("temperature", 0.2)),
                            "system": opts.get("system") or "Use the provided tool exactly once with schema-valid research output.",
                            "messages": [{"role": "user", "content": prompt_text}],
                            "tools": [
                                {
                                    "name": tool_name,
                                    "description": f"Emit a schema-valid {_schema_name(schema)} object.",
                                    "input_schema": _schema_payload(schema),
                                }
                            ],
                            "tool_choice": {"type": "tool", "name": tool_name},
                        },
                        timeout=self.timeout_seconds,
                    )
                if response.status_code >= 400:
                    raise LLMProviderError(f"Anthropic structured output failed with HTTP {response.status_code}.")
                payload = response.json()
                tool_payload = self._extract_tool_payload(payload, tool_name)
                value = _validate_payload(tool_payload, schema, provider=self.provider_name)
                return LLMCallResult(
                    value=value,
                    provider=self.provider_name,
                    model=self.model_name,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    usage=dict(payload.get("usage") or {}),
                    metadata={"response_id": payload.get("id"), "attempt": attempt + 1},
                )
            except Exception as exc:
                last_error = exc
                prompt_text = f"{prompt}\n\nThe previous tool input failed validation: {exc}\nUse the tool with valid JSON only."
        raise LLMProviderError(f"Anthropic structured output failed after retries: {last_error}") from last_error

    @staticmethod
    def _extract_tool_payload(payload: dict[str, Any], tool_name: str) -> dict[str, Any]:
        for item in payload.get("content", []) if isinstance(payload.get("content"), list) else []:
            if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name") == tool_name:
                tool_input = item.get("input")
                if isinstance(tool_input, dict):
                    return tool_input
        raise LLMSchemaValidationError("Anthropic response did not include the expected structured-output tool call.")
