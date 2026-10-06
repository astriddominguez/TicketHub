"""The consumer against a real RabbitMQ (test-only exchange and queues)."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import aio_pika
import pytest
from aio_pika.abc import AbstractChannel, AbstractExchange
from httpx import AsyncClient
from sqlalchemy import select

from booking.config import get_settings
from booking.consumer import ROUTING_KEY, connect, declare_topology, handle_message
from booking.db import SessionFactory
from booking.models import Inventory

settings = get_settings()
DEAD_LETTER_QUEUE = f"{settings.catalog_events_queue}.dead"

Publish = Callable[[bytes], Awaitable[None]]


@pytest.fixture
async def channel() -> AsyncIterator[AbstractChannel]:
    connection = await connect(settings)
    async with connection:
        channel = await connection.channel()
        yield channel
        # Leave nothing behind for the next run.
        await channel.queue_delete(settings.catalog_events_queue)
        await channel.queue_delete(DEAD_LETTER_QUEUE)


@pytest.fixture
async def publish(channel: AbstractChannel) -> AsyncIterator[Publish]:
    """Start the real consumer and return a function that publishes to it."""
    queue = await declare_topology(channel, settings)
    consumer_tag = await queue.consume(handle_message)
    exchange: AbstractExchange = await channel.get_exchange(
        settings.catalog_events_exchange
    )

    async def _publish(body: bytes) -> None:
        await exchange.publish(aio_pika.Message(body=body), routing_key=ROUTING_KEY)

    yield _publish
    await queue.cancel(consumer_tag)


def snapshot_body(version: int, tickets: int = 100) -> bytes:
    payload: dict[str, Any] = {
        "event_id": 77,
        "version": version,
        "status": "published",
        "starts_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        "zones": [
            {
                "event_zone_id": 700,
                "zone_name": "Pista",
                "price": "45.00",
                "tickets_for_sale": tickets,
            }
        ],
    }
    return json.dumps(payload).encode()


async def wait_for_total(expected: int, timeout: float = 5.0) -> None:
    """Poll the database: the consumer works asynchronously."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        async with SessionFactory() as session:
            total = await session.scalar(
                select(Inventory.total).where(Inventory.event_zone_id == 700)
            )
        if total == expected:
            return
        assert asyncio.get_running_loop().time() < deadline, f"total={total}"
        await asyncio.sleep(0.05)


async def test_published_snapshot_creates_inventory(publish: Publish) -> None:
    await publish(snapshot_body(version=1, tickets=100))
    await wait_for_total(100)


async def test_redelivered_and_late_messages_are_harmless(publish: Publish) -> None:
    await publish(snapshot_body(version=2, tickets=150))
    await wait_for_total(150)
    await publish(snapshot_body(version=2, tickets=150))  # redelivery
    await publish(snapshot_body(version=1, tickets=100))  # late, older
    await publish(snapshot_body(version=3, tickets=200))
    await wait_for_total(200)


async def test_invalid_message_goes_to_dead_letter_queue(
    publish: Publish, channel: AbstractChannel
) -> None:
    await publish(b'{"event_id": "not a number"}')

    dead = await channel.declare_queue(
        DEAD_LETTER_QUEUE, durable=True, arguments={"x-queue-type": "quorum"}
    )
    deadline = asyncio.get_running_loop().time() + 5
    message = None
    while message is None:
        message = await dead.get(fail=False)
        if message is None:
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.05)
    await message.ack()
    assert message.body == b'{"event_id": "not a number"}'


async def test_snapshot_invalidates_cached_availability(
    publish: Publish, client: AsyncClient
) -> None:
    await publish(snapshot_body(version=1, tickets=100))
    await wait_for_total(100)
    cached = await client.get("/events/77/availability")  # now cached for 5s
    assert cached.json()[0]["total"] == 100

    await publish(snapshot_body(version=2, tickets=300))

    # The cache lives 5s. Seeing the new value well within that (2s) proves the
    # consumer invalidated it. Poll the API, not the database: the consumer
    # commits first and invalidates right after, so checking the DB would race.
    deadline = asyncio.get_running_loop().time() + 2
    while (await client.get("/events/77/availability")).json()[0]["total"] != 300:
        assert asyncio.get_running_loop().time() < deadline, "cache not invalidated"
        await asyncio.sleep(0.05)
