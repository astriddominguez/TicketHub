"""Prometheus metrics. Counters are process-global, so tests compare before/after."""

from httpx import AsyncClient
from prometheus_client import REGISTRY

from .conftest import CreateInventory, auth


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_metrics_endpoint_is_in_prometheus_format(client: AsyncClient) -> None:
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE booking_reservations_total counter" in response.text


async def test_reservation_outcomes_are_counted(
    client: AsyncClient, create_inventory: CreateInventory
) -> None:
    created = sample("booking_reservations_total", outcome="created")
    sold_out = sample("booking_reservations_total", outcome="sold_out")
    inventory = await create_inventory(total=1)
    body = {"inventory_id": inventory.id, "quantity": 1}

    await client.post("/reservations", json=body, headers=auth(1))
    await client.post("/reservations", json=body, headers=auth(2))  # none left

    assert sample("booking_reservations_total", outcome="created") == created + 1
    assert sample("booking_reservations_total", outcome="sold_out") == sold_out + 1


async def test_http_metrics_use_the_route_template_not_the_real_path(
    client: AsyncClient,
) -> None:
    route = "/reservations/{reservation_id}"
    before = sample("http_requests_total", method="GET", route=route, status="404")
    await client.get(
        "/reservations/0b6f9c1e-4a8e-4f7a-9a64-1f0e0d1c2b3a", headers=auth(1)
    )
    # One series for every reservation id, not one per id (cardinality).
    assert (
        sample("http_requests_total", method="GET", route=route, status="404")
        == before + 1
    )
    assert sample("http_request_duration_seconds_count", method="GET", route=route) > 0


async def test_scraping_metrics_is_not_counted_as_traffic(client: AsyncClient) -> None:
    before = sample("http_requests_total", method="GET", route="/metrics", status="200")
    await client.get("/metrics")
    assert (
        sample("http_requests_total", method="GET", route="/metrics", status="200")
        == before
    )
