from rest_framework.routers import DefaultRouter

from .views import EventViewSet, VenueViewSet

router = DefaultRouter()
router.register("events", EventViewSet, basename="event")
router.register("venues", VenueViewSet, basename="venue")

urlpatterns = router.urls
