"""Stripe payments: Checkout sessions, webhook handling and refunds.

The Stripe SDK is synchronous: its calls run in a thread (asyncio.to_thread) so
they never block the event loop.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

import stripe
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from booking.config import Settings
from booking.models import ProcessedStripeEvent, Reservation, ReservationStatus
from booking.service import ReservationNotFoundError, ReservationNotPendingError

if TYPE_CHECKING:  # only for type checking: not importable at runtime
    from stripe.params.checkout import SessionCreateParams

# Stripe refuses Checkout sessions that expire sooner than 30 minutes from now.
CHECKOUT_LIFETIME = timedelta(minutes=31)


class PaymentsNotConfiguredError(Exception):
    pass


class ReservationExpiredError(Exception):
    pass


def stripe_client(settings: Settings) -> stripe.StripeClient:
    if settings.stripe_secret_key is None:
        raise PaymentsNotConfiguredError
    return stripe.StripeClient(
        settings.stripe_secret_key.get_secret_value(),
        max_network_retries=2,  # the SDK adds idempotency keys to its retries
    )


def to_cents(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


async def checkout_url(
    session: AsyncSession,
    client: stripe.StripeClient,
    settings: Settings,
    *,
    reservation_id: uuid.UUID,
    user_id: int,
    now: datetime,
) -> str:
    """URL of the Stripe page where the buyer pays this reservation.

    The row is locked while we talk to Stripe, so a double click can't create
    two payment pages for the same reservation.
    """
    reservation = await session.scalar(
        select(Reservation)
        .where(Reservation.id == reservation_id, Reservation.user_id == user_id)
        .with_for_update()
    )
    if reservation is None:
        raise ReservationNotFoundError
    if reservation.status != ReservationStatus.PENDING:
        raise ReservationNotPendingError(reservation.status)
    if reservation.expires_at <= now:
        raise ReservationExpiredError

    if reservation.stripe_checkout_session_id:
        existing = await asyncio.to_thread(
            client.v1.checkout.sessions.retrieve,
            reservation.stripe_checkout_session_id,
        )
        if existing.status == "open" and existing.url:
            await session.rollback()
            return existing.url

    params: SessionCreateParams = {
        "mode": "payment",
        "line_items": [
            {
                "quantity": reservation.quantity,
                "price_data": {
                    "currency": settings.currency,
                    "unit_amount": to_cents(reservation.unit_price),
                    "product_data": {"name": "TicketHub ticket"},
                },
            }
        ],
        # How the webhook finds the reservation again.
        "client_reference_id": str(reservation.id),
        "metadata": {"reservation_id": str(reservation.id)},
        "success_url": f"{settings.public_url}/payment/success",
        "cancel_url": f"{settings.public_url}/payment/cancelled",
        "expires_at": int((now + CHECKOUT_LIFETIME).timestamp()),
    }
    if reservation.buyer_email:
        params["customer_email"] = reservation.buyer_email
    checkout = await asyncio.to_thread(client.v1.checkout.sessions.create, params)

    reservation.stripe_checkout_session_id = checkout.id
    await session.commit()
    assert checkout.url is not None  # always set for "payment" mode sessions
    return checkout.url


class PaymentOutcome(StrEnum):
    CONFIRMED = "confirmed"
    LATE = "late"  # paid after the reservation expired or was cancelled: refund
    DUPLICATE = "duplicate"
    UNKNOWN_RESERVATION = "unknown_reservation"


async def record_checkout_completed(
    session: AsyncSession,
    *,
    event_id: str,
    reservation_id: uuid.UUID,
    payment_intent_id: str,
    now: datetime,
) -> PaymentOutcome:
    """Record a successful payment. Idempotent: a repeated event changes nothing."""
    first_time = await session.scalar(
        insert(ProcessedStripeEvent)
        .values(id=event_id, type="checkout.session.completed")
        .on_conflict_do_nothing()
        .returning(ProcessedStripeEvent.id)
    )
    if first_time is None:
        await session.rollback()
        return PaymentOutcome.DUPLICATE

    # Remember the payment whatever the reservation's state: if it can't be
    # confirmed, the refund task needs the payment intent to give the money back.
    paid = await session.execute(
        update(Reservation)
        .where(Reservation.id == reservation_id, Reservation.paid_at.is_(None))
        .values(paid_at=now, stripe_payment_intent_id=payment_intent_id)
        .returning(Reservation.id)
    )
    if paid.one_or_none() is None:
        exists = await session.scalar(
            select(Reservation.id).where(Reservation.id == reservation_id)
        )
        await session.commit()  # keep the event as processed either way
        return (
            PaymentOutcome.DUPLICATE if exists else PaymentOutcome.UNKNOWN_RESERVATION
        )

    # Confirm only if the tickets are still held: same conditional-UPDATE pattern.
    confirmed = await session.execute(
        update(Reservation)
        .where(
            Reservation.id == reservation_id,
            Reservation.status == ReservationStatus.PENDING,
        )
        .values(status=ReservationStatus.CONFIRMED)
        .returning(Reservation.id)
    )
    outcome = (
        PaymentOutcome.CONFIRMED
        if confirmed.one_or_none() is not None
        else PaymentOutcome.LATE
    )
    await session.commit()
    return outcome


async def refund(
    client: stripe.StripeClient, *, reservation_id: uuid.UUID, payment_intent_id: str
) -> str:
    """Refund a payment. The idempotency key makes a repeated call a no-op at Stripe."""
    result = await asyncio.to_thread(
        client.v1.refunds.create,
        {"payment_intent": payment_intent_id},
        {"idempotency_key": f"refund-{reservation_id}"},
    )
    return result.id
