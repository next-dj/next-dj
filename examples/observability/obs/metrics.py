"""LocMemCache-backed counter store used by every receiver.

Three public functions: `incr` to bump a counter, `read_all` to read
every counter the dashboard renders from, and `flush` to drain the
cache into a list of `(kind, key, value)` triples ready to be written
to the database. All keys live under one namespace so cache.clear in
tests resets everything atomically.
"""

from django.core.cache import cache


PREFIX = "obs:metric:"
INDEX_KEY = f"{PREFIX}__index__"


def _full_key(kind: str, key: str) -> str:
    return f"{PREFIX}{kind}:{key}"


def _track(full_key: str) -> None:
    """Record `full_key` in the index used by `read_all` and `flush`."""
    index = cache.get(INDEX_KEY) or set()
    if full_key not in index:
        index = set(index)
        index.add(full_key)
        cache.set(INDEX_KEY, index)


def incr(kind: str, key: str, by: int = 1) -> int:
    """Add `by` to the `(kind, key)` counter and return the new value."""
    full = _full_key(kind, key)
    new_value = cache.get(full)
    if new_value is None:
        cache.set(full, by)
        new_value = by
    else:
        new_value = cache.incr(full, by)
    _track(full)
    return int(new_value)


def read_all() -> dict[tuple[str, str], int]:
    """Return every recorded counter as a `(kind, key) -> value` mapping."""
    index = cache.get(INDEX_KEY) or set()
    out: dict[tuple[str, str], int] = {}
    for full in index:
        without_prefix = full[len(PREFIX) :]
        kind, _, key = without_prefix.partition(":")
        value = cache.get(full)
        if value is None:
            continue
        out[(kind, key)] = int(value)
    return out


def read_kind(kind: str) -> dict[str, int]:
    """Return the `(key -> value)` mapping for a single kind."""
    return {key: value for (k, key), value in read_all().items() if k == kind}


def total_for_kind(kind: str) -> int:
    """Return the sum of every counter under `kind`."""
    return sum(read_kind(kind).values())


def flush() -> list[tuple[str, str, int]]:
    """Drain every counter, clear the index, and return the snapshot.

    The returned list is sorted by kind and key for stable output. The
    cache is left empty so the next call to `incr` starts from zero.
    """
    index = cache.get(INDEX_KEY) or set()
    rows: list[tuple[str, str, int]] = []
    for full in index:
        without_prefix = full[len(PREFIX) :]
        kind, _, key = without_prefix.partition(":")
        value = cache.get(full)
        if value is None:
            continue
        rows.append((kind, key, int(value)))
        cache.delete(full)
    cache.delete(INDEX_KEY)
    rows.sort(key=lambda row: (row[0], row[1]))
    return rows
