"""The envelopes a navigation answers with, and the responses passed in their place."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final
from urllib.parse import urljoin

from django.conf import settings

from next.csrf import csrf_payload, secret_changed
from next.pages.loaders import layout_chain, template_source
from next.pages.metadata.resolve import published_metadata
from next.pages.paths import page_template_path
from next.pages.responses import never_store
from next.partial import keys
from next.partial.errors import CrossSiteHrefError
from next.partial.headers import set_partial_vary
from next.partial.manager import partial_backend_manager
from next.partial.patches import Patches, PatchResponse
from next.router.digest import chrome_zones, zone_html
from next.router.markers import CutKind
from next.static.nonce import resolve_nonce
from next.static.runtime import (
    CONSENT_PAYLOAD_KEY,
    RESERVED_PAYLOAD_KEYS,
    SCRIPTS_PAYLOAD_KEY,
)

from .clearing import clearing, stamp
from .intent import origin_path, target_href
from .shell import layouts_payload, swapped


if TYPE_CHECKING:
    from pathlib import Path

    from django.http import HttpRequest, HttpResponse
    from django.http.response import HttpResponseBase

    from next.router.markers import Cut, ScannedLayout, ScannedPage
    from next.static.collector import StaticCollector

    from .state import Routing


_REDIRECTS: Final = range(300, 400)


def _assets(patches: Patches, collector: StaticCollector | None) -> None:
    """Record the assets of the whole page, which the client registry deduplicates."""
    if collector is not None:
        patches.page_assets(collector)


def _context(collector: StaticCollector | None) -> dict[str, object]:
    """Return the client context of the page without the framework payload keys."""
    if collector is None:
        return {}
    return {
        name: value
        for name, value in collector.js_context_wire().items()
        if name not in RESERVED_PAYLOAD_KEYS
    }


def _chrome_zones(patches: Patches, shell: ScannedPage, layout: ScannedLayout) -> None:
    """Morph every zone in the kept chrome, a DOM no-op for one that did not change."""
    html = shell.html
    for name, bounds in chrome_zones(html, shell.response_id, layout.slot):
        patches.morph({keys.ZONE: name}, zone_html(html, bounds, shell.response_id))


def _dropped(page: Path, script: str) -> dict[str, str | None]:
    """Return the script that sent the page to load and the template that holds it.

    Of the page template and its layouts, the first whose source holds the tag counts.
    """
    sources = (page_template_path(page), *layout_chain(page))
    held = (path for path in sources if script in (template_source(path) or ""))
    found = next(held, None)
    return {"script": script, "template": None if found is None else str(found)}


def navigation_envelope(
    request: HttpRequest,
    routed: Routing,
    shell: ScannedPage,
    cut: Cut,
    *,
    shared: bool,
    complete: bool,
) -> Patches:
    """Build the envelope of one navigation through `Patches`.

    `complete` marks a prefetch that the navigation after it applies without a refetch.
    """
    patches = Patches(request)
    href = target_href(request)
    kept_count = len(cut.kept)
    fields: dict[str, object] = {
        keys.LAYOUTS: layouts_payload(shell, kept_count),
        keys.ROOT: shell.root,
    }
    if cut.kind is CutKind.VISIT or (cut.kind is CutKind.BODY and shell.body is None):
        patches.redirect(href)
        if cut.script is not None and settings.DEBUG:
            fields[keys.DROPPED] = _dropped(routed.file_path, cut.script)
        return patches.navigation(fields)
    if cut.kind is CutKind.SLOT:
        layout = shell.layouts[kept_count - 1]
        patches.morph({keys.SLOT: layout.ident}, swapped(shell, cut))
        _chrome_zones(patches, shell, layout)
    else:
        patches.morph({keys.BODY: True}, swapped(shell, cut))
    if origin_path(request) == href:
        patches.replace_url(href)
    else:
        patches.push_url(href)
    resolved = published_metadata(request)
    if resolved is not None:
        patches.head(resolved)
    collector = routed.render.collector
    patches.replace_context(_context(collector))
    _assets(patches, collector)
    if secret_changed(request):
        patches.set_csrf(csrf_payload(request))
    payload = routed.render.payload
    fields[keys.NONCE] = resolve_nonce(request)
    fields[keys.SCRIPTS] = payload.get(SCRIPTS_PAYLOAD_KEY)
    fields[keys.CONSENT] = payload.get(CONSENT_PAYLOAD_KEY)
    fields[keys.SHARED] = shared
    if complete:
        fields[keys.COMPLETE] = True
    return patches.navigation(fields)


def visit_envelope(request: HttpRequest) -> Patches:
    """Build the envelope that answers a navigation with a hard load of its URL.

    A path opening with `//` loads as the single-slash path, never as another host.
    """
    return Patches(request).redirect(target_href(request))


def answered(patches: Patches) -> HttpResponse:
    """Return the envelope response of a navigation, never stored."""
    backend = partial_backend_manager.get()
    body = backend.serialize_envelope(patches.envelope())
    return PatchResponse(
        body, content_type=backend.content_type, version=patches.version
    )


def redirects(response: HttpResponseBase) -> bool:
    """Whether `response` is a redirect with a `Location` the router's fetch follows."""
    return response.status_code in _REDIRECTS and response.has_header("Location")


def _hidden(response: HttpResponseBase, request: HttpRequest) -> bool:
    """Whether `response` redirects past a clear or a CSRF token the client must see."""
    if not redirects(response):
        return False
    return clearing(request) or secret_changed(request)


def _visit(response: HttpResponseBase, request: HttpRequest) -> HttpResponse:
    """Return the envelope that loads the target of `response`, with its cookies."""
    href = urljoin(target_href(request), response["Location"])
    patches = Patches(request)
    try:
        patches.redirect(href)
    except CrossSiteHrefError:
        patches.redirect(href, external=True)
    sent = answered(patches)
    sent.cookies.update(response.cookies)
    return sent


def passed(response: HttpResponseBase, request: HttpRequest) -> HttpResponseBase:
    """Send a response the page answered in place of its render, private and stamped.

    The router's fetch follows a redirect unseen, so one that must clear rides a visit.
    """
    if _hidden(response, request):
        return stamp(_visit(response, request), request)
    never_store(response)
    set_partial_vary(response)
    return stamp(response, request)


__all__ = ["answered", "navigation_envelope", "passed", "redirects", "visit_envelope"]
