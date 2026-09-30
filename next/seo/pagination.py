"""Lazy sequences a sitemap paginates, sliced part by part instead of materialised.

A page of a sitemap over a `QuerySet` costs one `COUNT` and one `LIMIT`/`OFFSET` read.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Protocol, overload, override

from django.utils.inspect import method_has_no_args


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence


class Rows(Protocol):
    """What a part slices its rows from, a list, a range or a `QuerySet`."""

    def __getitem__(self, key: slice, /) -> Iterable[Any]:
        """Return the rows between two offsets."""
        ...

    def __len__(self) -> int:
        """Return how many rows there are."""
        ...


def count_rows(rows: Rows) -> int:
    """Count `rows` the way the Django paginator does, a query for a `QuerySet`."""
    counter = getattr(rows, "count", None)
    if (
        callable(counter)
        and not inspect.isbuiltin(counter)
        and method_has_no_args(counter)
    ):
        return int(counter())
    return len(rows)


class Part[T]:
    """One run of rows a sitemap lists, each converted to an item as it is sliced.

    `lastmod_field` names the column a `QuerySet` part reads its latest date from.
    """

    __slots__ = ("_count", "convert", "lastmod_field", "rows")

    def __init__(
        self,
        rows: Rows,
        convert: Callable[[Any], T],
        *,
        lastmod_field: str | None = None,
    ) -> None:
        """Hold the rows unread until a count or a slice asks for them."""
        self.rows = rows
        self.convert = convert
        self.lastmod_field = lastmod_field
        self._count: int | None = None

    def count(self) -> int:
        """Return how many rows the part holds, counted once."""
        held = self._count
        if held is None:
            held = self._count = count_rows(self.rows)
        return held

    def slice(self, start: int, stop: int) -> list[T]:
        """Return the items of the rows between two offsets, reading only those."""
        return [self.convert(row) for row in self.rows[start:stop]]


class LazyRows[T](ABC):
    """A sequence counted and sliced on demand, the shape the Django paginator reads."""

    __slots__ = ()

    @abstractmethod
    def count(self) -> int:
        """Return how many items the sequence holds."""

    @abstractmethod
    def between(self, start: int, stop: int) -> list[T]:
        """Return the items between two offsets inside the sequence."""

    def __len__(self) -> int:
        """Return how many items the sequence holds."""
        return self.count()

    def __iter__(self) -> Iterator[T]:
        """Yield every item, read in one pass."""
        return iter(self.between(0, self.count()))

    @overload
    def __getitem__(self, key: int, /) -> T: ...

    @overload
    def __getitem__(self, key: slice, /) -> list[T]: ...

    def __getitem__(self, key: int | slice, /) -> T | list[T]:
        """Return one item or the items of a slice, reading only those."""
        total = self.count()
        if isinstance(key, slice):
            if key.step not in {None, 1}:
                msg = "a sitemap sequence slices with a step of one"
                raise ValueError(msg)
            start, stop, _step = key.indices(total)
            return self.between(start, stop) if start < stop else []
        index = key + total if key < 0 else key
        if not 0 <= index < total:
            msg = f"index {key} is out of a sequence of {total}"
            raise IndexError(msg)
        return self.between(index, index + 1)[0]


class ChainedEntries[T](LazyRows[T]):
    """The parts of one sitemap read as a single sequence, sliced across their seams."""

    __slots__ = ("parts",)

    def __init__(self, parts: Sequence[Part[T]]) -> None:
        """Chain `parts` in order."""
        self.parts = tuple(parts)

    @override
    def count(self) -> int:
        """Return the rows of every part together."""
        return sum(part.count() for part in self.parts)

    @override
    def between(self, start: int, stop: int) -> list[T]:
        """Return the items between two offsets, one slice per part they cross."""
        found: list[T] = []
        offset = 0
        for part in self.parts:
            if offset >= stop:
                break
            size = part.count()
            low, high = max(start - offset, 0), min(stop - offset, size)
            if low < high:
                found.extend(part.slice(low, high))
            offset += size
        return found


class LanguagePairs[T](LazyRows[tuple[T, str]]):
    """Every entry paired with every language, as a sitemap under `i18n` lists them."""

    __slots__ = ("entries", "languages")

    def __init__(self, entries: ChainedEntries[T], languages: Sequence[str]) -> None:
        """Pair `entries` with `languages`, the languages varying fastest."""
        self.entries = entries
        self.languages = tuple(languages)

    @override
    def count(self) -> int:
        """Return one row per entry and language."""
        return self.entries.count() * len(self.languages)

    @override
    def between(self, start: int, stop: int) -> list[tuple[T, str]]:
        """Return the pairs between two offsets, slicing only the entries they need."""
        width = len(self.languages)
        first, last = start // width, -(-stop // width)
        pairs = [
            (entry, code)
            for entry in self.entries.between(first, last)
            for code in self.languages
        ]
        offset = first * width
        return pairs[start - offset : stop - offset]


__all__ = ["ChainedEntries", "LanguagePairs", "LazyRows", "Part", "Rows", "count_rows"]
