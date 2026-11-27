"""The registry of what a `page.py` declares about its page through one `page` call.

A feature describes its call once, and the registry keeps, resets and reads it.
"""

from __future__ import annotations

import enum
import threading
from typing import TYPE_CHECKING, Any, Final, cast, override

from django.core.exceptions import ImproperlyConfigured

from next.caches import PageCache
from next.introspect import refused_declaration, registering_site
from next.permissions.access import declaring
from next.utils import template_edits_watched

from .loaders import (
    PAGE_MODULE,
    AncestorStamps,
    load_python_module_memo,
    module_generation,
    module_version,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from next.introspect import DeclaringSite


type _Stamp = tuple[int, int, int]

_MISSING: Final = object()


class Inherit(enum.StrEnum):
    """How a declaration reaches the pages below the `page.py` that made it."""

    OWN = "own"
    """It binds its own page alone, as `cache` does."""

    NEAREST = "nearest"
    """The closest `page.py` at or above the page that makes the call wins."""

    MERGE = "merge"
    """Every `page.py` at or above the page folds in, the root first, as `headers`."""


class _Call:
    """One call as made, with the globals of the module run that made it."""

    __slots__ = ("namespace", "value")

    def __init__(self, value: object, namespace: dict[str, object]) -> None:
        """Hold the parsed `value` and the `namespace` of its run."""
        self.value = value
        self.namespace = namespace


class PageDeclaration[**P, T]:
    """One `page.<name>()` call, how it parses arguments and what a bare page reads.

    `parse` raises `ValueError` naming the problem, which the call raises at import.
    """

    __slots__ = (
        "_calls",
        "_held",
        "_outside",
        "default",
        "inherit",
        "merge",
        "name",
        "parse",
    )

    def __init__(
        self,
        name: str,
        parse: Callable[P, T],
        default: T,
        *,
        inherit: Inherit = Inherit.OWN,
        merge: Callable[[T, T], T] | None = None,
    ) -> None:
        """Describe the call, `merge` folding a page value over the one above it."""
        if (inherit is Inherit.MERGE) is (merge is None):
            msg = f"page.{name}() takes a merge function exactly under Inherit.MERGE."
            raise ValueError(msg)
        self.name = name
        self.parse = parse
        self.default = default
        self.inherit = inherit
        self.merge = merge
        self._calls: dict[Path, _Call] = {}
        self._outside: dict[Path, None] = {}
        self._held: PageCache[Path, tuple[_Stamp, T]] = PageCache()

    @property
    def call(self) -> str:
        """Return the call as an error names it."""
        return f"page.{self.name}()"

    @override
    def __repr__(self) -> str:
        """Name the call."""
        return f"<PageDeclaration {self.call}>"

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> None:
        """Declare for the `page.py` whose top level makes this call."""
        self.declare(registering_site(), *args, **kwargs)

    def declare(
        self, site: DeclaringSite, /, *args: P.args, **kwargs: P.kwargs
    ) -> None:
        """Declare for `site`, which a wrapper reads with `registering_site()` itself.

        A call off the top level raises, one from another module is noted for a check.
        """
        file_path = declaring(site)
        try:
            value = self.parse(*args, **kwargs)
        except ValueError as error:
            raise refused_declaration(self.call, site, str(error)) from error
        page_declarations.record(self, file_path, _Call(value, site.namespace))

    def of(self, file_path: Path) -> T:
        """Return what the page at `file_path` declares, the default for nothing.

        An own-page declaration no page calls answers at once, keeping a request free.
        """
        if not self._calls and self.inherit is Inherit.OWN:
            return self.default
        if template_edits_watched():
            return self._read(file_path)
        stamp = (module_version(), module_generation(), page_declarations.version)
        held = self._held.get(file_path)
        if held is None or held[0] != stamp:
            held = self._held[file_path] = (stamp, self._read(file_path))
        return held[1]

    def outside(self) -> tuple[Path, ...]:
        """Return each module other than a `page.py` that made the call."""
        return tuple(self._outside)

    def _current(self, file_path: Path) -> object:
        """Return what the latest load of `file_path` declares, `_MISSING` for none."""
        module = load_python_module_memo(file_path)
        held = self._calls.get(file_path)
        if held is None or module is None or held.namespace is not vars(module):
            return _MISSING
        return held.value

    def _read(self, file_path: Path) -> T:
        """Return the value the inheritance rule gives the page at `file_path`."""
        if self.inherit is Inherit.OWN:
            found = self._current(file_path)
            return self.default if found is _MISSING else cast("T", found)
        paths = AncestorStamps.begin(file_path).paths
        if self.inherit is Inherit.NEAREST:
            return self._nearest(paths)
        return self._merged(paths)

    def _nearest(self, paths: tuple[Path, ...]) -> T:
        """Return the value of the `page.py` closest to the page that declares one."""
        for path in reversed(paths):
            found = self._current(path)
            if found is not _MISSING:
                return cast("T", found)
        return self.default

    def _merged(self, paths: tuple[Path, ...]) -> T:
        """Return the values of `paths` folded root first, the default for none."""
        merge = cast("Callable[[T, T], T]", self.merge)
        folded: object = _MISSING
        for path in paths:
            found = self._current(path)
            if found is _MISSING:
                continue
            value = cast("T", found)
            folded = value if folded is _MISSING else merge(cast("T", folded), value)
        return self.default if folded is _MISSING else cast("T", folded)

    def bind(self, file_path: Path, call: _Call) -> None:
        """Bind `call` to `file_path`, or note a call made outside a `page.py`.

        Only the registry calls it, under its lock, so a memo sees the version move.
        """
        if file_path.name == PAGE_MODULE:
            self._calls[file_path] = call
        else:
            self._outside[file_path] = None

    def clear(self) -> None:
        """Drop every call made, which only the registry asks under its lock."""
        self._calls.clear()
        self._outside.clear()


def _unregistered(name: str) -> ImproperlyConfigured:
    """Return the error a call to a declaration outside the registry raises."""
    return ImproperlyConfigured(
        f"page.{name}() is not registered, so no page can declare it. Register its "
        "declaration in the ready() of the app that owns it."
    )


class PageDeclarationRegistry:
    """The `page` declarations in the order registered, with one change counter.

    A memo built from what pages declare carries `version`, which every call moves.
    """

    __slots__ = ("_by_name", "_lock", "version")

    def __init__(self) -> None:
        """Start with no declaration."""
        self._by_name: dict[str, PageDeclaration[..., Any]] = {}
        self._lock = threading.Lock()
        self.version = 0
        """Monotonic counter bumped on every change, a token for derived memos."""

    def register(self, declaration: PageDeclaration[..., Any]) -> None:
        """Add `declaration`, refusing a second one under the same name."""
        with self._lock:
            held = self._by_name.get(declaration.name)
            if held is declaration:
                return
            if held is not None:
                msg = f"{declaration.call} is registered already, as {held!r}."
                raise ImproperlyConfigured(msg)
            self._by_name[declaration.name] = declaration
            self.version += 1

    def unregister(self, declaration: PageDeclaration[..., Any]) -> None:
        """Remove `declaration` and every call made to it."""
        with self._lock:
            if self._by_name.get(declaration.name) is declaration:
                del self._by_name[declaration.name]
            declaration.clear()
            self.version += 1

    def named(self, name: str) -> PageDeclaration[..., Any]:
        """Return the declaration registered as `name`, raising for none."""
        held = self._by_name.get(name)
        if held is None:
            raise _unregistered(name)
        return held

    def __iter__(self) -> Iterator[PageDeclaration[..., Any]]:
        """Yield each declaration in the order registered."""
        return iter(tuple(self._by_name.values()))

    def __contains__(self, declaration: object) -> bool:
        """Whether `declaration` itself is registered."""
        name = getattr(declaration, "name", None)
        return isinstance(name, str) and self._by_name.get(name) is declaration

    def record(
        self, declaration: PageDeclaration[..., Any], file_path: Path, call: _Call
    ) -> None:
        """Bind `call` of the registered `declaration` to `file_path`."""
        if self._by_name.get(declaration.name) is not declaration:
            raise _unregistered(declaration.name)
        with self._lock:
            declaration.bind(file_path, call)
            self.version += 1

    def reset(self) -> None:
        """Drop every call made to any declaration, keeping the declarations."""
        with self._lock:
            for declaration in self._by_name.values():
                declaration.clear()
            self.version += 1


page_declarations = PageDeclarationRegistry()
"""The process-wide registry every `page` declaration lives in."""


def register_declaration[D: PageDeclaration[..., Any]](declaration: D) -> D:
    """Register `declaration` with the page registry and return it."""
    page_declarations.register(declaration)
    return declaration


__all__ = [
    "Inherit",
    "PageDeclaration",
    "PageDeclarationRegistry",
    "page_declarations",
    "register_declaration",
]
