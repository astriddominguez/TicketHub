from django.db import models


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