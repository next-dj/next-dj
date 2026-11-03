"""Freshness stamps of files, which never trust an mtime of the current clock tick."""

import itertools
import threading
from collections.abc import Iterable
from pathlib import Path
from time import time_ns
from typing import Final


RACY_MTIME_WINDOW_NS: Final = 10_000_000
"""The age an mtime reaches before a stamp trusts it, past one coarse clock tick."""

_TOKENS: Final = itertools.count(-1, -1)
_TOKEN_LOCK: Final = threading.Lock()


def stat_mtime_ns(path: Path) -> int | None:
    """Return the nanosecond mtime of `path`, or `None` when it does not stat.

    A float timestamp would round two distinct writes into one value.
    """
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def settled(mtime_ns: int) -> bool:
    """Whether a read made now sees every write stamped `mtime_ns`."""
    return abs(time_ns() - mtime_ns) >= RACY_MTIME_WINDOW_NS


def stamp_of(mtime_ns: int) -> int:
    """Return `mtime_ns` once settled, otherwise a unique negative token no stat equals.

    The token is new per call, so two snapshots in one tick never pass as equal either.
    """
    if settled(mtime_ns):
        return mtime_ns
    with _TOKEN_LOCK:
        return next(_TOKENS)


def trusted(stamp: int | None) -> bool:
    """Whether `stamp` vouches for a read, so it is no token of an unsettled mtime."""
    return stamp is None or stamp >= 0


def snapshot(path: Path) -> int | None:
    """Return the stamp of `path` taken now, `None` when it does not stat."""
    mtime_ns = stat_mtime_ns(path)
    return None if mtime_ns is None else stamp_of(mtime_ns)


def fresh(held: int | None, path: Path) -> bool:
    """Whether `path` still stats to the stamp `held`, which a token never does."""
    return stat_mtime_ns(path) == held


def all_fresh(held: Iterable[tuple[Path, int | None]]) -> bool:
    """Whether every path in `held` still stats to the stamp paired with it."""
    return all(fresh(stamp, path) for path, stamp in held)
