from django.db.models import Min, Prefetch
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.permissions import AllowAny

from .filters import EventFilter
from .models import Event, EventZone, Venue
from .serializers import EventDetailSerializer, EventListSerializer, VenueSerializer


class EventViewSet(viewsets.ReadOnlyModelViewSet):
    """Public catalog: drafts are invisible (a draft id returns 404, not 403)."""

    permission_classes = [AllowAny]
    filterset_class = EventFilter
    search_fields = ["title", "venue__name", "venue__city"]
    ordering_fields = ["starts_at", "min_price", "title"]
    ordering = ["starts_at"]

    def get_queryset(self):
        queryset = (
            Event.objects.exclude(status=Event.Status.DRAFT)
            .select_related("venue")
            .annotate(min_price=Min("event_zones__price"))
        )
        if self.action == "list":
            # The listing shows upcoming events; past ones stay reachable by id.
            queryset = queryset.filter(ends_at__gte=timezone.now())
        if self.action == "retrieve":
            queryset = queryset.prefetch_related(
                Prefetch(
                    "event_zones", queryset=EventZone.objects.select_related("zone")
                )
            )
        return queryset

    def get_serializer_class(self):
        if self.action == "retrieve":
            return EventDetailSerializer
        return EventListSerializer


class VenueViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [AllowAny]
    queryset = Venue.objects.prefetch_related("zones").order_by("name")
    serializer_class = VenueSerializer
    search_fields = ["name", "city"]
    filterset_fields = ["city"]
