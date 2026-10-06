"""Structured logging: every log line is data (JSON in production).

Our code logs with structlog (`log.info("outbox_published", count=3)`); Django and
other libraries keep using the standard `logging` module.
Both go through the same processors, so all output has the same shape.

Context bound with `structlog.contextvars.bind_contextvars(request_id=...)` is
added to every line logged afterwards in the same request or task.
"""

import logging
import sys
from typing import Literal

import structlog
from opentelemetry import trace
from structlog.types import EventDict, Processor, WrappedLogger

LogFormat = Literal["console", "json"]


def add_trace_ids(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Put the current trace id on the log line: jump from a log to its trace."""
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        event_dict["trace_id"] = format(context.trace_id, "032x")
        event_dict["span_id"] = format(context.span_id, "016x")
    return event_dict


def configure_logging(*, level: str = "INFO", fmt: LogFormat = "console") -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,  # request_id, task_id...
        add_trace_ids,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    renderer: Processor
    if fmt == "json":
        # Exceptions as structured data, not a blob of text.
        final: list[Processor] = [structlog.processors.dict_tracebacks]
        renderer = structlog.processors.JSONRenderer()
    else:
        final = []
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # Applied to records from the standard `logging` module (libraries).
        foreign_pre_chain=[*shared, structlog.stdlib.ExtraAdder()],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *final,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)  # containers collect stdout
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Libraries that install their own handlers: send them through ours instead.
    for name in ("django", "django.request", "gunicorn", "gunicorn.error"):
        library_logger = logging.getLogger(name)
        library_logger.handlers.clear()
        library_logger.propagate = True
    # We log one line per request ourselves (config.middleware), with more detail.
    for name in ("django.server", "gunicorn.access"):
        logging.getLogger(name).disabled = True
    # Very chatty at INFO; their warnings and errors still come through.
    logging.getLogger("pika").setLevel(max(logging.WARNING, root.level))
