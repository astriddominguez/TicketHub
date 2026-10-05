from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from events.models import Event

from .factories import EventFactory, EventZoneFactory, VenueFactory, ZoneFactory

pytestmark = pytest.mark.django_db

EVENTS_URL = "/api/events/"


def titles(response) -> list[str]:
    return [event["title"] for event in response.json()["results"]]


class TestEventVisibility:
    def test_list_is_public(self, api_client):
        EventFactory(title="Rock")
        response = api_client.get(EVENTS_URL)
        assert response.status_code == 200
        assert titles(response) == ["Rock"]

    def test_drafts_are_not_listed(self, api_client):
        EventFactory(status=Event.Status.DRAFT)
        assert api_client.get(EVENTS_URL).json()["count"] == 0

    def test_draft_detail_is_404_not_403(self, api_client):
        # 403 would leak that the draft exists.
        draft = EventFactory(status=Event.Status.DRAFT)
        assert api_client.get(f"{EVENTS_URL}{draft.pk}/").status_code == 404

    def test_past_events_are_not_listed_but_reachable_by_id(self, api_client):
        starts = timezone.now() - timedelta(days=5)
        past = EventFactory(starts_at=starts, ends_at=starts + timedelta(hours=2))
        assert api_client.get(EVENTS_URL).json()["count"] == 0
        assert api_client.get(f"{EVENTS_URL}{past.pk}/").status_code == 200

    def test_cancelled_events_are_visible(self, api_client):
        # Ticket holders need to see that their event was cancelled.
        EventFactory(status=Event.Status.CANCELLED)
        assert api_client.get(EVENTS_URL).json()["count"] == 1

    def test_public_api_is_read_only(self, api_client):
        assert api_client.post(EVENTS_URL, {}).status_code == 405


class TestEventDetail:
    def test_detail_includes_prices_and_min_price(self, api_client):
        event = EventFactory()
        EventZoneFactory(event=event, price=Decimal("65.00"))
        EventZoneFactory(event=event, price=Decimal("40.00"))

        data = api_client.get(f"{EVENTS_URL}{event.pk}/").json()

        assert data["min_price"] == "40.00"
        assert sorted(p["price"] for p in data["prices"]) == ["40.00", "65.00"]


class TestFiltersSearchOrdering:
    def test_filter_by_city_is_case_insensitive(self, api_client):
        EventFactory(title="In Madrid", venue=VenueFactory(city="Madrid"))
        EventFactory(title="In Bilbao", venue=VenueFactory(city="Bilbao"))
        assert titles(api_client.get(EVENTS_URL, {"city": "bilbao"})) == ["In Bilbao"]

    def test_filter_by_start_date_range(self, api_client):
        now = timezone.now()
        EventFactory(title="Soon", starts_at=now + timedelta(days=3))
        EventFactory(title="Later", starts_at=now + timedelta(days=60))
        response = api_client.get(
            EVENTS_URL, {"starts_before": (now + timedelta(days=10)).isoformat()}
        )
        assert titles(response) == ["Soon"]

    def test_search_by_title_and_venue(self, api_client):
        EventFactory(title="Jazz night")
        EventFactory(title="Other", venue=VenueFactory(name="Jazz Club"))
        EventFactory(title="Rock")
        assert sorted(titles(api_client.get(EVENTS_URL, {"search": "jazz"}))) == [
            "Jazz night",
            "Other",
        ]

    def test_default_order_is_soonest_first(self, api_client):
        now = timezone.now()
        EventFactory(title="Later", starts_at=now + timedelta(days=20))
        EventFactory(title="Sooner", starts_at=now + timedelta(days=10))
        assert titles(api_client.get(EVENTS_URL)) == ["Sooner", "Later"]

    def test_order_by_min_price(self, api_client):
        EventZoneFactory(event__title="Expensive", price=Decimal("90"))
        EventZoneFactory(event__title="Cheap", price=Decimal("10"))
        response = api_client.get(EVENTS_URL, {"ordering": "min_price"})
        assert titles(response) == ["Cheap", "Expensive"]


class TestQueryCount:
    """Guards against N+1: the number of queries must not grow with the data."""

    def test_list_uses_constant_queries(self, api_client, assert_data_queries):
        for _ in range(10):
            EventZoneFactory()
        with assert_data_queries(2):  # COUNT for pagination + page
            api_client.get(EVENTS_URL)

    def test_detail_uses_constant_queries(self, api_client, assert_data_queries):
        event = EventFactory()
        for _ in range(5):
            EventZoneFactory(event=event)
        with assert_data_queries(2):  # event + venue, then its prices + zones
            api_client.get(f"{EVENTS_URL}{event.pk}/")

    def test_venues_use_constant_queries(self, api_client, assert_data_queries):
        for _ in range(5):
            ZoneFactory()
        with assert_data_queries(3):  # COUNT + venues + their zones
            api_client.get("/api/venues/")
