import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from booking import cache, service, tasks
from booking.auth import CurrentUserDep
from booking.config import get_settings
from booking.db import get_session
from booking.models import Inventory, Reservation
from booking.ratelimit import RateLimit
from booking.schemas import AvailabilityOut, ReservationCreate, ReservationOut

app = FastAPI(
    title="TicketHub Booking API",
    description="Temporary reservations that never sell the same ticket twice.",
    version="0.1.0",
)

logger = logging.getLogger(__name__)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

settings = get_settings()
reserve_rate_limit = RateLimit(
    "reserve",
    limit=settings.reservation_rate_limit,
    window_seconds=settings.reservation_rate_window_seconds,
)


async def _enqueue_pending_email(reservation_id: uuid.UUID) -> None:
    """Best effort: the reservation is already saved; an email must not undo it.

    .delay() talks to RabbitMQ with blocking I/O, so it runs in a thread instead
    of freezing the event loop (and every other request) while it waits.
    """
    try:
        await asyncio.to_thread(
            tasks.send_reservation_pending_email.delay, str(reservation_id)
        )
    except Exception:
        logger.warning("Could not enqueue email for %s", reservation_id, exc_info=True)


async def _invalidate_availability(session: AsyncSession, inventory_id: int) -> None:
    event_id = await service.event_id_for_inventory(session, inventory_id)
    if event_id is not None:
        await cache.invalidate(cache.availability_key(event_id))


@app.get("/health", tags=["ops"])
async def health(session: SessionDep) -> dict[str, str]:
    """Liveness + database check, for Docker/Kubernetes health probes."""
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


@app.get(
    "/events/{event_id}/availability",
    response_model=list[AvailabilityOut],
    tags=["availability"],
)
async def event_availability(
    event_id: int, session: SessionDep
) -> list[AvailabilityOut]:
    """Public: tickets left per zone of an event. Cached for a few seconds.

    The cached number may be slightly behind: it is only for display. Selling is
    always decided by the atomic UPDATE in Postgres, never by this value.
    """
    key = cache.availability_key(event_id)
    cached = await cache.get_json(key)
    if cached is not None:
        return [AvailabilityOut.model_validate(item) for item in cached]

    rows = await session.scalars(
        select(Inventory).where(Inventory.event_id == event_id).order_by(Inventory.id)
    )
    result = [AvailabilityOut.model_validate(row) for row in rows]
    await cache.set_json(
        key,
        [item.model_dump(mode="json") for item in result],
        settings.availability_cache_seconds,
    )
    return result


@app.post(
    "/reservations",
    response_model=ReservationOut,
    status_code=status.HTTP_201_CREATED,
    tags=["reservations"],
    dependencies=[Depends(reserve_rate_limit)],
    responses={
        404: {"description": "Unknown inventory"},
        409: {"description": "Not enough tickets left, or not on sale"},
        429: {"description": "Too many reservation attempts"},
    },
)
async def create_reservation(
    payload: ReservationCreate, user: CurrentUserDep, session: SessionDep
) -> Reservation:
    """Hold tickets for a few minutes while the buyer pays."""
    try:
        reservation = await service.reserve(
            session,
            user_id=user.id,
            inventory_id=payload.inventory_id,
            quantity=payload.quantity,
            now=datetime.now(UTC),
            ttl=timedelta(minutes=settings.reservation_ttl_minutes),
            buyer_email=user.email,
        )
    except service.InventoryNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inventory not found.") from None
    except service.NotEnoughTicketsError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Not enough tickets left."
        ) from None
    except service.NotOnSaleError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "These tickets are not on sale."
        ) from None
    await _invalidate_availability(session, reservation.inventory_id)
    if user.email:
        await _enqueue_pending_email(reservation.id)
    return reservation


@app.get("/reservations", response_model=list[ReservationOut], tags=["reservations"])
async def my_reservations(
    user: CurrentUserDep, session: SessionDep
) -> list[Reservation]:
    rows = await session.scalars(
        select(Reservation)
        .where(Reservation.user_id == user.id)
        .order_by(Reservation.created_at.desc())
    )
    return list(rows)


@app.get(
    "/reservations/{reservation_id}",
    response_model=ReservationOut,
    tags=["reservations"],
)
async def get_reservation(
    reservation_id: uuid.UUID, user: CurrentUserDep, session: SessionDep
) -> Reservation:
    try:
        return await service.get_for_user(
            session, reservation_id=reservation_id, user_id=user.id
        )
    except service.ReservationNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Reservation not found."
        ) from None


@app.post(
    "/reservations/{reservation_id}/cancel",
    response_model=ReservationOut,
    tags=["reservations"],
    responses={409: {"description": "The reservation is no longer pending"}},
)
async def cancel_reservation(
    reservation_id: uuid.UUID, user: CurrentUserDep, session: SessionDep
) -> Reservation:
    """Release the held tickets immediately instead of waiting for expiry."""
    try:
        reservation = await service.cancel(
            session, reservation_id=reservation_id, user_id=user.id
        )
    except service.ReservationNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Reservation not found."
        ) from None
    except service.ReservationNotPendingError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Reservation is already {exc.args[0]}."
        ) from None
    await _invalidate_availability(session, reservation.inventory_id)
    return reservation
