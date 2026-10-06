"""Apply the catalog's event snapshots to our inventory.

Delivery is at-least-once and messages may arrive out of order, so applying a
snapshot must be idempotent: the per-event version decides whether it's news.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from booking.models import CatalogEvent, Inventory

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


async def apply_event_snapshot(session: AsyncSession, snapshot: EventSnapshot) -> bool:
    """Bring the inventory in line with the snapshot. False if it was stale."""
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
        return False  # duplicate or out-of-order message: we already know better

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
    await session.commit()
    return True
