import logging

from redis.exceptions import RedisError
from rest_framework.throttling import ScopedRateThrottle

logger = logging.getLogger(__name__)


class FailOpenScopedRateThrottle(ScopedRateThrottle):
    """Like DRF's ScopedRateThrottle, but if Redis is down the request goes through.

    Throttling protects us; it must not take the whole login down with it.
    """

    def allow_request(self, request, view) -> bool:
        try:
            return super().allow_request(request, view)
        except RedisError:
            logger.warning("Redis unavailable: throttling skipped", exc_info=True)
            return True
