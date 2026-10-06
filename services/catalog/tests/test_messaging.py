from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone
from opentelemetry import trace
from opentelemetry.trace import SpanKind

from events.messages import EVENT_SNAPSHOT
from events.models import Event
from messaging.models import OutboxMessage
from messaging.relay import relay_batch

from .factories import EventFactory, EventZoneFactory, ZoneFactory

pytestmark = pytest.mark.django_db


def snapshots_for(event: Event) -> list[dict]:
    return [
        message.payload
        for message in OutboxMessage.objects.filter(routing_key=EVENT_SNAPSHOT)
        if message.payload["event_id"] == event.pk
    ]


class TestOutboxWrites:
    def test_publishing_an_event_writes_a_full_snapshot(
        self, organizer_client, organizer
    ):
        event = EventFactory(organizer=organizer, status=Event.Status.DRAFT)
        EventZoneFactory(event=event, price=Decimal("65.00"), tickets_for_sale=300)
        assert snapshots_for(event) == []  # drafts are never sent

        organizer_client.patch(
            f"/api/organizer/events/{event.pk}/", {"status": "published"}
        )

        snapshot = snapshots_for(event)[-1]
        assert snapshot["status"] == "published"
        assert snapshot["zones"] == [
            {
                "event_zone_id": event.event_zones.get().pk,
                "zone_name": event.event_zones.get().zone.name,
                "price": "65.00",
                "tickets_for_sale": 300,
            }
        ]

    def test_price_change_on_published_event_sends_new_snapshot(
        self, organizer_client, organizer
    ):
        event_zone = EventZoneFactory(event__organizer=organizer, price=Decimal("50"))
        organizer_client.patch(
            f"/api/organizer/prices/{event_zone.pk}/", {"price": "80.00"}
        )
        assert snapshots_for(event_zone.event)[-1]["zones"][0]["price"] == "80.00"

    def test_removed_zone_disappears_from_snapshot(self, organizer_client, organizer):
        event = EventFactory(organizer=organizer)
        kept = EventZoneFactory(event=event)
        removed = EventZoneFactory(event=event)
        organizer_client.delete(f"/api/organizer/prices/{removed.pk}/")
        zone_ids = [z["event_zone_id"] for z in snapshots_for(event)[-1]["zones"]]
        assert zone_ids == [kept.pk]

    def test_cancelling_sends_cancelled_status(self, organizer_client, organizer):
        event = EventFactory(organizer=organizer)
        organizer_client.patch(
            f"/api/organizer/events/{event.pk}/", {"status": "cancelled"}
        )
        assert snapshots_for(event)[-1]["status"] == "cancelled"

    def test_change_and_message_are_atomic(
        self, organizer_client, organizer, monkeypatch
    ):
        """If writing the message fails, the change itself is rolled back too."""
        event = EventFactory(organizer=organizer, status=Event.Status.DRAFT)

        def broken_create(*args, **kwargs):
            raise RuntimeError("outbox write failed")

        monkeypatch.setattr(OutboxMessage.objects, "create", broken_create)
        organizer_client.raise_request_exception = False
        response = organizer_client.patch(
            f"/api/organizer/events/{event.pk}/", {"status": "published"}
        )

        assert response.status_code == 500
        event.refresh_from_db()
        assert event.status == Event.Status.DRAFT  # not published without a message

    def test_admin_inline_save_is_covered_too(self, organizer):
        # Signals catch every way of saving, not only the API.
        event = EventFactory(organizer=organizer)
        ZoneFactory(venue=event.venue)
        event.starts_at += timedelta(days=1)
        event.ends_at += timedelta(days=1)
        event.save()
        assert snapshots_for(event)[-1]["starts_at"] == event.starts_at.isoformat()


class FakePublisher:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, dict[str, Any], str]] = []
        self.headers: list[dict[str, str]] = []
        self.fail = fail

    def publish(
        self,
        routing_key: str,
        body: dict[str, Any],
        message_id: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        if self.fail:
            raise ConnectionError("RabbitMQ is down")
        self.sent.append((routing_key, body, message_id))
        self.headers.append(headers or {})


class TestRelay:
    def test_publishes_pending_messages_in_order_with_version(self):
        first = OutboxMessage.objects.create(routing_key="x", payload={"n": 1})
        second = OutboxMessage.objects.create(routing_key="x", payload={"n": 2})
        publisher = FakePublisher()

        assert relay_batch(publisher) == 2

        assert [body for _, body, _ in publisher.sent] == [
            {"n": 1, "version": first.pk},
            {"n": 2, "version": second.pk},
        ]
        assert not OutboxMessage.objects.filter(published_at__isnull=True).exists()

    def test_already_published_messages_are_not_resent(self):
        OutboxMessage.objects.create(
            routing_key="x", payload={}, published_at=timezone.now()
        )
        assert relay_batch(FakePublisher()) == 0

    def test_broker_down_keeps_messages_pending(self):
        OutboxMessage.objects.create(routing_key="x", payload={})
        with pytest.raises(ConnectionError):
            relay_batch(FakePublisher(fail=True))
        assert OutboxMessage.objects.filter(published_at__isnull=True).count() == 1


class TestTracing:
    """The trace of the request that changed the event survives the outbox."""

    def test_outbox_keeps_the_trace_and_the_relay_continues_it(self, spans):
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("PATCH /api/organizer/events/") as request:
            event = EventFactory()  # its snapshot is written to the outbox
        trace_id = format(request.get_span_context().trace_id, "032x")

        message = OutboxMessage.objects.filter(payload__event_id=event.pk).last()
        assert trace_id in message.trace_context["traceparent"]

        publisher = FakePublisher()
        relay_batch(publisher)  # later, in another process, outside any request

        # The message leaves with the same trace in its headers...
        assert trace_id in publisher.headers[-1]["traceparent"]
        # ...and the relay's span is a child of the original request's span.
        [publish] = [
            s for s in spans.get_finished_spans() if s.name.endswith("publish")
        ]
        assert publish.parent.span_id == request.get_span_context().span_id
        assert publish.kind == SpanKind.PRODUCER

    def test_without_tracing_nothing_breaks(self):
        # No active span: the outbox simply has no trace context.
        event = EventFactory()
        message = OutboxMessage.objects.filter(payload__event_id=event.pk).last()
        assert message.trace_context == {}


class TestOutboxMetrics:
    def test_pending_count_and_age_are_exposed(self, api_client):
        old = OutboxMessage.objects.create(routing_key="x", payload={})
        OutboxMessage.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(minutes=5)
        )
        OutboxMessage.objects.create(routing_key="x", payload={})

        body = api_client.get("/metrics").content.decode()

        lines = dict(
            line.split(" ", 1)
            for line in body.splitlines()
            if line.startswith("catalog_outbox_")
        )
        assert float(lines["catalog_outbox_pending_messages"]) == 2
        assert float(lines["catalog_outbox_oldest_pending_age_seconds"]) >= 300

    def test_http_requests_are_counted_by_url_pattern(self, api_client):
        event = EventFactory()
        api_client.get(f"/api/events/{event.pk}/")
        body = api_client.get("/metrics").content.decode()
        # The pattern, not "/api/events/123/": one series for every event.
        assert 'route="api/events/(?P<pk>[^/.]+)/$"' in body
        assert f"/api/events/{event.pk}/" not in body
