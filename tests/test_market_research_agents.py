from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from pairs_trading.backend.config import BackendSettings, _env_or_dotenv
from pairs_trading.backend.llm_config import (
    build_structured_llm_provider,
    market_research_runtime_diagnostics,
    preflight_market_research_llm,
    probe_ollama_runtime,
    validate_market_research_llm_settings,
)
from pairs_trading.backend.market_research_services import MarketResearchService
from pairs_trading.backend.schemas import MarketResearchRunRequest
from pairs_trading.backend.secrets import SecretProvider
from pairs_trading.research.llm_providers import LLMCallResult, LLMProviderError, NvidiaStructuredLLMProvider, OllamaStructuredLLMProvider
from pairs_trading.research.market_research_agents import (
    AgentOutput,
    BearResearcher,
    BullResearcher,
    DemoMarketResearchDataProvider,
    FundamentalAnalyst,
    MarketResearchContext,
    MarketResearchInput,
    MarketResearchOrchestrator,
    MarketResearchReport,
    NewsSentimentAnalyst,
    PortfolioRiskManager,
    PriceBar,
    ResearchDecision,
    ResearchHorizon,
    RiskAnalyst,
    SignalDirection,
    TechnicalAnalyst,
    TraderSynthesizer,
)
from pairs_trading.research.market_research_prompts import RESEARCH_DISCLAIMER
from pairs_trading.research.nvidia_model_catalog import nvidia_market_research_models, nvidia_model_catalog_payload


class FakeStructuredLLMProvider:
    provider_name = "fake"
    model_name = "fake-structured-v1"

    def __init__(self) -> None:
        self.calls = 0

    def generate_structured(self, prompt, schema, options=None):  # noqa: ANN001
        del prompt, options
        self.calls += 1
        return LLMCallResult(
            value=schema.model_validate(
                {
                    "agent_name": "placeholder",
                    "display_name": "LLM Agent",
                    "summary": "LLM-refined research output grounded in the provided context.",
                    "signals": [],
                    "confidence": 61,
                    "warnings": [],
                    "details": {"llm_refined": True},
                }
            ),
            provider=self.provider_name,
            model=self.model_name,
            latency_ms=1,
        )


class FailingStructuredLLMProvider:
    provider_name = "nvidia"
    model_name = "mistralai/mistral-large-3-675b-instruct-2512"

    def __init__(self) -> None:
        self.calls = 0

    def generate_structured(self, prompt, schema, options=None):  # noqa: ANN001
        del prompt, schema, options
        self.calls += 1
        raise RuntimeError("synthetic hosted timeout")


class FakeSecretResolver:
    def __init__(self, values: dict[str, str | None]) -> None:
        self.values = values

    def resolve(self, secret_ref: str) -> str | None:
        return self.values.get(secret_ref)


class FailingNewsAgent:
    agent_name = "news_sentiment_analyst"
    display_name = "News/Sentiment Analyst"
    version = "test-failing"

    def run(self, context, previous_outputs):  # noqa: ANN001
        del context, previous_outputs
        raise RuntimeError("synthetic news failure")


