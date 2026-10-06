"""Redis: shared by every copy of the service (counters and cache must be global).

Redis is an optimisation and a protection layer, never the source of truth:
if it's down we log it and carry on with Postgres ("fail open").
"""

import json
from typing import Any

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from booking.config import get_settings

log = structlog.get_logger(__name__)

redis_client: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)


def availability_key(event_id: int) -> str:
    return f"availability:event:{event_id}"


async def get_json(key: str) -> Any | None:
    try:
        raw = await redis_client.get(key)
    except RedisError:
        log.warning("redis_unavailable", action="cache_read_skipped", exc_info=True)
        return None
    return None if raw is None else json.loads(raw)


async def set_json(key: str, value: Any, ttl_seconds: int) -> None:
    try:
        await redis_client.set(key, json.dumps(value), ex=ttl_seconds)
    except RedisError:
        log.warning("redis_unavailable", action="cache_write_skipped", exc_info=True)


async def invalidate(key: str) -> None:
    try:
        await redis_client.delete(key)
    except RedisError:
        # The short TTL bounds how long a stale value can survive.
        log.warning(
            "redis_unavailable", action="cache_invalidation_skipped", exc_info=True
        )
