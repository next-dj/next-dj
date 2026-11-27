"""The boundary a `component.py` declares through `component.defer` and `props`."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from next.boundaries.errors import BoundaryOptionError
from next.boundaries.options import BoundaryOptions
from next.introspect import refused_declaration
from next.permissions.access import declaring


if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Set as AbstractSet
    from pathlib import Path

    from next.introspect import DeclaringSite


COMPONENT_MODULE: Final = "component.py"

_TWICE: Final = (
    "it names props through both component.defer(props=) and component.props()"
)


@dataclass(slots=True)
class _Written:
    """The calls one execution of a `component.py` made, the last of each winning."""

    defer: dict[str, object] | None = None
    props: tuple[object, ...] | None = None


class _Collecting(threading.local):
    """The calls each execution under way in this thread gathered so far."""

    def __init__(self) -> None:
        """Start with no execution under way."""
        self.written: dict[Path, _Written] = {}


def _settle(written: _Written) -> tuple[BoundaryOptions | None, str | None]:
    """Return the options the calls declare, or why they declare none."""
    options: dict[str, object] = dict(written.defer or {"mode": None})
    if written.props is not None:
        if options.get("props"):
            return None, _TWICE
        options["props"] = written.props
    try:
        return BoundaryOptions.parse(**options), None
    except BoundaryOptionError as error:
        return None, str(error)


def _written(
    written: _Written, defer: dict[str, object] | None, props: tuple[object, ...] | None
) -> _Written:
    """Return `written` with one more call, the last of each kind winning."""
    if defer is not None:
        return replace(written, defer=defer)
    return replace(written, props=props)


def _place[V](
    held: dict[Path | None, V] | dict[Path, V], key: Path, value: V | None
) -> None:
    """Set `key` to `value`, or drop it for `None`."""
    if value is None:
        held.pop(key, None)
    else:
        held[key] = value


def _checked(site: DeclaringSite, call: str, written: _Written) -> _Written:
    """Return `written` once its calls declare options, raising for any that do not."""
    problem = _settle(written)[1]
    if problem is not None:
        raise refused_declaration(call, site, problem)
    return written


class DeferDeclarations:
    """Hold the boundary options each `component.py` declares, keyed by its path.

    A loader run swaps its calls in at the end, a plain import joins the calls held.
    """

    __slots__ = (
        "_addressable",
        "_collecting",
        "_deferring",
        "_held",
        "_lock",
        "_modules",
        "_options",
        "_outside",
        "_version",
    )

    def __init__(self) -> None:
        """Start empty."""
        self._held: dict[Path, _Written] = {}
        self._options: dict[Path | None, BoundaryOptions] = {}
        self._modules: set[Path] = set()
        self._addressable: set[Path] = set()
        self._outside: dict[Path, str] = {}
        self._collecting = _Collecting()
        self._lock = threading.Lock()
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

    def outside(self) -> tuple[tuple[Path, str], ...]:
        """Return each module other than a `component.py` that called one, and which."""
        with self._lock:
            return tuple(self._outside.items())

    def defer(self, site: DeclaringSite, options: dict[str, object]) -> None:
        """Record one `component.defer` call made at `site`, raising on a misfit."""
        self._record(site, "component.defer()", defer=options)

    def props(self, site: DeclaringSite, names: tuple[object, ...]) -> None:
        """Record one `component.props` call made at `site`, raising on a misfit."""
        self._record(site, "component.props()", props=names)

    def _record(
        self,
        site: DeclaringSite,
        call: str,
        *,
        defer: dict[str, object] | None = None,
        props: tuple[object, ...] | None = None,
    ) -> None:
        file_path = declaring(site)
        if file_path.name != COMPONENT_MODULE:
            with self._lock:
                self._outside[file_path] = call
            return
        pending = self._collecting.written
        if file_path in pending:
            pending[file_path] = _checked(
                site, call, _written(pending[file_path], defer, props)
            )
            return
        with self._lock:
            held = self._held.get(file_path, _Written())
            written = _checked(site, call, _written(held, defer, props))
            deferring = self._commit(file_path, written)
        if deferring:
            self._announce()

    @contextmanager
    def collecting(self, file_path: Path) -> Generator[None]:
        """Gather what one execution of `file_path` declares and swap it in at the end.

        A reader during the execution sees the earlier declaration rather than none.
        """
        collected = self._collecting.written
        collected[file_path] = _Written()
        try:
            yield
        finally:
            written = collected.pop(file_path)
            with self._lock:
                deferring = self._commit(
                    file_path, None if written == _Written() else written
                )
            if deferring:
                self._announce()

    def _announce(self) -> None:
        """Tell each `on_deferring` callback a declaration names a delivery mode."""
        for callback in self._deferring:
            callback()

    def _commit(self, file_path: Path, written: _Written | None) -> bool:
        """Hold what `written` settles to for `file_path`, `None` dropping it all.

        The caller holds the lock and announces a delivery mode once it is released.
        """
        held = (self._options.get(file_path), file_path in self._addressable)
        options = None if written is None else _settle(written)[0]
        named = options is not None and (
            bool(options.props) or (written is not None and written.props is not None)
        )
        _place(self._held, file_path, written)
        _place(self._options, file_path, options)
        if named:
            self._addressable.add(file_path)
        else:
            self._addressable.discard(file_path)
        if (options, named) != held:
            self._version += 1
        if options is not None and options.mode is not None:
            self._modules.add(file_path)
            return True
        self._modules.discard(file_path)
        return False

    def reset(self) -> None:
        """Drop every declaration."""
        with self._lock:
            self._held.clear()
            self._options.clear()
            self._modules.clear()
            self._addressable.clear()
            self._outside.clear()
            self._version += 1


defer_declarations = DeferDeclarations()


__all__ = ["COMPONENT_MODULE", "DeferDeclarations", "defer_declarations"]
