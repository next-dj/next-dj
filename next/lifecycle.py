"""The points a feature takes part in while a page answers one request.

A participant names its points once per page and kind, so a page none applies to runs
the bare render with no call on its behalf.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Protocol

from django.http import HttpRequest
from django.http.response import HttpResponseBase

from .caches import SETTINGS
from .requests import RequestKind


if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from django.template import Template

    from next.pages.boundaries import Caught
    from next.pages.loaders import BoundaryChain
    from next.pages.manager import Renderable
    from next.pages.manager.bodies import BodyResolution
    from next.pages.responses import ResponsePolicy
    from next.transactions import AtomicOverride


class PendingJobs(Protocol):
    """What the render of a page gathered to defer, as the view reads it after."""

    def release(self) -> None:
        """Give back what a response that will not stream holds."""

    def complete(self) -> bool:
        """Whether the render left no job behind for a response that answers whole."""


class ResponseSource(enum.Enum):
    """Where the response a finish point sees came from."""

    RENDER = "render"
    """The page rendered, or a participant served the kind itself."""

    BUILT = "built"
    """The `render()` of the page returned a response of its own."""


class PageCall:
    """One request to a page as its view serves it, which every point receives.

    `held` keeps what a participant carries from one of its points to the next.
    """

    __slots__ = (
        "body",
        "deferral",
        "dep_cache",
        "file_path",
        "held",
        "kind",
        "kwargs",
        "part",
        "policy",
        "request",
        "source",
    )

    def __init__(
        self,
        request: HttpRequest,
        file_path: Path,
        kwargs: dict[str, object],
        kind: RequestKind,
        dep_cache: dict[str, Any],
    ) -> None:
        """Hold the request, nothing yet resolved or rendered for it."""
        self.request = request
        self.file_path = file_path
        self.kwargs = kwargs
        self.kind = kind
        self.dep_cache = dep_cache
        self.held: dict[str, object] = {}
        self.body: BodyResolution | Caught | None = None
        self.part: Renderable | None = None
        self.policy: ResponsePolicy | None = None
        self.deferral: PendingJobs | None = None
        self.source = ResponseSource.RENDER


@dataclass(frozen=True, slots=True)
class PageFacts:
    """What the view knows of one page ahead of a request of one kind and method.

    A blind fact holds whatever the page may do, `slot` is `None` without a template.
    """

    file_path: Path
    kind: RequestKind
    safe: bool
    template: Template | None
    chain: BoundaryChain | None
    defers: bool
    streams: bool
    guarded: bool
    plain: bool
    atomic: AtomicOverride | None
    links: bool
    marks: bool
    fragments: bool
    layers: bool
    fixed: Callable[[], Mapping[str, tuple[str, str | None]]]
    slot: Callable[[str], Renderable | None] | None
    compiler: Callable[[Template, Any], bool]


type Opening = Callable[[PageCall], AbstractContextManager[object]]
type Responding = Callable[[PageCall, str], HttpResponseBase]
type Finishing = Callable[[PageCall, HttpResponseBase], HttpResponseBase]
type Serving = Callable[[PageCall], HttpResponseBase]
type Admitting = Callable[[PageCall], HttpResponseBase | None]
type Refusing = Callable[[PageCall, HttpResponseBase], HttpResponseBase]
type Claiming = Callable[[HttpRequest], RequestKind | None]


@dataclass(frozen=True, slots=True)
class Share:
    """The points one participant takes in the requests of one kind to one page.

    The innermost participant that names `serve` or `respond` is the one that answers.
    """

    around: Opening | None = None
    block: Opening | None = None
    serve: Serving | None = None
    respond: Responding | None = None
    finish: Finishing | None = None


@dataclass(frozen=True, slots=True)
class Gate:
    """What one participant does to a request of one kind before its page is read.

    `refuse` shapes the refusal of the page access, and a gate that `answers` the kind
    decides the access itself, so the page neither asks it nor composes its template.
    """

    admit: Admitting | None = None
    refuse: Refusing | None = None
    answers: bool = False


class Participant(Protocol):
    """One feature's part in the requests a page answers.

    `name` keys it in the registry. Every method runs once per page, kind and method,
    or once per kind, and a settings change asks them again.
    """

    @property
    def name(self) -> str:
        """Return the name the registry keys the participant by."""

    def claim(self) -> Claiming | None:
        """Return what tells whether a request is of a kind this participant serves."""

    def gate(self, kind: RequestKind, /) -> Gate | None:
        """Return what the participant does to a request of `kind` before its page."""

    def share(self, facts: PageFacts, /) -> Share | None:
        """Return the points the participant takes in a request `facts` describe."""


@dataclass(frozen=True, slots=True)
class Points:
    """The points every participant takes in one page, kind and method, in run order.

    Arounds and blocks open in registry order, and finishes run the other way.
    """

    around: tuple[Opening, ...] = ()
    block: tuple[Opening, ...] = ()
    serve: Serving | None = None
    respond: Responding | None = None
    finish: tuple[Finishing, ...] = ()


@dataclass(frozen=True, slots=True)
class Gates:
    """The gates of one kind, in registry order."""

    admit: tuple[Admitting, ...] = ()
    refuse: tuple[Refusing, ...] = ()
    answers: bool = False


_NO_POINTS: Final = Points()
_RANK: Final = {
    RequestKind.BOUNDARY: 0,
    RequestKind.ZONE: 1,
    RequestKind.NAVIGATE: 2,
    RequestKind.ACTION: 3,
    RequestKind.PAGE: 4,
}


@dataclass(slots=True)
class _Derived:
    """What the registry derives from its participants, dropped on every change."""

    gates: dict[RequestKind, Gates] = field(default_factory=dict)
    claims: tuple[Claiming, ...] | None = None


class Lifecycle:
    """The ordered participants of every page view, with a version plans compare."""

    __slots__ = ("_derived", "_index", "_items", "_version")

    def __init__(self) -> None:
        """Start empty, as a page view before the app is ready runs bare."""
        self._items: list[Participant] = []
        self._index: dict[str, int] = {}
        self._version = 0
        self._derived = _Derived()

    @property
    def version(self) -> int:
        """Return the counter every registration, reset and settings change moves."""
        return self._version

    def __iter__(self) -> Iterator[Participant]:
        """Yield the participants in registry order."""
        return iter(tuple(self._items))

    def named(self, name: str) -> Participant | None:
        """Return the participant registered under `name`, `None` for none."""
        at = self._index.get(name)
        return None if at is None else self._items[at]

    def register(self, participant: Participant) -> None:
        """Add `participant` last, or put it in the place of the one of its name."""
        at = self._index.get(participant.name)
        if at is None:
            self._index[participant.name] = len(self._items)
            self._items.append(participant)
        else:
            self._items[at] = participant
        self._moved()

    def reset(self) -> None:
        """Drop every participant."""
        self._items.clear()
        self._index.clear()
        self._moved()

    def _moved(self) -> None:
        """Move the version and drop what was derived from the old participants."""
        self._version += 1
        self._derived = _Derived()

    def forget(self) -> None:
        """Ask every participant again, as a changed setting may move its shares."""
        self._moved()

    def kind(self, request: HttpRequest) -> RequestKind:
        """Return the kind of a partial `request`, the most specific one claimed."""
        claims = self._derived.claims
        if claims is None:
            found = (item.claim() for item in self._items)
            claims = self._derived.claims = tuple(c for c in found if c is not None)
        best = RequestKind.PAGE
        for claim in claims:
            kind = claim(request)
            if kind is not None and _RANK[kind] < _RANK[best]:
                best = kind
        return best

    def gates(self, kind: RequestKind) -> Gates:
        """Return the gates every participant sets on a request of `kind`."""
        held = self._derived.gates.get(kind)
        if held is None:
            found = [gate for item in self._items if (gate := item.gate(kind))]
            held = Gates(
                admit=tuple(gate.admit for gate in found if gate.admit),
                refuse=tuple(gate.refuse for gate in found if gate.refuse),
                answers=any(gate.answers for gate in found),
            )
            self._derived.gates[kind] = held
        return held

    def points(self, facts: PageFacts) -> Points:
        """Return the points every participant takes in a request `facts` describe."""
        shares = [share for item in self._items if (share := item.share(facts))]
        if not shares:
            return _NO_POINTS
        served = [share.serve for share in shares if share.serve]
        responded = [share.respond for share in shares if share.respond]
        return Points(
            around=tuple(share.around for share in shares if share.around),
            block=tuple(share.block for share in shares if share.block),
            serve=served[-1] if served else None,
            respond=responded[-1] if responded else None,
            finish=tuple(s.finish for s in reversed(shares) if s.finish),
        )


lifecycle: Final = Lifecycle()
"""The participants of every page view, which `NextFrameworkConfig.ready` registers."""

SETTINGS.attach(lifecycle.forget)


def register_participant(participant: Participant) -> None:
    """Add `participant` to every page view, from the `AppConfig.ready` of a project."""
    lifecycle.register(participant)


__all__ = [
    "Gate",
    "Gates",
    "Lifecycle",
    "PageCall",
    "PageFacts",
    "Participant",
    "PendingJobs",
    "Points",
    "ResponseSource",
    "Share",
    "lifecycle",
    "register_participant",
]
