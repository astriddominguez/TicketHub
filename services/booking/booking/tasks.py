"""Celery tasks. Thin sync wrappers around async code (Celery workers are sync).

Every task is safe to run twice (acks_late means a task can be redelivered).
"""

import asyncio
import smtplib
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, update

from booking import emails, payments, service, tickets
from booking.celery_app import celery_app
from booking.config import get_settings
from booking.db import standalone_session
from booking.models import CatalogEvent, Inventory, Reservation, ReservationStatus

log = structlog.get_logger(__name__)


@celery_app.task(name="booking.expire_reservations")
def expire_reservations() -> int:
    """Release the tickets of unpaid reservations. Scheduled every minute by Beat."""
    return asyncio.run(_expire_reservations())


async def _expire_reservations() -> int:
    async with standalone_session() as session:
        count = await service.expire_due_reservations(session, now=datetime.now(UTC))
    if count:
        log.info("reservations_expired", count=count)
    return count


@celery_app.task(
    name="booking.send_reservation_pending_email",
    # SMTP hiccups are temporary: retry with growing delays (1s, 2s, 4s...).
    autoretry_for=(OSError, smtplib.SMTPException),
    retry_backoff=True,
    max_retries=5,
)
def send_reservation_pending_email(reservation_id: str) -> bool:
    return asyncio.run(_send_reservation_pending_email(uuid.UUID(reservation_id)))


async def _send_reservation_pending_email(reservation_id: uuid.UUID) -> bool:
    async with standalone_session() as session:
        reservation = await session.scalar(
            select(Reservation).where(Reservation.id == reservation_id)
        )
    # Re-check at send time: by now it may have been cancelled or paid.
    if (
        reservation is None
        or reservation.status != ReservationStatus.PENDING
        or not reservation.buyer_email
    ):
        return False
    settings = get_settings()
    message = emails.reservation_pending_email(
        to=reservation.buyer_email,
        sender=settings.email_from,
        reservation_id=str(reservation.id),
        quantity=reservation.quantity,
        unit_price=reservation.unit_price,
        expires_at=reservation.expires_at,
    )
    # Sync SMTP inside async code would block the loop; here it's the only thing
    # this loop does, but keep the habit: run blocking I/O in a thread.
    await asyncio.to_thread(emails.send, message, settings)
    return True


# --- Payments: tickets and refunds -------------------------------------------
# The webhook only records facts in the database ("paid at X"). These tasks act on
# them, and `sweep_payments` (every minute) retries anything that was missed, so
# a lost Celery message delays an email or refund but never loses it.


@celery_app.task(
    name="booking.send_tickets_email",
    autoretry_for=(OSError, smtplib.SMTPException),
    retry_backoff=True,
    max_retries=5,
)
def send_tickets_email(reservation_id: str) -> bool:
    return asyncio.run(_send_tickets_email(uuid.UUID(reservation_id)))


async def _send_tickets_email(reservation_id: uuid.UUID) -> bool:
    settings = get_settings()
    async with standalone_session() as session:
        reservation = await session.scalar(
            select(Reservation).where(Reservation.id == reservation_id)
        )
        if (
            reservation is None
            or reservation.status != ReservationStatus.CONFIRMED
            or reservation.tickets_sent_at is not None
            or not reservation.buyer_email
        ):
            return False
        key = settings.ticket_signing_key.get_secret_value()
        codes = tickets.ticket_codes(reservation.id, reservation.quantity, key)
        message = emails.tickets_email(
            to=reservation.buyer_email,
            sender=settings.email_from,
            reservation_id=str(reservation.id),
            tickets=[(code, tickets.qr_png(code)) for code in codes],
        )
        await asyncio.to_thread(emails.send, message, settings)
        # If we crash right here the email goes out twice: acceptable (at-least-
        # once). Losing the tickets would not be.
        await session.execute(
            update(Reservation)
            .where(Reservation.id == reservation_id)
            .values(tickets_sent_at=datetime.now(UTC))
        )
        await session.commit()
    return True


@celery_app.task(name="booking.refund_payment", max_retries=5)
def refund_payment(reservation_id: str) -> bool:
    return asyncio.run(_refund_payment(uuid.UUID(reservation_id)))


async def _refund_payment(reservation_id: uuid.UUID) -> bool:
    """Give the money back for a paid reservation that didn't end up confirmed:
    the payment arrived too late, or the event was cancelled.
    """
    settings = get_settings()
    async with standalone_session() as session:
        reservation = await session.scalar(
            select(Reservation).where(Reservation.id == reservation_id)
        )
        if (
            reservation is None
            or reservation.paid_at is None
            or reservation.status == ReservationStatus.CONFIRMED
            or reservation.refunded_at is not None
            or reservation.stripe_payment_intent_id is None
        ):
            return False
        try:
            client = payments.stripe_client(settings)
        except payments.PaymentsNotConfiguredError:
            log.warning(
                "refund_postponed_stripe_not_configured",
                reservation_id=str(reservation_id),
            )
            return False
        refund_id = await payments.refund(
            client,
            reservation_id=reservation.id,
            payment_intent_id=reservation.stripe_payment_intent_id,
        )
        if reservation.buyer_email:
            event_status = await session.scalar(
                select(CatalogEvent.status)
                .join(Inventory, Inventory.event_id == CatalogEvent.event_id)
                .where(Inventory.id == reservation.inventory_id)
            )
            reason = (
                "because the event was cancelled"
                if event_status == "cancelled"
                else "because your reservation had expired before the payment arrived"
            )
            message = emails.refund_email(
                to=reservation.buyer_email,
                sender=settings.email_from,
                reservation_id=str(reservation.id),
                amount=reservation.unit_price * reservation.quantity,
                reason=reason,
            )
            # If this fails, refunded_at stays empty and the sweep retries: the
            # refund itself is idempotent, so only the email is repeated.
            await asyncio.to_thread(emails.send, message, settings)
        await session.execute(
            update(Reservation)
            .where(Reservation.id == reservation_id)
            .values(refunded_at=datetime.now(UTC), stripe_refund_id=refund_id)
        )
        await session.commit()
    log.info(
        "reservation_refunded",
        reservation_id=str(reservation_id),
        refund_id=refund_id,
    )
    return True


@celery_app.task(name="booking.sweep_payments")
def sweep_payments() -> dict[str, int]:
    """Safety net, every minute: finish any ticket email or refund still pending."""
    return asyncio.run(_sweep_payments())


async def _sweep_payments() -> dict[str, int]:
    async with standalone_session() as session:
        to_email = list(
            await session.scalars(
                select(Reservation.id).where(
                    Reservation.status == ReservationStatus.CONFIRMED,
                    Reservation.tickets_sent_at.is_(None),
                    Reservation.buyer_email.is_not(None),
                )
            )
        )
        to_refund = list(
            await session.scalars(
                select(Reservation.id).where(
                    Reservation.paid_at.is_not(None),
                    Reservation.status != ReservationStatus.CONFIRMED,
                    Reservation.refunded_at.is_(None),
                )
            )
        )
    sent = sum([await _send_tickets_email(rid) for rid in to_email])
    refunded = sum([await _refund_payment(rid) for rid in to_refund])
    return {"tickets_sent": sent, "refunded": refunded}
