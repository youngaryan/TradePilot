from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from pairs_trading.backend.config import BackendSettings
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
    MarketResearchInput,
    MarketResearchOrchestrator,
    MarketResearchReport,
    NewsSentimentAnalyst,
    PortfolioRiskManager,
    ResearchDecision,
    ResearchHorizon,
    RiskAnalyst,
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


if __name__ == "__main__":
    unittest.main()
