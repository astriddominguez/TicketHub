"""Create (or remove) the load-test event directly in the booking database.

    python loadtests/seed.py           # create the inventory for event 900001
    python loadtests/seed.py --check   # oversell check: held + available == total
    python loadtests/seed.py --clean   # delete everything the load test created

Needs PYTHONPATH=services/booking (the Makefile targets set it).
"""

import argparse
import asyncio
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from booking.db import standalone_session
from booking.models import Inventory, Reservation, ReservationStatus

EVENT_ID = 900_001  # far from real catalog ids: never collides with your data
ZONES = [
    # (event_zone_id, price, tickets): a tiny "front row" everyone fights over,
    # and a big general zone.
    (900_101, Decimal("120.00"), 50),
    (900_102, Decimal("45.00"), 5_000),
]


async def seed() -> None:
    async with standalone_session() as session:
        await _delete_all(session)
        for event_zone_id, price, tickets in ZONES:
            session.add(
                Inventory(
                    event_zone_id=event_zone_id,
                    event_id=EVENT_ID,
                    price=price,
                    total=tickets,
                    available=tickets,
                )
            )
        await session.commit()
    print(f"Seeded event {EVENT_ID}: {[(z, t) for z, _, t in ZONES]}")


async def check() -> bool:
    """The invariant that must survive any load: no ticket held twice."""
    ok = True
    async with standalone_session() as session:
        rows = await session.scalars(
            select(Inventory).where(Inventory.event_id == EVENT_ID)
        )
        for inventory in rows:
            held = await session.scalar(
                select(func.coalesce(func.sum(Reservation.quantity), 0)).where(
                    Reservation.inventory_id == inventory.id,
                    Reservation.status.in_(
                        [ReservationStatus.PENDING, ReservationStatus.CONFIRMED]
                    ),
                )
            )
            balanced = held + inventory.available == inventory.total
            ok = ok and balanced
            print(
                f"zone {inventory.event_zone_id}: total={inventory.total} "
                f"held={held} available={inventory.available} -> "
                f"{'OK, no oversell' if balanced else 'MISMATCH'}"
            )
    return ok


async def _delete_all(session: AsyncSession) -> None:
    inventory_ids = select(Inventory.id).where(Inventory.event_id == EVENT_ID)
    await session.execute(
        delete(Reservation).where(Reservation.inventory_id.in_(inventory_ids))
    )
    await session.execute(delete(Inventory).where(Inventory.event_id == EVENT_ID))


async def clean() -> None:
    async with standalone_session() as session:
        await _delete_all(session)
        await session.commit()
    print(f"Removed load-test event {EVENT_ID} and its reservations")


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true")
    group.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    if args.check:
        raise SystemExit(0 if asyncio.run(check()) else 1)
    asyncio.run(clean() if args.clean else seed())


if __name__ == "__main__":
    main()
