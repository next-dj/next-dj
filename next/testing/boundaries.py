"""Failures an error boundary contained in a test, re-raised unless it opts out."""

from __future__ import annotations

import functools
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, cast

from next.boundaries import boundary_failed, contain
from next.defer import ErrorKind
from next.pages.boundaries import shown_source
from next.pages.loaders import LOADING_TEMPLATE
from next.suspense.connections import forcing_pool
from next.suspense.render import Gate, gated


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


type Mode = Literal["raise", "render"]

_MODES: Final = ("raise", "render")

_mode: ContextVar[Mode] = ContextVar("next_boundary_errors", default="raise")


@dataclass(frozen=True, slots=True)
class RecordedFailure:
    """One failure a boundary contained, as `boundary_failed` announced it."""

    id: str
    name: str
    addressing: str
    kind: ErrorKind
    digest: str
    exception: Exception


_recording: ContextVar[list[RecordedFailure] | None] = ContextVar(
    "next_boundary_failures", default=None
)


@contextmanager
def boundary_errors(mode: Mode) -> Iterator[None]:
    """Run the block with contained failures re-raised, or rendered for `"render"`.

    Tests re-raise by default, so only a test about an error UI asks for `"render"`.
    """
    if mode not in _MODES:
        msg = f"boundary_errors takes 'raise' or 'render', got {mode!r}"
        raise ValueError(msg)
    token = _mode.set(mode)
    try:
        yield
    finally:
        _mode.reset(token)


def _record(**kwargs: object) -> None:
    """Append one `boundary_failed` send to the failures the current context records.

    A failure on another thread or in another request leaves this context untouched.
    """
    failures = _recording.get()
    if failures is not None:
        failures.append(
            RecordedFailure(
                id=str(kwargs["id"]),
                name=str(kwargs["name"]),
                addressing=str(kwargs["addressing"]),
                kind=ErrorKind(str(kwargs["kind"])),
                digest=str(kwargs["digest"]),
                exception=cast("Exception", kwargs["exception"]),
            )
        )


@functools.cache
def _listen() -> None:
    """Connect `_record` on the first recording, so importing this module connects none.

    It stays connected, as a job that outlives its block still records into the list.
    """
    boundary_failed.connect(_record, sender=contain, dispatch_uid=f"{__name__}._record")


@contextmanager
def recording(failures: list[RecordedFailure]) -> Iterator[None]:
    """Record into `failures` every failure contained in the block on this context."""
    _listen()
    token = _recording.set(failures)
    try:
        yield
    finally:
        _recording.reset(token)


@contextmanager
def recorded_failures() -> Iterator[list[RecordedFailure]]:
    """Record every failure a boundary contains inside the block, in render order.

    An enclosing recording receives them too, once the block ends.
    """
    outer = _recording.get()
    failures: list[RecordedFailure] = []
    try:
        with recording(failures):
            yield failures
    finally:
        if outer is not None:
            outer.extend(failures)


def in_document_order(
    failures: Sequence[RecordedFailure], document: str | bytes
) -> tuple[RecordedFailure, ...]:
    """Return `failures` in the order their boundaries appear in `document`.

    A failure whose error UI a failed outer boundary discarded follows in render order.
    """
    text = (
        document.decode(errors="replace") if isinstance(document, bytes) else document
    )

    def position(failure: RecordedFailure) -> tuple[bool, int]:
        found = re.search(rf"{re.escape(failure.id)}(?!\d)", text)
        return (found is None, 0 if found is None else found.start())

    return tuple(sorted(failures, key=position))


@contextmanager
def parallel_boundaries() -> Iterator[None]:
    """Run the deferred jobs of the block on the pool, the test transaction aside.

    A pool thread reads only committed rows, so it suits a transactional test.
    """
    with forcing_pool():
        yield


_shell_gates: ContextVar[tuple[Gate, ...]] = ContextVar(
    "next_boundary_gates", default=()
)


def _gate_name(component: str | None, page: Path | str | None) -> str:
    """Return the site name the jobs of a component or of a page boundary carry."""
    if (component is None) == (page is None):
        msg = "boundary_gate takes exactly one of component= or page="
        raise TypeError(msg)
    if component is not None:
        return component
    return shown_source(Path(cast("Path | str", page)) / LOADING_TEMPLATE)


@contextmanager
def boundary_gate(
    *,
    component: str | None = None,
    page: Path | str | None = None,
    after_shell: bool = False,
) -> Iterator[Gate]:
    """Hold every deferred job of `component`, or of the page boundary at `page`.

    `page` is the directory of its `loading.djx`, and `after_shell` opens after a shell.
    """
    name = _gate_name(component, page)
    with gated(name) as gate:
        if not after_shell:
            yield gate
            return
        token = _shell_gates.set((*_shell_gates.get(), gate))
        try:
            yield gate
        finally:
            _shell_gates.reset(token)


def release_gates() -> None:
    """Release every `after_shell` gate open in this context, once a shell was read."""
    for gate in _shell_gates.get():
        gate.release()


def raise_first(failures: Sequence[RecordedFailure]) -> None:
    """Re-raise the first of `failures` as ordered, unless the test renders them."""
    if failures and _mode.get() == "raise":
        raise failures[0].exception


__all__ = [
    "Mode",
    "RecordedFailure",
    "boundary_errors",
    "boundary_gate",
    "in_document_order",
    "parallel_boundaries",
    "raise_first",
    "recorded_failures",
    "recording",
    "release_gates",
]
