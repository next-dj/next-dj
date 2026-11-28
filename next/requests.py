"""The request vocabulary every layer shares, and `request.next` that carries it.

Foundation holds the namespace so an area of any layer keeps its state in one slot.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, final, overload, override

from django.core.exceptions import ImproperlyConfigured

from next.conf.sentinels import UNSET


if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.utils.functional import SimpleLazyObject

    from next.components.info import ComponentInfo
    from next.conf.sentinels import Unset
    from next.consent.markers import Consent
    from next.defer.state import RequestState
    from next.forms.origin import OriginMatch
    from next.pages.metadata.resolve import ResolvedMetadata
    from next.partial.headers import PartialIntent
    from next.partial.intercept import Grant
    from next.site.config import DeclaredOrigin


class RequestKind(enum.StrEnum):
    """What a request to a next.dj view asks for."""

    PAGE = "page"
    """A full page, a browser document or speculation."""

    ACTION = "action"
    """A form action dispatch."""

    ZONE = "zone"
    """The zones of a page a partial request names."""

    BOUNDARY = "boundary"
    """One boundary a token reloads."""

    NAVIGATE = "navigate"
    """A client router navigation or prefetch."""


class PrefetchDepth(enum.StrEnum):
    """How much of a page a router prefetch asks for, which `request.next.depth` names.

    A shell runs no deferred job, and a full prefetch is the page a press may show.
    """

    SHELL = "shell"
    FULL = "full"


class Purpose(enum.StrEnum):
    """Why a page view rendered, which `request.next.purpose` names."""

    DOCUMENT = "document"
    NAVIGATE = "navigate"
    PREFETCH = "prefetch"
    SPECULATION = "speculation"


@dataclass(frozen=True, slots=True)
class RequestInfo:
    """What next.dj tells user code about the request a page view serves."""

    purpose: Purpose = Purpose.DOCUMENT
    depth: PrefetchDepth | None = None


NEXT_ATTR: Final = "next"
"""The request attribute next.dj keeps its state under, read by user code."""

_INFOS: Final[dict[tuple[Purpose, PrefetchDepth | None], RequestInfo]] = {
    key: RequestInfo(*key)
    for key in (
        (Purpose.DOCUMENT, None),
        (Purpose.NAVIGATE, None),
        (Purpose.SPECULATION, None),
        (Purpose.PREFETCH, PrefetchDepth.SHELL),
        (Purpose.PREFETCH, PrefetchDepth.FULL),
    )
}
_DOCUMENT: Final = _INFOS[Purpose.DOCUMENT, None]


@final
class RequestNext:
    """What next.dj keeps on one request, which user code reads as `request.next`.

    Only `purpose` and `depth` are public, every other attribute is internal.
    """

    __slots__ = (
        "bound_render",
        "boundary_token",
        "component_csrf",
        "component_lookups",
        "consent",
        "cookie_vary",
        "csrf_deferred",
        "csrf_payload",
        "defer",
        "dep_cache",
        "grants",
        "info",
        "intent",
        "metadata",
        "nonce",
        "origin_match",
        "personal_render",
        "response_id",
        "root",
        "router_clear",
        "shared_render",
        "site_declared",
        "site_indexable",
        "site_origin",
        "wizard_loads",
    )

    def __init__(self) -> None:
        """Store every slot once, so no read of an unset one ever raises."""
        self.info: RequestInfo = _DOCUMENT
        self.root: RequestNext | None = None
        self.defer: RequestState | None = None
        self.boundary_token: str | None = None
        self.intent: PartialIntent | None = None
        self.grants: list[Grant] | None = None
        self.csrf_deferred = False
        self.csrf_payload: tuple[str | None, dict[str, str]] | None = None
        self.nonce: str | Unset | None = UNSET
        self.component_csrf: SimpleLazyObject | None = None
        self.component_lookups: dict[tuple[str, str], ComponentInfo] | None = None
        self.origin_match: OriginMatch | Unset | None = UNSET
        self.wizard_loads: dict[str, dict[str, Any]] | None = None
        self.dep_cache: dict[str, Any] | None = None
        self.consent: Consent | None = None
        self.site_origin: tuple[str, str] | None = None
        self.site_declared: DeclaredOrigin | Unset = UNSET
        self.site_indexable: bool | Unset = UNSET
        self.shared_render = False
        self.personal_render = False
        self.bound_render = False
        self.cookie_vary = False
        self.response_id: str | None = None
        self.metadata: ResolvedMetadata | None = None
        self.router_clear: str | None = None

    @property
    def purpose(self) -> Purpose:
        """Return why the page view rendered, a document when no view named one."""
        return self.info.purpose

    @property
    def depth(self) -> PrefetchDepth | None:
        """Return how much of the page a router prefetch asks for."""
        return self.info.depth

    @override
    def __repr__(self) -> str:
        """Show the public attributes only."""
        return f"<RequestNext purpose={self.purpose!r} depth={self.depth!r}>"

    def fork(self) -> RequestNext:
        """Return the namespace of a visit, every slot by value and its root this one's.

        The dependency cache, the boundary token and the origin match start afresh.
        """
        made = RequestNext.__new__(RequestNext)
        for name in RequestNext.__slots__:
            setattr(made, name, getattr(self, name))
        made.dep_cache = None
        made.boundary_token = None
        made.origin_match = UNSET
        made.root = self.root or self
        return made

    def adopt(self, visit: RequestNext) -> None:
        """Take from `visit` what its render set to keep the live response private."""
        if visit.personal_render:
            self.personal_render = True
        if visit.bound_render:
            self.bound_render = True
        if visit.cookie_vary:
            self.cookie_vary = True
        if visit.nonce is not UNSET:
            self.nonce = visit.nonce


def _foreign(held: object) -> ImproperlyConfigured:
    """Return the error for a value under `request.next` that next.dj did not set."""
    return ImproperlyConfigured(
        f"request.{NEXT_ATTR} holds a {type(held).__name__}, but next.dj owns "
        f"request.{NEXT_ATTR} and refuses any other value there. Keep project state "
        "under another attribute name."
    )


def next_of(request: object) -> RequestNext:
    """Return `request.next`, made on the first call whichever thread asks."""
    held = getattr(request, NEXT_ATTR, UNSET)
    if type(held) is RequestNext:
        return held
    if held is not UNSET:
        raise _foreign(held)
    made: object = vars(request).setdefault(NEXT_ATTR, RequestNext())
    if type(made) is not RequestNext:
        raise _foreign(made)
    return made


def held_next(request: object) -> RequestNext | None:
    """Return `request.next`, `None` while no writer has made one yet."""
    held = getattr(request, NEXT_ATTR, UNSET)
    if type(held) is RequestNext:
        return held
    if held is UNSET:
        return None
    raise _foreign(held)


@overload
def mark_purpose(
    request: HttpRequest, purpose: Literal[Purpose.PREFETCH], depth: PrefetchDepth
) -> None: ...
@overload
def mark_purpose(
    request: HttpRequest,
    purpose: Literal[Purpose.DOCUMENT, Purpose.NAVIGATE, Purpose.SPECULATION],
) -> None: ...
def mark_purpose(
    request: HttpRequest, purpose: Purpose, depth: PrefetchDepth | None = None
) -> None:
    """Point `request.next` at the shared info of `purpose` at `depth`.

    Only a prefetch carries a depth, and any other pair is a `KeyError`.
    """
    next_of(request).info = _INFOS[purpose, depth]


def root_of(request: object) -> RequestNext:
    """Return the namespace `request.next` forked from, itself outside a visit."""
    namespace = next_of(request)
    return namespace.root or namespace


def purpose_of(request: object) -> Purpose:
    """Return the purpose `request.next` names, a document when no view set one."""
    held = held_next(request)
    return Purpose.DOCUMENT if held is None else held.info.purpose


def depth_of(request: object) -> PrefetchDepth | None:
    """Return the prefetch depth `request.next` names, `None` for any other request."""
    held = held_next(request)
    return None if held is None else held.info.depth


__all__ = [
    "NEXT_ATTR",
    "PrefetchDepth",
    "Purpose",
    "RequestInfo",
    "RequestKind",
    "RequestNext",
    "depth_of",
    "held_next",
    "mark_purpose",
    "next_of",
    "purpose_of",
    "root_of",
]