class MarketResearchAgentTests(unittest.TestCase):
    def test_ticker_validation_normalizes_and_rejects_invalid_symbols(self) -> None:
        request = MarketResearchInput(ticker="aapl", analysis_date="2026-05-08", horizon=ResearchHorizon.SWING)
        self.assertEqual(request.ticker, "AAPL")
        with self.assertRaises(ValueError):
            MarketResearchInput(ticker="bad symbol", analysis_date="2026-05-08")

    def test_orchestrator_happy_path_generates_complete_demo_report(self) -> None:
        request = MarketResearchInput(ticker="AAPL", analysis_date="2026-05-08", horizon=ResearchHorizon.SWING)
        context = DemoMarketResearchDataProvider().collect(request)
        report = MarketResearchOrchestrator(per_agent_timeout_seconds=2.0).run(context)
        payload = report.model_dump(mode="json")
        reparsed = MarketResearchReport.model_validate(payload)

        self.assertEqual(reparsed.ticker, "AAPL")
        self.assertIn(reparsed.decision, {ResearchDecision.BUY, ResearchDecision.HOLD, ResearchDecision.SELL, ResearchDecision.AVOID})
        self.assertGreaterEqual(reparsed.confidence, 0)
        self.assertLessEqual(reparsed.confidence, 100)
        self.assertTrue(reparsed.technical_signals)
        self.assertTrue(reparsed.fundamental_signals)
        self.assertTrue(reparsed.news_sentiment_signals)
        self.assertEqual(reparsed.disclaimer, RESEARCH_DISCLAIMER)
        self.assertEqual(len(reparsed.audit_trail), 8)
        self.assertEqual(reparsed.metadata["trade_execution"], "disabled")

    def test_orchestrator_continues_after_partial_agent_failure(self) -> None:
        request = MarketResearchInput(ticker="MSFT", analysis_date="2026-05-08", horizon=ResearchHorizon.LONG_TERM)
        context = DemoMarketResearchDataProvider().collect(request)
        orchestrator = MarketResearchOrchestrator(
            agents=[
                TechnicalAnalyst(),
                FundamentalAnalyst(),
                FailingNewsAgent(),
                RiskAnalyst(),
                BullResearcher(),
                BearResearcher(),
                TraderSynthesizer(),
                PortfolioRiskManager(),
            ],
            per_agent_timeout_seconds=2.0,
        )

        report = orchestrator.run(context)

        self.assertIn("synthetic news failure", " ".join(report.warnings))
        failed = [event for event in report.audit_trail if event.agent_name == "news_sentiment_analyst"]
        self.assertEqual(failed[0].status, "failed")
        self.assertIsNotNone(report.risk_assessment)
        self.assertEqual(report.disclaimer, RESEARCH_DISCLAIMER)

    def test_report_always_contains_disclaimer(self) -> None:
        request = MarketResearchInput(ticker="NVDA", analysis_date="2026-05-08")
        report = MarketResearchOrchestrator().run(DemoMarketResearchDataProvider().collect(request))

        self.assertEqual(report.disclaimer, "For research and educational purposes only. Not financial advice.")
        self.assertIn("Not financial advice", report.summary)

    def test_orchestrator_uses_hosted_structured_llm_provider_when_configured(self) -> None:
        request = MarketResearchInput(ticker="AAPL", analysis_date="2026-05-08", horizon=ResearchHorizon.SWING)
        provider = FakeStructuredLLMProvider()
        report = MarketResearchOrchestrator(llm_provider=provider, per_agent_timeout_seconds=2.0).run(
            DemoMarketResearchDataProvider().collect(request)
        )

        self.assertEqual(provider.calls, 8)
        self.assertEqual(report.metadata["llm_provider"], "fake")
        self.assertTrue(all(output.details.get("llm_refined") for output in report.raw_agent_outputs))
        self.assertEqual(report.disclaimer, RESEARCH_DISCLAIMER)

    def test_orchestrator_fail_fast_skips_remaining_hosted_llm_calls_after_failure(self) -> None:
        request = MarketResearchInput(ticker="AAPL", analysis_date="2026-05-08", horizon=ResearchHorizon.SWING)
        provider = FailingStructuredLLMProvider()
        events = []
        report = MarketResearchOrchestrator(
            agents=[TechnicalAnalyst(), FundamentalAnalyst(), NewsSentimentAnalyst()],
            llm_provider=provider,
            per_agent_timeout_seconds=2.0,
            max_llm_failures=1,
            progress_callback=events.append,
        ).run(DemoMarketResearchDataProvider().collect(request))

        self.assertEqual(provider.calls, 1)
        event_types = [event["event_type"] for event in events]
        self.assertIn("llm_refinement_failed", event_types)
        self.assertIn("llm_refinement_skipped", event_types)
        self.assertTrue(any(output.details.get("fallback_type") == "deterministic_after_llm_fail_fast" for output in report.raw_agent_outputs))
        self.assertIn("Hosted LLM refinement disabled", " ".join(report.warnings))

    def test_market_research_llm_config_fails_closed_in_production(self) -> None:
        with self.assertRaises(RuntimeError):
            validate_market_research_llm_settings(
                BackendSettings(
                    app_env="production",
                    enable_demo_accounts=False,
                    enable_in_process_jobs=False,
                    session_secret="x" * 32,
                    csrf_secret="y" * 32,
                    cors_origins=("https://app.example.com",),
                    database_url="postgresql://quantops:quantops@example.com:5432/quantops",
                    redis_url="redis://example.com:6379/0",
                    stripe_secret_key="sk_live_demo",
                    stripe_webhook_secret="whsec_demo",
                    stripe_price_pro_monthly="price_demo",
                    smtp_host="smtp.example.com",
                    email_from="ops@example.com",
                    s3_bucket="quantops",
                    s3_access_key_id="access",
                    s3_secret_access_key="secret",
                    market_research_llm_provider="mock",
                )
            )
        with self.assertRaises(RuntimeError):
            validate_market_research_llm_settings(
                BackendSettings(
                    app_env="production",
                    enable_demo_accounts=False,
                    enable_in_process_jobs=False,
                    session_secret="x" * 32,
                    csrf_secret="y" * 32,
                    cors_origins=("https://app.example.com",),
                    database_url="postgresql://quantops:quantops@example.com:5432/quantops",
                    redis_url="redis://example.com:6379/0",
                    stripe_secret_key="sk_live_demo",
                    stripe_webhook_secret="whsec_demo",
                    stripe_price_pro_monthly="price_demo",
                    smtp_host="smtp.example.com",
                    email_from="ops@example.com",
                    s3_bucket="quantops",
                    s3_access_key_id="access",
                    s3_secret_access_key="secret",
                    market_research_llm_provider="ollama",
                    market_research_llm_model="llama3.2:1b",
                )
            )
        with self.assertRaises(RuntimeError):
            validate_market_research_llm_settings(
                BackendSettings(
                    app_env="production",
                    enable_demo_accounts=False,
                    enable_in_process_jobs=False,
                    session_secret="x" * 32,
                    csrf_secret="y" * 32,
                    cors_origins=("https://app.example.com",),
                    database_url="postgresql://quantops:quantops@example.com:5432/quantops",
                    redis_url="redis://example.com:6379/0",
                    stripe_secret_key="sk_live_demo",
                    stripe_webhook_secret="whsec_demo",
                    stripe_price_pro_monthly="price_demo",
                    smtp_host="smtp.example.com",
                    email_from="ops@example.com",
                    s3_bucket="quantops",
                    s3_access_key_id="access",
                    s3_secret_access_key="secret",
                    market_research_llm_provider="nvidia",
                    market_research_llm_model="mistralai/mistral-large-3-675b-instruct-2512",
                )
            )

    def test_ollama_provider_builds_from_development_config(self) -> None:
        provider = build_structured_llm_provider(
            BackendSettings(
                market_research_llm_provider="ollama",
                market_research_llm_model="llama3.2:1b",
                market_research_ollama_base_url="http://127.0.0.1:11434",
            )
        )

        self.assertEqual(provider.provider_name, "ollama")
        self.assertEqual(provider.model_name, "llama3.2:1b")

    def test_nvidia_provider_builds_from_vetted_research_config(self) -> None:
        provider = build_structured_llm_provider(
            BackendSettings(
                market_research_llm_provider="nvidia",
                market_research_llm_model="mistralai/mistral-large-3-675b-instruct-2512",
                market_research_nvidia_api_key_ref="env:NVIDIA_API_KEY",
            ),
            resolver=FakeSecretResolver({"env:NVIDIA_API_KEY": "nv-test-key"}),
        )

        self.assertEqual(provider.provider_name, "nvidia")
        self.assertEqual(provider.model_name, "mistralai/mistral-large-3-675b-instruct-2512")

    def test_secret_provider_resolves_env_ref_from_local_dotenv_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text("NVIDIA_API_KEY=nv-dotenv-test\n", encoding="utf-8")
            with patch.dict(os.environ, {"PAIRS_TRADING_DOTENV_PATH": str(dotenv)}, clear=False):
                os.environ.pop("NVIDIA_API_KEY", None)
                value = SecretProvider(BackendSettings()).resolve("env:NVIDIA_API_KEY")

        self.assertEqual(value, "nv-dotenv-test")

    def test_nvidia_catalog_exposes_chat_and_utility_models(self) -> None:
        payload = nvidia_model_catalog_payload()
        chat_ids = {item["model"] for item in payload["market_research_models"]}
        utility_ids = {item["model"] for item in payload["utility_models"]}

        self.assertIn("mistralai/mistral-large-3-675b-instruct-2512", chat_ids)
        self.assertIn("qwen/qwen3-coder-480b-a35b-instruct", chat_ids)
        self.assertIn("nvidia/rerank-qa-mistral-4b", utility_ids)
        self.assertTrue(all(model.market_research_compatible for model in nvidia_market_research_models()))

    def test_nvidia_request_override_resolves_effective_development_settings(self) -> None:
        service = MarketResearchService(
            BackendSettings(
                market_research_llm_provider="mock",
                market_research_llm_model="mock-research-v1",
                market_research_agent_timeout_seconds=120,
                market_research_llm_timeout_seconds=120,
            )
        )
        settings = service._effective_settings(
            MarketResearchRunRequest(
                ticker="AAPL",
                provider="nvidia",
                model="qwen/qwen3-coder-480b-a35b-instruct",
            )
        )

        self.assertEqual(settings.market_research_llm_provider, "nvidia")
        self.assertEqual(settings.market_research_llm_model, "qwen/qwen3-coder-480b-a35b-instruct")
        self.assertEqual(settings.market_research_llm_timeout_seconds, 45.0)
        self.assertEqual(settings.market_research_agent_timeout_seconds, 50.0)

    def test_nvidia_provider_validates_structured_chat_completion(self) -> None:
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "id": "chatcmpl-test",
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"agent_name":"nvidia","display_name":"NVIDIA LLM","summary":"Schema-valid output",'
                            '"signals":[],"confidence":62,"warnings":[],"details":{"mode":"nvidia"}}'
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 22},
        }
        provider = NvidiaStructuredLLMProvider(
            api_key="nv-test-key",
            model="mistralai/mistral-large-3-675b-instruct-2512",
            timeout_seconds=1.0,
        )

        with patch("httpx.post", return_value=response) as post:
            result = provider.generate_structured("Return JSON", AgentOutput, {"system": "Only JSON."})

        self.assertEqual(result.value.summary, "Schema-valid output")
        self.assertEqual(result.provider, "nvidia")
        self.assertEqual(result.usage["completion_tokens"], 22)
        request_payload = post.call_args.kwargs["json"]
        self.assertEqual(request_payload["model"], "mistralai/mistral-large-3-675b-instruct-2512")
        self.assertEqual(request_payload["response_format"], {"type": "json_object"})
        self.assertIn("Authorization", post.call_args.kwargs["headers"])

    def test_nvidia_provider_does_not_retry_prompt_fallback_after_timeout(self) -> None:
        import httpx

        provider = NvidiaStructuredLLMProvider(
            api_key="nv-test-key",
            model="mistralai/mistral-large-3-675b-instruct-2512",
            timeout_seconds=1.0,
            max_retries=3,
        )

        with patch("httpx.post", side_effect=httpx.TimeoutException("read timed out")) as post:
            with self.assertRaisesRegex(LLMProviderError, "timed out after 1.0s"):
                provider.generate_structured("Return JSON", AgentOutput, {"system": "Only JSON."})

        self.assertEqual(post.call_count, 1)

    def test_ollama_provider_validates_local_structured_output(self) -> None:
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "message": {
                "content": (
                    '{"agent_name":"local","display_name":"Local LLM","summary":"Schema-valid local output",'
                    '"signals":[],"confidence":58,"warnings":[],"details":{"mode":"ollama"}}'
                )
            },
            "prompt_eval_count": 12,
            "eval_count": 24,
            "total_duration": 1000,
        }
        provider = OllamaStructuredLLMProvider(model="llama3.2:1b", timeout_seconds=1.0)

        with patch("httpx.post", return_value=response) as post:
            result = provider.generate_structured("Return JSON", AgentOutput, {"system": "Only JSON."})

        self.assertEqual(result.value.summary, "Schema-valid local output")
        self.assertEqual(result.provider, "ollama")
        self.assertEqual(result.model, "llama3.2:1b")
        self.assertEqual(result.usage["eval_count"], 24)
        request_payload = post.call_args.kwargs["json"]
        self.assertEqual(request_payload["model"], "llama3.2:1b")
        self.assertEqual(request_payload["stream"], False)
        self.assertIsInstance(request_payload["format"], dict)

    def test_ollama_provider_falls_back_to_json_mode_when_schema_format_fails(self) -> None:
        schema_response = Mock()
        schema_response.status_code = 400
        schema_response.json.return_value = {"error": "schema format unsupported"}
        json_response = Mock()
        json_response.status_code = 200
        json_response.json.return_value = {
            "message": {
                "content": (
                    '{"agent_name":"local","display_name":"Local LLM","summary":"JSON fallback output",'
                    '"signals":[],"confidence":57,"warnings":[],"details":{"mode":"json"}}'
                )
            },
            "prompt_eval_count": 10,
            "eval_count": 20,
        }
        provider = OllamaStructuredLLMProvider(model="llama3.2:1b", timeout_seconds=1.0, max_retries=0)

        with patch("httpx.post", side_effect=[schema_response, json_response]) as post:
            result = provider.generate_structured("Return JSON", AgentOutput, {"system": "Only JSON."})

        self.assertEqual(result.value.summary, "JSON fallback output")
        self.assertEqual(result.metadata["format"], "json")
        self.assertTrue(result.warnings)
        self.assertEqual(post.call_count, 2)

    def test_ollama_runtime_probe_and_preflight_report_missing_model(self) -> None:
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"models": [{"model": "llama3.2:1b"}]}
        settings = BackendSettings(
            market_research_llm_provider="ollama",
            market_research_llm_model="missing-model",
            market_research_ollama_base_url="http://127.0.0.1:11434",
        )

        with patch("httpx.get", return_value=response):
            diagnostics = probe_ollama_runtime(settings)

        self.assertTrue(diagnostics["reachable"])
        self.assertFalse(diagnostics["model_available"])
        self.assertIn("missing-model", str(diagnostics["error"]))
        with patch("pairs_trading.backend.llm_config.probe_ollama_runtime", return_value=diagnostics):
            with self.assertRaises(Exception) as raised:
                preflight_market_research_llm(settings)
        self.assertIn("ollama pull missing-model", str(raised.exception))

    def test_runtime_diagnostics_are_safe_and_include_ollama_status(self) -> None:
        settings = BackendSettings(
            market_research_data_provider="demo",
            market_research_llm_provider="ollama",
            market_research_llm_model="llama3.2:1b",
        )
        status = {
            "base_url": "http://127.0.0.1:11434",
            "reachable": True,
            "model_available": True,
            "configured_model": "llama3.2:1b",
            "models": ["llama3.2:1b"],
            "error": None,
        }
        with patch("pairs_trading.backend.llm_config.probe_ollama_runtime", return_value=status):
            diagnostics = market_research_runtime_diagnostics(settings)

        self.assertEqual(diagnostics["llm_provider"], "ollama")
        self.assertEqual(diagnostics["ollama"], status)
        self.assertNotIn("api_key", " ".join(diagnostics.keys()).lower())


