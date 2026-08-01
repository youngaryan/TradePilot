from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel

from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.schemas import BacktestRunRequest
from pairs_trading.backend.services import BacktestService
from pairs_trading.backend.strategy_builder import StrategyBuilderService, _build_draft_from_text
from pairs_trading.data.market import DataRequest


PROMPT = (
    "Trade SPY and QQQ on daily bars. Buy equal weight when RSI 14 is below 30, "
    "exit above 55, use a 10% stop loss and 3 bps costs."
)

PROVIDERS = (
    ("openai", "test-openai-structured"),
    ("anthropic", "test-anthropic-structured"),
    ("deepinfra", "deepseek-ai/DeepSeek-V3"),
    ("nvidia", "mistralai/mistral-large-3-675b-instruct-2512"),
    ("ollama", "llama3.2:3b"),
)


class _Response:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


def _provider_response(provider: str, candidate: dict) -> dict:
    envelope = {
        "candidate_spec": candidate,
        "state": "ready_for_validation",
        "clarification_questions": [],
        "assistant_summary": "A bounded RSI mean-reversion candidate for deterministic review.",
    }
    if provider == "openai":
        return {"id": "resp_test", "output_text": json.dumps(envelope), "usage": {"input_tokens": 100, "output_tokens": 200}}
    if provider == "anthropic":
        return {
            "id": "msg_test",
            "content": [{"type": "tool_use", "name": "emit_structured_output", "input": envelope}],
            "usage": {"input_tokens": 100, "output_tokens": 200},
        }
    if provider in {"deepinfra", "nvidia"}:
        return {
            "id": "chat_test",
            "choices": [{"message": {"content": json.dumps(envelope)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200},
        }
    return {
        "message": {"role": "assistant", "content": json.dumps({
            "accepted": True,
        })},
        "prompt_eval_count": 100,
        "eval_count": 200,
    }


def _settings(root: Path, provider: str, model: str, *, max_retries: int = 1) -> BackendSettings:
    return BackendSettings(
        strategy_builder_mode="llm",
        strategy_builder_llm_provider=provider,
        strategy_builder_llm_model=model,
        strategy_builder_llm_max_retries=max_retries,
        strategy_builder_openai_api_key_ref="env:TEST_OPENAI_KEY",
        strategy_builder_anthropic_api_key_ref="env:TEST_ANTHROPIC_KEY",
        strategy_builder_deepinfra_api_key_ref="env:TEST_DEEPINFRA_KEY",
        strategy_builder_deepinfra_base_url="https://deepinfra.test/v1/openai",
        strategy_builder_nvidia_api_key_ref="env:TEST_NVIDIA_KEY",
        strategy_builder_nvidia_base_url="https://nvidia.test/v1",
        strategy_builder_ollama_base_url="http://ollama.test:11434",
        metadata_db_path=root / "metadata.sqlite3",
        price_cache_dir=root / "price_cache",
        backtest_artifact_root=root / "backtests",
        backtest_job_state_dir=root / "backtest_jobs",
        paper_state_dir=root / "paper_state",
        paper_artifact_root=root / "paper_runs",
        paper_job_state_dir=root / "paper_jobs",
        sentiment_job_state_dir=root / "sentiment_jobs",
        default_paper_config=root / "missing.json",
    )


def _seed_prices(cache_dir: Path, *, start: str, end: str) -> None:
    request = DataRequest.from_inputs(["SPY", "QQQ"], start, end, "1d")
    index = pd.bdate_range(start, end, inclusive="left")
    x = np.arange(len(index), dtype=float)
    # Oscillation creates repeated oversold/recovery regimes while the positive
    # drift keeps prices realistic and strictly above zero.
    prices = pd.DataFrame(
        {
            "SPY": 100.0 + 0.035 * x + 11.0 * np.sin(x / 7.0) + 2.5 * np.sin(x / 2.3),
            "QQQ": 140.0 + 0.045 * x + 15.0 * np.sin((x + 3.0) / 8.0) + 3.0 * np.sin(x / 2.7),
        },
        index=index,
    )
    target = cache_dir / request.interval / f"{request.cache_key}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    prices.to_parquet(target)


@pytest.mark.parametrize(("provider", "model"), PROVIDERS)
def test_each_provider_builds_approves_and_really_backtests_strategy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    model: str,
) -> None:
    monkeypatch.setenv("TEST_OPENAI_KEY", "openai-test-secret")
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "anthropic-test-secret")
    monkeypatch.setenv("TEST_DEEPINFRA_KEY", "deepinfra-test-secret")
    monkeypatch.setenv("TEST_NVIDIA_KEY", "nvidia-test-secret")
    candidate, _, state = _build_draft_from_text(PROMPT)
    assert state == "ready_for_approval" and candidate is not None

    calls: list[dict] = []

    def fake_post(url: str, **kwargs: object) -> _Response:
        calls.append({"url": url, **kwargs})
        return _Response(_provider_response(provider, candidate))

    monkeypatch.setattr("httpx.post", fake_post)
    settings = _settings(tmp_path, provider, model)
    settings.validate_capabilities()
    builder = StrategyBuilderService(settings)
    workspace = builder.store.ensure_demo_workspace()
    organization_id = str(workspace["organization_id"])
    user_id = str(workspace["user_id"])

    generated = builder.chat(
        organization_id=organization_id,
        user_id=user_id,
        messages=[{"role": "user", "content": PROMPT}],
    )
    assert generated["state"] == "ready_for_approval"
    assert generated["generation_mode"] == "llm"
    assert generated["provider"] == provider
    assert generated["model"] == model
    assert generated["validation"]["ok"] is True
    assert generated["provenance_token"]
    assert calls and calls[0]["url"].startswith(
        {
            "openai": "https://api.openai.com/v1/responses",
            "anthropic": "https://api.anthropic.com/v1/messages",
            "deepinfra": "https://deepinfra.test/v1/openai/chat/completions",
            "nvidia": "https://nvidia.test/v1/chat/completions",
            "ollama": "http://ollama.test:11434/api/chat",
        }[provider]
    )
    headers = calls[0].get("headers")
    if provider in {"openai", "deepinfra", "nvidia"}:
        assert isinstance(headers, dict) and str(headers.get("Authorization", "")).startswith("Bearer ")
    elif provider == "anthropic":
        assert isinstance(headers, dict) and headers.get("x-api-key") == "anthropic-test-secret"
    else:
        assert headers is None
    if provider == "deepinfra":
        request_body = calls[0].get("json")
        assert isinstance(request_body, dict)
        assert request_body["response_format"]["type"] == "json_schema"
        assert request_body["response_format"]["json_schema"]["strict"] is True

    approved = builder.approve(
        organization_id=organization_id,
        user_id=user_id,
        spec=generated["draft_spec"],
        approval_text=f"Approved test strategy generated through {provider}.",
        provenance_token=generated["provenance_token"],
    )
    strategy = approved["strategy"]
    assert strategy["approval"]["generation"]["provider"] == provider
    assert strategy["approval"]["generation"]["model"] == model
    assert approved["validation"]["dry_run"]["status"] == "passed"
    audit_json = json.dumps(builder.store.list_audit_log(organization_id=organization_id, limit=20))
    for secret in ("openai-test-secret", "anthropic-test-secret", "deepinfra-test-secret", "nvidia-test-secret"):
        assert secret not in audit_json

    start, end = "2020-01-01", "2022-06-01"
    _seed_prices(settings.price_cache_dir, start=start, end=end)
    result = BacktestService(settings).run_backtest(
        BacktestRunRequest(
            pipeline=approved["catalog_item"]["pipeline"],
            symbols=["SPY", "QQQ"],
            start=start,
            end=end,
            train_bars=180,
            test_bars=45,
            step_bars=45,
            purge_bars=5,
            pbo_partitions=4,
        ),
        organization_id=organization_id,
        user_id=user_id,
    )

    assert result["user_strategy"]["id"] == strategy["id"]
    assert result["summary"]["folds"] >= 5
    assert result["fold_metrics_tail"]
    assert result["equity_curve_points"]
    assert result["decision"]["verdict"] in {"paper_candidate", "research_more", "reject_or_redesign"}
    for name in ("total_return", "annualized_return", "sharpe", "max_drawdown", "avg_turnover"):
        assert math.isfinite(float(result["summary"][name]))
    artifact_dir = Path(result["artifact_dir"])
    assert artifact_dir.is_dir()
    assert (artifact_dir / "summary.json").is_file()
    assert (artifact_dir / "validation.json").is_file()
    assert (artifact_dir / "fold_metrics.parquet").is_file()


