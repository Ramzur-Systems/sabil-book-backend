from __future__ import annotations

import logging
from functools import lru_cache

import redis
from django.conf import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL)


def is_rate_limited(user_id: int, offer_id: int) -> bool:
    """Fixed-window rate limit, backed by Redis so it holds across ASGI workers.

    INCR is atomic; EXPIRE with NX only arms the TTL the first time a window
    is opened, so later increments in the same window don't keep pushing the
    expiry back (which would let a steady drip of messages block forever).
    """
    key = f"chat:rate:{user_id}:{offer_id}"
    client = _get_redis_client()
    try:
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, settings.CHAT_MESSAGE_RATE_LIMIT_WINDOW, nx=True)
        count, _expire_set = pipe.execute()
    except redis.RedisError:
        # Fail open: a Redis outage shouldn't take down chat entirely.
        logger.warning("Redis unavailable for chat rate limiting", exc_info=True)
        return False
    return count > settings.CHAT_MESSAGE_RATE_LIMIT
