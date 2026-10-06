"""One request id per HTTP request, on every log line and in the response."""

import re
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response

log = structlog.get_logger("booking.http")

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller's id (e.g. from the gateway) only if it looks like an id: a
# header is user input, and a raw one could inject fake lines into our logs.
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
QUIET_PATHS = {"/health"}  # probes hit this every few seconds


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
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    log_line = log.debug if request.url.path in QUIET_PATHS else log.info
    log_line(
        "request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response
