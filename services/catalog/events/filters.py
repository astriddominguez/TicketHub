import django_filters

from .models import Event


class EventFilter(django_filters.FilterSet):
    city = django_filters.CharFilter(field_name="venue__city", lookup_expr="iexact")
    venue = django_filters.NumberFilter(field_name="venue_id")
    starts_after = django_filters.DateTimeFilter(
        field_name="starts_at", lookup_expr="gte"
    )
    starts_before = django_filters.DateTimeFilter(
        field_name="starts_at", lookup_expr="lte"
    )

    class Meta:
        model = Event
        fields = ["city", "venue", "status", "starts_after", "starts_before"]
