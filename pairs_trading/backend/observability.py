"""Role-wide logging, tracing, and low-cardinality operational metrics.

The module deliberately keeps exporters optional: the service remains usable
without Sentry/OpenTelemetry packages or endpoints, while production can opt
into strict Sentry startup validation with ``SENTRY_REQUIRED=true``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
import hmac
import json
import logging
import math
import os
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import RLock, Thread
from time import monotonic
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit, urlunsplit

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest, multiprocess

from .config import BackendSettings


MAX_LOG_STRING = 2_048
MAX_LOG_COLLECTION = 50
MAX_LOG_DEPTH = 6
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|set-cookie|password|passwd|secret|token|api[-_]?key|credential|private[-_]?key|dsn)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_INLINE_URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+")
_BEARER = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_CORRELATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TRACE_VALUE = re.compile(r"^[\x20-\x7e]{1,512}$")
_DYNAMIC_PATH_SEGMENT = re.compile(r"^(?:\d{4,}|[0-9a-fA-F]{16,}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$")
_STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
    "event",
    "service",
    "role",
    "environment",
}

_context: ContextVar[dict[str, Any]] = ContextVar("tradepilot_observability_context", default={})
_configured_roles: set[str] = set()


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def valid_correlation_id(value: str | None) -> bool:
    return bool(value and _CORRELATION.fullmatch(value))


def sanitize_text(value: str, *, limit: int = MAX_LOG_STRING) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    def redact_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parsed = urlsplit(raw)
            host = parsed.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            if parsed.port:
                host = f"{host}:{parsed.port}"
            path = "/[REDACTED]" if parsed.path not in {"", "/"} else parsed.path
            return urlunsplit((parsed.scheme, host, path, "[REDACTED]" if parsed.query else "", ""))
        except ValueError:
            return "[REDACTED_URL]"

    text = _INLINE_URL.sub(redact_url, text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


def scrub(value: Any, *, _depth: int = 0, _seen: set[int] | None = None) -> Any:
    """Recursively produce a JSON-safe, bounded, secret-free value."""

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, bytes):
        return "[BYTES]"
    if _depth >= MAX_LOG_DEPTH:
        return "[MAX_DEPTH]"
    seen = _seen if _seen is not None else set()
    identity = id(value)
    if identity in seen:
        return "[CYCLE]"
    seen.add(identity)
    try:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for index, (raw_key, raw_value) in enumerate(value.items()):
                if index >= MAX_LOG_COLLECTION:
                    result["_truncated"] = True
                    break
                key = sanitize_text(str(raw_key), limit=128)
                result[key] = "[REDACTED]" if _SENSITIVE_KEY.search(key) else scrub(raw_value, _depth=_depth + 1, _seen=seen)
            return result
        if isinstance(value, (list, tuple, set, frozenset)):
            items = list(value)
            result = [scrub(item, _depth=_depth + 1, _seen=seen) for item in items[:MAX_LOG_COLLECTION]]
            if len(items) > MAX_LOG_COLLECTION:
                result.append("[TRUNCATED]")
            return result
        return sanitize_text(str(value))
    finally:
        seen.discard(identity)


def bind_context(**values: Any) -> Token[dict[str, Any]]:
    current = dict(_context.get())
    current.update(scrub(values))
    return _context.set(current)


def reset_context(token: Token[dict[str, Any]]) -> None:
    _context.reset(token)


def clear_context() -> None:
    _context.set({})


class JsonLogFormatter(logging.Formatter):
    def __init__(self, *, service: str, role: str, environment: str) -> None:
        super().__init__()
        self.base = {"service": service, "role": role, "environment": environment}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": utc_timestamp(),
            "level": record.levelname.lower(),
            **self.base,
            "event": sanitize_text(record.getMessage(), limit=256),
            **scrub(_context.get()),
        }
        extras = {key: value for key, value in record.__dict__.items() if key not in _STANDARD_RECORD_FIELDS}
        payload.update(scrub(extras))
        if record.exc_info:
            payload["exception"] = sanitize_text(self.formatException(record.exc_info))
        return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


class ReadableLogFormatter(JsonLogFormatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = json.loads(super().format(record))
        prefix = f"{payload.pop('timestamp')} {payload.pop('level').upper()} {payload.pop('role')} {payload.pop('event')}"
        return prefix if not payload else f"{prefix} {json.dumps(payload, separators=(',', ':'), sort_keys=True)}"


def configure_logging(settings: BackendSettings, *, role: str) -> None:
    normalized_role = sanitize_text(role.lower(), limit=32)
    handler = logging.StreamHandler()
    formatter_type = JsonLogFormatter if settings.log_json or settings.is_production else ReadableLogFormatter
    handler.setFormatter(formatter_type(service="tradepilot", role=normalized_role, environment=settings.app_env))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))
    _configured_roles.add(normalized_role)


def log_exception(logger: logging.Logger, event: str, error: BaseException, **fields: Any) -> None:
    """Log/capture an exception without placing its raw text on a LogRecord."""

    safe_traceback = sanitize_text("".join(traceback.format_exception(type(error), error, error.__traceback__)))
    logger.error(
        event,
        extra={**fields, "exception_type": type(error).__name__, "exception": safe_traceback},
    )
    try:
        import sentry_sdk

        sentry_sdk.capture_exception(error)
    except Exception:
        return


def sentry_before_send(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    del hint
    event = dict(event)
    event.pop("user", None)
    request = event.get("request")
    if isinstance(request, dict):
        request = dict(request)
        request.pop("data", None)
        request.pop("cookies", None)
        event["request"] = request
    scrubbed = scrub(event)
    return scrubbed if isinstance(scrubbed, dict) else None


def configure_sentry(settings: BackendSettings, *, role: str) -> bool:
    if not settings.sentry_dsn:
        return False
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.app_env,
            release=settings.release,
            traces_sample_rate=max(0.0, min(1.0, settings.sentry_traces_sample_rate)),
            send_default_pii=False,
            before_send=sentry_before_send,
            server_name=None,
        )
        sentry_sdk.set_tag("service", "tradepilot")
        sentry_sdk.set_tag("role", role)
        return True
    except Exception as error:
        log_exception(logging.getLogger("pairs_trading.observability"), "sentry_initialization_failed", error)
        if settings.is_production and settings.sentry_required:
            raise RuntimeError("Sentry initialization failed while SENTRY_REQUIRED=true.") from None
        return False


def configure_tracing(settings: BackendSettings, *, role: str) -> bool:
    if not settings.otel_exporter_otlp_endpoint:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": f"tradepilot-{role}",
                    "service.version": settings.release or "unknown",
                    "deployment.environment": settings.app_env,
                }
            ),
            sampler=ParentBased(TraceIdRatioBased(max(0.0, min(1.0, settings.otel_traces_sample_rate)))),
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)))
        trace.set_tracer_provider(provider)
        return True
    except Exception as error:
        log_exception(logging.getLogger("pairs_trading.observability"), "otel_initialization_failed", error)
        return False


def configure_role_observability(settings: BackendSettings, *, role: str) -> None:
    if settings.observability_metrics_enabled:
        if len(str(settings.observability_metrics_token or "")) < 32:
            raise RuntimeError("OBSERVABILITY_METRICS_TOKEN must contain at least 32 characters when metrics are enabled.")
        if role in {"worker", "controller"} and not 1 <= settings.observability_metrics_port <= 65_535:
            raise RuntimeError(f"OBSERVABILITY_METRICS_PORT is required for the {role} role when metrics are enabled.")
    configure_logging(settings, role=role)
    configure_sentry(settings, role=role)
    configure_tracing(settings, role=role)
    METRICS.set_gauge("tradepilot_role_process_up", {"role": safe_role(role)}, 1.0)


class MetricsServer:
    def __init__(self, settings: BackendSettings) -> None:
        token = settings.observability_metrics_token

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path != "/internal/metrics":
                    self.send_error(404)
                    return
                if not metrics_authorized(token, self.headers.get("Authorization")):
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", "Bearer")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = METRICS.render().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *args: Any) -> None:
                del args

        class Server(ThreadingHTTPServer):
            daemon_threads = True

        self.server = Server(("0.0.0.0", settings.observability_metrics_port), Handler)
        self.thread = Thread(target=self.server.serve_forever, name="observability-metrics", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def start_metrics_server(settings: BackendSettings) -> MetricsServer | None:
    if not settings.observability_metrics_enabled or settings.observability_metrics_port <= 0:
        return None
    server = MetricsServer(settings)
    server.start()
    return server


def inject_trace_context() -> dict[str, str]:
    carrier: dict[str, str] = {}
    try:
        from opentelemetry.propagate import inject

        inject(carrier)
    except Exception:
        return {}
    return {
        key: value
        for key, value in carrier.items()
        if key.lower() in {"traceparent", "tracestate"} and _TRACE_VALUE.fullmatch(str(value))
    }


def current_correlation_id() -> str | None:
    value = str(_context.get().get("correlation_id") or "")
    return value if valid_correlation_id(value) else None


@contextmanager
def span(name: str, *, attributes: Mapping[str, Any] | None = None, carrier: Mapping[str, str] | None = None) -> Iterator[Any]:
    try:
        from opentelemetry import propagate, trace
    except ImportError:
        yield None
        return
    safe_carrier = {
        key: value
        for key, value in (carrier or {}).items()
        if key.lower() in {"traceparent", "tracestate"} and _TRACE_VALUE.fullmatch(str(value))
    }
    parent = propagate.extract(safe_carrier) if safe_carrier else None
    with trace.get_tracer("tradepilot").start_as_current_span(
        sanitize_text(name, limit=128),
        context=parent,
        attributes=scrub(dict(attributes or {})),
    ) as active_span:
        yield active_span


def current_rq_trace_context() -> dict[str, str]:
    try:
        from rq import get_current_job

        job = get_current_job()
        raw = (job.meta or {}).get("trace_context") if job is not None else None
    except Exception:
        return {}
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key): str(value)
        for key, value in raw.items()
        if str(key).lower() in {"traceparent", "tracestate"} and _TRACE_VALUE.fullmatch(str(value))
    }


def current_rq_correlation_id() -> str | None:
    try:
        from rq import get_current_job

        job = get_current_job()
        value = str((job.meta or {}).get("correlation_id") or "") if job is not None else ""
    except Exception:
        return None
    return value if valid_correlation_id(value) else None


def safe_role(value: str) -> str:
    normalized = str(value).lower()
    return normalized if normalized in {"api", "worker", "controller"} else "unknown"


def safe_job_kind(value: str) -> str:
    normalized = str(value).lower()
    return normalized if normalized in {"backtest", "paper", "sentiment", "market_research"} else "unknown"


def safe_job_status(value: str) -> str:
    normalized = str(value).lower()
    return normalized if normalized in {"queued", "running", "completed", "failed", "interrupted", "claim_lost"} else "unknown"


def safe_route(value: str | None) -> str:
    route = str(value or "unmatched")
    if len(route) > 160 or "?" in route or "#" in route:
        return "unmatched"
    if not route.startswith("/"):
        return "unmatched"
    if any(_DYNAMIC_PATH_SEGMENT.fullmatch(segment) for segment in route.split("/") if segment and not segment.startswith("{")):
        return "unmatched"
    return route


class MetricRegistry:
    """Thread-safe prometheus-client facade with stable label schemas.

    When ``PROMETHEUS_MULTIPROC_DIR`` is configured before process start,
    prometheus-client persists samples per Gunicorn worker and ``render`` merges
    them. Without it, values intentionally describe only the current process.
    """

    def __init__(self) -> None:
        self._registry = CollectorRegistry()
        self._collectors: dict[str, Any] = {}
        self._schemas: dict[str, tuple[str, ...]] = {}
        self._lock = RLock()

    def _collector(self, name: str, kind: str, labels: Mapping[str, str]) -> Any:
        label_names = tuple(sorted(labels))
        with self._lock:
            existing = self._collectors.get(name)
            if existing is not None:
                if self._schemas[name] != label_names:
                    raise ValueError(f"Metric {name} label schema changed")
                return existing
            help_text = (
                "TradePilot process-local duration distribution in seconds."
                if kind == "histogram"
                else "TradePilot process-local counter."
                if kind == "counter"
                else "TradePilot operational gauge."
            )
            if kind == "counter":
                collector = Counter(name, help_text, label_names, registry=self._registry)
            elif kind == "histogram":
                collector = Histogram(
                    name,
                    help_text,
                    label_names,
                    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 120.0, float("inf")),
                    registry=self._registry,
                )
            else:
                mode = "livesum" if name in {"tradepilot_http_requests_in_flight", "tradepilot_role_process_up"} else "mostrecent"
                collector = Gauge(name, help_text, label_names, registry=self._registry, multiprocess_mode=mode)
            self._collectors[name] = collector
            self._schemas[name] = label_names
            return collector

    @staticmethod
    def _child(collector: Any, labels: Mapping[str, str]) -> Any:
        ordered = {key: str(labels[key]) for key in sorted(labels)}
        return collector.labels(**ordered) if ordered else collector

    def inc(self, name: str, labels: Mapping[str, str], amount: float = 1.0) -> None:
        self._child(self._collector(name, "counter", labels), labels).inc(float(amount))

    def set_gauge(self, name: str, labels: Mapping[str, str], value: float) -> None:
        self._child(self._collector(name, "gauge", labels), labels).set(float(value))

    def add_gauge(self, name: str, labels: Mapping[str, str], amount: float) -> None:
        child = self._child(self._collector(name, "gauge", labels), labels)
        child.inc(float(amount))

    def observe(self, name: str, labels: Mapping[str, str], value: float) -> None:
        self._child(self._collector(name, "histogram", labels), labels).observe(float(value))

    def render(self) -> str:
        if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
            return generate_latest(registry).decode("utf-8")
        return generate_latest(self._registry).decode("utf-8")

    def clear(self) -> None:
        with self._lock:
            self._registry = CollectorRegistry()
            self._collectors.clear()
            self._schemas.clear()


METRICS = MetricRegistry()


def record_http_request(*, method: str, route: str | None, status_code: int, duration_seconds: float) -> None:
    labels = {"method": str(method).upper() if str(method).upper() in {"GET", "POST", "PATCH", "DELETE", "PUT", "OPTIONS", "HEAD"} else "OTHER", "route": safe_route(route), "status": str(int(status_code)) if 100 <= int(status_code) <= 599 else "unknown"}
    METRICS.inc("tradepilot_http_requests_total", labels)
    METRICS.observe("tradepilot_http_request_duration_seconds", labels, max(0.0, float(duration_seconds)))


def record_job(*, kind: str, status: str, duration_seconds: float) -> None:
    labels = {"kind": safe_job_kind(kind), "status": safe_job_status(status)}
    METRICS.inc("tradepilot_jobs_total", labels)
    METRICS.observe("tradepilot_job_duration_seconds", labels, max(0.0, float(duration_seconds)))


def record_controller_result(summary: Mapping[str, Any], *, duration_seconds: float) -> None:
    recovery = summary.get("recovery") if isinstance(summary.get("recovery"), Mapping) else {}
    reconciliation = summary.get("reconciliation") if isinstance(summary.get("reconciliation"), Mapping) else {}
    for result, value in {
        "recovered": recovery.get("recovered", 0),
        "redispatched": recovery.get("redispatched", 0),
        "interrupted": recovery.get("interrupted", 0),
        "recovery_error": recovery.get("errors", 0),
        "dispatched": reconciliation.get("dispatched", 0),
        "reconciliation_error": reconciliation.get("errors", 0),
    }.items():
        METRICS.inc("tradepilot_controller_results_total", {"result": result}, float(value or 0))
    METRICS.observe("tradepilot_controller_pass_duration_seconds", {}, max(0.0, duration_seconds))


def metrics_authorized(configured_token: str | None, authorization: str | None) -> bool:
    expected = str(configured_token or "").encode("utf-8")
    prefix = "Bearer "
    supplied = str(authorization or "")
    candidate = supplied[len(prefix) :].encode("utf-8") if supplied.startswith(prefix) else b""
    return bool(expected) and hmac.compare_digest(expected, candidate)


def timed() -> float:
    return monotonic()


__all__ = [
    "METRICS",
    "JsonLogFormatter",
    "bind_context",
    "clear_context",
    "configure_role_observability",
    "current_correlation_id",
    "current_rq_correlation_id",
    "current_rq_trace_context",
    "inject_trace_context",
    "metrics_authorized",
    "log_exception",
    "record_controller_result",
    "record_http_request",
    "record_job",
    "reset_context",
    "safe_job_kind",
    "safe_route",
    "sanitize_text",
    "scrub",
    "span",
    "start_metrics_server",
    "timed",
    "valid_correlation_id",
]
