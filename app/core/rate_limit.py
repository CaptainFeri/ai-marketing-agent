"""Fixed-window rate limiting.

Every authenticated endpoint is already gated by requiring a bearer token —
the one place that needs its own protection is the handful of endpoints
that accept an anonymous caller, starting with self-service signup (handoff
section 11, phase 2). No such protection existed anywhere in the app before.

Two implementations, the same shape as :mod:`app.services.storage`:

``RedisRateLimiter``
    The real one — an ``INCR``/``EXPIRE`` fixed window. Redis is already a
    required dependency of the whole app (it is the Celery broker), so this
    is not a new piece of infrastructure to stand up.
``InMemoryRateLimiter``
    A per-process dict. The default — correct as long as the API runs as a
    single process (``docker-compose.yml``'s ``api`` service has no
    ``--workers``, the same assumption ``InMemoryStorageBackend`` makes).
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

from app.core.config import settings


class RateLimiter(Protocol):
    def check(self, key: str, *, limit: int, window_seconds: int) -> bool:
        """True if this call is within the limit, False if it should be
        refused. A refused call is not counted as an extra hit."""
        ...


class RedisRateLimiter:
    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or str(settings.redis_url)
        self._client = None

    def _client_instance(self):
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(self._redis_url, socket_connect_timeout=2)
        return self._client

    def check(self, key: str, *, limit: int, window_seconds: int) -> bool:
        client = self._client_instance()
        redis_key = f"ratelimit:{key}"
        count = client.incr(redis_key)
        if count == 1:
            client.expire(redis_key, window_seconds)
        return count <= limit


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            count, window_start = self._hits.get(key, (0, now))
            if now - window_start >= window_seconds:
                count, window_start = 0, now
            count += 1
            self._hits[key] = (count, window_start)
            return count <= limit


_limiter: RateLimiter | None = None


def build_rate_limiter(name: str | None = None) -> RateLimiter:
    name = name or settings.rate_limit_backend
    if name == "redis":
        return RedisRateLimiter()
    return InMemoryRateLimiter()


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = build_rate_limiter()
    return _limiter


def set_rate_limiter(limiter: RateLimiter | None) -> None:
    """Test hook."""
    global _limiter
    _limiter = limiter


__all__ = [
    "InMemoryRateLimiter",
    "RateLimiter",
    "RedisRateLimiter",
    "build_rate_limiter",
    "get_rate_limiter",
    "set_rate_limiter",
]
