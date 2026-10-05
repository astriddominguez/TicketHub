"""Rate limiting and availability cache (Redis)."""

import pytest
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import update

from booking.cache import redis_client
from booking.config import get_settings
from booking.db import SessionFactory
from booking.models import Inventory

from .conftest import CreateInventory, auth

ANA, LUIS = 1, 2
LIMIT = get_settings().reservation_rate_limit


async def _reserve(client: AsyncClient, user_id: int, inventory_id: int) -> int:
    response = await client.post(
        "/reservations",
        json={"inventory_id": inventory_id, "quantity": 1},
        headers=auth(user_id),
    )
    return response.status_code


class TestRateLimit:
    async def test_over_the_limit_gets_429_with_retry_after(
        self, client: AsyncClient, create_inventory: CreateInventory
    ) -> None:
        inventory = await create_inventory(total=100)
        for _ in range(LIMIT):
            assert await _reserve(client, ANA, inventory.id) == 201

        response = await client.post(
            "/reservations",
            json={"inventory_id": inventory.id, "quantity": 1},
            headers=auth(ANA),
        )
        assert response.status_code == 429
        assert 1 <= int(response.headers["Retry-After"]) <= 61

    async def test_limit_is_per_user(
        self, client: AsyncClient, create_inventory: CreateInventory
    ) -> None:
        inventory = await create_inventory(total=100)
        for _ in range(LIMIT + 1):
            await _reserve(client, ANA, inventory.id)
        assert await _reserve(client, LUIS, inventory.id) == 201

    async def test_failed_attempts_also_count(
        self, client: AsyncClient, create_inventory: CreateInventory
    ) -> None:
        # A bot hammering a sold-out zone is throttled too (409s count).
        inventory = await create_inventory(total=1)
        statuses = [await _reserve(client, ANA, inventory.id) for _ in range(LIMIT + 1)]
        assert statuses[-1] == 429

    async def test_redis_down_fails_open(
        self,
        client: AsyncClient,
        create_inventory: CreateInventory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        inventory = await create_inventory(total=10)

        def broken_pipeline(*args: object, **kwargs: object) -> None:
            raise RedisConnectionError("Redis is down")

        monkeypatch.setattr(redis_client, "pipeline", broken_pipeline)
        # Sales go on: Postgres, not Redis, guarantees correctness.
        assert await _reserve(client, ANA, inventory.id) == 201


class TestAvailabilityCache:
    async def test_second_read_is_served_from_cache(
        self, client: AsyncClient, create_inventory: CreateInventory
    ) -> None:
        inventory = await create_inventory(total=10, event_id=7)
        assert (await client.get("/events/7/availability")).json()[0]["available"] == 10

        # Change the database behind the API's back: the cached value is served.
        async with SessionFactory() as session:
            await session.execute(
                update(Inventory)
                .where(Inventory.id == inventory.id)
                .values(available=3)
            )
            await session.commit()
        assert (await client.get("/events/7/availability")).json()[0]["available"] == 10

    async def test_reserving_invalidates_the_cache(
        self, client: AsyncClient, create_inventory: CreateInventory
    ) -> None:
        inventory = await create_inventory(total=10, event_id=7)
        await client.get("/events/7/availability")  # fill the cache

        await _reserve(client, ANA, inventory.id)

        assert (await client.get("/events/7/availability")).json()[0]["available"] == 9

    async def test_cancelling_invalidates_the_cache(
        self, client: AsyncClient, create_inventory: CreateInventory
    ) -> None:
        inventory = await create_inventory(total=10, event_id=7)
        created = await client.post(
            "/reservations",
            json={"inventory_id": inventory.id, "quantity": 4},
            headers=auth(ANA),
        )
        await client.get("/events/7/availability")  # cache says 6

        await client.post(
            f"/reservations/{created.json()['id']}/cancel", headers=auth(ANA)
        )

        assert (await client.get("/events/7/availability")).json()[0]["available"] == 10

    async def test_cache_entries_expire(
        self, client: AsyncClient, create_inventory: CreateInventory
    ) -> None:
        await create_inventory(total=10, event_id=7)
        await client.get("/events/7/availability")
        ttl = await redis_client.ttl("availability:event:7")
        assert 0 < ttl <= get_settings().availability_cache_seconds

    async def test_redis_down_falls_back_to_database(
        self,
        client: AsyncClient,
        create_inventory: CreateInventory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await create_inventory(total=10, event_id=7)

        async def broken(*args: object, **kwargs: object) -> None:
            raise RedisConnectionError("Redis is down")

        for method in ("get", "set", "delete"):
            monkeypatch.setattr(redis_client, method, broken)
        response = await client.get("/events/7/availability")
        assert response.status_code == 200
        assert response.json()[0]["available"] == 10
