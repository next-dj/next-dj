"""Zone branch of the unified page view and its status short-circuits."""

from typing import TYPE_CHECKING, Any

from django.core.exceptions import PermissionDenied
from django.core.handlers.exception import response_for_exception
from django.http import Http404, HttpResponse
from django.http.response import HttpResponseBase

from next.intercept import Intercept
from next.pages.manager import page
from next.pages.responses import never_store, zone_policy
from next.static.runtime import html_finish

from . import keys
from .envelope import Envelope
from .errors import UnknownZoneError
from .headers import (
    ORIGIN,
    STREAM_VARY,
    VARY_HEADERS,
    MergeMode,
    set_partial_vary,
    vary_on,
)
from .intercept import admit, pick_intercept
from .manager import asset_version, partial_backend_manager
from .patches import Patches, PatchResponse
from .render import render_plain_zones, render_zone
from .signals import intercept_access_denied
from .stream import PROTOCOL, streamable


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from django.http import HttpRequest
    from django.template import Template

    from next.pages.boundaries import Caught

    from .headers import PartialIntent
    from .render import ZoneRenderResult
    from .stream import Deferred


_SAFE_METHODS = frozenset({"GET", "HEAD"})
_BAD_REQUEST = 400
_VERSION_CONFLICT = 409
_PROTOCOL = str(PROTOCOL)
_VARY = {
    (False, False): VARY_HEADERS,
    (True, False): (*VARY_HEADERS, *STREAM_VARY),
    (False, True): (*VARY_HEADERS, ORIGIN),
    (True, True): (*VARY_HEADERS, *STREAM_VARY, ORIGIN),
}
_SHORT_VARY = {False: VARY_HEADERS, True: (*VARY_HEADERS, ORIGIN)}


def zone_response(
    page_path: "Path",
    intent: "PartialIntent",
    request: "HttpRequest",
    *,
    dynamic_body: bool,
    url_kwargs: dict[str, object],
    dep_cache: dict[str, Any],
    caught: "Caught | None" = None,
) -> HttpResponseBase:
    """Build the partial response for a zone GET, or a refusal or 400/409 short-circuit.

    An intercept serving the zones refuses on its own rule before any status check.
    """
    served = _served_intercept(page_path, intent, request)
    if served is not None:
        denial = _intercept_refusal(served, page_path, request, url_kwargs, dep_cache)
        if denial is not None:
            set_partial_vary(denial)
            return denial
    elif dynamic_body:
        return _bad_request("zone in dynamic body")
    version = asset_version()
    if request.method in _SAFE_METHODS and _version_conflict(intent, version):
        return _conflict()
    backend = partial_backend_manager.get()
    with streamable(request) as block:
        try:
            result = render_zone(
                page_path if served is None else served,
                intent.zones,
                request,
                url_kwargs=url_kwargs,
                caught=caught,
                stashed=served is not None,
            )
        except UnknownZoneError:
            return _bad_request("unknown zone")
    pending = None if block is None else block.deferred
    try:
        envelope = _build_envelope(
            result, intent, version, request, served=served, url_kwargs=url_kwargs
        )
        if pending is not None:
            return _streamed(pending, envelope, page_path)
    finally:
        if pending is not None:
            pending.release()
    body = backend.serialize_envelope(envelope)
    vary = _VARY[request.method in _SAFE_METHODS, html_finish.get() is not None]
    return PatchResponse(
        body, content_type=backend.content_type, version=version, vary=vary
    )


def plain_zone_response(
    page_path: "Path",
    intent: "PartialIntent",
    request: "HttpRequest",
    url_kwargs: dict[str, object],
    template: "Template",
    *,
    origin: bool,
) -> HttpResponseBase:
    """Answer the safe zone request of a page no intercept serves and nothing guards.

    Its zones never stream, so `template` renders straight to an envelope, and
    `origin` says whether every answer varies on `X-Next-Origin` for the link pass.
    """
    vary = _SHORT_VARY[origin]
    version = asset_version()
    if _version_conflict(intent, version):
        return _conflict(vary)
    try:
        result = render_plain_zones(
            page_path, template, intent.zones, request, url_kwargs
        )
    except UnknownZoneError:
        return _bad_request("unknown zone", vary)
    envelope = _build_envelope(
        result, intent, version, request, served=None, url_kwargs=url_kwargs
    )
    backend = partial_backend_manager.get()
    return PatchResponse(
        backend.serialize_envelope(envelope),
        content_type=backend.content_type,
        version=version,
        vary=_VARY[True, origin],
    )