class ConfigEnvOrDotenvTests(unittest.TestCase):
    def test_env_or_dotenv_returns_default_when_no_env_and_no_dotenv(self) -> None:
        with patch.dict(os.environ, {"PAIRS_TRADING_DOTENV_PATH": "/nonexistent/.env"}):
            key = "PAIRS_TRADING_MARKET_RESEARCH_DATA_PROVIDER"
            os.environ.pop(key, None)
            val = _env_or_dotenv(key, "demo")
        self.assertEqual(val, "demo")

    def test_env_or_dotenv_uses_env_var_when_set(self) -> None:
        with patch.dict(os.environ, {"MY_TEST_KEY": "from_env"}, clear=True):
            val = _env_or_dotenv("MY_TEST_KEY", "default")
        self.assertEqual(val, "from_env")

    def test_env_or_dotenv_reads_from_dotenv_when_no_env_var(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text("MY_TEST_KEY=from_dotenv\n", encoding="utf-8")
            with patch.dict(os.environ, {"PAIRS_TRADING_DOTENV_PATH": str(dotenv)}, clear=True):
                val = _env_or_dotenv("MY_TEST_KEY", "default")
        self.assertEqual(val, "from_dotenv")

    def test_env_or_dotenv_returns_default_when_key_missing_from_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text("OTHER_KEY=value\n", encoding="utf-8")
            with patch.dict(os.environ, {"PAIRS_TRADING_DOTENV_PATH": str(dotenv)}, clear=True):
                val = _env_or_dotenv("MISSING_KEY", "default")
        self.assertEqual(val, "default")

    def test_env_or_dotenv_strips_quotes_from_dotenv_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text('MY_TEST_KEY="quoted_value"\n', encoding="utf-8")
            with patch.dict(os.environ, {"PAIRS_TRADING_DOTENV_PATH": str(dotenv)}, clear=True):
                val = _env_or_dotenv("MY_TEST_KEY", "default")
        self.assertEqual(val, "quoted_value")

    def test_env_or_dotenv_skips_comments_and_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text("# comment\n\nKEY=value\n", encoding="utf-8")
            with patch.dict(os.environ, {"PAIRS_TRADING_DOTENV_PATH": str(dotenv)}, clear=True):
                val = _env_or_dotenv("KEY", "default")
        self.assertEqual(val, "value")

    def test_env_or_dotenv_handles_missing_dotenv_file_gracefully(self) -> None:
        with patch.dict(os.environ, {"PAIRS_TRADING_DOTENV_PATH": "/nonexistent/.env"}, clear=True):
            val = _env_or_dotenv("ANY_KEY", "default")
        self.assertEqual(val, "default")

    def test_env_or_dotenv_env_var_takes_priority_over_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text("MY_TEST_KEY=from_dotenv\n", encoding="utf-8")
            with patch.dict(os.environ, {"PAIRS_TRADING_DOTENV_PATH": str(dotenv), "MY_TEST_KEY": "from_env"}, clear=True):
                val = _env_or_dotenv("MY_TEST_KEY", "default")
        self.assertEqual(val, "from_env")

    def test_backend_settings_reads_market_research_llm_runtime_from_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text(
                "\n".join(
                    [
                        "PAIRS_TRADING_MARKET_RESEARCH_LLM_PROVIDER=nvidia",
                        "PAIRS_TRADING_MARKET_RESEARCH_LLM_MODEL=mistralai/mistral-nemotron",
                        "PAIRS_TRADING_MARKET_RESEARCH_LLM_TIMEOUT_SECONDS=75",
                        "PAIRS_TRADING_MARKET_RESEARCH_LLM_MAX_RETRIES=2",
                        "PAIRS_TRADING_MARKET_RESEARCH_LLM_MAX_CONCURRENCY=3",
                        "PAIRS_TRADING_MARKET_RESEARCH_FREE_ENDPOINT_TIMEOUT_CAP_SECONDS=55",
                        "PAIRS_TRADING_MARKET_RESEARCH_LLM_FAIL_FAST_AFTER_FAILURES=4",
                        "PAIRS_TRADING_MARKET_RESEARCH_ALLOW_REQUEST_MODEL_OVERRIDE=false",
                        "PAIRS_TRADING_MARKET_RESEARCH_NVIDIA_API_KEY_REF=env:NVIDIA_API_KEY",
                        "NVIDIA_API_KEY=nv-dotenv-test",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"PAIRS_TRADING_DOTENV_PATH": str(dotenv)}, clear=True):
                settings = BackendSettings.from_env()
                secret = SecretProvider(settings).resolve(settings.market_research_nvidia_api_key_ref or "")

        self.assertEqual(settings.market_research_llm_provider, "nvidia")
        self.assertEqual(settings.market_research_llm_model, "mistralai/mistral-nemotron")
        self.assertEqual(settings.market_research_llm_timeout_seconds, 75.0)
        self.assertEqual(settings.market_research_llm_max_retries, 2)
        self.assertEqual(settings.market_research_llm_max_concurrency, 3)
        self.assertEqual(settings.market_research_free_endpoint_timeout_cap_seconds, 55.0)
        self.assertEqual(settings.market_research_llm_fail_fast_after_failures, 4)
        self.assertFalse(settings.market_research_allow_request_model_override)
        self.assertEqual(secret, "nv-dotenv-test")


class TechnicalAnalystSignalTests(unittest.TestCase):
    def run_with_closes(self, closes: list[float]) -> AgentOutput:
        bars = [
            PriceBar(date=f"2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}", close=float(close))
            for i, close in enumerate(closes)
        ]
        context = MarketResearchContext(
            ticker="TEST",
            analysis_date="2026-05-01",
            horizon=ResearchHorizon.SWING,
            price_history=bars,
            news=[],
            provenance=[],
        )
        return TechnicalAnalyst().run(context, [])

    def test_technical_analyst_emits_all_five_signal_types(self) -> None:
        bars = [
            PriceBar(date=f"2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}", close=round(150.0 + i * 0.5 + (i % 7) * 0.3, 4))
            for i in range(200)
        ]
        context = MarketResearchContext(
            ticker="TEST",
            analysis_date="2026-05-01",
            horizon=ResearchHorizon.SWING,
            price_history=bars,
            news=[],
            provenance=[],
        )
        output = TechnicalAnalyst().run(context, [])
        signal_labels = {s.label for s in output.signals}
        expected = {"trend", "momentum", "volatility", "mean_reversion", "technical_composite"}
        self.assertEqual(signal_labels, expected)

    def test_technical_analyst_signal_directions_are_valid(self) -> None:
        bars = [
            PriceBar(date=f"2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}", close=round(150.0 + i * 0.5 + (i % 7) * 0.3, 4))
            for i in range(200)
        ]
        context = MarketResearchContext(
            ticker="TEST",
            analysis_date="2026-05-01",
            horizon=ResearchHorizon.SWING,
            price_history=bars,
            news=[],
            provenance=[],
        )
        output = TechnicalAnalyst().run(context, [])
        valid = {"bullish", "bearish", "neutral", "mixed"}
        for s in output.signals:
            with self.subTest(signal=s.label):
                self.assertIn(s.direction.value, valid)

    def test_technical_analyst_confidence_within_range(self) -> None:
        bars = [
            PriceBar(date=f"2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}", close=round(150.0 + i * 0.5 + (i % 7) * 0.3, 4))
            for i in range(200)
        ]
        context = MarketResearchContext(
            ticker="TEST",
            analysis_date="2026-05-01",
            horizon=ResearchHorizon.SWING,
            price_history=bars,
            news=[],
            provenance=[],
        )
        output = TechnicalAnalyst().run(context, [])
        self.assertGreaterEqual(output.confidence, 0)
        self.assertLessEqual(output.confidence, 100)

    def test_technical_analyst_warns_for_short_history_under_20_bars(self) -> None:
        bars = [
            PriceBar(date=f"2025-01-{i + 1:02d}", close=150.0 + i * 0.5) for i in range(10)
        ]
        context = MarketResearchContext(
            ticker="TEST",
            analysis_date="2025-01-20",
            horizon=ResearchHorizon.SWING,
            price_history=bars,
            news=[],
            provenance=[],
        )
        output = TechnicalAnalyst().run(context, [])
        self.assertTrue(
            any("fewer than 20 bars" in w for w in output.warnings),
            msg=f"Expected '<20 bars' warning but got: {output.warnings}",
        )

    def test_technical_analyst_warns_for_medium_history_under_50_bars(self) -> None:
        bars = [
            PriceBar(date=f"2025-01-{i + 1:02d}", close=150.0 + i * 0.5) for i in range(30)
        ]
        context = MarketResearchContext(
            ticker="TEST",
            analysis_date="2025-02-10",
            horizon=ResearchHorizon.SWING,
            price_history=bars,
            news=[],
            provenance=[],
        )
        output = TechnicalAnalyst().run(context, [])
        self.assertTrue(
            any("fewer than 50 bars" in w for w in output.warnings),
            msg=f"Expected '<50 bars' warning but got: {output.warnings}",
        )

    def test_technical_analyst_composite_has_evidence_and_provenance(self) -> None:
        bars = [
            PriceBar(date=f"2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}", close=round(150.0 + i * 0.5 + (i % 7) * 0.3, 4))
            for i in range(200)
        ]
        context = MarketResearchContext(
            ticker="TEST",
            analysis_date="2026-05-01",
            horizon=ResearchHorizon.SWING,
            price_history=bars,
            news=[],
            provenance=[],
        )
        output = TechnicalAnalyst().run(context, [])
        composite = next((s for s in output.signals if s.label == "technical_composite"), None)
        self.assertIsNotNone(composite)
        self.assertTrue(composite.evidence, "Composite signal should have evidence")
        self.assertTrue(composite.provenance, "Composite signal should have provenance")

    def test_technical_analyst_produces_annualized_volatility_in_details(self) -> None:
        bars = [
            PriceBar(date=f"2025-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}", close=round(150.0 + i * 0.5 + (i % 7) * 0.3, 4))
            for i in range(200)
        ]
        context = MarketResearchContext(
            ticker="TEST",
            analysis_date="2026-05-01",
            horizon=ResearchHorizon.SWING,
            price_history=bars,
            news=[],
            provenance=[],
        )
        output = TechnicalAnalyst().run(context, [])
        self.assertIn("annualized_volatility", output.details)

    def test_technical_analyst_marks_clear_downtrend_as_bearish(self) -> None:
        output = self.run_with_closes([320.0 - i for i in range(220)])
        trend = next(signal for signal in output.signals if signal.label == "trend")
        self.assertEqual(trend.direction.value, "bearish")

    def test_technical_analyst_rsi_handles_zero_loss_uptrend(self) -> None:
        output = self.run_with_closes([100.0 + i for i in range(220)])
        self.assertEqual(output.details["rsi_14"], 100.0)
        self.assertFalse(any("RSI computation may be incomplete" in warning for warning in output.warnings))

    def test_technical_composite_adds_mean_reversion_direction(self) -> None:
        closes = [100.0 + ((i % 5) - 2) * 0.1 for i in range(39)] + [90.0]
        output = self.run_with_closes(closes)
        signals = {signal.label: signal for signal in output.signals}
        self.assertNotEqual(signals["mean_reversion"].direction.value, "neutral")

        def signed_strength(label: str) -> int:
            signal = signals[label]
            if signal.direction == SignalDirection.BULLISH:
                return signal.strength
            if signal.direction == SignalDirection.BEARISH:
                return -signal.strength
            return 0

        expected_components = signed_strength("trend") + signed_strength("momentum") + signed_strength("mean_reversion")
        if signals["volatility"].direction == SignalDirection.BEARISH:
            expected_components -= signals["volatility"].strength
        expected = expected_components / 4
        self.assertAlmostEqual(output.details["composite_score"], round(expected, 4), places=4)


class BackendMarketResearchProviderTests(unittest.TestCase):

    def make_provider(self, provider: str = "demo") -> BackendMarketResearchDataProvider:
        from pairs_trading.backend.market_research_services import BackendMarketResearchDataProvider
        settings = BackendSettings(market_research_data_provider=provider)
        with patch("pairs_trading.backend.market_research_services.SentimentService") as mock_sent, \
             patch("pairs_trading.backend.market_research_services.FinancialEventsService") as mock_fin:
            provider = BackendMarketResearchDataProvider(settings)
        return provider

    def test_lookback_days_returns_correct_values(self) -> None:
        provider = self.make_provider()
        self.assertEqual(provider._lookback_days(ResearchHorizon.INTRADAY), 45)
        self.assertEqual(provider._lookback_days(ResearchHorizon.SWING), 180)
        self.assertEqual(provider._lookback_days(ResearchHorizon.LONG_TERM), 540)

    def test_lookback_days_honors_override_within_bounds(self) -> None:
        provider = self.make_provider()
        self.assertEqual(provider._lookback_days(ResearchHorizon.SWING, override=90), 90)
        self.assertEqual(provider._lookback_days(ResearchHorizon.SWING, override=5), 5)

    def test_lookback_days_clamps_override_to_valid_range(self) -> None:
        provider = self.make_provider()
        self.assertEqual(provider._lookback_days(ResearchHorizon.SWING, override=3), 5)
        self.assertEqual(provider._lookback_days(ResearchHorizon.SWING, override=1000), 900)

    def test_demo_provider_branch_sets_correct_metadata(self) -> None:
        provider = self.make_provider(provider="demo")
        request = MarketResearchInput(ticker="AAPL", analysis_date="2026-05-01", horizon=ResearchHorizon.SWING)
        context = provider.collect(request)
        self.assertEqual(context.provider_metadata.get("backend_data_provider"), "demo")

    @patch("pairs_trading.backend.market_research_services.BackendMarketResearchDataProvider._enrich_context")
    def test_cached_yahoo_branch_calls_collect_cached_yahoo(self, mock_enrich: Mock) -> None:
        mock_enrich.return_value = MarketResearchContext(
            ticker="AAPL",
            analysis_date="2026-05-01",
            horizon=ResearchHorizon.SWING,
            price_history=[PriceBar(date="2026-05-01", close=150.0)],
            news=[],
            provenance=[],
            provider_metadata={"backend_data_provider": "cached_yahoo"},
        )
        provider = self.make_provider(provider="cached_yahoo")
        with patch.object(provider, "_collect_cached_yahoo") as mock_cached:
            mock_cached.return_value = MarketResearchContext(
                ticker="AAPL",
                analysis_date="2026-05-01",
                horizon=ResearchHorizon.SWING,
                price_history=[],
                news=[],
                provenance=[],
            )
            request = MarketResearchInput(ticker="AAPL", analysis_date="2026-05-01", horizon=ResearchHorizon.SWING)
            context = provider.collect(request)
        mock_cached.assert_called_once_with(request)
        mock_enrich.assert_called_once()
        self.assertEqual(context.provider_metadata.get("backend_data_provider"), "cached_yahoo")

    def test_cached_yahoo_keeps_short_price_series_when_extension_fetch_fails(self) -> None:
        from pairs_trading.backend.market_research_services import BackendMarketResearchDataProvider

        class ShortThenFailProvider:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def get_close_prices(self, tickers: list[str], *, start: str, end: str, interval: str = "1d") -> pd.DataFrame:
                del tickers, interval
                self.calls.append((start, end))
                if len(self.calls) > 1:
                    raise RuntimeError("extension unavailable")
                index = pd.date_range("2026-04-01", periods=10, freq="D")
                return pd.DataFrame({"AAPL": [150.0 + i for i in range(10)]}, index=index)

        stub = ShortThenFailProvider()
        settings = BackendSettings(market_research_data_provider="cached_yahoo")
        with patch("pairs_trading.backend.market_research_services.SentimentService"), \
             patch("pairs_trading.backend.market_research_services.FinancialEventsService"):
            provider = BackendMarketResearchDataProvider(settings, market_data_provider=stub)
        request = MarketResearchInput(
            ticker="AAPL",
            analysis_date="2026-05-01",
            horizon=ResearchHorizon.SWING,
            include_sentiment=False,
            include_financial_events=False,
            lookback_days=30,
        )

        context = provider.collect(request)

        self.assertEqual(context.provider_metadata.get("backend_data_provider"), "cached_yahoo")
        self.assertEqual(len(context.price_history), 10)
        self.assertEqual(len(stub.calls), 2)
        self.assertTrue(any("365-day extension was unavailable" in warning for warning in context.warnings))


class MultiStockResearchExtensionTests(unittest.TestCase):
    def test_pair_request_validates_both_symbols(self) -> None:
        request = MarketResearchRunRequest(ticker="ko", pair="ko,pep")
        self.assertEqual(request.ticker, "KO")
        self.assertEqual(request.pair, "KO,PEP")
        with self.assertRaises(ValueError):
            MarketResearchRunRequest(ticker="KO", pair="BAD SYMBOL,PEP")
        with self.assertRaises(ValueError):
            MarketResearchRunRequest(ticker="KO", pair="KO")

    def test_universe_filter_is_case_insensitive_and_supports_liquidity_alias(self) -> None:
        from pairs_trading.research.stock_universe import UniverseBuilder

        universe = UniverseBuilder().build_default()
        software = universe.filter(sector="technology", industry="software", min_liquidity=True)

        self.assertGreater(len(universe.stocks), 400)
        self.assertTrue(software.stocks)
        self.assertTrue(all(item.sector == "Technology" for item in software.stocks))
        self.assertTrue(all(item.is_liquid for item in software.stocks))

    def test_chart_data_single_observation_does_not_emit_nan_bands(self) -> None:
        from pairs_trading.research.chart_data import ChartDataBuilder

        point = [PriceBar(date="2026-05-01", close=100.0)]
        spread = ChartDataBuilder().spread_chart(point, point, "AAA", "BBB")

        self.assertEqual(spread["bands"]["std"], 0.0)
        self.assertEqual(spread["bands"]["upper_2sigma"], 0.0)
        self.assertEqual(spread["data"][0]["zscore"], 0.0)

    def test_pair_job_persists_aggregate_multi_stock_result(self) -> None:
        from pairs_trading.backend.market_research_services import MarketResearchJobRunner

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            settings = BackendSettings(
                metadata_db_path=root / "metadata.sqlite3",
                market_research_job_state_dir=root / "jobs",
                market_research_artifact_root=root / "reports",
                market_research_data_provider="demo",
                market_research_llm_provider="mock",
                enable_in_process_jobs=True,
            )
            runner = MarketResearchJobRunner(settings)
            demo = runner.metadata_store.ensure_demo_workspace()
            job = runner.submit(
                MarketResearchRunRequest(ticker="KO", pair="KO,PEP"),
                organization_id=demo["organization_id"],
                user_id=demo["user_id"],
            )

            completed = None
            for _ in range(80):
                current = runner.get_job(job["id"], organization_id=demo["organization_id"])
                if current and current["status"] not in {"queued", "running"}:
                    completed = current
                    break
                time.sleep(0.05)

            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertEqual(completed["status"], "completed", completed.get("error"))
            result = completed["result"]
            self.assertEqual(result["report_type"], "multi_stock")
            self.assertEqual(result["ticker"], "KO,PEP")
            self.assertIn(result["decision"], {"BUY", "HOLD", "SELL", "AVOID"})
            self.assertGreaterEqual(result["confidence"], 0)
            self.assertEqual(len(result["reports"]), 2)
            self.assertIn("pair_metrics", result["cross_stock_analysis"])


if __name__ == "__main__":
    unittest.main()
