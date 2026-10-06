"""Celery tasks, run directly (no worker or broker needed)."""

import asyncio
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import pytest
from httpx import AsyncClient

from booking import emails, service, tasks
from booking.celery_app import celery_app
from booking.db import SessionFactory
from booking.models import Reservation

from .conftest import CreateInventory, auth, available


async def _reserve(
    inventory_id: int, *, email: str | None, minutes_ago: int = 0
) -> Reservation:
    async with SessionFactory() as session:
        return await service.reserve(
            session,
            user_id=1,
            inventory_id=inventory_id,
            quantity=2,
            now=datetime.now(UTC) - timedelta(minutes=minutes_ago),
            ttl=timedelta(minutes=10),
            buyer_email=email,
        )


class FakeSMTP:
    sent: list[EmailMessage] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        pass

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def send_message(self, message: EmailMessage) -> None:
        FakeSMTP.sent.append(message)


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> list[EmailMessage]:
    FakeSMTP.sent = []
    monkeypatch.setattr(emails.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP.sent


def test_expiry_is_scheduled_every_minute() -> None:
    schedule = celery_app.conf.beat_schedule["expire-reservations-every-minute"]
    assert schedule["task"] == "booking.expire_reservations"
    assert schedule["schedule"] == 60.0


async def test_expire_task_releases_overdue_tickets(
    create_inventory: CreateInventory,
) -> None:
    inventory = await create_inventory(total=10)
    await _reserve(inventory.id, email=None, minutes_ago=11)
    assert await available(inventory.id) == 8

    # A worker thread runs the task with its own event loop, like Celery does.
    assert await asyncio.to_thread(tasks.expire_reservations) == 1
    assert await available(inventory.id) == 10


async def test_pending_email_is_sent_to_the_buyer(
    create_inventory: CreateInventory, smtp: list[EmailMessage]
) -> None:
    inventory = await create_inventory(price="40.00")
    reservation = await _reserve(inventory.id, email="ana@example.com")

    sent = await asyncio.to_thread(
        tasks.send_reservation_pending_email, str(reservation.id)
    )

    assert sent is True
    [message] = smtp
    assert message["To"] == "ana@example.com"
    body = message.get_content()
    assert "2 ticket(s) for you at 40.00 € each (80.00 € in total)" in body
    assert str(reservation.id) in body


async def test_no_email_once_the_reservation_is_no_longer_pending(
    create_inventory: CreateInventory, smtp: list[EmailMessage]
) -> None:
    inventory = await create_inventory()
    reservation = await _reserve(inventory.id, email="ana@example.com")
    async with SessionFactory() as session:
        await service.cancel(session, reservation_id=reservation.id, user_id=1)

    sent = await asyncio.to_thread(
        tasks.send_reservation_pending_email, str(reservation.id)
    )
    assert sent is False
    assert smtp == []


async def test_reserving_enqueues_the_email_without_waiting_for_it(
    client: AsyncClient,
    create_inventory: CreateInventory,
    enqueued_tasks: list[tuple[str, str]],
) -> None:
    inventory = await create_inventory()
    response = await client.post(
        "/reservations",
        json={"inventory_id": inventory.id, "quantity": 1},
        headers=auth(1, email="ana@example.com"),
    )
    assert enqueued_tasks == [
        ("booking.send_reservation_pending_email", response.json()["id"])
    ]


async def test_no_email_enqueued_without_an_address(
    client: AsyncClient,
    create_inventory: CreateInventory,
    enqueued_tasks: list[tuple[str, str]],
) -> None:
    inventory = await create_inventory()
    await client.post(
        "/reservations",
        json={"inventory_id": inventory.id, "quantity": 1},
        headers=auth(1),
    )
    assert enqueued_tasks == []


async def test_broker_down_does_not_fail_the_reservation(
    client: AsyncClient,
    create_inventory: CreateInventory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_delay(reservation_id: str) -> None:
        raise ConnectionError("RabbitMQ is down")

    monkeypatch.setattr(tasks.send_reservation_pending_email, "delay", broken_delay)
    inventory = await create_inventory(total=5)
    response = await client.post(
        "/reservations",
        json={"inventory_id": inventory.id, "quantity": 1},
        headers=auth(1, email="ana@example.com"),
    )
    assert response.status_code == 201  # the email is best effort
    assert await available(inventory.id) == 4
