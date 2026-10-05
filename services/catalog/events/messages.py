"""Messages the catalog sends to other services about events."""

from django.db import transaction

from messaging.models import OutboxMessage

from .models import Event

EVENT_SNAPSHOT = "event.snapshot"


def build_event_snapshot(event: Event) -> dict:
    """The full current state the booking service needs ("event-carried state").

    A full snapshot instead of "price changed to X" messages: applying it twice
    gives the same result, and a lost message is fixed by the next one.
    """
    return {
        "event_id": event.pk,
        "status": event.status,
        "starts_at": event.starts_at.isoformat(),
        "zones": [
            {
                "event_zone_id": event_zone.pk,
                "zone_name": event_zone.zone.name,
                "price": str(event_zone.price),
                "tickets_for_sale": event_zone.tickets_for_sale,
            }
            for event_zone in event.event_zones.select_related("zone").order_by("id")
        ],
    }


def enqueue_event_snapshot(event_id: int) -> None:
    """Write a snapshot to the outbox, in the caller's transaction.

    Drafts are private to the organizer: nothing is sent until it's published.
    """
    with transaction.atomic():
        # Lock the event: concurrent changes to it (or its prices) take turns, so
        # each snapshot sees all the changes committed before it, and the outbox
        # id (used as the version) grows in the same order as the real state.
        event = Event.objects.select_for_update().filter(pk=event_id).first()
        if event is None or event.status == Event.Status.DRAFT:
            return
        OutboxMessage.objects.create(
            routing_key=EVENT_SNAPSHOT, payload=build_event_snapshot(event)
        )
