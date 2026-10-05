from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from booking import service
from booking.db import SessionFactory
from booking.models import Reservation, ReservationStatus

from .conftest import CreateInventory, available

TTL = timedelta(minutes=10)


async def _reserve(inventory_id: int, quantity: int, now: datetime) -> Reservation:
    async with SessionFactory() as session:
        return await service.reserve(
            session,
            user_id=1,
            inventory_id=inventory_id,
            quantity=quantity,
            now=now,
            ttl=TTL,
        )


async def _expire(now: datetime) -> int:
    async with SessionFactory() as session:
        return await service.expire_due_reservations(session, now=now)


async def test_overdue_reservations_expire_and_return_tickets(
    create_inventory: CreateInventory,
) -> None:
    inventory = await create_inventory(total=10)
    now = datetime.now(UTC)
    await _reserve(inventory.id, 3, now - timedelta(minutes=11))  # overdue
    await _reserve(inventory.id, 2, now)  # still within its 10 minutes
    assert await available(inventory.id) == 5

    assert await _expire(now) == 1
    assert await available(inventory.id) == 8


async def test_expiry_is_idempotent(create_inventory: CreateInventory) -> None:
    inventory = await create_inventory(total=10)
    now = datetime.now(UTC)
    await _reserve(inventory.id, 4, now - timedelta(minutes=11))

    assert await _expire(now) == 1
    assert await _expire(now) == 0  # running it again gives nothing back twice
    assert await available(inventory.id) == 10


async def test_confirmed_reservations_never_expire(
    create_inventory: CreateInventory,
) -> None:
    inventory = await create_inventory(total=10)
    now = datetime.now(UTC)
    reservation = await _reserve(inventory.id, 4, now - timedelta(minutes=11))
    async with SessionFactory() as session:
        await session.execute(
            update(Reservation)
            .where(Reservation.id == reservation.id)
            .values(status=ReservationStatus.CONFIRMED)
        )
        await session.commit()

    assert await _expire(now) == 0
    assert await available(inventory.id) == 6
