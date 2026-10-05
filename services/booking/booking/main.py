import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from booking import service
from booking.auth import CurrentUserDep
from booking.config import get_settings
from booking.db import get_session
from booking.models import Inventory, Reservation
from booking.schemas import AvailabilityOut, ReservationCreate, ReservationOut

app = FastAPI(
    title="TicketHub Booking API",
    description="Temporary reservations that never sell the same ticket twice.",
    version="0.1.0",
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


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
async def event_availability(event_id: int, session: SessionDep) -> list[Inventory]:
    """Public: tickets left per zone of an event."""
    rows = await session.scalars(
        select(Inventory).where(Inventory.event_id == event_id).order_by(Inventory.id)
    )
    return list(rows)


@app.post(
    "/reservations",
    response_model=ReservationOut,
    status_code=status.HTTP_201_CREATED,
    tags=["reservations"],
    responses={
        404: {"description": "Unknown inventory"},
        409: {"description": "Not enough tickets left"},
    },
)
async def create_reservation(
    payload: ReservationCreate, user: CurrentUserDep, session: SessionDep
) -> Reservation:
    """Hold tickets for a few minutes while the buyer pays."""
    try:
        return await service.reserve(
            session,
            user_id=user.id,
            inventory_id=payload.inventory_id,
            quantity=payload.quantity,
            now=datetime.now(UTC),
            ttl=timedelta(minutes=get_settings().reservation_ttl_minutes),
        )
    except service.InventoryNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inventory not found.") from None
    except service.NotEnoughTicketsError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Not enough tickets left."
        ) from None


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
        return await service.cancel(
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
