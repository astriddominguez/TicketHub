import os

# Point the app at a separate test database BEFORE any booking module reads settings.
os.environ["BOOKING_DB_NAME"] = os.environ.get("BOOKING_TEST_DB_NAME", "booking_test")
# ...and at Redis logical database 15, which is flushed after every test.
os.environ["BOOKING_REDIS_URL"] = os.environ.get(
    "BOOKING_TEST_REDIS_URL", "redis://localhost:6379/15"
)
# ...and at test-only RabbitMQ exchange/queues, so dev messages are never touched.
os.environ["CATALOG_EVENTS_EXCHANGE"] = "catalog.events.test"
os.environ["BOOKING_CATALOG_EVENTS_QUEUE"] = "booking.catalog-events.test"

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import jwt
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from booking.cache import redis_client
from booking.config import get_settings
from booking.db import SessionFactory, engine
from booking.main import app
from booking.models import Inventory

ALEMBIC_INI = os.path.join(os.path.dirname(__file__), "..", "alembic.ini")


@pytest.fixture(scope="session", autouse=True)
async def database() -> AsyncIterator[None]:
    """Create the test database and build the schema with our real migrations."""
    settings = get_settings()
    admin = await asyncpg.connect(
        user=settings.db_user,
        password=settings.db_password.get_secret_value(),
        host=settings.db_host,
        port=settings.db_port,
        database="postgres",
    )
    if not await admin.fetchval(
        "SELECT 1 FROM pg_database WHERE datname = $1", settings.db_name
    ):
        await admin.execute(f'CREATE DATABASE "{settings.db_name}"')
    await admin.close()

    # Down to nothing and up again: this also tests that the migrations work.
    # Alembic's env.py runs its own event loop, so it goes in a thread.
    config = Config(ALEMBIC_INI)
    await asyncio.to_thread(command.downgrade, config, "base")
    await asyncio.to_thread(command.upgrade, config, "head")
    yield
    await engine.dispose()
    await redis_client.aclose()


@pytest.fixture(autouse=True)
async def clean_tables() -> AsyncIterator[None]:
    # No "rollback after each test" trick here: the concurrency tests need real
    # commits on separate connections, exactly like production. So we truncate.
    yield
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE reservation, inventory, catalog_event RESTART IDENTITY CASCADE"
            )
        )
    await redis_client.flushdb()  # rate-limit counters and cached availability


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    # Calls the app in-process (no network), but through the full HTTP stack.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def make_token(
    user_id: int,
    *,
    roles: tuple[str, ...] = ("buyer",),
    token_type: str = "access",
    expires_in: timedelta = timedelta(minutes=15),
    key: str | None = None,
) -> str:
    """A token shaped like the ones the catalog (simplejwt) issues."""
    now = datetime.now(UTC)
    claims = {
        "token_type": token_type,
        "exp": now + expires_in,
        "iat": now,
        "jti": uuid.uuid4().hex,
        "user_id": str(user_id),
        "roles": list(roles),
    }
    signing_key = key or get_settings().jwt_signing_key.get_secret_value()
    return jwt.encode(claims, signing_key, algorithm="HS256")


def auth(user_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(user_id)}"}


CreateInventory = Callable[..., Awaitable[Inventory]]


@pytest.fixture
def create_inventory() -> CreateInventory:
    counter = iter(range(1, 10_000))

    async def _create(
        *, total: int = 10, price: str = "50.00", event_id: int = 1
    ) -> Inventory:
        async with SessionFactory() as session:
            inventory = Inventory(
                event_zone_id=next(counter),
                event_id=event_id,
                price=Decimal(price),
                total=total,
                available=total,
            )
            session.add(inventory)
            await session.commit()
            return inventory

    return _create


async def available(inventory_id: int) -> int:
    async with SessionFactory() as session:
        inventory = await session.get(Inventory, inventory_id)
        assert inventory is not None
        return inventory.available
