"""The heart of the project: many buyers, few tickets, never a double sale.

These tests use real concurrent requests on separate database connections,
exactly like production traffic: no mocks, no shared transaction.
"""

import asyncio
import random
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import func, select

from booking import service
from booking.db import SessionFactory
from booking.models import Inventory, Reservation, ReservationStatus

from .conftest import CreateInventory, auth, available


async def test_fifty_buyers_ten_tickets_exactly_ten_win(
    client: AsyncClient, create_inventory: CreateInventory
) -> None:
    inventory = await create_inventory(total=10)

    async def buy(user_id: int) -> int:
        response = await client.post(
            "/reservations",
            json={"inventory_id": inventory.id, "quantity": 1},
            headers=auth(user_id),
        )
        return response.status_code

    statuses = await asyncio.gather(*(buy(user_id) for user_id in range(1, 51)))

    assert statuses.count(201) == 10
    assert statuses.count(409) == 40
    assert await available(inventory.id) == 0
    async with SessionFactory() as session:
        reserved = await session.scalar(select(func.sum(Reservation.quantity)))
    assert reserved == 10


async def test_mixed_quantities_never_oversell(
    client: AsyncClient, create_inventory: CreateInventory
) -> None:
    inventory = await create_inventory(total=25)
    rng = random.Random(42)  # fixed seed: reproducible if it ever fails

    async def buy(user_id: int) -> None:
        await client.post(
            "/reservations",
            json={"inventory_id": inventory.id, "quantity": rng.randint(1, 4)},
            headers=auth(user_id),
        )

    await asyncio.gather(*(buy(user_id) for user_id in range(1, 41)))

    # The invariant: every ticket is either still available or held, never both.
    async with SessionFactory() as session:
        held = await session.scalar(
            select(func.coalesce(func.sum(Reservation.quantity), 0))
        )
    assert held is not None  # COALESCE turns "no rows" into 0
    assert held + await available(inventory.id) == 25
    assert held <= 25


async def test_cancel_and_expiry_racing_release_tickets_once(
    create_inventory: CreateInventory,
) -> None:
    inventory = await create_inventory(total=10)
    now = datetime.now(UTC)
    async with SessionFactory() as session:
        reservation = await service.reserve(
            session,
            user_id=1,
            inventory_id=inventory.id,
            quantity=4,
            now=now - timedelta(minutes=11),
            ttl=timedelta(minutes=10),
        )

    async def cancel() -> None:
        async with SessionFactory() as session:
            try:
                await service.cancel(session, reservation_id=reservation.id, user_id=1)
            except service.ReservationNotPendingError:
                pass  # expiry won the race: that's fine

    async def expire() -> None:
        async with SessionFactory() as session:
            await service.expire_due_reservations(session, now=now)

    await asyncio.gather(cancel(), expire(), cancel(), expire())

    assert await available(inventory.id) == 10  # released once, not 14 or 18
    async with SessionFactory() as session:
        final = await session.get(Reservation, reservation.id)
    assert final is not None
    assert final.status in {ReservationStatus.CANCELLED, ReservationStatus.EXPIRED}


async def test_naive_read_then_write_oversells(
    create_inventory: CreateInventory,
) -> None:
    """Why we don't do it the obvious way: this is the Ana & Luis race.

    Everyone reads "1 left" before anyone writes. The barrier forces that
    interleaving so the bug shows up every time, not just under real load.
    """
    inventory = await create_inventory(total=1)
    buyers = 5
    barrier = asyncio.Barrier(buyers)

    async def naive_buy() -> bool:
        async with SessionFactory() as session:
            row = await session.get(Inventory, inventory.id)  # 1. read
            assert row is not None
            await barrier.wait()
            if row.available >= 1:  # 2. check (with stale data!)
                row.available -= 1  # 3. subtract in Python
                await session.commit()  # 4. write "available = 0"
                return True
            return False

    sold = await asyncio.gather(*(naive_buy() for _ in range(buyers)))

    assert sum(sold) == buyers  # 5 buyers "got" the single ticket
    assert await available(inventory.id) == 0  # ...and the database can't tell
