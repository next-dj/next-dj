from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from next.caches import BoundedCache


def assert_bounded_by_insert_age[S](
    install: Callable[[int], Sequence[BoundedCache[Any, Any]]],
    read: Callable[[S], object],
    subjects: Sequence[S],
    *,
    key_of: Callable[[S], Any] = lambda subject: subject,
) -> None:
    """Check that a memo evicts by insert age and a warm read reorders nothing."""
    first, second, third = subjects

    memos = install(1)
    read(first)
    read(second)
    for memo in memos:
        assert list(memo) == [key_of(second)]

    memos = install(2)
    read(first)
    read(second)
    read(first)
    read(third)
    for memo in memos:
        assert list(memo) == [key_of(second), key_of(third)]
