from rest_framework.routers import DefaultRouter

from .views import (
    EventViewSet,
    OrganizerEventViewSet,
    OrganizerPriceViewSet,
    VenueViewSet,
)

router = DefaultRouter()
router.register("events", EventViewSet, basename="event")
router.register("venues", VenueViewSet, basename="venue")
router.register("organizer/events", OrganizerEventViewSet, basename="organizer-event")
router.register("organizer/prices", OrganizerPriceViewSet, basename="organizer-price")

urlpatterns = router.urls
