"""Fixed-window rate limiting."""

from __future__ import annotations

import time

from app.core.config import settings
from app.core.rate_limit import (
    InMemoryRateLimiter,
    build_rate_limiter,
    get_rate_limiter,
    set_rate_limiter,
)


def test_calls_within_the_limit_are_allowed() -> None:
    limiter = InMemoryRateLimiter()
    for _ in range(5):
        assert limiter.check("k", limit=5, window_seconds=60) is True


def test_a_call_past_the_limit_is_refused() -> None:
    limiter = InMemoryRateLimiter()
    for _ in range(5):
        limiter.check("k", limit=5, window_seconds=60)
    assert limiter.check("k", limit=5, window_seconds=60) is False


def test_a_refused_call_is_not_counted() -> None:
    limiter = InMemoryRateLimiter()
    for _ in range(5):
        limiter.check("k", limit=5, window_seconds=60)
    limiter.check("k", limit=5, window_seconds=60)
    limiter.check("k", limit=5, window_seconds=60)
    # Still refused, not permanently stuck further past the limit.
    assert limiter.check("k", limit=5, window_seconds=60) is False


def test_different_keys_do_not_share_a_budget() -> None:
    limiter = InMemoryRateLimiter()
    for _ in range(5):
        limiter.check("a", limit=5, window_seconds=60)
    assert limiter.check("b", limit=5, window_seconds=60) is True


def test_the_window_resets_after_it_elapses() -> None:
    limiter = InMemoryRateLimiter()
    for _ in range(3):
        limiter.check("k", limit=3, window_seconds=0.05)
    assert limiter.check("k", limit=3, window_seconds=0.05) is False
    time.sleep(0.1)
    assert limiter.check("k", limit=3, window_seconds=0.05) is True


def test_build_rate_limiter_defaults_to_in_memory() -> None:
    assert isinstance(build_rate_limiter("memory"), InMemoryRateLimiter)


def test_build_rate_limiter_picks_redis_when_asked() -> None:
    from app.core.rate_limit import RedisRateLimiter

    assert isinstance(build_rate_limiter("redis"), RedisRateLimiter)


def test_set_rate_limiter_is_the_test_hook() -> None:
    sentinel = InMemoryRateLimiter()
    set_rate_limiter(sentinel)
    try:
        assert get_rate_limiter() is sentinel
    finally:
        set_rate_limiter(None)


def test_default_backend_setting_is_memory() -> None:
    """Redis is a real, always-available dependency (the Celery broker), but
    the default stays 'memory' so the test suite never needs one running —
    the same reasoning app.services.storage defaults to 'memory'."""
    assert settings.rate_limit_backend == "memory"
