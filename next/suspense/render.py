"""What one deferred component job does, from its capture to a `Rendered` result."""

from __future__ import annotations

import functools
import logging
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Final, override

from django.http import HttpRequest
from django.template import Context

from next import diagnostics
from next.boundaries import kind_of, note_retry, render_boundaries
from next.defer import deadline
from next.pages.processors import context_processors
from next.seeding import COLLECTOR_KEY, REQUEST_KEY
from next.static.collector import BundleUrl, StaticCollector
from next.suspense.connections import effects, fresh_querysets, request_state
from next.suspense.markers import Rendered
from next.suspense.trip import retry_token


if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.template import Template

    from next.defer import BoundaryError, DeferredCall, ErrorKind
    from next.static.assets import StaticAsset
    from next.static.serializers import JsContextSerializer
    from next.suspense.components import Capture
    from next.suspense.markers import Job
    from next.suspense.session import BoundarySession


logger = logging.getLogger("next.suspense")

_PROCESSED_ATTR: Final = "_next_processed"


@dataclass(frozen=True, slots=True)
class Added:
    """One asset a job added, `prepend` placing it before the others of its slot."""

    asset: StaticAsset
    prepend: bool = False


@dataclass(frozen=True, slots=True)
class Noted:
    """One value a job noted for another area to read at injection time."""

    key: str
    value: object


@dataclass(frozen=True, slots=True)
class Merged:
    """One JS context entry a job registered, with the serializer it named."""

    key: str
    value: object
    serializer: JsContextSerializer | None = None


type Step = Added | Noted | Merged


@dataclass(frozen=True, slots=True)
class Delta:
    """What a job added to its own collector, in the order it added it."""

    steps: tuple[Step, ...] = ()

    def apply(self, collector: StaticCollector) -> None:
        """Replay every step onto `collector`, whose dedup and merge policy decide."""
        for step in self.steps:
            if isinstance(step, Added):
                collector.add(step.asset, prepend=step.prepend)
            elif isinstance(step, Noted):
                collector.note(step.key, step.value)
            else:
                collector.add_js_context(
                    step.key, step.value, serializer=step.serializer
                )


class Recording(StaticCollector):
    """A collector that keeps each change it accepts, a rollback taking it back too."""

    def __init__(self, bundle_url: BundleUrl | None = None) -> None:
        """Start with no step and no mark, resolving bundles as the page does."""
        super().__init__(bundle_url=bundle_url)
        self._steps: list[Step] = []
        self._lengths: list[int] = []

    @override
    def mark(self) -> int:
        """Remember how many steps stand, then mark the journal."""
        self._lengths.append(len(self._steps))
        return super().mark()

    @override
    def release(self) -> None:
        """End the innermost mark, keeping its steps."""
        super().release()
        self._lengths.pop()

    @override
    def rollback(self, mark: int) -> None:
        """Undo the changes since `mark` and drop the steps they recorded."""
        kept = self._lengths[-1] if self._lengths else len(self._steps)
        super().rollback(mark)
        del self._steps[kept:]

    @override
    def add(self, asset: StaticAsset, *, prepend: bool = False) -> bool:
        """Add the asset and record it when the collector took it."""
        added = super().add(asset, prepend=prepend)
        if added:
            self._steps.append(Added(asset, prepend))
        return added

    @override
    def note(self, key: str, value: object) -> None:
        """Note the value and record it."""
        super().note(key, value)
        self._steps.append(Noted(key, value))

    @override
    def add_js_context(
        self, key: str, value: object, *, serializer: JsContextSerializer | None = None
    ) -> None:
        """Merge the entry and record it once it serialized."""
        super().add_js_context(key, value, serializer=serializer)
        self._steps.append(Merged(key, value, serializer))

    def delta(self) -> Delta:
        """Return the steps recorded so far."""
        return Delta(tuple(self._steps))


@dataclass(slots=True)
class Frame:
    """The job rendering in this context, which a nested deferred call joins."""

    session: BoundarySession[Delta]
    ident: str
    depth: int
    nested: list[Job[Delta]] = field(default_factory=list)


_frame: ContextVar[Frame | None] = ContextVar("next_defer_frame", default=None)


def current_frame() -> Frame | None:
    """Return the job rendering in this context, `None` outside every job."""
    return _frame.get()


type Processor = Callable[[Any], dict[str, Any]]


def _processors(template: Template | None) -> tuple[Processor, ...]:
    """Return the engine processors, then the page processors, each one once."""
    engine: tuple[Processor, ...] = ()
    if template is not None:
        engine = tuple(template.engine.template_context_processors)
    return tuple(dict.fromkeys((*engine, *context_processors())))


class _Processed:
    """What the context processors returned for one request, run once per set."""

    __slots__ = ("_held", "_lock")

    def __init__(self) -> None:
        """Hold no result yet."""
        self._held: dict[tuple[Processor, ...], dict[str, Any]] = {}
        self._lock = threading.Lock()

    def data(
        self, processors: tuple[Processor, ...], request: object
    ) -> dict[str, Any]:
        """Return what `processors` give `request`, running them on the first call."""
        with self._lock:
            found = self._held.get(processors)
            if found is None:
                found = {}
                for processor in processors:
                    found.update(processor(request))
                self._held[processors] = found
        return found


def _processed(request: object) -> _Processed:
    """Return the processor results the jobs and error UIs of `request` share."""
    held = getattr(request, _PROCESSED_ATTR, None)
    if isinstance(held, _Processed):
        return held
    shared: _Processed = vars(request).setdefault(_PROCESSED_ATTR, _Processed())
    return shared


