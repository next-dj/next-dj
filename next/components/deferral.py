"""The boundary a `component.py` declares through `component.defer` and `props`."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from next.defer import BoundaryOptionError, BoundaryOptions


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Set as AbstractSet
    from pathlib import Path


COMPONENT_MODULE: Final = "component.py"

_NESTED: Final = "it runs inside a function rather than at the top of the module"
_TWICE: Final = (
    "it names props through both component.defer(props=) and component.props()"
)


@dataclass(slots=True)
class _Written:
    """The calls one execution of a `component.py` made, the last of each winning."""

    defer: dict[str, object] | None = None
    props: tuple[object, ...] | None = None
    nested: bool = False


class _Collecting(threading.local):
    """The calls each execution under way in this thread gathered so far."""

    def __init__(self) -> None:
        """Start with no execution under way."""
        self.written: dict[Path, _Written] = {}


def _settle(written: _Written) -> tuple[BoundaryOptions | None, str | None]:
    """Return the options the calls declare, or why they declare none."""
    if written.nested:
        return None, _NESTED
    options: dict[str, object] = dict(written.defer or {"mode": None})
    if written.props is not None:
        if options.get("props"):
            return None, _TWICE
        options["props"] = written.props
    try:
        return BoundaryOptions.parse(**options), None
    except BoundaryOptionError as error:
        return None, str(error)


def _place[V](
    held: dict[Path | None, V] | dict[Path, V], key: Path, value: V | None
) -> None:
    """Set `key` to `value`, or drop it for `None`."""
    if value is None:
        held.pop(key, None)
    else:
        held[key] = value


class DeferDeclarations:
    """Hold the boundary options each `component.py` declares, keyed by its path.

    A loader run swaps its calls in at the end, a plain import joins the calls held.
    """

    __slots__ = (
        "_addressable",
        "_collecting",
        "_deferring",
        "_held",
        "_modules",
        "_options",
        "_outside",
        "_problems",
        "_version",
    )

    def __init__(self) -> None:
        """Start empty."""
        self._held: dict[Path, _Written] = {}
        self._options: dict[Path | None, BoundaryOptions] = {}
        self._modules: set[Path] = set()
        self._addressable: set[Path] = set()
        self._problems: dict[Path, str] = {}
        self._outside: dict[Path, str] = {}
        self._collecting = _Collecting()
        self._version = 0
        self._deferring: list[Callable[[], object]] = []

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every change, a token for derived memos."""
        return self._version

    def on_deferring(self, callback: Callable[[], object]) -> None:
        """Call `callback` each time a declaration naming a delivery mode is committed.

        The flag that opens a page's deferring block rises then, before any render.
        """
        self._deferring.append(callback)

    def options(self, module_path: Path | None) -> BoundaryOptions | None:
        """Return what the `component.py` at `module_path` declares, else `None`."""
        return self._options.get(module_path)

    def deferring_modules(self) -> AbstractSet[Path]:
        """Return the live set of `component.py` paths declaring a delivery mode."""
        return self._modules

    def addressable(self, module_path: Path | None) -> bool:
        """Whether the `component.py` at `module_path` names its props, even none.

        Such a component renders from its props alone, so a token can reload it.
        """
        return module_path in self._addressable

    def problems(self) -> tuple[tuple[Path, str], ...]:
        """Return each `component.py` whose calls declare nothing, with the reason."""
        return tuple(self._problems.items())

    def outside(self) -> tuple[tuple[Path, str], ...]:
        """Return each module other than a `component.py` that called one, and which."""
        return tuple(self._outside.items())

    def defer(self, site: tuple[Path, bool], options: dict[str, object]) -> None:
        """Record one `component.defer` call made at `site`."""
        self._record(site, "component.defer()", defer=options)

    def props(self, site: tuple[Path, bool], names: tuple[object, ...]) -> None:
        """Record one `component.props` call made at `site`."""
        self._record(site, "component.props()", props=names)

    def _record(
        self,
        site: tuple[Path, bool],
        call: str,
        *,
        defer: dict[str, object] | None = None,
        props: tuple[object, ...] | None = None,
    ) -> None:
        file_path, top_level = site
        if file_path.name != COMPONENT_MODULE:
            self._outside[file_path] = call
            return
        pending = self._collecting.written.get(file_path)
        written = (
            replace(self._held.get(file_path, _Written()))
            if pending is None
            else pending
        )
        if not top_level:
            written.nested = True
        elif defer is not None:
            written.defer = defer
        else:
            written.props = props
        if pending is None:
            self._commit(file_path, written)

    @contextmanager
    def collecting(self, file_path: Path) -> Iterator[None]:
        """Gather what one execution of `file_path` declares and swap it in at the end.

        A reader during the execution sees the earlier declaration rather than none.
        """
        collected = self._collecting.written
        collected[file_path] = _Written()
        try:
            yield
        finally:
            written = collected.pop(file_path)
            self._commit(file_path, None if written == _Written() else written)

    def _commit(self, file_path: Path, written: _Written | None) -> None:
        """Hold what `written` settles to for `file_path`, `None` dropping it all."""
        held = (
            self._options.get(file_path),
            self._problems.get(file_path),
            file_path in self._addressable,
        )
        options, problem = (None, None) if written is None else _settle(written)
        named = options is not None and (
            bool(options.props) or (written is not None and written.props is not None)
        )
        _place(self._held, file_path, written)
        _place(self._options, file_path, options)
        _place(self._problems, file_path, problem)
        if named:
            self._addressable.add(file_path)
        else:
            self._addressable.discard(file_path)
        if (options, problem, named) != held:
            self._version += 1
        if options is not None and options.mode is not None:
            self._modules.add(file_path)
            for callback in self._deferring:
                callback()
        else:
            self._modules.discard(file_path)

    def reset(self) -> None:
        """Drop every declaration."""
        self._held.clear()
        self._options.clear()
        self._modules.clear()
        self._addressable.clear()
        self._problems.clear()
        self._outside.clear()
        self._version += 1


defer_declarations = DeferDeclarations()


__all__ = ["COMPONENT_MODULE", "DeferDeclarations", "defer_declarations"]
