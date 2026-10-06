"""Apply the catalog's event snapshots to our inventory.

Delivery is at-least-once and messages may arrive out of order, so applying a
snapshot must be idempotent: the per-event version decides whether it's news.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from booking.models import CatalogEvent, Inventory, Reservation, ReservationStatus

# Only published events sell. Postponed ones pause sales until a new date is set.
ON_SALE_STATUSES = {"published"}


class ZoneSnapshot(BaseModel):
    event_zone_id: int
    zone_name: str
    price: Decimal = Field(ge=0)
    tickets_for_sale: int = Field(gt=0)


class EventSnapshot(BaseModel):
    """Contract of the catalog's `event.snapshot` message."""

    event_id: int
    version: int
    status: str
    starts_at: datetime
    zones: list[ZoneSnapshot]


@dataclass
class SnapshotResult:
    applied: bool  # False: stale or duplicate snapshot, nothing changed
    # Paid reservations cancelled because the event was cancelled: to refund.
    to_refund: list[uuid.UUID] = field(default_factory=list)


async def apply_event_snapshot(
    session: AsyncSession, snapshot: EventSnapshot
) -> SnapshotResult:
    """Bring the inventory in line with the snapshot."""
    # Record the version only if it's newer: one atomic statement. It also locks
    # this event's row, so two consumers applying snapshots of the same event
    # take turns instead of interleaving.
    accepted = await session.scalar(
        insert(CatalogEvent)
        .values(
            event_id=snapshot.event_id,
            status=snapshot.status,
            version=snapshot.version,
        )
        .on_conflict_do_update(
            index_elements=[CatalogEvent.event_id],
            set_={"status": snapshot.status, "version": snapshot.version},
            where=CatalogEvent.version < snapshot.version,
        )
        .returning(CatalogEvent.event_id)
    )
    if accepted is None:
        await session.rollback()
        # Duplicate or out-of-order message: we already know better.
        return SnapshotResult(applied=False)

    on_sale = snapshot.status in ON_SALE_STATUSES
    for zone in snapshot.zones:
        stmt = insert(Inventory).values(
            event_zone_id=zone.event_zone_id,
            event_id=snapshot.event_id,
            price=zone.price,
            total=zone.tickets_for_sale,
            available=zone.tickets_for_sale,
            on_sale=on_sale,
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[Inventory.event_zone_id],
                set_={
                    "price": stmt.excluded.price,
                    # Keep what's already held: shift `available` by the change in
                    # `total`. If the organizer lowered it below what's held, stop
                    # at 0 rather than break the CHECK (overbooking is then visible).
                    "available": func.greatest(
                        Inventory.available + (stmt.excluded.total - Inventory.total),
                        0,
                    ),
                    "total": stmt.excluded.total,
                    "on_sale": stmt.excluded.on_sale,
                },
            )
        )

    # Zones no longer in the snapshot were removed from the event: stop selling.
    current_zone_ids = [zone.event_zone_id for zone in snapshot.zones]
    await session.execute(
        update(Inventory)
        .where(
            Inventory.event_id == snapshot.event_id,
            Inventory.event_zone_id.not_in(current_zone_ids),
        )
        .values(on_sale=False)
    )

    to_refund = []
    if snapshot.status == "cancelled":
        to_refund = await _cancel_reservations_of_event(session, snapshot.event_id)
    await session.commit()  # inventory and reservations change together
    return SnapshotResult(applied=True, to_refund=to_refund)


async def _cancel_reservations_of_event(
    session: AsyncSession, event_id: int
) -> list[uuid.UUID]:
    """Cancel every live reservation of a cancelled event; return the paid ones.

    No refund happens here: a cancelled reservation with `paid_at` set is exactly
    what the payment sweep refunds, so a lost task can't lose anyone's money.
    """
    inventory_ids = select(Inventory.id).where(Inventory.event_id == event_id)
    cancelled = await session.execute(
        update(Reservation)
        .where(
            Reservation.inventory_id.in_(inventory_ids),
            Reservation.status.in_(
                [ReservationStatus.PENDING, ReservationStatus.CONFIRMED]
            ),
        )
        .values(status=ReservationStatus.CANCELLED)
        .returning(Reservation.id, Reservation.paid_at)
    )
    paid = [row.id for row in cancelled if row.paid_at is not None]
    # Nothing is held any more: every ticket of the event is back (and off sale).
    await session.execute(
        update(Inventory)
        .where(Inventory.event_id == event_id)
        .values(available=Inventory.total)
    )
    return paid