def _streamed(
    pending: "Deferred", envelope: Envelope, page_path: "Path"
) -> HttpResponseBase:
    """Return the stream of what the zone render left, plain when the page opts out."""
    compress = zone_policy(page, page_path).compress
    return pending.respond(envelope, page_path=page_path, compress=compress)


def _served_intercept(
    page_path: "Path", intent: "PartialIntent", request: "HttpRequest"
) -> "Path | None":
    """Return the intercept page serving a zone GET of `page_path`, None for the page.

    A POST naming zones keeps the page, so a mutation never reaches an intercept.
    """
    if request.method not in _SAFE_METHODS:
        return None
    rule = pick_intercept(page_path, intent.zones)
    return None if rule is None else rule.file


def _intercept_refusal(
    served: "Path",
    target: "Path",
    request: "HttpRequest",
    url_kwargs: dict[str, object],
    dep_cache: dict[str, Any],
) -> HttpResponse | None:
    """Return the response the intercept rule refuses `request` with, None to serve.

    The denial depends on the zone, so it is rendered here to be never stored.
    """
    denial: HttpResponse | None
    reason = "raised"
    try:
        denial = admit(served, request, url_kwargs, dep_cache)
        reason = "redirect"
    except (PermissionDenied, Http404) as exc:
        denial = response_for_exception(request, exc)
    if denial is None:
        return None
    never_store(denial)
    _announce_denial(served, target, request, denial.status_code, reason)
    return denial


def _announce_denial(
    served: "Path", target: "Path", request: "HttpRequest", status: int, reason: str
) -> None:
    """Send `intercept_access_denied` when a receiver listens for it."""
    if intercept_access_denied.has_listeners(Intercept):
        intercept_access_denied.send(
            sender=Intercept,
            file_path=served,
            target_file=target,
            request=request,
            status=status,
            reason=reason,
        )


def _version_conflict(intent: "PartialIntent", version: str) -> bool:
    """Return True when the client asserts an asset version or a wire protocol it lacks.

    The runtime sends both from its first request, a client without one asserts nothing.
    """
    if intent.protocol is not None and intent.protocol != _PROTOCOL:
        return True
    return bool(intent.version) and intent.version != version


def _build_envelope(
    result: "ZoneRenderResult",
    intent: "PartialIntent",
    version: str,
    request: "HttpRequest",
    *,
    served: "Path | None",
    url_kwargs: dict[str, object],
) -> Envelope:
    """Assemble one envelope patching every rendered zone with its assets.

    The verb is server-authored from the intent, and a serving intercept adds its head.
    """
    patches = Patches.versioned(version, request=request)
    marks = html_finish.get()
    for name in result.html:
        _patch_zone(patches, name, result, intent.merge, marks)
    patches.absorb_zone_result(result)
    if served is not None:
        patches.page_meta(served, url_kwargs, result.context)
    return patches.envelope()


def _patch_zone(
    patches: Patches,
    name: str,
    result: "ZoneRenderResult",
    merge: "MergeMode | None",
    marks: "Callable[[str], str] | None" = None,
) -> None:
    """Patch one zone in place, morphing it or merging deduplicated children.

    Append and prepend graft the bare inner body to avoid nesting a second wrapper.
    A failed zone morphs, since its error UI stands for the whole body.
    """
    target = {keys.ZONE: name}
    grafted = None if result.failed else merge
    html = (result.html if grafted is None else result.bodies)[name]
    if marks is not None:
        html = marks(html)
    if grafted is MergeMode.APPEND:
        patches.append(target, html)
    elif grafted is MergeMode.PREPEND:
        patches.prepend(target, html)
    else:
        patches.morph(target, html)


def _bad_request(reason: str, vary: tuple[str, ...] = VARY_HEADERS) -> HttpResponse:
    """Return a 400 with a plain reason, varied on the partial headers or on `vary`."""
    response = HttpResponse(reason, status=_BAD_REQUEST, content_type="text/plain")
    vary_on(response, vary)
    return response


def _conflict(vary: tuple[str, ...] = VARY_HEADERS) -> HttpResponse:
    """Return an empty-bodied 409, varied on the partial headers or on `vary`."""
    response = HttpResponse(b"", status=_VERSION_CONFLICT)
    vary_on(response, vary)
    return response


__all__ = ["plain_zone_response", "zone_response"]
