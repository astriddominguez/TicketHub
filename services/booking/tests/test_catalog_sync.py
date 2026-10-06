"""Applying catalog snapshots: idempotent, order-proof, never losing held tickets."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select, update

from booking.catalog_sync import EventSnapshot, apply_event_snapshot
from booking.db import SessionFactory
from booking.models import Inventory, Reservation, ReservationStatus

from .conftest import auth

EVENT_ID = 12


def snapshot(
    version: int,
    *,
    status: str = "published",
    zones: list[dict[str, Any]] | None = None,
) -> EventSnapshot:
    if zones is None:
        zones = [zone(1, price="50.00", tickets=100)]
    return EventSnapshot.model_validate(
        {
            "event_id": EVENT_ID,
            "version": version,
            "status": status,
            "starts_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "zones": zones,
        }
    )


def zone(
    event_zone_id: int, *, price: str = "50.00", tickets: int = 100
) -> dict[str, Any]:
    return {
        "event_zone_id": event_zone_id,
        "zone_name": f"Zone {event_zone_id}",
        "price": price,
        "tickets_for_sale": tickets,
    }


async def apply(snap: EventSnapshot) -> bool:
    async with SessionFactory() as session:
        return (await apply_event_snapshot(session, snap)).applied


async def inventory(event_zone_id: int) -> Inventory:
    async with SessionFactory() as session:
        row = await session.scalar(
            select(Inventory).where(Inventory.event_zone_id == event_zone_id)
        )
        assert row is not None
        return row


async def reserve(client: AsyncClient, event_zone_id: int, quantity: int) -> int:
    row = await inventory(event_zone_id)
    response = await client.post(
        "/reservations",
        json={"inventory_id": row.id, "quantity": quantity},
        headers=auth(1),
    )
    return response.status_code


async def test_first_snapshot_creates_inventory() -> None:
    assert await apply(snapshot(1, zones=[zone(1, tickets=100), zone(2, tickets=50)]))
    first, second = await inventory(1), await inventory(2)
    assert (first.total, first.available, first.on_sale) == (100, 100, True)
    assert (second.total, second.available) == (50, 50)


async def test_duplicate_and_older_versions_are_ignored() -> None:
    assert await apply(snapshot(5, zones=[zone(1, price="70.00")]))
    assert not await apply(snapshot(5, zones=[zone(1, price="70.00")]))  # duplicate
    assert not await apply(snapshot(3, zones=[zone(1, price="10.00")]))  # arrived late
    assert (await inventory(1)).price == 70


async def test_price_change_is_applied() -> None:
    await apply(snapshot(1, zones=[zone(1, price="50.00")]))
    await apply(snapshot(2, zones=[zone(1, price="65.00")]))
    assert (await inventory(1)).price == 65


async def test_more_tickets_keeps_what_is_already_held(client: AsyncClient) -> None:
    await apply(snapshot(1, zones=[zone(1, tickets=10)]))
    assert await reserve(client, 1, 4) == 201  # 6 left

    await apply(snapshot(2, zones=[zone(1, tickets=15)]))

    row = await inventory(1)
    assert (row.total, row.available) == (15, 11)  # the 4 held tickets stay held


async def test_fewer_tickets_than_held_stops_at_zero(client: AsyncClient) -> None:
    await apply(snapshot(1, zones=[zone(1, tickets=10)]))
    assert await reserve(client, 1, 8) == 201

    await apply(snapshot(2, zones=[zone(1, tickets=5)]))

    row = await inventory(1)
    assert (row.total, row.available) == (5, 0)  # no negative stock, no crash


async def test_removed_zone_stops_selling(client: AsyncClient) -> None:
    await apply(snapshot(1, zones=[zone(1), zone(2)]))
    await apply(snapshot(2, zones=[zone(1)]))

    assert (await inventory(2)).on_sale is False
    assert await reserve(client, 2, 1) == 409
    assert await reserve(client, 1, 1) == 201


async def test_cancelled_event_stops_all_sales(client: AsyncClient) -> None:
    await apply(snapshot(1, zones=[zone(1), zone(2)]))
    await apply(snapshot(2, status="cancelled", zones=[zone(1), zone(2)]))

    assert (await inventory(1)).on_sale is False
    assert (await inventory(2)).on_sale is False
    assert await reserve(client, 1, 1) == 409


async def test_postponed_event_pauses_sales_until_republished(
    client: AsyncClient,
) -> None:
    await apply(snapshot(1))
    await apply(snapshot(2, status="postponed"))
    assert await reserve(client, 1, 1) == 409

    await apply(snapshot(3, status="published"))
    assert await reserve(client, 1, 1) == 201


async def test_cancelled_event_cancels_reservations_and_lists_paid_ones(
    client: AsyncClient,
) -> None:
    await apply(snapshot(1, zones=[zone(1, tickets=10)]))
    row = await inventory(1)
    statuses = []
    for user_id in (1, 2):
        response = await client.post(
            "/reservations",
            json={"inventory_id": row.id, "quantity": 2},
            headers=auth(user_id),
        )
        statuses.append(response.json()["id"])
    unpaid_id, paid_id = statuses
    async with SessionFactory() as session:  # the second one was paid
        await session.execute(
            update(Reservation)
            .where(Reservation.id == uuid.UUID(paid_id))
            .values(status=ReservationStatus.CONFIRMED, paid_at=datetime.now(UTC))
        )
        await session.commit()

    async with SessionFactory() as session:
        result = await apply_event_snapshot(
            session, snapshot(2, status="cancelled", zones=[zone(1, tickets=10)])
        )

    assert result.to_refund == [uuid.UUID(paid_id)]
    async with SessionFactory() as session:
        for reservation_id in (unpaid_id, paid_id):
            stored = await session.get(Reservation, uuid.UUID(reservation_id))
            assert stored is not None
            assert stored.status == ReservationStatus.CANCELLED
    row = await inventory(1)
    assert (row.available, row.total, row.on_sale) == (10, 10, False)
