"""Redis: shared by every copy of the service (counters and cache must be global).

Redis is an optimisation and a protection layer, never the source of truth:
if it's down we log it and carry on with Postgres ("fail open").
"""

import json
import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from booking.config import get_settings

logger = logging.getLogger(__name__)

redis_client: Redis = Redis.from_url(get_settings().redis_url, decode_responses=True)


def availability_key(event_id: int) -> str:
    return f"availability:event:{event_id}"


async def get_json(key: str) -> Any | None:
    try:
        raw = await redis_client.get(key)
    except RedisError:
        logger.warning("Redis unavailable: cache read skipped", exc_info=True)
        return None
    return None if raw is None else json.loads(raw)


async def set_json(key: str, value: Any, ttl_seconds: int) -> None:
    try:
        await redis_client.set(key, json.dumps(value), ex=ttl_seconds)
    except RedisError:
        logger.warning("Redis unavailable: cache write skipped", exc_info=True)


async def invalidate(key: str) -> None:
    try:
        await redis_client.delete(key)
    except RedisError:
        # The short TTL bounds how long a stale value can survive.
        logger.warning("Redis unavailable: cache invalidation skipped", exc_info=True)
