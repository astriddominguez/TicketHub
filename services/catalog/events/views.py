from django.db.models import Min, Prefetch
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny, IsAuthenticated

from accounts.permissions import IsOrganizer

from .filters import EventFilter
from .models import Event, EventZone, Venue
from .serializers import (
    EventDetailSerializer,
    EventListSerializer,
    OrganizerEventSerializer,
    OrganizerPriceSerializer,
    VenueSerializer,
)


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The request conflicts with the current state of the resource."
    default_code = "conflict"


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


class OrganizerEventViewSet(viewsets.ModelViewSet):
    """An organizer's own events, drafts included. Other organizers' events -> 404."""

    permission_classes = [IsAuthenticated, IsOrganizer]
    serializer_class = OrganizerEventSerializer
    filterset_fields = ["status", "venue"]
    search_fields = ["title"]
    ordering_fields = ["starts_at", "created_at"]
    ordering = ["starts_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # OpenAPI schema generation
            return Event.objects.none()
        user = self.request.user
        assert user.is_authenticated  # guaranteed by IsAuthenticated; narrows the type
        return (
            Event.objects.filter(organizer=user)
            .select_related("venue")
            .prefetch_related(
                Prefetch(
                    "event_zones", queryset=EventZone.objects.select_related("zone")
                )
            )
        )

    def perform_destroy(self, instance):
        # Same rule as the admin: published events may have sold tickets.
        if instance.status != Event.Status.DRAFT:
            raise Conflict("Only draft events can be deleted. Cancel it instead.")
        instance.delete()


class OrganizerPriceViewSet(viewsets.ModelViewSet):
    """Prices (event + zone) of the organizer's own events."""

    permission_classes = [IsAuthenticated, IsOrganizer]
    serializer_class = OrganizerPriceSerializer
    filterset_fields = ["event"]
    ordering = ["id"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return EventZone.objects.none()
        user = self.request.user
        assert user.is_authenticated  # guaranteed by IsAuthenticated; narrows the type
        return EventZone.objects.filter(event__organizer=user).select_related(
            "event", "zone"
        )
