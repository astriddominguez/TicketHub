import time

import structlog
from django.conf import settings
from django.core.management.base import BaseCommand
from pika.exceptions import AMQPError, UnroutableError

from messaging.publisher import RabbitPublisher
from messaging.relay import relay_batch

log = structlog.get_logger("catalog.relay")

MAX_BACKOFF_SECONDS = 30.0


class Command(BaseCommand):
    help = "Publish pending outbox messages to RabbitMQ (runs until Ctrl+C)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="One batch, then exit.")
        parser.add_argument("--interval", type=float, default=1.0)

    def handle(self, *args, **options):
        log.info("relay_started", exchange=settings.CATALOG_EVENTS_EXCHANGE)
        try:
            self._run(once=options["once"], interval=options["interval"])
        except KeyboardInterrupt:
            log.info("relay_stopped", note="pending messages stay in the outbox")

    def _run(self, *, once: bool, interval: float) -> None:
        publisher: RabbitPublisher | None = None
        backoff = interval
        try:
            while True:
                failed = False
                sent = 0
                try:
                    if publisher is None:
                        publisher = RabbitPublisher(
                            settings.RABBITMQ_URL, settings.CATALOG_EVENTS_EXCHANGE
                        )
                    sent = relay_batch(publisher)
                    if sent:
                        log.info("outbox_published", count=sent)
                except UnroutableError:
                    # RabbitMQ is fine, but no queue is bound for these messages yet.
                    failed = True
                    log.warning(
                        "outbox_unroutable",
                        hint="is the booking consumer running?",
                        retry_in_seconds=backoff,
                    )
                except AMQPError as exc:
                    failed = True
                    log.warning(
                        "rabbitmq_unavailable",
                        error=type(exc).__name__,
                        retry_in_seconds=backoff,
                    )
                    if publisher is not None:
                        publisher.close()
                    publisher = None

                if once:
                    return
                if failed:
                    # Exponential backoff (1, 2, 4... up to 30s): don't hammer a
                    # broken broker, and don't flood the logs.
                    time.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                else:
                    backoff = interval
                    if not sent:  # idle: poll the outbox at the normal pace
                        time.sleep(interval)
        finally:
            if publisher is not None:
                publisher.close()
