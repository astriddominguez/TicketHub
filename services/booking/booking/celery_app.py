"""Celery: background jobs for the booking service.

Worker:  celery -A booking.celery_app worker --without-mingle --without-gossip
         (or `make worker`). Mingle and gossip are worker-to-worker chatter over
         transient queues, which RabbitMQ 4 refuses; we don't need them.
Beat:    celery -A booking.celery_app beat     (or `make beat`) - run ONE beat only:
         it's the scheduler, two of them would schedule every job twice.
"""

from typing import Any

import structlog
from celery import Celery
from celery.signals import (
    setup_logging,
    task_postrun,
    task_prerun,
    worker_process_init,
)

from booking.config import get_settings
from booking.logging_config import configure_logging
from booking.telemetry import configure_telemetry

settings = get_settings()

celery_app = Celery(
    "booking",
    broker=settings.rabbitmq_url.get_secret_value(),  # RabbitMQ, already running
    include=["booking.tasks"],
)
celery_app.conf.update(
    # Our own queue name, so we never mix with another app's default "celery" queue.
    task_default_queue="booking.tasks",
    task_ignore_result=True,  # nobody waits for results: no result backend needed
    # Ack only when the task has finished: if a worker dies mid-task, RabbitMQ
    # hands the task to another worker. So tasks must be safe to run twice.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # long tasks don't hoard messages from others
    # Remote control ("celery inspect/control") uses a transient, non-exclusive
    # queue, which RabbitMQ 4 refuses ("transient_nonexcl_queues is deprecated").
    # We don't use it; without this the worker can't even start.
    worker_enable_remote_control=False,
    broker_connection_retry_on_startup=True,
    timezone="UTC",
    beat_schedule={
        "expire-reservations-every-minute": {
            "task": "booking.expire_reservations",
            "schedule": 60.0,
        },
        "sweep-payments-every-minute": {
            "task": "booking.sweep_payments",
            "schedule": 60.0,
        },
    },
)


@setup_logging.connect
def _configure_logging(**kwargs: Any) -> None:
    # Connecting this signal stops Celery from installing its own log format.
    configure_logging(level=settings.log_level, fmt=settings.log_format)


@worker_process_init.connect
def _configure_telemetry(**kwargs: Any) -> None:
    # Per worker process: tracing's background exporter thread doesn't survive
    # the fork that creates each worker process.
    configure_telemetry("booking-worker")


@task_prerun.connect
def _bind_task_context(task_id: str, task: Any, **kwargs: Any) -> None:
    # Every log line inside a task says which task (and which run of it) it was.
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(task_id=task_id, task=task.name)


@task_postrun.connect
def _clear_task_context(**kwargs: Any) -> None:
    structlog.contextvars.clear_contextvars()
