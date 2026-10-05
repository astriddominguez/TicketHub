import copy

from django.core.exceptions import NON_FIELD_ERRORS
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.settings import api_settings

from .models import Event, EventZone, Venue, Zone


class ModelValidationMixin:
    """Run the model's full_clean() so DRF enforces the same rules as the admin.

    DRF doesn't call Model.clean() or check CheckConstraints by itself: without this,
    a negative price would reach Postgres and come back as a 500 instead of a 400.
    """

    def validate(self, attrs):
        attrs = super().validate(attrs)
        # Work on a copy so a failed validation doesn't leave the instance modified.
        instance = copy.copy(self.instance) if self.instance else self.Meta.model()
        for field, value in attrs.items():
            setattr(instance, field, value)
        try:
            instance.full_clean()
        except DjangoValidationError as exc:
            errors = exc.message_dict
            # Django uses "__all__" for non-field errors; DRF uses "non_field_errors".
            if NON_FIELD_ERRORS in errors:
                errors[api_settings.NON_FIELD_ERRORS_KEY] = errors.pop(NON_FIELD_ERRORS)
            raise serializers.ValidationError(errors) from exc
        return attrs


class ZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = Zone
        fields = ["id", "name", "capacity"]


class VenueSerializer(serializers.ModelSerializer):
    zones = ZoneSerializer(many=True, read_only=True)

    class Meta:
        model = Venue
        fields = ["id", "name", "address", "city", "zones"]


class VenueSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Venue
        fields = ["id", "name", "city"]


class EventZoneSerializer(serializers.ModelSerializer):
    zone_id = serializers.IntegerField(source="zone.id", read_only=True)
    zone_name = serializers.CharField(source="zone.name", read_only=True)

    class Meta:
        model = EventZone
        fields = ["zone_id", "zone_name", "price", "tickets_for_sale"]


class EventListSerializer(serializers.ModelSerializer):
    venue = VenueSummarySerializer(read_only=True)
    # Computed in the queryset with an annotation ("from 40.00 €").
    min_price = serializers.DecimalField(
        max_digits=8, decimal_places=2, read_only=True, allow_null=True
    )

    class Meta:
        model = Event
        fields = ["id", "title", "venue", "starts_at", "ends_at", "status", "min_price"]


class EventDetailSerializer(EventListSerializer):
    prices = EventZoneSerializer(source="event_zones", many=True, read_only=True)

    class Meta(EventListSerializer.Meta):
        fields = [*EventListSerializer.Meta.fields, "description", "prices"]


# --- Organizer (write) serializers ---


class OrganizerEventSerializer(ModelValidationMixin, serializers.ModelSerializer):
    # Not sent by the client: always the logged-in user, so nobody can create
    # events on behalf of another organizer.
    organizer = serializers.HiddenField(default=serializers.CurrentUserDefault())
    prices = EventZoneSerializer(source="event_zones", many=True, read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "organizer",
            "title",
            "description",
            "venue",
            "starts_at",
            "ends_at",
            "status",
            "prices",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class OwnEventField(serializers.PrimaryKeyRelatedField):
    """Only accepts events of the logged-in organizer; others look non-existent."""

    def get_queryset(self):
        request = self.context.get("request")
        if request is None:  # e.g. while generating the OpenAPI schema
            return Event.objects.none()
        return Event.objects.filter(organizer=request.user)


class OrganizerPriceSerializer(ModelValidationMixin, serializers.ModelSerializer):
    event = OwnEventField()

    class Meta:
        model = EventZone
        fields = ["id", "event", "zone", "price", "tickets_for_sale"]
