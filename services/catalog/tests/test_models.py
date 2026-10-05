from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from events.models import EventZone, Zone

from .factories import EventFactory, EventZoneFactory, VenueFactory, ZoneFactory

pytestmark = pytest.mark.django_db


class TestDatabaseConstraints:
    """Postgres enforces these even if Django's validation is bypassed."""

    def test_zone_capacity_must_be_positive(self):
        with pytest.raises(IntegrityError, match="check_zone_capacity_positive"):
            ZoneFactory(capacity=0)

    def test_zone_name_is_unique_per_venue(self):
        zone = ZoneFactory(name="Pista")
        with pytest.raises(IntegrityError, match="unique_zone_name_per_venue"):
            ZoneFactory(venue=zone.venue, name="Pista")

    def test_same_zone_name_allowed_in_another_venue(self):
        ZoneFactory(name="Pista")
        ZoneFactory(name="Pista")  # different venue: no error
        assert Zone.objects.filter(name="Pista").count() == 2

    def test_event_must_end_after_it_starts(self):
        starts = timezone.now() + timedelta(days=1)
        with pytest.raises(IntegrityError, match="check_event_ends_after_starts"):
            EventFactory(starts_at=starts, ends_at=starts)

    def test_price_cannot_be_negative(self):
        with pytest.raises(IntegrityError, match="check_event_zone_price_non_negative"):
            EventZoneFactory(price=Decimal("-0.01"))

    def test_price_can_be_zero_for_free_events(self):
        assert EventZoneFactory(price=Decimal("0")).price == 0

    def test_zone_listed_once_per_event(self):
        event_zone = EventZoneFactory()
        with pytest.raises(IntegrityError, match="unique_zone_per_event"):
            EventZoneFactory(event=event_zone.event, zone=event_zone.zone)


class TestEventZoneClean:
    """Cross-table rules that a CHECK constraint can't express."""

    def test_valid_event_zone_passes(self):
        event = EventFactory()
        zone = ZoneFactory(venue=event.venue, capacity=100)
        EventZone(
            event=event, zone=zone, price=Decimal("10"), tickets_for_sale=100
        ).full_clean()  # no exception

    def test_zone_from_another_venue_is_rejected(self):
        event = EventFactory()
        foreign_zone = ZoneFactory(venue=VenueFactory())
        event_zone = EventZone(
            event=event, zone=foreign_zone, price=Decimal("10"), tickets_for_sale=1
        )
        with pytest.raises(ValidationError) as exc:
            event_zone.full_clean()
        assert "zone" in exc.value.message_dict

    def test_tickets_cannot_exceed_zone_capacity(self):
        event = EventFactory()
        zone = ZoneFactory(venue=event.venue, capacity=100)
        event_zone = EventZone(
            event=event, zone=zone, price=Decimal("10"), tickets_for_sale=101
        )
        with pytest.raises(ValidationError) as exc:
            event_zone.full_clean()
        assert "tickets_for_sale" in exc.value.message_dict

    def test_missing_relations_report_field_errors_not_crash(self):
        with pytest.raises(ValidationError) as exc:
            EventZone(price=Decimal("10"), tickets_for_sale=1).full_clean()
        assert {"event", "zone"} <= exc.value.message_dict.keys()


class TestEventIsPast:
    def test_future_event_is_not_past(self):
        assert EventFactory().is_past is False

    def test_finished_event_is_past(self):
        starts = timezone.now() - timedelta(days=2)
        event = EventFactory(starts_at=starts, ends_at=starts + timedelta(hours=3))
        assert event.is_past is True
