"""One request id per HTTP request, on every log line and in the response."""

import re
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response

from booking.metrics import HTTP_LATENCY, HTTP_REQUESTS

log = structlog.get_logger("booking.http")

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller's id (e.g. from the gateway) only if it looks like an id: a
# header is user input, and a raw one could inject fake lines into our logs.
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
QUIET_PATHS = {"/health"}  # probes hit this every few seconds
UNMEASURED_PATHS = {"/metrics"}  # Prometheus scraping itself: not user traffic


def request_id_from(request: Request) -> str:
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    return incoming if SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex


async def request_context_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request_id_from(request)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("request_failed", method=request.method, path=request.url.path)
        raise
    response.headers[REQUEST_ID_HEADER] = request_id
    elapsed = time.perf_counter() - started
    duration_ms = round(elapsed * 1000, 1)
    if request.url.path not in UNMEASURED_PATHS:
        route = _route_template(request)
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, route).observe(elapsed)
    log_line = log.debug if request.url.path in QUIET_PATHS else log.info
    log_line(
        "request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


def _route_template(request: Request) -> str:
    """ "/reservations/{reservation_id}", never the real id (see booking.metrics)."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"  # e.g. 404s
