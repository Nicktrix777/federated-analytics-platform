"""
Tiny TTL memoization for agent tool functions.

Schema/metadata tools (schema_tools.py, metadata_tools.py) open a fresh
DB/Trino connection on every call, and the same tool can be called several
times across a single pipeline run (orchestrator + schema-analyst turns) or
across concurrent requests. This cache avoids re-fetching identical data
within a short TTL window.
"""

import functools
import time

_caches: list[dict] = []


def async_ttl_cache(ttl_seconds: float):
    """Decorator that memoizes an async function's result for ttl_seconds."""

    def decorator(fn):
        cache: dict[tuple, tuple[float, object]] = {}
        _caches.append(cache)

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            cached = cache.get(key)
            if cached is not None and now < cached[0]:
                return cached[1]
            value = await fn(*args, **kwargs)
            cache[key] = (now + ttl_seconds, value)
            return value

        return wrapper

    return decorator


def clear_all() -> None:
    """Drop every cached entry — call this on /api/invalidate-cache."""
    for cache in _caches:
        cache.clear()
