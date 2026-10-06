"""Celery: background jobs for the booking service.

Worker:  celery -A booking.celery_app worker --without-mingle --without-gossip
         (or `make worker`). Mingle and gossip are worker-to-worker chatter over
         transient queues, which RabbitMQ 4 refuses; we don't need them.
Beat:    celery -A booking.celery_app beat     (or `make beat`) - run ONE beat only:
         it's the scheduler, two of them would schedule every job twice.
"""

from celery import Celery

from booking.config import get_settings

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
    },
)
