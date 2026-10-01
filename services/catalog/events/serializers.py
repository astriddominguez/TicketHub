from rest_framework import serializers

from .models import Event, EventZone, Venue, Zone


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
