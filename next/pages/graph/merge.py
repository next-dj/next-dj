"""The steps of a context run in order and the merge of their results."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Protocol

from next.introspect import callable_name
from next.pages.context import ContextResult
from next.pages.errors import PageContextShapeError


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from next.pages.registry import PageContextEntry
    from next.static.serializers import JsContextSerializer


def keyless_shape_error(
    func: Callable[..., object], file_path: Path
) -> PageContextShapeError:
    """Return the error a keyless `@context` returning a non-mapping raises."""
    return PageContextShapeError(callable_name(func), file_path)


def keyless_result(
    result: object,
    entry: PageContextEntry | Step,
    file_path: Path,
    shielded: frozenset[str],
) -> dict[str, object]:
    """Return the mapping a keyless callable returned, without the shielded keys."""
    if not isinstance(result, dict):
        raise keyless_shape_error(entry.func, file_path)
    if not shielded:
        return result
    return {name: value for name, value in result.items() if name not in shielded}


class Step(NamedTuple):
    """One context callable of a render in run order, `group` -1 for the page's own.

    An ancestor callable carries its file's group, and `inherits` marks an own one.
    """

    key: str | None
    func: Callable[..., object]
    is_async: bool
    serialize: bool
    serializer: JsContextSerializer | None
    zones: frozenset[str] | None
    group: int
    guard: bool | None = None
    inherits: bool = False


class RunOrder(NamedTuple):
    """The steps of one render in run order, with the keys the keyless merge keeps."""

    steps: tuple[Step, ...]
    shielded: frozenset[str]


class Ordered(Protocol):
    """The steps a merge consumes, a `RunOrder` or a compiled `ContextGraph`."""

    @property
    def steps(self) -> tuple[Step, ...]:
        """Return the steps in run order."""

    @property
    def shielded(self) -> frozenset[str]:
        """Return the keyed values the keyless callable's merge must not overwrite."""


def spread(merged: dict[str, object], value: dict[str, object]) -> tuple[str, ...]:
    """Merge a keyless inherited result into `merged`, adding only the names still free.

    The names it added come back, as a keyed step of the same group may replace them.
    """
    added = tuple(name for name in value if name not in merged)
    for name in added:
        merged[name] = value[name]
    return added


class Merge:
    """Merge the results of one context run in run order, the one carrier of its rules.

    It also answers whether an inherited keyed step runs, from the keys merged so far.
    """

    __slots__ = (
        "_file_path",
        "_group",
        "_merged",
        "_page",
        "_shielded",
        "_steps",
        "inherited",
        "js_context",
        "serializers",
    )

    def __init__(self, order: Ordered, file_path: Path) -> None:
        """Start with nothing merged, the page context at once when nothing inherits.

        Inherited steps lead the run, so without one the page context needs no copy.
        """
        steps = self._steps = order.steps
        self._shielded = order.shielded
        self._file_path = file_path
        self.inherited: dict[str, object] = {}
        self._page: dict[str, object] | None = (
            None if steps and steps[0].group >= 0 else self.inherited
        )
        self.js_context: dict[str, object] = {}
        self.serializers: dict[str, JsContextSerializer] = {}
        self._group = -1
        self._merged: tuple[str, ...] = ()

    def _open(self) -> dict[str, object]:
        """Start the page context as a copy of the inherited one and return it."""
        page = self._page = dict(self.inherited)
        return page

    def reads(self, index: int) -> dict[str, object] | None:
        """Return the mapping the step at `index` reads, `None` when it does not run.

        An inherited keyed step is skipped once an earlier group took its key.
        """
        step = self._steps[index]
        group = step.group
        if group < 0:
            page = self._page
            return self._open() if page is None else page
        key = step.key
        if key is not None:
            if group != self._group:
                self._group = group
                self._merged = ()
            if key in self.inherited and key not in self._merged:
                return None
        return self.inherited

    def add(self, index: int, value: object) -> None:
        """Merge the result of the step at `index`, publishing what it serializes."""
        step = self._steps[index]
        if step.group >= 0:
            self._inherit(step, value)
            return
        key = step.key
        context_data = self._page
        if context_data is None:
            context_data = self._open()
        if key is None:
            result = keyless_result(value, step, self._file_path, self._shielded)
            context_data.update(result)
            if step.serialize:
                self.js_context.update(result)
                if step.serializer is not None:
                    for name in result:
                        self.serializers[name] = step.serializer
            return
        context_data[key] = value
        if step.serialize and key not in self.js_context:
            self.js_context[key] = value
            if step.serializer is not None:
                self.serializers[key] = step.serializer

    def _inherit(self, step: Step, value: object) -> None:
        """Merge an ancestor result, a keyless one adding only the names still free."""
        group = step.group
        if group != self._group:
            self._group = group
            self._merged = ()
        if step.key is not None:
            self.inherited[step.key] = value
        elif isinstance(value, dict):
            self._merged = spread(self.inherited, value)
        else:
            raise keyless_shape_error(step.func, self._file_path)

    def result(self) -> ContextResult:
        """Return the merged context, built positionally as keywords cost a dict."""
        page = self._page
        return ContextResult(
            self._open() if page is None else page, self.js_context, self.serializers
        )


__all__ = [
    "Merge",
    "Ordered",
    "RunOrder",
    "Step",
    "keyless_result",
    "keyless_shape_error",
    "spread",
]
