"""Declaration parsing, the read of a rendered page and the navigation envelope.

A navigation renders the page as a document does, then answers only what changed.
"""

from __future__ import annotations

import json
import logging
import math
import re
from contextlib import contextmanager
from functools import partial
from typing import TYPE_CHECKING, Final
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.sessions.backends.signed_cookies import SessionStore as SignedStore
from django.http import HttpResponse

from next.conf.defer import defer_seconds
from next.conf.router import LINKS, router_enabled, router_settings
from next.csrf import csrf_payload, secret_changed
from next.forms.uid import posted_origin_path, validated_origin_path
from next.intercept import intercepts
from next.pages.layouts import mint_seal
from next.pages.loaders import layout_chain, layout_ids, template_source
from next.pages.metadata.resolve import published_metadata
from next.pages.navigation import page_router
from next.pages.placeholder import placeholder_spans
from next.pages.responses import depth_of, never_store, purpose_of
from next.partial import keys
from next.partial.errors import CrossSiteHrefError
from next.partial.headers import (
    NAVIGATE,
    ORIGIN,
    ROUTER,
    partial_intent,
    set_partial_vary,
    vary_on,
)
from next.partial.manager import asset_version, partial_backend_manager
from next.partial.patches import Patches, PatchResponse
from next.partial.registry import zones_served
from next.static.nonce import resolve_nonce
from next.static.runtime import (
    CONSENT_PAYLOAD_KEY,
    RESERVED_PAYLOAD_KEYS,
    SCRIPTS_PAYLOAD_KEY,
    RouterRender,
    html_finish,
    router_render,
)
from next.suspense.manager import suspense_manager

from .current import current_links, origin_of
from .digest import body_bounds, chrome, chrome_zones, find_slot, split_head, zone_html
from .manager import router_manager
from .markers import (
    Cut,
    CutKind,
    Declaration,
    Divergence,
    LayoutKey,
    ScannedLayout,
    Shell,
)
from .signals import navigation_rendered, prefetch_shed, router_cleared


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path
    from types import TracebackType

    from django.http import HttpRequest
    from django.http.response import HttpResponseBase

    from next.pages.responses import Purpose
    from next.static.collector import StaticCollector

    from .backends import ChromeDigest
    from .current import CurrentLinks


DECLARATION_BYTES: Final = 1024
"""The size past which a layout declaration is ignored, as one never sent whole."""

CLEAR: Final = "clear"
"""The `X-Next-Router` directive that flushes every prefetch and snapshot."""

NO_SNAPSHOT: Final = "no-snapshot"
"""The `X-Next-Router` directive that keeps a page out of history snapshots."""

_KEY: Final = re.compile(r"([0-9a-f]{16})\.([0-9a-f]{16}|[0-9a-f]{32})")
_CLEAR_ATTR: Final = "_next_router_clear"
_PREFETCH_REFUSED: Final = 204
_SHED: Final = 429
_CONFLICT: Final = 409
_REDIRECTS: Final = range(300, 400)
_ORIGIN_VARY: Final = (ORIGIN,)

logger = logging.getLogger("next.router")

_noted: set[str] = set()


def parse_declaration(raw: str | None, limit: int) -> Declaration | None:
    """Return the layouts the client declares, `None` for an absent or unusable value.

    A value past `limit` entries or `DECLARATION_BYTES` counts as no declaration.
    """
    if raw is None or len(raw.encode()) > DECLARATION_BYTES:
        return None
    if not raw.strip():
        return Declaration(())
    parts = raw.split(",")
    if len(parts) > limit:
        return None
    found: list[LayoutKey] = []
    for part in parts:
        match = _KEY.fullmatch(part.strip())
        if match is None:
            return None
        found.append(LayoutKey(match[1], match[2]))
    return Declaration(tuple(found))


def _digests(
    text: str, seal: str, backend: ChromeDigest, *, root: bool
) -> tuple[str, str | None]:
    """Return the digest of one chrome and, for a root opening a document, its head."""
    normal = backend.normalise(text, seal)
    parts = split_head(normal) if root else None
    if parts is None:
        return backend.digest(normal), None
    head = backend.digest(parts[0])
    return head + backend.digest(parts[1]), head


def read_shell(
    html: str, seal: str, ids: Sequence[str], backend: ChromeDigest | None = None
) -> Shell:
    """Return the layouts of `ids` that `html` holds, nested outer first, digested.

    Without `backend` only the bounds are read. A missing slot ends the chain there.
    """
    layouts: list[ScannedLayout] = []
    region = (0, len(html))
    for ident in ids:
        found = find_slot(html, ident, seal, region)
        if found is None:
            break
        inner, outer = found
        digest, head = "", None
        if backend is not None:
            text = chrome(html, region, outer)
            digest, head = _digests(text, seal, backend, root=not layouts)
        layouts.append(ScannedLayout(ident, digest, inner, outer, head))
        region = inner
    return Shell(html, seal, tuple(layouts), body=body_bounds(html))


