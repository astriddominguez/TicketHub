"""Consume the catalog's event snapshots from RabbitMQ.

Run with: python -m booking.consumer   (or `make consumer`)

Guarantees:
- Durable quorum queue: messages wait here while this process is down.
- Ack only after the database commit: a crash mid-way means redelivery, which is
  harmless because applying a snapshot is idempotent (per-event version).
- Poison messages (invalid payload) go straight to a dead-letter queue to be
  inspected by a human, instead of being retried forever and blocking others.
  Other failures (e.g. the database is down) are retried; after RabbitMQ's
  delivery limit they are dead-lettered too.
"""

import asyncio

import aio_pika
import structlog
from aio_pika.abc import (
    AbstractChannel,
    AbstractIncomingMessage,
    AbstractQueue,
    AbstractRobustConnection,
)
from pydantic import ValidationError

from booking import cache, tasks
from booking.catalog_sync import EventSnapshot, apply_event_snapshot
from booking.config import Settings, get_settings
from booking.db import SessionFactory
from booking.logging_config import configure_logging

log = structlog.get_logger("booking.consumer")

ROUTING_KEY = "event.snapshot"


async def declare_topology(
    channel: AbstractChannel, settings: Settings
) -> AbstractQueue:
    """Exchange, our queue and its dead-letter queue. Safe to run on every start."""
    exchange = await channel.declare_exchange(
        settings.catalog_events_exchange, aio_pika.ExchangeType.TOPIC, durable=True
    )
    dead_letter_queue = f"{settings.catalog_events_queue}.dead"
    await channel.declare_queue(
        dead_letter_queue, durable=True, arguments={"x-queue-type": "quorum"}
    )
    queue = await channel.declare_queue(
        settings.catalog_events_queue,
        durable=True,
        arguments={
            # Quorum queues are replicated and count redeliveries per message.
            "x-queue-type": "quorum",
            "x-delivery-limit": 5,
            # Rejected or over-the-limit messages go to the ".dead" queue.
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": dead_letter_queue,
        },
    )
    await queue.bind(exchange, routing_key=ROUTING_KEY)
    return queue


async def handle_message(message: AbstractIncomingMessage) -> None:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(message_id=message.message_id)
    try:
        snapshot = EventSnapshot.model_validate_json(message.body)
    except ValidationError as exc:
        log.error(
            "snapshot_invalid_dead_lettered",
            body=message.body.decode(errors="replace")[:500],
            errors=exc.errors(include_url=False),
        )
        await message.reject(requeue=False)
        return

    try:
        async with SessionFactory() as session:
            result = await apply_event_snapshot(session, snapshot)
    except Exception:
        log.exception("snapshot_apply_failed", event_id=snapshot.event_id)
        await asyncio.sleep(1)  # don't spin if the database is down
        await message.nack(requeue=True)
        return

    if result.applied:
        await cache.invalidate(cache.availability_key(snapshot.event_id))
        for reservation_id in result.to_refund:
            # Best effort: if this is lost, the payment sweep refunds it anyway.
            try:
                await asyncio.to_thread(tasks.refund_payment.delay, str(reservation_id))
            except Exception:
                log.warning("refund_enqueue_failed", reservation_id=str(reservation_id))
        log.info(
            "snapshot_applied",
            event_id=snapshot.event_id,
            version=snapshot.version,
            status=snapshot.status,
            zones=len(snapshot.zones),
            refunds=len(result.to_refund),
        )
    else:
        log.info(
            "snapshot_ignored_stale",
            event_id=snapshot.event_id,
            version=snapshot.version,
        )
    await message.ack()  # only now: the change is safely committed


async def connect(settings: Settings) -> AbstractRobustConnection:
    # "Robust": reconnects by itself and re-declares the topology after a restart.
    return await aio_pika.connect_robust(settings.rabbitmq_url.get_secret_value())


async def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    connection = await connect(settings)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=10)  # at most 10 unacked at a time
        queue = await declare_topology(channel, settings)
        await queue.consume(handle_message)
        log.info("consumer_started", queue=settings.catalog_events_queue)
        await asyncio.Future()  # run until cancelled (Ctrl+C)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("consumer_stopped")
