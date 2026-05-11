"""LocMemCache-backed counter store used by every receiver.

Two parallel hierarchies live under one cache namespace.

The cumulative side is the one the per-page tables read from.
`incr(kind, key)` bumps a single value per `(kind, key)` pair so
`/stats/pages/`, `/stats/components/`, `/stats/forms/`, and
`/stats/static/` keep growing across the lifetime of the process.

The bucketed side carries the same events sliced by minute. Each
`incr` also bumps a key whose tail is the minute-floor of `_now()`.
`read_window(kind, minutes)` sums every bucket that falls inside
`[now - minutes, now]`, which is what the live page on `/stats/`
renders through its `?window=1m|5m|1h` querystring.

Atomicity matters even with `LocMemCache` because a threaded ASGI
worker can run two `incr` calls concurrently. Both branches use
`cache.add` to seed and `cache.incr` to accumulate, both of which the
Django cache contract documents as atomic.

`flush()` drains both hierarchies in one pass and clears the index.
The next `incr` starts from zero.
"""

from datetime import UTC, datetime, timedelta

from django.core.cache import cache


PREFIX = "obs:metric:"
INDEX_KEY = f"{PREFIX}__index__"
BUCKET_PREFIX = f"{PREFIX}bucket:"

BUCKET_TTL_SECONDS = 3700


def _now() -> datetime:
    """Return the current UTC moment.

    Centralised so `time_machine.travel` can drive bucket-boundary
    tests through this single seam.
    """
    return datetime.now(tz=UTC)


_MINUTE_FORMAT = "%Y%m%d%H%M"


def _floor_minute(moment: datetime) -> str:
    """Return the minute-floor of `moment` as a colon-free stamp.

    The output stays free of colons so the bucket key can be split
    back into its parts with `rpartition(":")` even when the user-
    supplied `key` itself contains colons.
    """
    return moment.replace(second=0, microsecond=0).strftime(_MINUTE_FORMAT)


def _full_key(kind: str, key: str) -> str:
    return f"{PREFIX}{kind}:{key}"


def _bucket_key(kind: str, key: str, minute_iso: str) -> str:
    return f"{BUCKET_PREFIX}{kind}:{key}:{minute_iso}"


def _track(full_key: str) -> None:
    """Record `full_key` in the index used by `read_all` and `flush`.

    The index key itself never expires, otherwise a long idle stretch
    would let the default cache timeout drop the index even though the
    individual entries are still alive.
    """
    index = cache.get(INDEX_KEY) or set()
    if full_key not in index:
        index = set(index)
        index.add(full_key)
        cache.set(INDEX_KEY, index, timeout=None)


def _atomic_bump(full: str, by: int, *, ttl: int | None = None) -> int:
    """Add `by` to the counter at `full`, seeding atomically when missing.

    The `ttl` value of ``None`` means the counter never expires, which
    is the desired behaviour for cumulative counters. Bucket counters
    pass an explicit `BUCKET_TTL_SECONDS`.
    """
    if cache.add(full, by, timeout=ttl):
        _track(full)
        return int(by)
    new_value = cache.incr(full, by)
    _track(full)
    return int(new_value)


def incr(kind: str, key: str, by: int = 1) -> int:
    """Add `by` to the cumulative counter and to the current minute bucket.

    Returns the new cumulative value.
    """
    cumulative = _atomic_bump(_full_key(kind, key), by)
    _atomic_bump(
        _bucket_key(kind, key, _floor_minute(_now())),
        by,
        ttl=BUCKET_TTL_SECONDS,
    )
    return cumulative


def read_all() -> dict[tuple[str, str], int]:
    """Return every cumulative counter as a `(kind, key) -> value` mapping."""
    out: dict[tuple[str, str], int] = {}
    for full in cache.get(INDEX_KEY) or set():
        if full.startswith(BUCKET_PREFIX):
            continue
        without_prefix = full[len(PREFIX) :]
        kind, _, key = without_prefix.partition(":")
        value = cache.get(full)
        if value is None:
            continue
        out[(kind, key)] = int(value)
    return out


def read_kind(kind: str) -> dict[str, int]:
    """Return the cumulative `(key -> value)` mapping for a single kind."""
    return {key: value for (k, key), value in read_all().items() if k == kind}


def read_window(kind: str, minutes: int) -> dict[str, int]:
    """Return per-key totals across every bucket in the last `minutes` minutes.

    The function is read-only with one exception: bucket entries whose
    cache value already expired are dropped from the index in passing.
    The window itself is a query parameter and never mutates state, so
    a `read_window(kind, 5)` call cannot evict the bucket that the next
    `read_window(kind, 60)` call needs to see.
    """
    cutoff = _now() - timedelta(minutes=minutes)
    index = cache.get(INDEX_KEY) or set()
    pruned = set(index)
    out: dict[str, int] = {}
    for full in index:
        if not full.startswith(BUCKET_PREFIX):
            continue
        tail = full[len(BUCKET_PREFIX) :]
        full_kind, _, rest = tail.partition(":")
        if full_kind != kind:
            continue
        key, _, minute_iso = rest.rpartition(":")
        ts = datetime.strptime(minute_iso, _MINUTE_FORMAT).replace(tzinfo=UTC)
        value = cache.get(full)
        if value is None:
            pruned.discard(full)
            continue
        if ts < cutoff:
            continue
        out[key] = out.get(key, 0) + int(value)
    if pruned != index:
        cache.set(INDEX_KEY, pruned, timeout=None)
    return out


def total_for_kind(kind: str) -> int:
    """Return the sum of every cumulative counter under `kind`."""
    return sum(read_kind(kind).values())


def top_by_kind(kind: str, *, limit: int | None = None) -> list[tuple[str, int]]:
    """Return cumulative `(key, value)` pairs sorted by value descending."""
    pairs = sorted(read_kind(kind).items(), key=lambda item: -item[1])
    if limit is None:
        return pairs
    return pairs[:limit]


def top_by_window(
    kind: str, minutes: int, *, limit: int | None = None
) -> list[tuple[str, int]]:
    """Return windowed `(key, value)` pairs sorted by value descending."""
    pairs = sorted(read_window(kind, minutes).items(), key=lambda item: -item[1])
    if limit is None:
        return pairs
    return pairs[:limit]


def flush() -> list[tuple[str, str, int]]:
    """Drain every counter, clear the index, and return the snapshot.

    Both cumulative and bucket entries are drained. The returned list
    contains only cumulative `(kind, key, value)` triples, sorted by
    kind and key for stable output. The cache is left empty so the
    next `incr` starts from zero.
    """
    index = cache.get(INDEX_KEY) or set()
    rows: list[tuple[str, str, int]] = []
    for full in index:
        value = cache.get(full)
        cache.delete(full)
        if value is None:
            continue
        if full.startswith(BUCKET_PREFIX):
            continue
        without_prefix = full[len(PREFIX) :]
        kind, _, key = without_prefix.partition(":")
        rows.append((kind, key, int(value)))
    cache.delete(INDEX_KEY)
    rows.sort(key=lambda row: (row[0], row[1]))
    return rows