def _context(capture: Capture, collector: StaticCollector) -> Context:
    """Rebuild the call-site context over the processor results of its request.

    The captured values sit on top, as a page, a `{% with %}` or a loop shadows them.
    """
    data: dict[str, Any] = {}
    request = capture.data.get(REQUEST_KEY)
    if request is not None:
        data.update(_processed(request).data(_processors(capture.template), request))
    data.update(fresh_querysets(capture.data))
    data[COLLECTOR_KEY] = collector
    return Context(
        data,
        autoescape=capture.autoescape,
        use_l10n=capture.use_l10n,
        use_tz=capture.use_tz,
    )


@contextmanager
def _bound(context: Context, template: Template | None) -> Iterator[None]:
    """Bind `context` to the template the call site rendered in, when there was one."""
    if template is None:
        yield
        return
    with context.render_context.push_state(template), context.bind_template(template):
        yield


def _rendered[T](capture: Capture, draw: Callable[[Context], T]) -> tuple[T, Recording]:
    """Return what `draw` makes of the rebuilt context, with the assets it added."""
    page = capture.data.get(COLLECTOR_KEY)
    collector = Recording(
        page.bundle_url if isinstance(page, StaticCollector) else None
    )
    context = _context(capture, collector)
    with render_boundaries(), _bound(context, capture.template):
        drawn = draw(context)
    return drawn, collector


class Gate:
    """A hold on the jobs of one component, open until released or out of time."""

    __slots__ = ("entered", "name", "released")

    def __init__(self, name: str) -> None:
        """Hold the jobs of the component `name`, none of them entered yet."""
        self.name = name
        self.entered = threading.Event()
        self.released = threading.Event()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until a job of the component is held, false once `timeout` passed."""
        return self.entered.wait(timeout)

    def release(self) -> None:
        """Let every held job of the component and every later one run."""
        self.released.set()


_gates: dict[str, Gate] = {}


@contextmanager
def gated(name: str) -> Iterator[Gate]:
    """Hold every job of the component `name` inside the block until it is released."""
    held = _gates[name] = Gate(name)
    try:
        yield held
    finally:
        if _gates.get(name) is held:
            del _gates[name]
        held.release()


def _hold(name: str, at: float) -> None:
    """Wait at the gate of `name` when one is set, at most until the instant `at`."""
    held = _gates.get(name)
    if held is not None:
        held.entered.set()
        held.released.wait(max(0.0, at - time.monotonic()))


def _effects(request: object, build: Callable[[], Rendered[Delta]]) -> Rendered[Delta]:
    """Return what `build` rendered with the request-state writes it made."""
    if not isinstance(request, HttpRequest):
        return build()
    before = request_state(request)
    rendered = build()
    found = effects(request, before)
    return replace(rendered, effects=found) if found else rendered


def run(
    call: DeferredCall, capture: Capture, frame: Frame, at: float
) -> Rendered[Delta]:
    """Render the call from its capture under its deadline, nested calls recorded.

    An exception the error boundary let through returns for the request thread to raise.
    """
    return _effects(
        capture.data.get(REQUEST_KEY), functools.partial(_run, call, capture, frame, at)
    )


def _run(
    call: DeferredCall, capture: Capture, frame: Frame, at: float
) -> Rendered[Delta]:
    def build() -> tuple[tuple[str, BoundaryError | None], Recording, int | None]:
        _frame.set(frame)
        if _gates:
            _hold(call.site.name, at)
        with deadline(at):
            drawn, recording = _rendered(capture, call.renderer.body)
        return drawn, recording, diagnostics.degraded_status()

    caught = diagnostics.Capture()
    with caught:
        ((html, failure), recording, status), degraded = diagnostics.isolated_build(
            build
        )
    error = caught.error
    if error is not None:
        return Rendered("", Delta(), kind=kind_of(error), intended=error)
    retry = ""
    if failure is not None:
        retry = retry_token(capture.data.get(REQUEST_KEY), call, failure)
        note_retry(recording, html, retry)
    return Rendered(
        html,
        recording.delta(),
        kind=None if failure is None else failure.kind,
        nested=tuple(frame.nested),
        degraded=degraded,
        status=status,
        failure=failure,
        retry=retry,
    )


@contextmanager
def unframed() -> Iterator[None]:
    """Render the block outside every job, so a deferred call in it renders in place."""
    token = _frame.set(None)
    try:
        yield
    finally:
        _frame.reset(token)


def fallback(call: DeferredCall, capture: Capture, kind: ErrorKind) -> Rendered[Delta]:
    """Render the error UI of `kind` without running the component.

    A failing error UI leaves the bare wrapper, which still reads as an error.
    """

    def draw(context: Context) -> tuple[str, BoundaryError]:
        return call.renderer.error(context, kind)

    try:
        (html, failure), recording = _rendered(capture, draw)
    except Exception:
        logger.exception("The error UI of %r failed to render.", call.site.name)
        return Rendered("", Delta(), kind=kind)
    retry = retry_token(capture.data.get(REQUEST_KEY), call, failure)
    note_retry(recording, html, retry)
    return Rendered(
        html, recording.delta(), kind=failure.kind, failure=failure, retry=retry
    )


__all__ = [
    "Added",
    "Delta",
    "Frame",
    "Gate",
    "Merged",
    "Noted",
    "Recording",
    "Step",
    "current_frame",
    "fallback",
    "gated",
    "run",
    "unframed",
]
