"""Celery tasks. Thin sync wrappers around async code (Celery workers are sync).

Every task is safe to run twice (acks_late means a task can be redelivered).
"""

import asyncio
import logging
import smtplib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from booking import emails, service
from booking.celery_app import celery_app
from booking.config import get_settings
from booking.db import standalone_session
from booking.models import Reservation, ReservationStatus

logger = logging.getLogger(__name__)


@celery_app.task(name="booking.expire_reservations")
def expire_reservations() -> int:
    """Release the tickets of unpaid reservations. Scheduled every minute by Beat."""
    return asyncio.run(_expire_reservations())


async def _expire_reservations() -> int:
    async with standalone_session() as session:
        count = await service.expire_due_reservations(session, now=datetime.now(UTC))
    if count:
        logger.info("Expired %d reservation(s)", count)
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
