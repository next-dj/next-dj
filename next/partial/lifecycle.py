"""The part the partial protocol takes in the requests a page answers.

It claims a zone request and serves its envelope, opens the stream block a navigation
fills, and turns off the layer pass of a document whose templates spell no layer link.
"""

from __future__ import annotations

from contextlib import nullcontext
from functools import partial
from typing import TYPE_CHECKING, Final

from next.intercept import intercepts
from next.lifecycle import Share
from next.pages.boundaries import Caught
from next.pages.manager.bodies import BodyResolution
from next.pages.responses import depth_of
from next.requests import PrefetchDepth, RequestKind

from .headers import partial_intent
from .layers import layer_links
from .stream import Streamable, streamable
from .view import plain_zone_response, zone_response


if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from contextvars import Token
    from types import TracebackType

    from django.http import HttpRequest
    from django.http.response import HttpResponseBase
    from django.template import Template

    from next.lifecycle import Claiming, Gate, PageCall, PageFacts


NAME: Final = "partial"
_CLOSED: Final[AbstractContextManager[None]] = nullcontext()


def stream_of(call: PageCall) -> Streamable | None:
    """Return the stream block a navigation render left, `None` when none opened."""
    held = call.held.get(NAME)
    return held if isinstance(held, Streamable) else None


class _Streaming:
    """The stream block of one navigation, kept on its call for the router to read."""

    __slots__ = ("_call", "_inner")

    def __init__(self, call: PageCall) -> None:
        """Open no block for a shell-deep prefetch, which never streams."""
        self._call = call
        shallow = depth_of(call.request) is PrefetchDepth.SHELL
        self._inner = _CLOSED if shallow else streamable(call.request)

    def __enter__(self) -> None:
        """Enter the stream block and keep it on the call."""
        self._call.held[NAME] = self._inner.__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Leave the stream block."""
        return self._inner.__exit__(exc_type, exc, traceback)


class _Unlayered:
    """The block turning the layer pass off for a render that spells no layer link."""

    __slots__ = ("_token",)

    def __init__(self, _call: PageCall) -> None:
        """Hold nothing until the render opens the block."""

    def __enter__(self) -> None:
        """Turn the pass off for the render inside."""
        self._token: Token[bool] = layer_links.set(False)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Turn it back, whatever the render did."""
        layer_links.reset(self._token)


def _zone(
    template: Template | None, call: PageCall, *, links: bool
) -> HttpResponseBase:
    """Serve the zones a request names, straight from `template` when nothing guards.

    A body `render()` resolved, an intercept or a page boundary takes the full path.
    """
    request, file_path = call.request, call.file_path
    intent = partial_intent(request)
    if template is not None and not intercepts.of(file_path):
        return plain_zone_response(
            file_path, intent, request, dict(call.kwargs), template, origin=links
        )
    body = call.body
    return zone_response(
        file_path,
        intent,
        request,
        dynamic_body=isinstance(body, BodyResolution) and body.dynamic,
        url_kwargs=dict(call.kwargs),
        dep_cache=call.dep_cache,
        caught=body if isinstance(body, Caught) else None,
    )


def _claimed(request: HttpRequest) -> RequestKind | None:
    """Return `ZONE` for a partial request that names zones."""
    return RequestKind.ZONE if partial_intent(request).zones else None


class PartialParticipant:
    """The partial protocol's part in the requests a page answers."""

    name = NAME

    def claim(self) -> Claiming:
        """Return the claim of a zone request."""
        return _claimed

    def gate(self, _kind: RequestKind) -> Gate | None:
        """Return no gate, the zone gate of a prefetch is the router's."""
        return None

    def share(self, facts: PageFacts) -> Share | None:
        """Return the zone serve, the navigation stream block or the layer block."""
        kind = facts.kind
        if kind is RequestKind.ZONE:
            plain = facts.template if facts.plain else None
            return Share(serve=partial(_zone, plain, links=facts.links))
        if kind is RequestKind.NAVIGATE:
            return Share(block=_Streaming)
        if kind is RequestKind.PAGE and facts.plain and not facts.layers:
            return Share(block=_Unlayered)
        return None


__all__ = ["NAME", "PartialParticipant", "stream_of"]
