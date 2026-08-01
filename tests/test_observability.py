from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json
import logging
import sys
from threading import Event
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from apps.worker import job_control
from pairs_trading.backend.app import scrub_trace_request_span
from pairs_trading.backend.config import BackendSettings
from pairs_trading.backend.job_queue import enqueue_quant_job
from pairs_trading.backend.observability import (
    METRICS,
    JsonLogFormatter,
    bind_context,
    configure_sentry,
    metrics_authorized,
    record_http_request,
    reset_context,
    safe_route,
    scrub,
    sentry_before_send,
)
from pairs_trading.backend.routers.metrics import build_metrics_router
from pairs_trading.backend.routers.metrics import _bounded_jobs
from pairs_trading.backend.security import SecurityHeadersMiddleware
from pairs_trading.backend import worker_tasks


def test_recursive_redaction_bounds_values_and_blocks_log_injection() -> None:
    recursive: dict[str, object] = {}
    recursive["self"] = recursive
    payload = scrub(
        {
            "Authorization": "Bearer top-secret",
            "nested": {"password": "hunter2", "url": "https://user:pass@example.test/path?token=secret"},
            "message": "first\r\nforged@example.test " + "x" * 3_000,
            "cycle": recursive,
        }
    )
    encoded = json.dumps(payload)
    assert "top-secret" not in encoded
    assert "hunter2" not in encoded
    assert "user:pass" not in encoded
    assert "token=secret" not in encoded
    assert "/path" not in encoded
    assert "forged@example.test" not in encoded
    assert "\r" not in str(payload["message"])
    assert "\n" not in str(payload["message"])
    assert str(payload["message"]).endswith("...[truncated]")
    assert "truncated" in encoded
    assert "CYCLE" in encoded


def test_json_formatter_has_required_context_and_never_emits_pii() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter(service="tradepilot", role="worker", environment="test"))
    logger = logging.getLogger("test.observability.format")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    token = bind_context(correlation_id="corr-1", job_kind="paper", job_id="job-123")
    try:
        logger.info("job_finished\nforged", extra={"cookie": "session=secret", "email": "person@example.test"})
    finally:
        reset_context(token)
    row = json.loads(stream.getvalue())
    assert row["timestamp"].endswith("Z")
    assert row["service"] == "tradepilot"
    assert row["role"] == "worker"
    assert row["environment"] == "test"
    assert row["event"] == "job_finished\\nforged"
    assert row["correlation_id"] == "corr-1"
    assert row["cookie"] == "[REDACTED]"
    assert "person@example.test" not in stream.getvalue()


def test_sentry_scrubber_removes_user_request_body_and_credentials() -> None:
    event = sentry_before_send(
        {
            "user": {"email": "person@example.test"},
            "request": {
                "url": "https://example.test/path?api_key=secret",
                "data": {"password": "secret"},
                "cookies": {"session": "secret"},
                "headers": {"Authorization": "Bearer secret"},
            },
        }
    )
    encoded = json.dumps(event)
    assert "person@example.test" not in encoded
    assert "Bearer secret" not in encoded
    assert '"data"' not in encoded
    assert '"cookies"' not in encoded


def test_trace_request_hook_replaces_raw_paths_and_queries() -> None:
    attributes: dict[str, object] = {}
    active_span = SimpleNamespace(is_recording=lambda: True, set_attribute=attributes.__setitem__)
    scope = {
        "route": SimpleNamespace(path="/jobs/{job_id}"),
        "path": "/jobs/tenant-sensitive-id",
        "query_string": b"api_key=secret",
    }
    scrub_trace_request_span(active_span, scope)
    encoded = json.dumps(attributes)
    assert attributes["http.route"] == "/jobs/{job_id}"
    assert attributes["url.full"] == "[REDACTED]"
    assert "tenant-sensitive-id" not in encoded
    assert "api_key" not in encoded


