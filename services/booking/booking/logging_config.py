"""Structured logging: every log line is data (JSON in production).

Our code logs with structlog (`log.info("snapshot_applied", event_id=20)`);
libraries keep using the standard `logging` module (uvicorn, celery, aio-pika...).
Both go through the same processors, so all output has the same shape.

Context bound with `structlog.contextvars.bind_contextvars(request_id=...)` is
added to every line logged afterwards in the same request or task.
"""

import logging
import sys
from typing import Literal

import structlog
from structlog.types import Processor

LogFormat = Literal["console", "json"]


def configure_logging(*, level: str = "INFO", fmt: LogFormat = "console") -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,  # request_id, task_id...
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
    for name in ("uvicorn", "uvicorn.error", "celery", "aio_pika", "aiormq"):
        library_logger = logging.getLogger(name)
        library_logger.handlers.clear()
        library_logger.propagate = True
    # We log one line per request ourselves, with more detail than uvicorn's.
    logging.getLogger("uvicorn.access").disabled = True
    # Very chatty at INFO; their warnings and errors still come through.
    for name in ("aiormq", "aio_pika", "httpx"):
        logging.getLogger(name).setLevel(max(logging.WARNING, root.level))
