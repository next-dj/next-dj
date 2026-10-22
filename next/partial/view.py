"""Zone branch of the unified page view and its status short-circuits."""

from typing import TYPE_CHECKING, Any

from django.core.exceptions import PermissionDenied
from django.core.handlers.exception import response_for_exception
from django.http import Http404, HttpResponse

from next.intercept import Intercept
from next.pages.responses import never_store

from . import keys
from .envelope import Envelope
from .errors import UnknownZoneError
from .headers import MergeMode, set_partial_vary
from .intercept import admit, pick_intercept
from .manager import asset_version, partial_backend_manager
from .patches import Patches, PatchResponse
from .render import render_zone
from .signals import intercept_access_denied


if TYPE_CHECKING:
    from pathlib import Path

    from django.http import HttpRequest

    from .headers import PartialIntent
    from .render import ZoneRenderResult


_SAFE_METHODS = frozenset({"GET", "HEAD"})
_BAD_REQUEST = 400
_VERSION_CONFLICT = 409


def zone_response(
    page_path: "Path",
    intent: "PartialIntent",
    request: "HttpRequest",
    *,
    dynamic_body: bool,
    url_kwargs: dict[str, object],
    dep_cache: dict[str, Any],
) -> HttpResponse:
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
    try:
        result = render_zone(
            page_path if served is None else served,
            intent.zones,
            request,
            url_kwargs=url_kwargs,
        )
    except UnknownZoneError:
        return _bad_request("unknown zone")
    envelope = _build_envelope(
        result, intent, version, request, served=served, url_kwargs=url_kwargs
    )
    body = backend.serialize_envelope(envelope)
    return PatchResponse(body, content_type=backend.content_type, version=version)


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
    """Return True when the client asserts a version that differs from the current.

    An absent or empty client version asserts nothing, so a first request is safe.
    """
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
    for name in result.html:
        _patch_zone(patches, name, result, intent.merge)
    patches.absorb_zone_result(result)
    if served is not None:
        patches._page_meta(served, url_kwargs, result.context)
    return patches.envelope()


def _patch_zone(
    patches: Patches, name: str, result: "ZoneRenderResult", merge: "MergeMode | None"
) -> None:
    """Patch one zone in place, morphing it or merging deduplicated children.

    Append and prepend graft the bare inner body to avoid nesting a second wrapper.
    """
    target = {keys.ZONE: name}
    if merge is MergeMode.APPEND:
        patches.append(target, result.bodies[name])
    elif merge is MergeMode.PREPEND:
        patches.prepend(target, result.bodies[name])
    else:
        patches.morph(target, result.html[name])


def _bad_request(reason: str) -> HttpResponse:
    """Return a 400 with a plain reason and the partial Vary headers."""
    response = HttpResponse(reason, status=_BAD_REQUEST, content_type="text/plain")
    set_partial_vary(response)
    return response


def _conflict() -> HttpResponse:
    """Return an empty-bodied 409 with the partial Vary headers."""
    response = HttpResponse(b"", status=_VERSION_CONFLICT)
    set_partial_vary(response)
    return response


__all__ = ["zone_response"]
