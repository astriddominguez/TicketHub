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
    model_config = ConfigDict(from_attributes=True)

    inventory_id: int = Field(validation_alias="id")
    event_zone_id: int
    price: Decimal
    total: int
    available: int