def test_sentry_is_optional_unless_production_explicitly_requires_it(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_sentry = ModuleType("sentry_sdk")

    def fail_init(**_kwargs: object) -> None:
        raise ValueError("invalid DSN")

    fake_sentry.init = fail_init  # type: ignore[attr-defined]
    fake_sentry.set_tag = lambda *_args: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)
    optional = BackendSettings(app_env="production", sentry_dsn="invalid", sentry_required=False)
    assert configure_sentry(optional, role="api") is False
    required = BackendSettings(app_env="production", sentry_dsn="invalid", sentry_required=True)
    with pytest.raises(RuntimeError, match="Sentry initialization failed"):
        configure_sentry(required, role="worker")


def _middleware_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_enabled=False,
        hsts_max_age_seconds=0,
        hsts_include_subdomains=False,
        hsts_preload=False,
    )

    @app.get("/jobs/{job_id}")
    def job(job_id: str) -> dict[str, str]:
        return {"id": job_id}

    return app


def test_correlation_ids_are_validated_returned_and_isolated() -> None:
    METRICS.clear()
    client = TestClient(_middleware_app())
    first = client.get("/jobs/123456", headers={"X-Correlation-ID": "valid-correlation.1"})
    second = client.get("/jobs/abcdef", headers={"X-Correlation-ID": "bad\nvalue"})
    assert first.headers["X-Correlation-ID"] == "valid-correlation.1"
    assert second.headers["X-Correlation-ID"] != "bad\nvalue"
    assert len(second.headers["X-Correlation-ID"]) == 32
    rendered = METRICS.render()
    assert 'route="/jobs/{job_id}"' in rendered
    assert "123456" not in rendered
    assert "abcdef" not in rendered


def test_metric_exposition_has_help_type_histogram_and_rejects_raw_id_routes() -> None:
    METRICS.clear()
    record_http_request(method="GET", route="/jobs/{job_id}", status_code=200, duration_seconds=0.2)
    record_http_request(method="GET", route="/jobs/123456", status_code=404, duration_seconds=0.1)
    rendered = METRICS.render()
    assert "# HELP tradepilot_http_requests_total" in rendered
    assert "# TYPE tradepilot_http_requests_total counter" in rendered
    assert "# TYPE tradepilot_http_request_duration_seconds histogram" in rendered
    assert 'le="+Inf"' in rendered
    assert 'route="unmatched"' in rendered
    assert "123456" not in rendered
    assert safe_route("/jobs/0123456789abcdef0123456789abcdef") == "unmatched"


def test_metric_collector_creation_is_thread_safe() -> None:
    METRICS.clear()
    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(lambda _: METRICS.inc("tradepilot_concurrent_test_total", {"role": "worker"}), range(100)))
    assert 'tradepilot_concurrent_test_total{role="worker"} 100.0' in METRICS.render()


