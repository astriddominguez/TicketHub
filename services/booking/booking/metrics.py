"""Prometheus metrics: numbers Prometheus scrapes from /metrics every few seconds.

Label values must come from a small, fixed set. A label like the URL path
("/reservations/3f2a9c1e-...") would create one time series per reservation and
eventually exhaust Prometheus' memory ("cardinality explosion"): that's why HTTP
metrics use the route *template* ("/reservations/{reservation_id}").
"""

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled.",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Time to handle an HTTP request.",
    ["method", "route"],
    # Buckets around what matters to a buyer: 5 ms ... 5 s.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

RESERVATIONS = Counter(
    "booking_reservations_total",
    "Reservation attempts, by outcome.",
    ["outcome"],  # created | sold_out | not_on_sale | not_found | rate_limited
)
PAYMENTS = Counter(
    "booking_payments_total",
    "Stripe payment webhooks handled, by outcome.",
    ["outcome"],  # confirmed | late | duplicate | unknown_reservation
)
SNAPSHOTS = Counter(
    "booking_catalog_snapshots_total",
    "Catalog event snapshots received, by result.",
    ["result"],  # applied | stale | invalid | failed
)
REFUNDS_REQUESTED = Counter(
    "booking_refunds_requested_total",
    "Refunds queued (late payments and cancelled events).",
    ["reason"],  # late_payment | event_cancelled
)
