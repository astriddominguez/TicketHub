from datetime import timedelta
from decimal import Decimal

import factory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone

from accounts.roles import ORGANIZER
from events.models import Event, EventZone, Venue, Zone

PASSWORD = "Sup3r-Secret-pw"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda u: f"{u.username}@example.com")
    password = factory.django.Password(PASSWORD)


class OrganizerFactory(UserFactory):
    @factory.post_generation
    def organizer_group(self, create, extracted, **kwargs):
        if create:
            self.groups.add(Group.objects.get_or_create(name=ORGANIZER)[0])


class VenueFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Venue

    name = factory.Sequence(lambda n: f"Arena {n}")
    address = factory.Faker("street_address")
    city = "Madrid"


class ZoneFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Zone

    venue = factory.SubFactory(VenueFactory)
    name = factory.Sequence(lambda n: f"Zone {n}")
    capacity = 1000


class EventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Event

    organizer = factory.SubFactory(OrganizerFactory)
    venue = factory.SubFactory(VenueFactory)
    title = factory.Sequence(lambda n: f"Concert {n}")
    starts_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=30))
    ends_at = factory.LazyAttribute(lambda e: e.starts_at + timedelta(hours=3))
    status = Event.Status.PUBLISHED


class EventZoneFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EventZone

    event = factory.SubFactory(EventFactory)
    # Same venue as the event, so the default object is always valid.
    zone = factory.SubFactory(ZoneFactory, venue=factory.SelfAttribute("..event.venue"))
    price = Decimal("50.00")
    tickets_for_sale = 100
