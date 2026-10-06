import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from booking.models import ReservationStatus

MAX_TICKETS_PER_RESERVATION = 10


class ReservationCreate(BaseModel):
    inventory_id: int
    # A cap per reservation stops one buyer (or bot) from holding a whole zone.
    quantity: int = Field(ge=1, le=MAX_TICKETS_PER_RESERVATION)


class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    inventory_id: int
    quantity: int
    unit_price: Decimal
    status: ReservationStatus
    expires_at: datetime
    created_at: datetime


class AvailabilityOut(BaseModel):
    # Built from an Inventory row ("id") or from the cached JSON ("inventory_id").
    model_config = ConfigDict(
        from_attributes=True, validate_by_name=True, validate_by_alias=True
    )

    inventory_id: int = Field(validation_alias="id")
    event_zone_id: int
    price: Decimal
    total: int
    available: int
    on_sale: bool


class CheckoutOut(BaseModel):
    checkout_url: str
