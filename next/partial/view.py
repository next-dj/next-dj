"""Zone branch of the unified page view and its status short-circuits."""

from typing import TYPE_CHECKING

from django.http import HttpResponse

from next.static.collector import default_placeholders

from .backends import partial_backend_manager
from .headers import set_partial_vary
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
    version = backend.version()
    if dynamic_body:
        return _bad_request("zone in dynamic body")
    if request.method in _SAFE_METHODS and _version_conflict(intent, version):
        return _conflict()
    try:
        result = render_zone(page_path, intent.zones, request, url_kwargs=url_kwargs)
    except UnknownZoneError as exc:
        return _bad_request(str(exc))
    envelope = _build_envelope(result, intent.zones, version)
    body = backend.serialize_envelope(envelope)
    return PatchResponse(body, content_type=backend.content_type, version=version)


def _version_conflict(intent: "PartialIntent", version: str) -> bool:
    """Return True when the client version is set and differs from the current."""
    return intent.version is not None and intent.version != version


def _build_envelope(
    result: "ZoneRenderResult",
    zone_names: tuple[str, ...],
    version: str,
) -> Envelope:
    """Assemble one envelope morphing every rendered zone with its assets."""
    patches = Patches(version)
    for name in zone_names:
        patches.morph({"zone": name}, result.html[name])
    for asset in _collected_assets(result):
        patches.add_asset(asset.kind, asset.url)
    return patches.envelope()


def _collected_assets(result: "ZoneRenderResult") -> list[Asset]:
    """Return the URL-form assets the rendered zone bodies collected."""
    return [
        Asset(kind=static_asset.kind, url=static_asset.url)
        for slot in default_placeholders
        for static_asset in result.collector.assets_in_slot(slot.name)
        if static_asset.url
    ]


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
