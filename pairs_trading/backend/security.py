from __future__ import annotations

from collections import defaultdict, deque
import logging
import time
from typing import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import BackendSettings
from .observability import METRICS, bind_context, log_exception, record_http_request, reset_context, safe_route, valid_correlation_id


logger = logging.getLogger("pairs_trading.api")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        hsts_enabled: bool,
        hsts_max_age_seconds: int,
        hsts_include_subdomains: bool,
        hsts_preload: bool,
    ) -> None:
        super().__init__(app)
        directives = [f"max-age={max(0, int(hsts_max_age_seconds))}"]
        if hsts_include_subdomains:
            directives.append("includeSubDomains")
        if hsts_preload:
            directives.append("preload")
        self.hsts_enabled = bool(hsts_enabled)
        self.hsts_value = "; ".join(directives)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        inbound_correlation_id = request.headers.get("x-correlation-id")
        correlation_id = inbound_correlation_id if valid_correlation_id(inbound_correlation_id) else uuid4().hex
        request.state.correlation_id = correlation_id
        context_token = bind_context(correlation_id=correlation_id)
        started = time.perf_counter()
        method_labels = {"method": request.method if request.method in {"GET", "POST", "PATCH", "DELETE", "PUT", "OPTIONS", "HEAD"} else "OTHER"}
        METRICS.add_gauge("tradepilot_http_requests_in_flight", method_labels, 1)
        try:
            try:
                response = await call_next(request)
            except Exception as error:
                duration = max(0.0, time.perf_counter() - started)
                route = safe_route(getattr(request.scope.get("route"), "path", None))
                record_http_request(method=request.method, route=route, status_code=500, duration_seconds=duration)
                log_exception(logger, "api_request_failed", error, method=request.method, route=route, status_code=500, duration_ms=round(duration * 1000, 2))
                raise
            duration = max(0.0, time.perf_counter() - started)
            route = safe_route(getattr(request.scope.get("route"), "path", None))
            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            response.headers["X-Frame-Options"] = "DENY"
            if self.hsts_enabled:
                response.headers["Strict-Transport-Security"] = self.hsts_value
            record_http_request(method=request.method, route=route, status_code=response.status_code, duration_seconds=duration)
            logger.info(
                "api_request_completed",
                extra={"method": request.method, "route": route, "status_code": response.status_code, "duration_ms": round(duration * 1000, 2)},
            )
            return response
        finally:
            METRICS.add_gauge("tradepilot_http_requests_in_flight", method_labels, -1)
            reset_context(context_token)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    return Response(
                        content='{"detail":{"code":"request_too_large","message":"Request body is too large."}}',
                        status_code=413,
                        media_type="application/json",
                    )
            except ValueError:
                pass
        return await call_next(request)


class InMemoryRateLimitMiddleware(BaseHTTPMiddleware):
    """Small single-process limiter for dev/test and Docker-compose v1.

    Production deployments should front this with Redis or managed edge limits,
    but this still prevents accidental abuse when running one API process.
    """

    def __init__(self, app, *, enabled: bool, window_seconds: int, max_requests: int) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.window_seconds = max(1, int(window_seconds))
        self.max_requests = max(1, int(max_requests))
        self.requests: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not self.enabled:
            return await call_next(request)
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return await call_next(request)
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{request.url.path}"
        now = time.time()
        bucket = self.requests[key]
        while bucket and now - bucket[0] > self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            return Response(
                content='{"detail":{"code":"rate_limited","message":"Too many requests. Please wait and try again."}}',
                status_code=429,
                media_type="application/json",
            )
        bucket.append(now)
        return await call_next(request)


class RedisRateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-backed fixed-window limiter for multi-process deployments."""

    def __init__(self, app, *, settings: BackendSettings) -> None:
        super().__init__(app)
        self.settings = settings
        self.window_seconds = max(1, int(settings.rate_limit_window_seconds))
        self.max_requests = max(1, int(settings.rate_limit_max_requests))
        self.client = None
        if settings.rate_limit_enabled:
            try:
                import redis

                self.client = redis.Redis.from_url(str(settings.redis_url), decode_responses=True)
            except Exception as exc:
                if settings.is_production:
                    raise RuntimeError("Redis rate limiting requires a working redis package and REDIS_URL.") from exc
                logger.warning("redis_rate_limiter_unavailable", exc_info=True)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not self.settings.rate_limit_enabled or request.method in {"GET", "HEAD", "OPTIONS"}:
            return await call_next(request)
        if self.client is None:
            return await call_next(request)
        client_ip = request.client.host if request.client else "unknown"
        window = int(time.time() // self.window_seconds)
        key = f"rate-limit:{client_ip}:{request.url.path}:{window}"
        try:
            count = int(self.client.incr(key))
            if count == 1:
                self.client.expire(key, self.window_seconds + 5)
        except Exception as exc:
            logger.exception("redis_rate_limit_failed")
            if self.settings.is_production:
                return Response(
                    content='{"detail":{"code":"rate_limit_unavailable","message":"Rate limiting is temporarily unavailable."}}',
                    status_code=503,
                    media_type="application/json",
                )
            return await call_next(request)
        if count > self.max_requests:
            return Response(
                content='{"detail":{"code":"rate_limited","message":"Too many requests. Please wait and try again."}}',
                status_code=429,
                media_type="application/json",
            )
        return await call_next(request)


def install_security_middleware(app, settings: BackendSettings) -> None:
    if settings.is_production:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_enabled=settings.hsts_enabled,
        hsts_max_age_seconds=settings.hsts_max_age_seconds,
        hsts_include_subdomains=settings.hsts_include_subdomains,
        hsts_preload=settings.hsts_preload,
    )
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    if settings.redis_url:
        app.add_middleware(RedisRateLimitMiddleware, settings=settings)
    else:
        app.add_middleware(
            InMemoryRateLimitMiddleware,
            enabled=settings.rate_limit_enabled,
            window_seconds=settings.rate_limit_window_seconds,
            max_requests=settings.rate_limit_max_requests,
        )
