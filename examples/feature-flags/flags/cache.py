from __future__ import annotations

from django.core.cache import cache

from .models import Flag


FLAG_PREFIX = "flags:flag:"
MISSING_SENTINEL = "__missing__"
FLAG_CACHE_TTL = 300


def _key(name: str) -> str:
    return f"{FLAG_PREFIX}{name}"


def get_cached_flag(name: str) -> Flag | None:
    """Fetch a `Flag` through LocMemCache. Return `None` when absent."""
    key = _key(name)
    cached = cache.get(key)
    if cached == MISSING_SENTINEL:
        return None
    if cached is not None:
        return cached
    try:
        flag = Flag.objects.get(name=name)
    except Flag.DoesNotExist:
        cache.set(key, MISSING_SENTINEL, FLAG_CACHE_TTL)
        return None
    cache.set(key, flag, FLAG_CACHE_TTL)
    return flag


def invalidate_flag(name: str) -> None:
    """Drop the cached entry for `name` so the next read refetches from DB."""
    cache.delete(_key(name))
