"""Zone branch of the unified page view and its status short-circuits."""

from typing import TYPE_CHECKING

from django.http import HttpResponse

from . import keys
from .headers import MergeMode, set_partial_vary
from .manager import partial_backend_manager
from .patches import Asset, Envelope, Patches, PatchResponse
from .render import UnknownZoneError, render_zone


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
) -> HttpResponse:
    """Build the partial response for a zone GET, or a 400/409 short-circuit.

    A zone named on a dynamic body has no compiled source to render, so
    the response is a 400 before any render. A version mismatch on a safe
    method is a 409 with an empty body. An unknown zone is a 400 raised
    before any render. Otherwise the named zones render in one batch and
    travel back as one patch envelope.
    """
    backend = partial_backend_manager.get()
    version = partial_backend_manager.version()
    if dynamic_body:
        return _bad_request("zone in dynamic body")
    if request.method in _SAFE_METHODS and _version_conflict(intent, version):
        return _conflict()
    try:
        result = render_zone(page_path, intent.zones, request, url_kwargs=url_kwargs)
    except UnknownZoneError:
        return _bad_request("unknown zone")
    envelope = _build_envelope(result, intent, version)
    body = backend.serialize_envelope(envelope)
    return PatchResponse(body, content_type=backend.content_type, version=version)


def _version_conflict(intent: "PartialIntent", version: str) -> bool:
    """Return True when the client asserts a version that differs from the current.

    An absent or empty client version asserts nothing, so the first partial
    request of a page, made before the client has learned a version, never
    conflicts.
    """
    return bool(intent.version) and intent.version != version


def _build_envelope(
    result: "ZoneRenderResult",
    intent: "PartialIntent",
    version: str,
) -> Envelope:
    """Assemble one envelope patching every rendered zone with its assets.

    Without a merge intent each zone morphs in place. With an `append` or
    `prepend` merge intent each zone is patched with the matching merge
    verb instead, so a paginating request grows the zone with deduplicated
    children rather than replacing its body. The verb is server-authored
    from the parsed intent, the client never names it.
    """
    patches = Patches(version)
    for name in intent.zones:
        _patch_zone(patches, name, result.html[name], intent.merge)
    for asset in _collected_assets(result):
        patches.add_asset(asset.kind, asset.url)
    return patches.envelope()


def _patch_zone(
    patches: Patches,
    name: str,
    html: str,
    merge: "MergeMode | None",
) -> None:
    """Patch one zone in place, morphing it or merging deduplicated children."""
    target = {keys.ZONE: name}
    if merge is MergeMode.APPEND:
        patches.append(target, html)
    elif merge is MergeMode.PREPEND:
        patches.prepend(target, html)
    else:
        patches.morph(target, html)


def _collected_assets(result: "ZoneRenderResult") -> list[Asset]:
    """Return the URL-form assets the rendered zone bodies collected."""
    return [Asset(kind=kind, url=url) for kind, url in result.url_assets()]


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
