"""What a page error boundary contains and the `error.djx` it renders in its place."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from django.template import Origin, Template, TemplateDoesNotExist

from next.boundaries import contained, render_boundaries
from next.caches import PageCache
from next.components.registry import error_files
from next.diagnostics import mark_degraded
from next.seeding import JS_CONTEXT_KEY, JS_SERIALIZERS_KEY
from next.utils import is_within, resolve_base_dir

from .loaders import template_source


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from next.defer import ErrorKind

    from .context import ContextResult
    from .graph import Blame
    from .loaders import BoundaryChain, ErrorBoundary


FAILED_STATUS: Final = 500
"""The status of a response whose page body a page boundary contained."""

FAILURE_KEY: Final = "_next_page_failure"
"""Context key of a failure met before the template rendered, out of template reach."""

CAUGHT_KEY: Final = "_next_page_caught"
"""Context key of a failure a job met, which the boundary that holds it renders."""

SHELL_KEY: Final = "_next_page_shell"
"""Context key of the shell a page boundary renders its error UI in."""


@dataclass(frozen=True, slots=True)
class ErrorShell:
    """The context the error UI of a page boundary renders in, cut at its directory.

    Only inherited groups at or above the cut reach it, never what failed below.
    """

    head: Mapping[str, object]
    tail: Mapping[str, object]
    levels: tuple[int, ...]
    blame: Blame
    chain: BoundaryChain

    def around(self, result: ContextResult) -> dict[str, object]:
        """Return the full context of a page whose every callable returned `result`."""
        context = dict(self.head)
        context.update(result.context_data)
        context[JS_CONTEXT_KEY] = result.js_context
        context[JS_SERIALIZERS_KEY] = result.js_context_serializers
        context.update(self.tail)
        context[SHELL_KEY] = self
        return context

    def at(self, level: int) -> dict[str, object]:
        """Return the shell of a boundary `level` directories above the page."""
        return self._cut(sum(1 for found in self.levels if found >= level))

    def of(self, source: Path) -> dict[str, object]:
        """Return the shell of the boundary of the `error.djx` at `source`."""
        for guard in self.chain.errors:
            if guard.source == source:
                return self.at(guard.level)
        return self._cut(0)

    def _cut(self, groups: int) -> dict[str, object]:
        context = dict(self.head)
        context.update(self.blame.cut(groups))
        context[JS_CONTEXT_KEY] = {}
        context[JS_SERIALIZERS_KEY] = {}
        context.update(self.tail)
        context[SHELL_KEY] = self
        return context


@dataclass(frozen=True, slots=True)
class Caught:
    """A failure met before the template rendered and the boundary that contains it."""

    boundary: ErrorBoundary
    error: Exception
    shell: ErrorShell | None = None


_NO_SCOPE: Final[AbstractContextManager[None]] = nullcontext()


def boundary_scope(chain: BoundaryChain) -> AbstractContextManager[None]:
    """Return the per-render boundary block where the page or a component can catch.

    Without an `error.djx` in the chain or on a component the render opens nothing.
    """
    if chain.errors or error_files.held:
        return render_boundaries()
    return _NO_SCOPE


def mark_failed(kind: ErrorKind) -> None:
    """Give the response the status of a page body that settled as `kind`.

    An expected kind answers its own status, and every other kind `FAILED_STATUS`.
    """
    mark_degraded(kind.status or FAILED_STATUS)


def shown_source(source: Path) -> str:
    """Return `source` relative to `BASE_DIR` when it lies inside, as DEBUG shows it."""
    base = resolve_base_dir()
    if base is not None and is_within(source, base):
        return str(source.relative_to(base))
    return str(source)


def catching(
    chain: BoundaryChain, exc: BaseException, level: int = -1
) -> ErrorBoundary | None:
    """Return the nearest boundary of `chain` that contains `exc`, raised `level` up.

    A callable of a directory belongs to its layout, which only a boundary above holds.
    """
    if not contained(exc):
        return None
    return next((guard for guard in chain.errors if guard.level > level), None)


_COMPILED: PageCache[Path, tuple[str, Template]] = PageCache()


def boundary_template(source: Path) -> Template:
    """Return the boundary file at `source` compiled, again once its text moved."""
    text = template_source(source)
    if text is None:
        raise TemplateDoesNotExist(str(source))
    held = _COMPILED.get(source)
    if held is not None and held[0] is text:
        return held[1]
    compiled = Template(text, origin=Origin(str(source)), name=str(source))
    _COMPILED[source] = (text, compiled)
    return compiled


__all__ = [
    "CAUGHT_KEY",
    "FAILED_STATUS",
    "FAILURE_KEY",
    "SHELL_KEY",
    "Caught",
    "ErrorShell",
    "boundary_scope",
    "boundary_template",
    "catching",
    "mark_failed",
    "shown_source",
]