def _relocated(shell: Shell, html: str) -> Shell:
    """Return `shell` with the bounds of its layouts read again from `html`."""
    moved = read_shell(html, shell.seal, [layout.ident for layout in shell.layouts])
    layouts = tuple(
        ScannedLayout(old.ident, old.digest, new.slot, new.outer, old.head)
        for old, new in zip(shell.layouts, moved.layouts, strict=False)
    )
    return Shell(html, shell.seal, layouts, body=moved.body)


def layouts_payload(shell: Shell, kept: int = 0) -> list[list[str | None]]:
    """Return `[id, digest, seal]` per layout, the seal null for the `kept` ones."""
    return [
        [layout.ident, layout.digest, None if index < kept else shell.seal]
        for index, layout in enumerate(shell.layouts)
    ]


def kept(html: str, request: HttpRequest) -> str:
    """Return `html` with its current links marked, once its digests are taken."""
    links = current_links()
    if links is None or not links.holds(html):
        return html
    return links.mark(html, request.get_full_path(), origin_of(request))


class Routing:
    """The client router state of one page view, from its render to its response."""

    __slots__ = ("file_path", "html", "purpose", "render", "request", "shell")

    def __init__(self, request: HttpRequest, file_path: Path, purpose: Purpose) -> None:
        """Mint the seal of the response and the state the injector reads."""
        self.request = request
        self.file_path = file_path
        self.purpose = purpose
        self.render = RouterRender(
            mint_seal(request),
            router_manager.entry(),
            self.finish,
            snapshot=page_router(file_path).snapshot,
            speculation=router_manager.speculation(),
            progress=router_settings().progress,
        )
        self.shell: Shell | None = None
        self.html: str | None = None

    @property
    def seal(self) -> str:
        """Return the seal every marker of the response carries."""
        return self.render.seal

    def finish(self, html: str) -> str:
        """Digest the layouts of `html`, fill `$layouts` and run the passes after."""
        return self._read(html)[0]

    def settled(self, html: str) -> Shell:
        """Return the shell of `html`, read here when the injector never finished it."""
        shell = self.shell
        if shell is not None and self.html is html:
            return shell
        return self._read(html)[1]

    def _read(self, html: str) -> tuple[str, Shell]:
        """Return the finished HTML and its shell, holding both."""
        shell = read_shell(
            html, self.seal, layout_ids(self.file_path), router_manager.digest()
        )
        payload = json.dumps(layouts_payload(shell), separators=(",", ":"))
        html = html.replace(self.render.mark, payload, 1)
        html = kept(html, self.request)
        self.shell = shell = _relocated(shell, html)
        self.html = html
        return html, shell


