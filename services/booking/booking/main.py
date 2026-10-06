import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import stripe
import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from booking import cache, payments, service, tasks
from booking.auth import CurrentUserDep
from booking.config import get_settings
from booking.db import get_session
from booking.logging_config import configure_logging
from booking.metrics import PAYMENTS, REFUNDS_REQUESTED, RESERVATIONS
from booking.models import Inventory, Reservation
from booking.ratelimit import RateLimit
from booking.request_context import request_context_middleware
from booking.schemas import (
    AvailabilityOut,
    CheckoutOut,
    ReservationCreate,
    ReservationOut,
)
from booking.telemetry import configure_telemetry

settings = get_settings()
configure_logging(level=settings.log_level, fmt=settings.log_format)

app = FastAPI(
    title="TicketHub Booking API",
    description="Temporary reservations that never sell the same ticket twice.",
    version="0.1.0",
)
app.middleware("http")(request_context_middleware)
configure_telemetry("booking-api", app=app)

log = structlog.get_logger(__name__)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

reserve_rate_limit = RateLimit(
    "reserve",
    limit=settings.reservation_rate_limit,
    window_seconds=settings.reservation_rate_window_seconds,
)


async def _enqueue(delay: Callable[[str], Any], reservation_id: uuid.UUID) -> None:
    """Best effort: the database already has the truth; a lost task is retried by
    the periodic sweep (payments) or is merely a missing reminder (pending email).

    .delay() talks to RabbitMQ with blocking I/O, so it runs in a thread instead
    of freezing the event loop (and every other request) while it waits.
    """
    try:
        await asyncio.to_thread(delay, str(reservation_id))
    except Exception:
        log.warning(
            "task_enqueue_failed", reservation_id=str(reservation_id), exc_info=True
        )


def get_stripe_client() -> stripe.StripeClient:
    try:
        return payments.stripe_client(settings)
    except payments.PaymentsNotConfiguredError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Payments are not configured."
        ) from None


StripeDep = Annotated[stripe.StripeClient, Depends(get_stripe_client)]


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
        RESERVATIONS.labels(outcome="not_found").inc()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inventory not found.") from None
    except service.NotEnoughTicketsError:
        RESERVATIONS.labels(outcome="sold_out").inc()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Not enough tickets left."
        ) from None
    except service.NotOnSaleError:
        RESERVATIONS.labels(outcome="not_on_sale").inc()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "These tickets are not on sale."
        ) from None
    RESERVATIONS.labels(outcome="created").inc()
    await _invalidate_availability(session, reservation.inventory_id)
    if user.email:
        await _enqueue(tasks.send_reservation_pending_email.delay, reservation.id)
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


@app.post(
    "/reservations/{reservation_id}/checkout",
    response_model=CheckoutOut,
    tags=["payments"],
    responses={
        409: {"description": "Not pending any more, or already expired"},
        503: {"description": "Payments are not configured"},
    },
)
async def create_checkout(
    reservation_id: uuid.UUID,
    user: CurrentUserDep,
    session: SessionDep,
    client: StripeDep,
) -> CheckoutOut:
    """A Stripe payment page for this reservation (same page if asked twice)."""
    try:
        url = await payments.checkout_url(
            session,
            client,
            settings,
            reservation_id=reservation_id,
            user_id=user.id,
            now=datetime.now(UTC),
        )
    except service.ReservationNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Reservation not found."
        ) from None
    except service.ReservationNotPendingError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Reservation is already {exc.args[0]}."
        ) from None
    except payments.ReservationExpiredError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Reservation has expired."
        ) from None
    return CheckoutOut(checkout_url=url)


@app.post("/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    session: SessionDep,
    stripe_signature: Annotated[str | None, Header(alias="stripe-signature")] = None,
) -> dict[str, str]:
    """Called by Stripe, not by users: trusted only if the signature is valid."""
    if settings.stripe_webhook_secret is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Payments are not configured."
        )
    payload = await request.body()  # the raw bytes: the signature covers them
    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, settings.stripe_webhook_secret.get_secret_value()
        )
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Invalid payload or signature."
        ) from None

    if event.type != "checkout.session.completed":
        return {"status": "ignored"}
    checkout = event.data.object  # a stripe Session object (attributes, not a dict)
    reference = getattr(checkout, "client_reference_id", None)
    # Card payments are "paid" immediately; slower methods arrive later as
    # "checkout.session.async_payment_succeeded" (not enabled in this project).
    if getattr(checkout, "payment_status", None) != "paid" or not reference:
        return {"status": "ignored"}

    reservation_id = uuid.UUID(reference)
    outcome = await payments.record_checkout_completed(
        session,
        event_id=event.id,
        reservation_id=reservation_id,
        payment_intent_id=str(checkout.payment_intent),
        now=datetime.now(UTC),
    )
    PAYMENTS.labels(outcome=outcome.value).inc()
    if outcome is payments.PaymentOutcome.CONFIRMED:
        await _enqueue(tasks.send_tickets_email.delay, reservation_id)
    elif outcome is payments.PaymentOutcome.LATE:
        REFUNDS_REQUESTED.labels(reason="late_payment").inc()
        log.warning("late_payment_refunding", reservation_id=str(reservation_id))
        await _enqueue(tasks.refund_payment.delay, reservation_id)
    elif outcome is payments.PaymentOutcome.UNKNOWN_RESERVATION:
        log.error("payment_for_unknown_reservation", reservation_id=str(reservation_id))
    # Always 200 once verified: an error would make Stripe retry for days.
    return {"status": outcome.value}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Scraped by Prometheus. Internal only: the gateway won't expose it publicly."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/payment/success", response_class=HTMLResponse, include_in_schema=False)
async def payment_success() -> str:
    return (
        "<h1>Payment received</h1><p>Your tickets are on their way to your email.</p>"
    )


@app.get("/payment/cancelled", response_class=HTMLResponse, include_in_schema=False)
async def payment_cancelled() -> str:
    return (
        "<h1>Payment cancelled</h1>"
        "<p>Your tickets stay reserved until the reservation expires.</p>"
    )
