from django.db import transaction
from django.utils import timezone
from opentelemetry import trace
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import SpanKind

from .models import OutboxMessage
from .publisher import Publisher

tracer = trace.get_tracer(__name__)


def relay_batch(publisher: Publisher, batch_size: int = 100) -> int:
    """Publish pending outbox messages in order; return how many were sent.

    SELECT ... FOR UPDATE SKIP LOCKED lets several relays run at once without
    sending the same message twice concurrently: each one skips rows another
    relay has already locked.

    Delivery is at-least-once: if we crash after publishing but before commit,
    the message is sent again next time. Consumers must be idempotent.
    """
    with transaction.atomic():
        messages = list(
            OutboxMessage.objects.select_for_update(skip_locked=True)
            .filter(published_at__isnull=True)
            .order_by("id")[:batch_size]
        )
        for message in messages:
            body = {**message.payload, "version": message.pk}
            # Continue the trace of the request that wrote the message (it may be
            # seconds old and in another process), then pass it on in the headers.
            with tracer.start_as_current_span(
                f"{message.routing_key} publish",
                context=extract(message.trace_context),
                kind=SpanKind.PRODUCER,
                attributes={
                    "messaging.system": "rabbitmq",
                    "messaging.message.id": str(message.pk),
                },
            ):
                headers: dict[str, str] = {}
                inject(headers)
                # If this raises, the whole batch rolls back and stays pending.
                publisher.publish(
                    message.routing_key,
                    body,
                    message_id=str(message.pk),
                    headers=headers,
                )
            message.published_at = timezone.now()
            message.save(update_fields=["published_at"])
    return len(messages)
