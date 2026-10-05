from datetime import timedelta

import pytest
from django.utils import timezone

from events.models import Event, EventZone

from .factories import (
    EventFactory,
    EventZoneFactory,
    OrganizerFactory,
    VenueFactory,
    ZoneFactory,
)

pytestmark = pytest.mark.django_db

EVENTS_URL = "/api/organizer/events/"
PRICES_URL = "/api/organizer/prices/"


def event_payload(venue, **overrides) -> dict:
    starts = timezone.now() + timedelta(days=30)
    return {
        "title": "New show",
        "venue": venue.pk,
        "starts_at": starts.isoformat(),
        "ends_at": (starts + timedelta(hours=2)).isoformat(),
        **overrides,
    }


class TestAccess:
    def test_anonymous_gets_401(self, api_client):
        assert api_client.get(EVENTS_URL).status_code == 401

    def test_buyer_gets_403(self, buyer_client):
        assert buyer_client.get(EVENTS_URL).status_code == 403

    def test_organizer_only_sees_own_events_including_drafts(
        self, organizer_client, organizer
    ):
        EventFactory(organizer=organizer, title="Mine", status=Event.Status.DRAFT)
        EventFactory(title="Someone else's")
        response = organizer_client.get(EVENTS_URL)
        assert [e["title"] for e in response.json()["results"]] == ["Mine"]

    def test_other_organizers_event_is_404(self, organizer_client):
        assert (
            organizer_client.get(f"{EVENTS_URL}{EventFactory().pk}/").status_code == 404
        )


class TestCreateEvent:
    def test_new_event_is_a_draft_owned_by_the_caller(
        self, organizer_client, organizer
    ):
        other = OrganizerFactory()
        response = organizer_client.post(
            EVENTS_URL, event_payload(VenueFactory(), organizer=other.pk)
        )
        assert response.status_code == 201
        event = Event.objects.get(pk=response.json()["id"])
        assert event.status == Event.Status.DRAFT
        assert event.organizer == organizer  # the "organizer" in the body is ignored

    def test_end_before_start_is_400_not_500(self, organizer_client):
        payload = event_payload(VenueFactory())
        payload["ends_at"] = payload["starts_at"]
        response = organizer_client.post(EVENTS_URL, payload)
        assert response.status_code == 400
        assert "non_field_errors" in response.json()


class TestDeleteEvent:
    def test_draft_can_be_deleted(self, organizer_client, organizer):
        draft = EventFactory(organizer=organizer, status=Event.Status.DRAFT)
        assert organizer_client.delete(f"{EVENTS_URL}{draft.pk}/").status_code == 204

    def test_published_event_cannot_be_deleted(self, organizer_client, organizer):
        event = EventFactory(organizer=organizer, status=Event.Status.PUBLISHED)
        response = organizer_client.delete(f"{EVENTS_URL}{event.pk}/")
        assert response.status_code == 409
        assert Event.objects.filter(pk=event.pk).exists()


class TestPrices:
    @pytest.fixture
    def event(self, organizer):
        return EventFactory(organizer=organizer)

    def test_create_valid_price(self, organizer_client, event):
        zone = ZoneFactory(venue=event.venue, capacity=500)
        response = organizer_client.post(
            PRICES_URL,
            {
                "event": event.pk,
                "zone": zone.pk,
                "price": "45.00",
                "tickets_for_sale": 500,
            },
        )
        assert response.status_code == 201
        assert EventZone.objects.filter(event=event, zone=zone).exists()

    def test_zone_from_another_venue_is_rejected(self, organizer_client, event):
        foreign_zone = ZoneFactory()
        response = organizer_client.post(
            PRICES_URL,
            {
                "event": event.pk,
                "zone": foreign_zone.pk,
                "price": "10",
                "tickets_for_sale": 1,
            },
        )
        assert response.status_code == 400
        assert "zone" in response.json()

    def test_tickets_over_capacity_are_rejected(self, organizer_client, event):
        zone = ZoneFactory(venue=event.venue, capacity=100)
        response = organizer_client.post(
            PRICES_URL,
            {
                "event": event.pk,
                "zone": zone.pk,
                "price": "10",
                "tickets_for_sale": 101,
            },
        )
        assert response.status_code == 400
        assert "tickets_for_sale" in response.json()

    def test_update_over_capacity_is_rejected(self, organizer_client, event):
        event_zone = EventZoneFactory(event=event, zone__capacity=100)
        response = organizer_client.patch(
            f"{PRICES_URL}{event_zone.pk}/", {"tickets_for_sale": 101}
        )
        assert response.status_code == 400
        event_zone.refresh_from_db()
        assert event_zone.tickets_for_sale == 100  # unchanged

    def test_negative_price_is_400_not_500(self, organizer_client, event):
        zone = ZoneFactory(venue=event.venue)
        response = organizer_client.post(
            PRICES_URL,
            {"event": event.pk, "zone": zone.pk, "price": "-1", "tickets_for_sale": 1},
        )
        assert response.status_code == 400

    def test_duplicate_zone_is_rejected(self, organizer_client, event):
        event_zone = EventZoneFactory(event=event)
        response = organizer_client.post(
            PRICES_URL,
            {
                "event": event.pk,
                "zone": event_zone.zone.pk,
                "price": "10",
                "tickets_for_sale": 1,
            },
        )
        assert response.status_code == 400

    def test_cannot_add_prices_to_another_organizers_event(self, organizer_client):
        foreign_event = EventFactory()
        zone = ZoneFactory(venue=foreign_event.venue)
        response = organizer_client.post(
            PRICES_URL,
            {
                "event": foreign_event.pk,
                "zone": zone.pk,
                "price": "1",
                "tickets_for_sale": 1,
            },
        )
        assert response.status_code == 400
        assert "event" in response.json()