class _Marking:
    """The block that binds one pass over the HTML rendered inside it."""

    __slots__ = ("_marks", "_token")

    def __init__(self, marks: Callable[[str], str] | None) -> None:
        """Hold the pass to bind, `None` to bind none."""
        self._marks = marks

    def __enter__(self) -> None:
        """Bind the pass as the one the render inside runs over its HTML."""
        self._token = html_finish.set(self._marks)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Unbind the pass, whatever the render inside it did."""
        html_finish.reset(self._token)


def page_marking(request: HttpRequest) -> AbstractContextManager[None]:
    """Mark the current links of the page the block renders for `request`."""
    return _Marking(partial(kept, request=request))


@contextmanager
def _routed(
    request: HttpRequest, file_path: Path, purpose: Purpose
) -> Iterator[Routing]:
    """Bind the router state of one page view to the render inside the block."""
    held = Routing(request, file_path, purpose)
    token = router_render.set(held.render)
    try:
        yield held
    finally:
        router_render.reset(token)


def routing(
    request: HttpRequest, file_path: Path, purpose: Purpose
) -> AbstractContextManager[Routing | None]:
    """Render the block under the client router, yielding `None` while it is off.

    With the router off the injector still runs the active-link pass over the page.
    """
    if not router_enabled():
        return page_marking(request)
    return _routed(request, file_path, purpose)


def declaration(request: HttpRequest) -> Declaration | None:
    """Return the layouts the navigation of `request` declares on screen."""
    raw = partial_intent(request).layouts
    return parse_declaration(raw, router_settings().max_layouts)


def _origin(request: HttpRequest) -> str | None:
    """Return the same-site path the client names in `X-Next-Origin`, if any."""
    return validated_origin_path(partial_intent(request).origin)


class _ZoneMarks:
    """The active-link pass over the zone HTML of one request.

    The path and the origin are read once a zone opens a navigation container.
    """

    __slots__ = ("_links", "_origin", "_path", "_request")

    def __init__(
        self, links: CurrentLinks, request: HttpRequest, path: str | None
    ) -> None:
        """Hold the backend and the request, with `path` when the caller names one."""
        self._links = links
        self._request = request
        self._path = path
        self._origin: str | None = None

    def __call__(self, html: str) -> str:
        """Return `html` marked for the page the client shows, itself when unmarked."""
        links = self._links
        if not links.holds(html):
            return html
        request = self._request
        path = self._path
        if path is None:
            path = self._path = _origin(request) or request.get_full_path()
        origin = self._origin
        if origin is None:
            origin = self._origin = origin_of(request)
        return links.mark(html, path, origin)


def zone_marking(request: HttpRequest) -> AbstractContextManager[None]:
    """Mark the zone HTML the block renders for the page the client shows.

    A layer and the zones inside it name the page beneath in `X-Next-Origin`.
    """
    links = current_links()
    return _Marking(None if links is None else _ZoneMarks(links, request, None))


def action_marking(request: HttpRequest) -> AbstractContextManager[None]:
    """Mark the zone HTML an action response renders for the page its form lives on."""
    links = current_links()
    if links is None:
        return _Marking(None)
    path = _origin(request) or posted_origin_path(request)
    return _Marking(None if path is None else _ZoneMarks(links, request, path))


def origin_vary[R: HttpResponseBase](response: R) -> R:
    """Vary zone `response` on `X-Next-Origin` while the active-link pass marks it."""
    if current_links() is not None:
        vary_on(response, _ORIGIN_VARY)
    return response


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


def _chrome_zones(patches: Patches, shell: Shell, layout: ScannedLayout) -> None:
    """Morph every zone in the kept chrome, a DOM no-op for one that did not change."""
    html = shell.html
    for name, bounds in chrome_zones(html, shell.seal, layout.slot):
        patches.morph({keys.ZONE: name}, zone_html(html, bounds, shell.seal))


def navigation_envelope(
    request: HttpRequest,
    routed: Routing,
    shell: Shell,
    cut: Cut,
    *,
    shared: bool,
    complete: bool,
) -> Patches:
    """Build the envelope of one navigation through `Patches`.

    A slot cut morphs the slot and the kept chrome zones, a body cut the body, and a
    visit asks for a hard load, every rung telling the client the new layout chain.
    `complete` marks a prefetch the navigation after it applies without a refetch.
    """
    patches = Patches(request)
    html = shell.html
    href = _href(request)
    kept_count = len(cut.kept)
    fields: dict[str, object] = {
        keys.LAYOUTS: layouts_payload(shell, kept_count),
        keys.ROOT: shell.root,
    }
    if cut.kind is CutKind.VISIT or (cut.kind is CutKind.BODY and shell.body is None):
        patches.redirect(href)
        return patches.navigation(fields)
    if cut.kind is CutKind.SLOT:
        layout = shell.layouts[kept_count - 1]
        start, end = layout.slot
        patches.morph({keys.SLOT: layout.ident}, html[start:end])
        _chrome_zones(patches, shell, layout)
    else:
        start, end = shell.body or (0, len(html))
        patches.morph({keys.BODY: True}, html[start:end])
    if _origin(request) == href:
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


def _href(request: HttpRequest) -> str:
    """Return the path and query of `request` with its leading slashes run into one."""
    return "/" + request.get_full_path().lstrip("/")


def hostlike(request: HttpRequest) -> bool:
    """Whether the path of `request` opens with `//`, which browsers read as a host."""
    return request.get_full_path().startswith("//")


def visit_envelope(request: HttpRequest) -> Patches:
    """Build the envelope that answers a navigation with a hard load of its URL.

    A path opening with `//` loads as the single-slash path, never as another host.
    """
    return Patches(request).redirect(_href(request))


def _layer_zone(file_path: Path) -> str | None:
    """Return the first zone the first intercept of `file_path` serves, by file order.

    A zone GET of the page with that zone selects the same intercept.
    """
    for rule in intercepts.of(file_path):
        served = zones_served(rule.file, file_path)
        if served:
            return served[0]
    return None


def intercepted(request: HttpRequest, file_path: Path) -> HttpResponseBase | None:
    """Answer a link to an intercepted page with its layer under `INTERCEPT = "links"`.

    A navigation to the page shown, or one with no origin, renders the page instead.
    """
    if router_settings().intercept != LINKS:
        return None
    href = _href(request)
    origin = _origin(request)
    if origin is None or origin == href:
        return None
    zone = _layer_zone(file_path)
    if zone is None:
        return None
    if partial_intent(request).prefetch:
        return refused_prefetch()
    return stamp(answered(Patches(request).layer_open(zone=zone, href=href)), request)


def cleared(request: HttpRequest, cut: Cut, shell: Shell) -> Cut:
    """Return `cut`, a kept layout given up for a body morph once the router clears.

    Kept chrome may still show what the session held before it changed.
    """
    if cut.kind is not CutKind.SLOT or not _clearing(request):
        return cut
    return Cut(CutKind.BODY, diverged_at=shell.root, reason=Divergence.CLEAR)


def answered(patches: Patches) -> HttpResponse:
    """Return the envelope response of a navigation, never stored."""
    backend = partial_backend_manager.get()
    body = backend.serialize_envelope(patches.envelope())
    return PatchResponse(
        body, content_type=backend.content_type, version=patches.version
    )


def passed(response: HttpResponseBase, request: HttpRequest) -> HttpResponseBase:
    """Send a response the page answered in place of its render, private and stamped.

    The router's fetch follows a redirect unseen, so one that must clear rides a visit.
    """
    if _hidden(response, request):
        return stamp(_visit(response, request), request)
    never_store(response)
    set_partial_vary(response)
    return stamp(response, request)


def _hidden(response: HttpResponseBase, request: HttpRequest) -> bool:
    """Whether `response` redirects past a clear or a CSRF token the client must see."""
    if response.status_code not in _REDIRECTS or not response.has_header("Location"):
        return False
    return _clearing(request) or secret_changed(request)


def _visit(response: HttpResponseBase, request: HttpRequest) -> HttpResponse:
    """Return the envelope that loads the target of `response`, with its cookies."""
    href = urljoin(_href(request), response["Location"])
    patches = Patches(request)
    try:
        patches.redirect(href)
    except CrossSiteHrefError:
        patches.redirect(href, external=True)
    sent = answered(patches)
    sent.cookies.update(response.cookies)
    return sent


def refused_prefetch() -> HttpResponse:
    """Return the empty answer to a prefetch of a page that refuses prefetches."""
    return _private(HttpResponse(b"", status=_PREFETCH_REFUSED))


def stale(request: HttpRequest) -> HttpResponse | None:
    """Return the 409 a navigation asserting another asset version answers."""
    version = partial_intent(request).version
    if not version or version == asset_version():
        return None
    return _private(HttpResponse(b"", status=_CONFLICT))


def _private(response: HttpResponse) -> HttpResponse:
    never_store(response)
    set_partial_vary(response)
    return response


def shed(request: HttpRequest, page_path: Path) -> HttpResponse | None:
    """Return the 429 a prefetch answers while the pool queue lags, else `None`.

    No job built the pool yet in a process that never queued one, so none lags.
    """
    capacity = suspense_manager.capacity()
    if capacity is None:
        return None
    waited = capacity.delay
    if waited <= defer_seconds("EXECUTOR", "OPTIONS", "QUEUE_TIMEOUT"):
        return None
    response = _private(HttpResponse(b"", status=_SHED))
    response["Retry-After"] = str(max(1, math.ceil(waited)))
    if prefetch_shed.has_listeners(Routing):
        prefetch_shed.send_robust(
            sender=Routing,
            page_path=page_path,
            waited_ms=waited * 1000,
            request=request,
        )
    return response


def describe(cut: Cut) -> str:
    """Return the DEBUG value of `X-Next-Navigate` naming the cut."""
    if cut.kind is CutKind.SLOT:
        text = f"kept {','.join(cut.kept)} slot {cut.slot}"
    elif cut.kind is CutKind.BODY:
        text = "body-morph"
    else:
        text = f"visit {cut.reason or 'id'}"
    if cut.diverged_at is not None and cut.kind is not CutKind.VISIT:
        text += f", diverged {cut.diverged_at} {cut.reason}"
    return text


def _layout_file(page_path: Path, ident: str) -> Path | None:
    """Return the layout of `page_path` whose id is `ident`, `None` for none.

    It pairs the ids with the layouts that hold a placeholder, as `layout_ids` does.
    """
    slotted = [
        layout
        for layout in reversed(layout_chain(page_path))
        if (text := template_source(layout)) is not None and placeholder_spans(text)
    ]
    return next(
        (
            layout
            for layout, found in zip(slotted, layout_ids(page_path), strict=False)
            if found == ident
        ),
        None,
    )


def _note_divergence(page_path: Path, cut: Cut) -> None:
    """Log at INFO, once per layout and process, a layout whose chrome digest moved."""
    ident = cut.diverged_at
    if cut.reason is not Divergence.DIGEST or ident is None or ident in _noted:
        return
    _noted.add(ident)
    logger.info(
        "The chrome of the layout %s differs on the navigation to %s, so the client "
        "router cannot keep it (%s). Wrap the part the pages set differently in a "
        "{%% zone %%} or move it into the page slot, as next.W183 names.",
        _layout_file(page_path, ident) or ident,
        page_path,
        describe(cut),
    )


def announce(
    request: HttpRequest, page_path: Path, cut: Cut, *, streamed: bool
) -> None:
    """Send `navigation_rendered` for one navigation or prefetch.

    Under `DEBUG` it also logs the first divergence of each layout by its digest.
    """
    if settings.DEBUG:
        _note_divergence(page_path, cut)
    if navigation_rendered.has_listeners(Routing):
        navigation_rendered.send_robust(
            sender=Routing,
            page_path=page_path,
            purpose=purpose_of(request),
            depth=depth_of(request),
            cut=cut.kind,
            kept=cut.kept,
            diverged_at=cut.diverged_at,
            reason=cut.reason,
            streamed=streamed,
            request=request,
        )


def debug_cut(response: HttpResponseBase, cut: Cut) -> None:
    """Name the cut on `response` under `DEBUG`."""
    if settings.DEBUG:
        response[NAVIGATE] = describe(cut)


def router_clear(request: HttpRequest, reason: str = "policy") -> None:
    """Tell the client router to flush every prefetch and snapshot on this response.

    Login, logout and a session key cycle send it on their own.
    """
    if getattr(request, _CLEAR_ATTR, None) is not None:
        return
    setattr(request, _CLEAR_ATTR, reason)
    if router_cleared.has_listeners(Routing):
        router_cleared.send_robust(sender=Routing, reason=reason, request=request)


def clear_on_login(*, request: HttpRequest, **kwargs: object) -> None:
    """Mark the request a user logged in on, as `user_logged_in` reports it."""
    router_clear(request, "login")


def clear_on_logout(*, request: HttpRequest, **kwargs: object) -> None:
    """Mark the request a user logged out on, as `user_logged_out` reports it."""
    router_clear(request, "logout")


def _cycled(request: HttpRequest) -> bool:
    """Whether the session of `request` changed its key during the request.

    A signed-cookie session names its data, so its key moves on every write.
    """
    session = getattr(request, "session", None)
    if session is None or isinstance(session, SignedStore):
        return False
    sent = request.COOKIES.get(settings.SESSION_COOKIE_NAME)
    return sent is not None and getattr(session, "session_key", None) != sent


def _clearing(request: HttpRequest) -> bool:
    """Whether the response to `request` clears the router, a cycled session too."""
    if getattr(request, _CLEAR_ATTR, None) is None and _cycled(request):
        router_clear(request, "session")
    return getattr(request, _CLEAR_ATTR, None) is not None


def directives(request: HttpRequest, *, snapshot: bool = True) -> tuple[str, ...]:
    """Return the `X-Next-Router` directives the response to `request` carries."""
    found: list[str] = []
    if _clearing(request):
        found.append(CLEAR)
    if not snapshot:
        found.append(NO_SNAPSHOT)
    return tuple(found)


def stamp(
    response: HttpResponseBase, request: HttpRequest, *, snapshot: bool = True
) -> HttpResponseBase:
    """Set `X-Next-Router` on `response` when the router is on and a directive holds."""
    if not router_enabled():
        return response
    found = directives(request, snapshot=snapshot)
    if found:
        response[ROUTER] = ", ".join(found)
    return response


__all__ = [
    "CLEAR",
    "DECLARATION_BYTES",
    "NO_SNAPSHOT",
    "Routing",
    "action_marking",
    "announce",
    "answered",
    "clear_on_login",
    "clear_on_logout",
    "cleared",
    "debug_cut",
    "declaration",
    "describe",
    "directives",
    "hostlike",
    "intercepted",
    "kept",
    "layouts_payload",
    "navigation_envelope",
    "origin_vary",
    "page_marking",
    "parse_declaration",
    "passed",
    "read_shell",
    "refused_prefetch",
    "router_clear",
    "routing",
    "shed",
    "stale",
    "stamp",
    "visit_envelope",
    "zone_marking",
]
