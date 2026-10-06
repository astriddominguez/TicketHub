"""One request id per HTTP request, on every log line and in the response."""

import re
import time
import uuid
from collections.abc import Callable

import structlog
from django.http import HttpRequest, HttpResponse

log = structlog.get_logger("catalog.http")

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller's id (e.g. from the gateway) only if it looks like an id: a
# header is user input, and a raw one could inject fake lines into our logs.
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()

        response = self.get_response(request)

        response[REQUEST_ID_HEADER] = request_id
        # DRF authenticates (JWT) inside the view and stores the user back on
        # the request, so by now we know who it was.
        user = getattr(request, "user", None)
        log.info(
            "request",
            method=request.method,
            path=request.path,
            status_code=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            user_id=user.pk if user is not None and user.is_authenticated else None,
        )
        return response