def test_internal_metrics_endpoint_is_disabled_or_constant_time_bearer_protected() -> None:
    token = "m" * 40
    settings = BackendSettings(app_env="production", observability_metrics_enabled=True, observability_metrics_token=token)
    app = FastAPI()
    app.include_router(build_metrics_router(settings))
    client = TestClient(app)
    with patch("pairs_trading.backend.routers.metrics.collect_runtime_metrics"):
        assert client.get("/internal/metrics").status_code == 401
        assert client.get("/internal/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        allowed = client.get("/internal/metrics", headers={"Authorization": f"Bearer {token}"})
        assert allowed.status_code == 200
        assert allowed.headers["Cache-Control"] == "no-store"
    assert metrics_authorized(token, f"Bearer {token}") is True
    assert metrics_authorized(token, "Basic anything") is False

    development = BackendSettings(observability_metrics_enabled=True, observability_metrics_token=token)
    development_app = FastAPI()
    development_app.include_router(build_metrics_router(development))
    development_client = TestClient(development_app)
    assert development_client.get("/internal/metrics").status_code == 401


def test_runtime_job_collection_uses_supported_bounded_pages() -> None:
    calls: list[tuple[int, int]] = []

    class Store:
        def list_jobs(self, **kwargs: object) -> list[dict[str, object]]:
            limit = int(kwargs["limit"])
            offset = int(kwargs["offset"])
            calls.append((limit, offset))
            assert limit <= 200
            return [{"id": str(index)} for index in range(offset, min(offset + limit, 225))]

    jobs, truncated = _bounded_jobs(Store(), kind="paper", status="queued")
    assert len(jobs) == 225
    assert truncated is False
    assert calls == [(200, 0), (200, 200)]


def test_enqueue_propagates_only_safe_trace_and_correlation_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeQueue:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def fetch_job(self, _job_id: str) -> None:
            return None

        def enqueue(self, *_args: object, **kwargs: object) -> SimpleNamespace:
            captured.update(kwargs)
            return SimpleNamespace(id=kwargs["job_id"])

    redis_module = ModuleType("redis")
    redis_module.Redis = SimpleNamespace(from_url=lambda _url: object())  # type: ignore[attr-defined]
    rq_module = ModuleType("rq")
    rq_module.Queue = FakeQueue  # type: ignore[attr-defined]
    rq_exceptions = ModuleType("rq.exceptions")
    rq_exceptions.DuplicateJobError = RuntimeError  # type: ignore[attr-defined]
    rq_serializers = ModuleType("rq.serializers")
    rq_serializers.JSONSerializer = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis", redis_module)
    monkeypatch.setitem(sys.modules, "rq", rq_module)
    monkeypatch.setitem(sys.modules, "rq.exceptions", rq_exceptions)
    monkeypatch.setitem(sys.modules, "rq.serializers", rq_serializers)

    token = bind_context(correlation_id="request-123")
    try:
        with patch("pairs_trading.backend.job_queue.inject_trace_context", return_value={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"}):
            enqueue_quant_job(BackendSettings(redis_url="redis://example"), kind="paper", job_id="job-1")
    finally:
        reset_context(token)
    assert captured["meta"] == {
        "correlation_id": "request-123",
        "trace_context": {"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"},
    }
    json.dumps(captured["meta"])


def test_controller_pass_records_manual_span_and_result_metrics() -> None:
    settings = BackendSettings(
        enable_in_process_jobs=False,
        redis_url="redis://example",
        database_url="postgresql://example/db",
        market_research_data_provider="cached_yahoo",
        market_research_allow_demo_fallback=False,
        job_recovery_poll_seconds=1,
    )
    stopped = Event()
    summary = {
        "recovery": {"recovered": 1, "redispatched": 1, "interrupted": 0, "errors": 0},
        "reconciliation": {"dispatched": 2, "errors": 0},
    }

    def one_pass(*_args: object, **_kwargs: object) -> dict[str, object]:
        stopped.set()
        return summary

    with (
        patch.object(job_control, "run_control_pass", side_effect=one_pass),
        patch.object(job_control, "span") as span_mock,
        patch.object(job_control, "record_controller_result") as record_mock,
    ):
        span_mock.return_value.__enter__.return_value = None
        job_control.run_forever(settings, stop_event=stopped)
    span_mock.assert_called_once()
    record_mock.assert_called_once()


def test_worker_execution_uses_propagated_context_span_and_terminal_metric() -> None:
    result = {"id": "job-1", "status": "completed"}
    with (
        patch.object(worker_tasks, "current_rq_correlation_id", return_value="corr-worker"),
        patch.object(worker_tasks, "current_rq_trace_context", return_value={"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01"}),
        patch.object(worker_tasks, "_execute_queued_job", return_value=result),
        patch.object(worker_tasks, "span") as span_mock,
        patch.object(worker_tasks, "record_job") as record_mock,
    ):
        span_mock.return_value.__enter__.return_value = None
        assert worker_tasks.run_queued_job("paper", "job-1") == result
    span_mock.assert_called_once()
    assert span_mock.call_args.kwargs["carrier"]["traceparent"].startswith("00-")
    record_mock.assert_called_once()
    assert record_mock.call_args.kwargs["kind"] == "paper"
    assert record_mock.call_args.kwargs["status"] == "completed"
