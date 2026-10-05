import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from booking.db import Base


class Inventory(Base):
    """Tickets of one zone of one event (an EventZone in the catalog)."""

    __tablename__ = "inventory"
    __table_args__ = (
        CheckConstraint("total > 0", name="total_positive"),
        # The safety net against double selling: Postgres refuses to go below zero
        # even if a bug skips the application logic.
        CheckConstraint("available >= 0", name="available_non_negative"),
        CheckConstraint("available <= total", name="available_le_total"),
        CheckConstraint("price >= 0", name="price_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Ids from the catalog service: plain integers, not foreign keys, because the
    # catalog lives in another database.
    event_zone_id: Mapped[int] = mapped_column(unique=True)
    event_id: Mapped[int] = mapped_column(index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    total: Mapped[int]
    available: Mapped[int]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReservationStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Reservation(Base):
    __tablename__ = "reservation"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        # Partial index: the expiry job only looks for *pending* reservations,
        # so only those rows are indexed (smaller and faster).
        Index(
            "ix_reservation_pending_expires_at",
            "expires_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    # UUID instead of 1, 2, 3...: ids can't be guessed or used to count our sales.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(index=True)
    inventory_id: Mapped[int] = mapped_column(
        ForeignKey("inventory.id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    status: Mapped[ReservationStatus] = mapped_column(
        # VARCHAR + CHECK: unlike Django's choices, Postgres enforces the values.
        Enum(
            ReservationStatus,
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        default=ReservationStatus.PENDING,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    inventory: Mapped[Inventory] = relationship()
