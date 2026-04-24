from __future__ import annotations

from django.core.cache import cache
from django.db import transaction
from django.db.models import F

from .models import Link


CLICK_PREFIX = "shortener:click:"


def _key(slug: str) -> str:
    return f"{CLICK_PREFIX}{slug}"


def increment_clicks(slug: str) -> int:
    """Bump the hot click counter for `slug` in LocMemCache and return it."""
    key = _key(slug)
    cache.add(key, 0)
    return cache.incr(key)


def pending_clicks() -> dict[str, int]:
    """Return the slug → pending-clicks map held in cache."""
    keys = [_key(slug) for slug in Link.objects.values_list("slug", flat=True)]
    snapshot = cache.get_many(keys)
    return {k.removeprefix(CLICK_PREFIX): int(v) for k, v in snapshot.items() if v}


def flush_clicks() -> int:
    """Transfer cached counters into the database and reset the cache."""
    pending = pending_clicks()
    if not pending:
        return 0
    with transaction.atomic():
        for slug, delta in pending.items():
            Link.objects.filter(slug=slug).update(clicks=F("clicks") + delta)
    cache.delete_many([_key(slug) for slug in pending])
    return sum(pending.values())
