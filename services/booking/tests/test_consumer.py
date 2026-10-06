"""The consumer against a real RabbitMQ (test-only exchange and queues)."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import aio_pika
import pytest
from aio_pika.abc import AbstractChannel, AbstractExchange
from httpx import AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind
from sqlalchemy import select, update

from booking.config import get_settings
from booking.consumer import ROUTING_KEY, connect, declare_topology, handle_message
from booking.db import SessionFactory
from booking.models import Inventory, Reservation, ReservationStatus

from .conftest import auth

settings = get_settings()
DEAD_LETTER_QUEUE = f"{settings.catalog_events_queue}.dead"

Publish = Callable[..., Awaitable[None]]


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

    async def _publish(body: bytes, headers: dict[str, str] | None = None) -> None:
        await exchange.publish(
            aio_pika.Message(body=body, headers=headers or {}), routing_key=ROUTING_KEY
        )

    yield _publish
    await queue.cancel(consumer_tag)


def snapshot_body(version: int, tickets: int = 100, status: str = "published") -> bytes:
    payload: dict[str, Any] = {
        "event_id": 77,
        "version": version,
        "status": status,
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


async def test_cancelled_event_queues_refunds_for_paid_reservations(
    publish: Publish, client: AsyncClient, enqueued_tasks: list[tuple[str, str]]
) -> None:
    await publish(snapshot_body(version=1, tickets=100))
    await wait_for_total(100)
    async with SessionFactory() as session:
        inventory_id = await session.scalar(
            select(Inventory.id).where(Inventory.event_zone_id == 700)
        )
    reservation = (
        await client.post(
            "/reservations",
            json={"inventory_id": inventory_id, "quantity": 2},
            headers=auth(5),
        )
    ).json()
    async with SessionFactory() as session:  # it gets paid
        await session.execute(
            update(Reservation)
            .where(Reservation.id == uuid.UUID(reservation["id"]))
            .values(status=ReservationStatus.CONFIRMED, paid_at=datetime.now(UTC))
        )
        await session.commit()

    await publish(snapshot_body(version=2, tickets=100, status="cancelled"))

    expected = ("booking.refund_payment", reservation["id"])
    deadline = asyncio.get_running_loop().time() + 5
    while expected not in enqueued_tasks:
        assert asyncio.get_running_loop().time() < deadline, enqueued_tasks
        await asyncio.sleep(0.05)


async def test_consumer_continues_the_catalog_trace(
    publish: Publish, spans: InMemorySpanExporter
) -> None:
    # What the catalog's relay puts in the message headers (W3C trace context).
    trace_id, parent_span_id = "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7"
    await publish(
        snapshot_body(version=1, tickets=100),
        headers={"traceparent": f"00-{trace_id}-{parent_span_id}-01"},
    )
    await wait_for_total(100)

    deadline = asyncio.get_running_loop().time() + 5
    while not [s for s in spans.get_finished_spans() if s.name.endswith("process")]:
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.05)
    [process] = [s for s in spans.get_finished_spans() if s.name.endswith("process")]

    assert process.context is not None and process.parent is not None
    assert format(process.context.trace_id, "032x") == trace_id  # same trace
    assert format(process.parent.span_id, "016x") == parent_span_id  # child of relay
    assert process.kind == SpanKind.CONSUMER