def test_provider_credentials_are_required_only_for_hosted_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pairs_trading.backend.llm_config import build_strategy_builder_llm_provider
    from pairs_trading.research.llm_providers import LLMProviderUnavailable, OllamaStructuredLLMProvider

    monkeypatch.delenv("TEST_OPENAI_KEY", raising=False)
    with pytest.raises(LLMProviderUnavailable, match="openai API key"):
        build_strategy_builder_llm_provider(_settings(tmp_path / "openai", "openai", "test-model"))

    monkeypatch.delenv("TEST_DEEPINFRA_KEY", raising=False)
    with pytest.raises(LLMProviderUnavailable, match="deepinfra API key"):
        build_strategy_builder_llm_provider(
            _settings(tmp_path / "deepinfra", "deepinfra", "deepseek-ai/DeepSeek-V3")
        )

    local = build_strategy_builder_llm_provider(_settings(tmp_path / "ollama", "ollama", "llama3.2:3b"))
    assert isinstance(local, OllamaStructuredLLMProvider)
    assert local.base_url == "http://ollama.test:11434"


def test_deepinfra_falls_back_to_json_object_with_local_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    from pairs_trading.research.llm_providers import DeepInfraStructuredLLMProvider

    class TinyOutput(BaseModel):
        accepted: bool

    calls: list[dict] = []

    def fake_post(url: str, **kwargs: object) -> _Response:
        calls.append({"url": url, **kwargs})
        if len(calls) == 1:
            return _Response({}, status_code=422)
        return _Response({
            "id": "fallback-response",
            "choices": [{"message": {"content": '{"accepted":true}'}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        })

    monkeypatch.setattr("httpx.post", fake_post)
    provider = DeepInfraStructuredLLMProvider(
        api_key="deepinfra-test-secret",
        model="test-model",
        base_url="https://deepinfra.test/v1/openai",
        max_retries=0,
    )
    result = provider.generate_structured("Review this candidate.", TinyOutput)
    assert result.value.accepted is True
    assert result.provider == "deepinfra"
    assert result.metadata["response_format"] == "json_object"
    assert result.warnings
    assert [call["json"]["response_format"]["type"] for call in calls] == [
        "json_schema",
        "json_object",
    ]


@pytest.mark.parametrize(("provider", "model"), PROVIDERS)
def test_invalid_provider_output_fails_closed_without_approvable_strategy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    model: str,
) -> None:
    monkeypatch.setenv("TEST_OPENAI_KEY", "openai-test-secret")
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "anthropic-test-secret")
    monkeypatch.setenv("TEST_DEEPINFRA_KEY", "deepinfra-test-secret")
    monkeypatch.setenv("TEST_NVIDIA_KEY", "nvidia-test-secret")

    def invalid_post(url: str, **_: object) -> _Response:
        if provider == "openai":
            return _Response({"output_text": "{}"})
        if provider == "anthropic":
            return _Response({"content": [{"type": "tool_use", "name": "emit_structured_output", "input": {}}]})
        if provider in {"deepinfra", "nvidia"}:
            return _Response({"choices": [{"message": {"content": "{}"}}]})
        return _Response({"message": {"content": "{}"}})

    monkeypatch.setattr("httpx.post", invalid_post)
    settings = _settings(tmp_path, provider, model, max_retries=0)
    builder = StrategyBuilderService(settings)
    workspace = builder.store.ensure_demo_workspace()
    result = builder.chat(
        organization_id=str(workspace["organization_id"]),
        user_id=str(workspace["user_id"]),
        messages=[{"role": "user", "content": PROMPT}],
    )
    assert result["state"] == "needs_clarification"
    assert result["draft_spec"] is None
    assert result["provenance_token"] is None
    assert result["validation"]["ok"] is False
    assert "temporarily unavailable" in result["questions"][0]
    assert builder.user_strategies(
        organization_id=str(workspace["organization_id"]),
        user_id=str(workspace["user_id"]),
    ) == []


