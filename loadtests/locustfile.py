"""Ticket rush: many people browsing, some fighting for a few front-row tickets.

    make loadtest       # headless run, results in loadtests/results/
    make loadtest-ui    # interactive, at http://localhost:8089

Expected outcomes (409 sold out, 429 rate limited) count as successes: in a
rush they're the correct answer. Only the unexpected (5xx, odd codes) fails.
"""

import os
import random
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from locust import HttpUser, between, events, task
from locust.env import Environment

CATALOG_URL = os.environ.get("LOADTEST_CATALOG_URL", "http://localhost:8000")
BOOKING_URL = os.environ.get("LOADTEST_BOOKING_URL", "http://localhost:8001")
EVENT_ID = 900_001  # created by loadtests/seed.py

_inventory: dict[str, int] = {}  # "front" / "general" -> inventory id


@events.test_start.add_listener
def find_inventory(environment: Environment, **kwargs: object) -> None:
    """Look up the seeded zones once, through the public API."""
    import requests

    zones = requests.get(f"{BOOKING_URL}/events/{EVENT_ID}/availability", timeout=10)
    zones.raise_for_status()
    by_size = sorted(zones.json(), key=lambda zone: zone["total"])
    if len(by_size) != 2:
        raise RuntimeError("Run `make loadtest-seed` first.")
    _inventory["front"] = by_size[0]["inventory_id"]
    _inventory["general"] = by_size[1]["inventory_id"]


def buyer_token() -> str:
    """A token like the catalog issues, signed with the shared key.

    Logging in for real would hit the login throttle (5/min per IP): every
    simulated user comes from this one machine.
    """
    now = datetime.now(UTC)
    claims = {
        "token_type": "access",
        "user_id": str(random.randint(1_000_000, 9_999_999)),
        "roles": ["buyer"],
        "email": "",  # no email: don't flood the worker and Mailpit
        "exp": now + timedelta(minutes=30),
        "iat": now,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, os.environ["JWT_SIGNING_KEY"], algorithm="HS256")


EXPECTED_RESERVATION_CODES = {201, 409, 429}


class Browser(HttpUser):
    """Looks around: catalog listings and ticket availability."""

    host = CATALOG_URL
    weight = 6
    wait_time = between(1, 3)

    @task(3)
    def list_events(self) -> None:
        self.client.get("/api/events/", name="catalog: list events")

    @task(1)
    def search_events(self) -> None:
        self.client.get("/api/events/?search=rock", name="catalog: search")

    @task(4)
    def availability(self) -> None:
        self.client.get(
            f"{BOOKING_URL}/events/{EVENT_ID}/availability",
            name="booking: availability",
        )


class Buyer(HttpUser):
    """Checks availability and tries to reserve, mostly the scarce front row."""

    host = BOOKING_URL
    weight = 3
    wait_time = between(0.5, 2)

    def on_start(self) -> None:
        self.client.headers["Authorization"] = f"Bearer {buyer_token()}"

    @task(2)
    def availability(self) -> None:
        self.client.get(
            f"/events/{EVENT_ID}/availability", name="booking: availability"
        )

    @task(3)
    def reserve(self) -> None:
        zone = "front" if random.random() < 0.7 else "general"
        with self.client.post(
            "/reservations",
            json={"inventory_id": _inventory[zone], "quantity": random.choice([1, 2])},
            name=f"booking: reserve ({zone})",
            catch_response=True,
        ) as response:
            if response.status_code in EXPECTED_RESERVATION_CODES:
                response.success()
            else:
                response.failure(f"unexpected {response.status_code}")


class Hesitant(HttpUser):
    """Reserves, thinks about it, cancels: tickets go back to the pool."""

    host = BOOKING_URL
    weight = 1
    wait_time = between(1, 3)

    def on_start(self) -> None:
        self.client.headers["Authorization"] = f"Bearer {buyer_token()}"

    @task
    def reserve_then_cancel(self) -> None:
        with self.client.post(
            "/reservations",
            json={"inventory_id": _inventory["general"], "quantity": 1},
            name="booking: reserve (general)",
            catch_response=True,
        ) as response:
            if response.status_code not in EXPECTED_RESERVATION_CODES:
                response.failure(f"unexpected {response.status_code}")
                return
            response.success()
            if response.status_code != 201:
                return
            reservation_id = response.json()["id"]
        time.sleep(random.uniform(0.5, 1.5))
        self.client.post(
            f"/reservations/{reservation_id}/cancel",
            name="booking: cancel",
        )
