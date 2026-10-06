"""Prometheus metrics for the catalog (scraped from /metrics).

Label values must come from a small, fixed set: HTTP metrics use the URL
*pattern* ("api/events/<pk>/"), never the real path with ids in it, or every
event would become its own time series ("cardinality explosion").
"""

from collections.abc import Iterator

from django.db.models import Min
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled.",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Time to handle an HTTP request.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)


class OutboxCollector(Collector):
    """Read from the database at scrape time: how far behind is the relay?

    A growing "oldest pending age" means messages aren't leaving: the relay or
    RabbitMQ is down. That's the alert worth having.
    """

    def describe(self) -> Iterator[GaugeMetricFamily]:
        # Without this, registering the collector would call collect() (a DB
        # query) while Django is still starting up.
        yield GaugeMetricFamily("catalog_outbox_pending_messages", "")
        yield GaugeMetricFamily("catalog_outbox_oldest_pending_age_seconds", "")

    def collect(self) -> Iterator[GaugeMetricFamily]:
        from messaging.models import OutboxMessage  # needs the app registry ready

        pending = OutboxMessage.objects.filter(published_at__isnull=True)
        stats = pending.aggregate(oldest=Min("created_at"))
        yield GaugeMetricFamily(
            "catalog_outbox_pending_messages",
            "Outbox messages not yet published to RabbitMQ.",
            value=pending.count(),
        )
        oldest = stats["oldest"]
        yield GaugeMetricFamily(
            "catalog_outbox_oldest_pending_age_seconds",
            "Age of the oldest unpublished outbox message (0 if none).",
            value=(timezone.now() - oldest).total_seconds() if oldest else 0.0,
        )


def register_outbox_collector() -> None:
    REGISTRY.register(OutboxCollector())


def metrics_view(request: HttpRequest) -> HttpResponse:
    """Scraped by Prometheus. Internal only: the gateway won't expose it publicly."""
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)


def route_template(request: HttpRequest) -> str:
    match = getattr(request, "resolver_match", None)
    return match.route if match is not None and match.route else "unmatched"