@pytest.mark.parametrize(("provider", "model"), PROVIDERS)
def test_provider_environment_configuration_round_trips(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    model: str,
) -> None:
    monkeypatch.setenv("PAIRS_TRADING_STRATEGY_BUILDER_MODE", "llm")
    monkeypatch.setenv("PAIRS_TRADING_STRATEGY_BUILDER_LLM_PROVIDER", provider)
    monkeypatch.setenv("PAIRS_TRADING_STRATEGY_BUILDER_LLM_MODEL", model)
    monkeypatch.setenv("PAIRS_TRADING_STRATEGY_BUILDER_NVIDIA_API_KEY_REF", "secret-manager:aws:tradepilot#nvidia")
    monkeypatch.setenv("PAIRS_TRADING_STRATEGY_BUILDER_DEEPINFRA_API_KEY_REF", "secret-manager:aws:tradepilot#deepinfra")
    monkeypatch.setenv("PAIRS_TRADING_STRATEGY_BUILDER_DEEPINFRA_BASE_URL", "https://deepinfra.internal/v1/openai")
    monkeypatch.setenv("PAIRS_TRADING_STRATEGY_BUILDER_NVIDIA_BASE_URL", "https://private-nim.test/v1")
    monkeypatch.setenv("PAIRS_TRADING_STRATEGY_BUILDER_OLLAMA_BASE_URL", "http://ollama.internal:11434")
    settings = BackendSettings.from_env()
    assert settings.strategy_builder_mode == "llm"
    assert settings.strategy_builder_llm_provider == provider
    assert settings.strategy_builder_llm_model == model
    assert settings.strategy_builder_nvidia_api_key_ref == "secret-manager:aws:tradepilot#nvidia"
    assert settings.strategy_builder_deepinfra_api_key_ref == "secret-manager:aws:tradepilot#deepinfra"
    assert settings.strategy_builder_deepinfra_base_url == "https://deepinfra.internal/v1/openai"
    assert settings.strategy_builder_nvidia_base_url == "https://private-nim.test/v1"
    assert settings.strategy_builder_ollama_base_url == "http://ollama.internal:11434"
    settings.validate_capabilities()
