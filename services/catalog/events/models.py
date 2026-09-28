from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Venue(TimestampedModel):
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100, db_index=True)

    def __str__(self) -> str:
        return self.name


class Zone(TimestampedModel):
    name = models.CharField(max_length=255)
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="zones")
    capacity = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(capacity__gt=0),
                name="check_zone_capacity_positive",
                violation_error_message="Capacity must be greater than 0.",
            ),
            models.UniqueConstraint(
                fields=["venue", "name"],
                name="unique_zone_name_per_venue",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} - {self.venue.name}"


class Event(TimestampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        POSTPONED = "postponed", "Postponed"
        CANCELLED = "cancelled", "Cancelled"

    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="events"
    )
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="events")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status, default=Status.DRAFT)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="check_event_ends_after_starts",
                violation_error_message="The event must end after it starts.",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_past(self) -> bool:
        return self.ends_at < timezone.now()


class EventZone(TimestampedModel):
    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name="event_zones"
    )
    zone = models.ForeignKey(Zone, on_delete=models.PROTECT, related_name="event_zones")
    price = models.DecimalField(max_digits=8, decimal_places=2)
    tickets_for_sale = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price__gte=0),
                name="check_event_zone_price_non_negative",
                violation_error_message="Price cannot be negative.",
            ),
            models.CheckConstraint(
                condition=models.Q(tickets_for_sale__gt=0),
                name="check_event_zone_tickets_positive",
                violation_error_message="Tickets for sale must be greater than 0.",
            ),
            models.UniqueConstraint(
                fields=["event", "zone"],
                name="unique_zone_per_event",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event} - {self.zone.name}"

    def clean(self) -> None:
        # Rules that compare against other tables can't be CHECK constraints.
        # getattr: an unset FK raises RelatedObjectDoesNotExist (an AttributeError);
        # the missing field itself is already reported by clean_fields().
        zone = getattr(self, "zone", None)
        event = getattr(self, "event", None)
        if zone is None or event is None:
            return
        errors = {}
        if zone.venue_id != event.venue_id:
            errors["zone"] = "The zone must belong to the event's venue."
        if self.tickets_for_sale is not None and self.tickets_for_sale > zone.capacity:
            errors["tickets_for_sale"] = (
                f"Cannot exceed the zone capacity ({zone.capacity})."
            )
        if errors:
            raise ValidationError(errors)
