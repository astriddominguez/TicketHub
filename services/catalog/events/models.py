from django.conf import settings
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
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_past(self) -> bool:
        return self.ends_at < timezone.now()
