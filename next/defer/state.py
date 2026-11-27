"""The state the deferred boundaries of one request share, kept on `request.next`."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from next.pages.responses import next_of


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from next.identity import SubjectBinding

    from .assembly.partials import Decision
    from .ledger import ResponseJobs
    from .probe import ServerFacts
    from .render import Processed
    from .tokens.backends import IssuedSubject


_lock = threading.Lock()


class RequestState:
    """What the deferred boundaries of one request share, from its view to its stream.

    The request thread sets the plain fields, a job thread reaches the two lazy ones.
    """

    __slots__ = (
        "_held",
        "_processed",
        "decision",
        "issued",
        "noscript",
        "probe",
        "route",
        "tripped",
    )

    def __init__(self) -> None:
        """Hold nothing until a reader of the request asks."""
        self.probe: ServerFacts | None = None
        self.decision: Decision | None = None
        self.route: Mapping[str, object] | None = None
        self.noscript: str | None = None
        self.tripped = False
        self.issued: dict[SubjectBinding, IssuedSubject] = {}
        self._held: ResponseJobs | None = None
        self._processed: Processed | None = None

    def held(self, opened: Callable[[], ResponseJobs]) -> ResponseJobs:
        """Return the session a render outside a render session counts wrappers on."""
        held = self._held
        if held is None:
            with _lock:
                held = self._held
                if held is None:
                    held = self._held = opened()
        return held

    def processed(self, made: Callable[[], Processed]) -> Processed:
        """Return the context processor results the jobs and error UIs share."""
        held = self._processed
        if held is None:
            with _lock:
                held = self._processed
                if held is None:
                    held = self._processed = made()
        return held


def state_of(request: object) -> RequestState:
    """Return the boundary state of `request`, made once whichever thread asks first."""
    root = next_of(request)
    held = root.boundaries
    if isinstance(held, RequestState):
        return held
    with _lock:
        held = root.boundaries
        if not isinstance(held, RequestState):
            held = root.boundaries = RequestState()
    return held


__all__ = ["RequestState", "state_of"]
