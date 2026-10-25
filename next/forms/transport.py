"""The transports a form action admits and the marks that carry the choice.

An action answers over HTTP alone unless it opts in, since a socket sets no cookie.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Final, Literal, NoReturn

from django.contrib.sessions.backends.base import SessionBase
from django.db import connections
from django.http import HttpResponse

from .errors import SessionRotationError
from .registration import registration_diagnostics


if TYPE_CHECKING:
    from django.http import HttpRequest


type Transport = Literal["http", "any"]

TRANSPORTS: Final[tuple[Transport, ...]] = ("http", "any")
TRANSPORT_ATTR: Final = "data-next-transport"
TRANSPORT_HEADER: Final = "X-Next-Transport"
TRANSPORT_SCOPE_KEY: Final = "next.transport"
ACTION_KIND: Final = "action"
MISDIRECTED: Final = HTTPStatus.MISDIRECTED_REQUEST
ROTATION: Final = "session"
DISPATCHED_SCOPE_KEY: Final = "next.dispatched"
_REFUSED: Final = ("cycle_key", "flush")
_AREFUSED: Final = ("acycle_key", "aflush")


def checked_transport(value: object, owner: str) -> Transport:
    """Return a declared transport, or record an unknown one and fall back to HTTP."""
    for transport in TRANSPORTS:
        if value == transport:
            return transport
    registration_diagnostics.invalid_transport.append((owner, str(value)))
    return "http"


def tunneled(request: "HttpRequest") -> bool:
    """Whether a request reached Django through a tunnel rather than plain HTTP.

    The tunnel marks the ASGI scope it builds, a key no HTTP client can set.
    """
    scope = getattr(request, "scope", None)
    if not isinstance(scope, Mapping):
        return False
    transport: object = scope.get(TRANSPORT_SCOPE_KEY, "http")
    return transport != "http"


@dataclass(slots=True)
class DispatchMark:
    """Whether a tunneled request reached the action dispatch, shared through the scope.

    A reply without it never reached an action, so the client may send it again.
    """

    reached: bool = False


def mark_dispatched(request: "HttpRequest") -> None:
    """Record on the tunnel's mark that the request reached the action dispatch."""
    scope = getattr(request, "scope", None)
    mark = scope.get(DISPATCHED_SCOPE_KEY) if isinstance(scope, Mapping) else None
    if isinstance(mark, DispatchMark):
        mark.reached = True


def dispatches_actions(view: object) -> bool:
    """Whether a resolved view is the form action dispatch, the one tunnel target."""
    return getattr(view, "next_access_kind", None) == ACTION_KIND


def http_only() -> HttpResponse:
    """Refuse a tunneled request to an action that answers over HTTP only.

    Nothing ran, so the client may send the same request again over HTTP.
    """
    response = HttpResponse(status=MISDIRECTED)
    response[TRANSPORT_HEADER] = "http"
    return response


class SessionPin:
    """The refusals a pinned session runs in place of a rotation or a flush."""

    __slots__ = ("rotated",)

    def __init__(self) -> None:
        """Start with no refusal recorded."""
        self.rotated = False

    def refuse(self) -> NoReturn:
        """Record the attempt and raise before the store writes."""
        self.rotated = True
        raise SessionRotationError

    async def arefuse(self) -> NoReturn:
        """Record the attempt and raise, for the async methods of a store."""
        self.refuse()


def _pin_of(session: object) -> SessionPin | None:
    """Return the pin a session already carries, None for an unpinned one."""
    shadow = getattr(session, "__dict__", {}).get(_REFUSED[0])
    owner = getattr(shadow, "__self__", None)
    return owner if isinstance(owner, SessionPin) else None


def pin_session(request: "HttpRequest") -> SessionPin:
    """Make the session of a request raise on a rotation or a flush, before it writes.

    The refusals shadow the methods on the instance, so the class keeps its salt.
    """
    session = getattr(request, "session", None)
    current = _pin_of(session)
    if current is not None:
        return current
    pin = SessionPin()
    if isinstance(session, SessionBase):
        shadows = vars(session)
        shadows.update(dict.fromkeys(_REFUSED, pin.refuse))
        shadows.update(dict.fromkeys(_AREFUSED, pin.arefuse))
    return pin


def roll_back(request: "HttpRequest") -> None:
    """Mark the `ATOMIC_REQUESTS` transaction of the view for rollback, like a raise."""
    view = getattr(request.resolver_match, "func", None)
    exempt = getattr(view, "_non_atomic_requests", ())
    for connection in connections.all(initialized_only=True):
        if (
            connection.settings_dict["ATOMIC_REQUESTS"]
            and connection.alias not in exempt
            and connection.in_atomic_block
        ):
            connection.set_rollback(True)


def rotation_refused() -> HttpResponse:
    """Answer a tunneled action that rotated the session, so the browser reloads."""
    response = HttpResponse(status=MISDIRECTED)
    response[TRANSPORT_HEADER] = ROTATION
    return response


__all__ = [
    "ACTION_KIND",
    "DISPATCHED_SCOPE_KEY",
    "MISDIRECTED",
    "ROTATION",
    "TRANSPORTS",
    "TRANSPORT_ATTR",
    "TRANSPORT_HEADER",
    "TRANSPORT_SCOPE_KEY",
    "DispatchMark",
    "SessionPin",
    "Transport",
    "checked_transport",
    "dispatches_actions",
    "http_only",
    "mark_dispatched",
    "pin_session",
    "roll_back",
    "rotation_refused",
    "tunneled",
]
