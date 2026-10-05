"""Fixed-window rate limiting on Redis.

Time is cut into windows of N seconds. Each request does INCR on the counter for
(scope, user, current window); the first INCR also sets an expiry so old windows
clean themselves up. Over the limit -> 429 with Retry-After.

Simple and cheap (one round trip). Its known weakness: a burst at the end of one
window plus another at the start of the next can reach 2x the limit for a moment.
A sliding window or token bucket fixes that, at the cost of more complexity.
"""

import logging
import time

from fastapi import HTTPException, status
from redis.exceptions import RedisError

from booking.auth import CurrentUserDep
from booking.cache import redis_client

logger = logging.getLogger(__name__)


class RateLimit:
    """FastAPI dependency: `Depends(RateLimit("reserve", limit=10, window=60))`."""

    def __init__(self, scope: str, *, limit: int, window_seconds: int) -> None:
        self.scope = scope
        self.limit = limit
        self.window_seconds = window_seconds

    async def __call__(self, user: CurrentUserDep) -> None:
        now = time.time()
        window = int(now // self.window_seconds)
        key = f"ratelimit:{self.scope}:user:{user.id}:{window}"
        try:
            async with redis_client.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, self.window_seconds, nx=True)
                count, _ = await pipe.execute()
        except RedisError:
            # The limiter protects us; it is not what keeps tickets correct (Postgres
            # does). Blocking every sale because Redis is down would be worse.
            logger.warning("Redis unavailable: rate limit skipped", exc_info=True)
            return

        if count > self.limit:
            retry_after = int((window + 1) * self.window_seconds - now) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )
