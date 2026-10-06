"""Reservation rules. Every state change is a single conditional UPDATE.

The pattern used everywhere: "change X only if it is still in the state I expect",
in one SQL statement. Postgres locks the row while it runs, so two concurrent
requests can never both see the old state (no read-then-write gap).
"""

import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from booking.models import Inventory, Reservation, ReservationStatus


class InventoryNotFoundError(Exception):
    pass


class NotEnoughTicketsError(Exception):
    pass


class NotOnSaleError(Exception):
    pass


class ReservationNotFoundError(Exception):
    pass


class ReservationNotPendingError(Exception):
    pass


async def reserve(
    session: AsyncSession,
    *,
    user_id: int,
    inventory_id: int,
    quantity: int,
    now: datetime,
    ttl: timedelta,
    buyer_email: str | None = None,
) -> Reservation:
    # Check, subtract and read the price in ONE statement.
    taken = await session.execute(
        update(Inventory)
        .where(
            Inventory.id == inventory_id,
            Inventory.on_sale.is_(True),
            Inventory.available >= quantity,
        )
        .values(available=Inventory.available - quantity)
        .returning(Inventory.price)
    )
    price = taken.scalar_one_or_none()
    if price is None:
        # Nothing was updated: find out which condition failed, for a clear error.
        on_sale = await session.scalar(
            select(Inventory.on_sale).where(Inventory.id == inventory_id)
        )
        await session.rollback()
        if on_sale is None:
            raise InventoryNotFoundError
        if not on_sale:
            raise NotOnSaleError
        raise NotEnoughTicketsError

    reservation = Reservation(
        user_id=user_id,
        buyer_email=buyer_email,
        inventory_id=inventory_id,
        quantity=quantity,
        unit_price=price,  # snapshot: later price changes don't affect this booking
        status=ReservationStatus.PENDING,
        expires_at=now + ttl,
    )
    session.add(reservation)
    # Same transaction as the UPDATE: if the INSERT fails, the tickets come back.
    await session.commit()
    return reservation


async def cancel(
    session: AsyncSession, *, reservation_id: uuid.UUID, user_id: int
) -> Reservation:
    # Only a *pending* reservation can be cancelled, and only once: if cancel and
    # expiry race, exactly one of them changes the status and releases the tickets.
    released = await session.execute(
        update(Reservation)
        .where(
            Reservation.id == reservation_id,
            Reservation.user_id == user_id,
            Reservation.status == ReservationStatus.PENDING,
        )
        .values(status=ReservationStatus.CANCELLED)
        .returning(Reservation.inventory_id, Reservation.quantity)
    )
    row = released.one_or_none()
    if row is None:
        reservation = await get_for_user(
            session, reservation_id=reservation_id, user_id=user_id
        )
        # Read it BEFORE rollback: rollback expires loaded objects, and reloading an
        # attribute without `await` is not allowed in async SQLAlchemy.
        current_status = reservation.status
        await session.rollback()
        raise ReservationNotPendingError(current_status)

    await _release_tickets(session, {row.inventory_id: row.quantity})
    await session.commit()
    return await get_for_user(session, reservation_id=reservation_id, user_id=user_id)


async def expire_due_reservations(session: AsyncSession, *, now: datetime) -> int:
    """Expire overdue pending reservations and give their tickets back.

    Safe to run often and from several workers at once (e.g. Celery Beat in Fase 3).
    """
    expired = await session.execute(
        update(Reservation)
        .where(
            Reservation.status == ReservationStatus.PENDING,
            Reservation.expires_at <= now,
        )
        .values(status=ReservationStatus.EXPIRED)
        .returning(Reservation.inventory_id, Reservation.quantity)
    )
    to_release: defaultdict[int, int] = defaultdict(int)
    count = 0
    for inventory_id, quantity in expired:
        to_release[inventory_id] += quantity
        count += 1
    await _release_tickets(session, to_release)
    await session.commit()
    return count


async def get_for_user(
    session: AsyncSession, *, reservation_id: uuid.UUID, user_id: int
) -> Reservation:
    # Someone else's reservation looks exactly like a missing one (404, not 403).
    reservation = await session.scalar(
        select(Reservation).where(
            Reservation.id == reservation_id, Reservation.user_id == user_id
        )
    )
    if reservation is None:
        raise ReservationNotFoundError
    return reservation


async def event_id_for_inventory(
    session: AsyncSession, inventory_id: int
) -> int | None:
    return await session.scalar(
        select(Inventory.event_id).where(Inventory.id == inventory_id)
    )


async def _release_tickets(session: AsyncSession, quantities: dict[int, int]) -> None:
    # Sorted ids: concurrent jobs lock rows in the same order, avoiding deadlocks.
    for inventory_id in sorted(quantities):
        await session.execute(
            update(Inventory)
            .where(Inventory.id == inventory_id)
            .values(available=Inventory.available + quantities[inventory_id])
        )
