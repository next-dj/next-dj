"""Failures an error boundary contained in a test, re-raised unless it opts out."""

from __future__ import annotations

import functools
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from next.boundaries import boundary_failed, contain
from next.components.sources import get_components_manager
from next.pages.boundaries import shown_source
from next.pages.loaders import LOADING_TEMPLATE
from next.suspense.connections import forcing_pool
from next.suspense.render import job_gates
from next.utils import resolve_base_dir


if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from next.defer import ErrorKind


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


type _Sink = list[RecordedFailure] | HeldFailures

_recording: ContextVar[tuple[_Sink, ...]] = ContextVar(
    "next_boundary_failures", default=()
)


def _hand_on(sinks: Sequence[_Sink], failures: Sequence[RecordedFailure]) -> None:
    """Append each of `failures`, in order, to every one of `sinks`."""
    for sink in sinks:
        for failure in failures:
            sink.append(failure)


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


def _record(
    *,
    name: str,
    addressing: str,
    kind: ErrorKind,
    digest: str,
    exception: Exception,
    **signal: object,
) -> None:
    """Append one `boundary_failed` send to every recording open on this context.

    A failure on another thread or in another request leaves this context untouched.
    """
    recordings = _recording.get()
    if recordings:
        ident = str(signal["id"])
        failure = RecordedFailure(ident, name, addressing, kind, digest, exception)
        _hand_on(recordings, (failure,))


@functools.cache
def _listen() -> None:
    """Connect `_record` on the first recording, so importing this module connects none.

    It stays connected, as a job that outlives its block still records into the list.
    """
    boundary_failed.connect(_record, sender=contain, dispatch_uid=f"{__name__}._record")


@contextmanager
def _recording_into(recordings: tuple[_Sink, ...]) -> Iterator[None]:
    """Record every failure contained in the block on this context into `recordings`."""
    _listen()
    token = _recording.set(recordings)
    try:
        yield
    finally:
        _recording.reset(token)


@contextmanager
def recorded_failures() -> Iterator[list[RecordedFailure]]:
    """Record every failure a boundary contains inside the block, in render order.

    Every enclosing recording receives each failure too, as it is recorded.
    """
    failures: list[RecordedFailure] = []
    with _recording_into((*_recording.get(), failures)):
        yield failures


class HeldFailures:
    """The failures of one response, held apart until its document is complete.

    The recordings open where it is made get them once, wherever the response renders.
    """

    __slots__ = ("_lock", "_outer", "_settled", "failures")

    def __init__(self) -> None:
        """Hold no failure yet, noting the recordings open on this context."""
        self.failures: list[RecordedFailure] = []
        self._outer = _recording.get()
        self._settled = False
        self._lock = threading.Lock()

    @contextmanager
    def recording(self) -> Iterator[None]:
        """Record the failures of the block here alone, handing them on if it raises."""
        with _recording_into((self,)):
            try:
                yield
            except Exception:
                self.settle(b"")
                raise

    def append(self, failure: RecordedFailure) -> None:
        """Hold `failure`, or hand it on at once when it arrives after the settle."""
        with self._lock:
            if self._settled:
                _hand_on(self._outer, (failure,))
            else:
                self.failures.append(failure)

    def settle(self, document: str | bytes) -> tuple[RecordedFailure, ...]:
        """Return the failures in the order of `document`, handed on the first time."""
        with self._lock:
            ordered = in_document_order(self.failures, document)
            if not self._settled:
                self._settled = True
                _hand_on(self._outer, ordered)
        return ordered


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


class ComponentGate:
    """A hold on the jobs of one component, open until released or out of time."""

    __slots__ = ("entered", "name", "released")

    def __init__(self, name: str) -> None:
        """Hold the jobs of the component `name`, none of them entered yet."""
        self.name = name
        self.entered = threading.Event()
        self.released = threading.Event()

    def hold(self, at: float) -> None:
        """Note a held job and wait until released, at most until the instant `at`."""
        self.entered.set()
        self.released.wait(max(0.0, at - time.monotonic()))

    def wait(self, timeout: float | None = None) -> bool:
        """Block until a job of the component is held, false once `timeout` passed."""
        return self.entered.wait(timeout)

    def release(self) -> None:
        """Let every held job of the component and every later one run."""
        self.released.set()


@contextmanager
def gated(name: str) -> Iterator[ComponentGate]:
    """Hold every job of the component `name` inside the block until it is released."""
    held = ComponentGate(name)
    job_gates[name] = held
    try:
        yield held
    finally:
        if job_gates.get(name) is held:
            del job_gates[name]
        held.release()


_shell_gates: ContextVar[tuple[ComponentGate, ...]] = ContextVar(
    "next_boundary_gates", default=()
)


def _component_name(name: str) -> str:
    """Return `name` when a component source holds a component of that name."""
    for backend in get_components_manager().backends:
        if any(info.name == name for info in backend.iter_components()):
            return name
    msg = f"boundary_gate(component={name!r}) names no registered component"
    raise LookupError(msg)


def _page_name(page: Path | str) -> str:
    """Return the name of the page boundary whose `loading.djx` sits in `page`.

    A relative `page` reads from `BASE_DIR`, as the name a site carries does.
    """
    directory = Path(page)
    base = resolve_base_dir()
    if not directory.is_absolute() and base is not None:
        directory = base / directory
    loading = directory / LOADING_TEMPLATE
    if not loading.is_file():
        msg = f"boundary_gate(page={str(page)!r}) finds no {LOADING_TEMPLATE} there"
        raise LookupError(msg)
    return shown_source(loading)


def _gate_name(component: str | None, page: Path | str | None) -> str:
    """Return the site name the jobs of a component or of a page boundary carry."""
    if component is not None and page is None:
        return _component_name(component)
    if component is None and page is not None:
        return _page_name(page)
    msg = "boundary_gate takes exactly one of component= or page="
    raise TypeError(msg)


@contextmanager
def boundary_gate(
    *,
    component: str | None = None,
    page: Path | str | None = None,
    after_shell: bool = False,
) -> Iterator[ComponentGate]:
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
    "ComponentGate",
    "HeldFailures",
    "Mode",
    "RecordedFailure",
    "boundary_errors",
    "boundary_gate",
    "gated",
    "in_document_order",
    "parallel_boundaries",
    "raise_first",
    "recorded_failures",
    "release_gates",
]
