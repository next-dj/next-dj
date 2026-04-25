from __future__ import annotations

import threading

from django.core.cache import cache


RENDER_PREFIX = "flags:renders:"
RENDER_INDEX_KEY = "flags:renders:__index__"

_index_lock = threading.Lock()


def _key(page_path: str) -> str:
    return f"{RENDER_PREFIX}{page_path}"


def _add_to_index(page_path: str) -> None:
    with _index_lock:
        tracked: set[str] = set(cache.get(RENDER_INDEX_KEY) or ())
        if page_path in tracked:
            return
        tracked.add(page_path)
        cache.set(RENDER_INDEX_KEY, tracked)


def record_render(page_path: str) -> int:
    """Increment the render counter for `page_path` and return the new total."""
    key = _key(page_path)
    cache.add(key, 0)
    count = cache.incr(key)
    _add_to_index(page_path)
    return count


def render_counts() -> dict[str, int]:
    """Return `{page_path: count}` for every page tracked so far."""
    with _index_lock:
        tracked: set[str] = set(cache.get(RENDER_INDEX_KEY) or ())
    if not tracked:
        return {}
    snapshot = cache.get_many([_key(p) for p in tracked])
    counts = {
        page.removeprefix(RENDER_PREFIX): int(value) for page, value in snapshot.items()
    }
    return dict(sorted(counts.items()))
