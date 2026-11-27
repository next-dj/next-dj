"""The reload of one client-trip boundary, its component rendered from props alone.

Every 403 it answers carries `X-Next-Reload: refused`, so the browser reloads the page.
"""

import weakref
from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, Final, Protocol

from django.conf import settings
from django.core.handlers.exception import response_for_exception
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.http.response import HttpResponseBase
from django.template import Context, Engine

from next.boundaries.calls import CallRenderer
from next.boundaries.failures import BoundaryFailure, kind_of
from next.boundaries.ids import IDENT
from next.boundaries.options import DeferMode
from next.boundaries.scope import render_boundaries, wrapping
from next.caches import PageCache
from next.components import collect_visible_components
from next.components.access import PENDING, decide
from next.components.calls import props_lookup
from next.components.context import component
from next.components.defer import defer_declarations
from next.components.info import ComponentInfo
from next.components.manager import components_manager
from next.components.tags import isolated_renderer
from next.components.trip import (
    TripInput,
    TripSource,
    client_mode,
    component_scope,
    prop_types,
    token_overrides,
)
from next.deps.scope import bridged_scope
from next.diagnostics import Capture
from next.pages.gating import guard_steps
from next.pages.graph import ContextGraph, Phase, arun_phase, run_phase
from next.pages.guards import GUARD_RAISES
from next.pages.loaders import page_tree_depth
from next.pages.manager import page
from next.pages.processors import page_context
from next.pages.responses import never_store
from next.partial import keys
from next.partial.envelope import Patch
from next.partial.headers import (
    SEC_FETCH_SITE,
    partial_intent,
    set_partial_vary,
    set_stream_vary,
)
from next.partial.manager import partial_backend_manager
from next.partial.patches import Patches, PatchResponse
from next.partial.render import ZoneRenderResult
from next.partial.stream import Deferred, owned, streamable
from next.permissions.access import holds
from next.seeding import COLLECTOR_KEY, REQUEST_KEY, TEMPLATE_PATH_KEY
from next.static.collector import StaticCollector
from next.static.manager import get_static_manager
from next.utils import request_header, template_edits_watched

from .assembly import assembled
from .errors import BoundaryMintError, BoundaryTokenError
from .manager import defer_manager
from .markers import TokenRejection
from .render import processors_of
from .response import PartialStream
from .tokens import (
    BoundaryClaims,
    BoundaryTarget,
    BoundaryTokenBackend,
    decode_props,
    url_pairs,
)
from .tokens.claims import Pairs
from .tokens.settings import override_profile
from .trip import mint


type _Found = dict[tuple[str, str], ComponentInfo]

_SAME_ORIGIN: Final = "same-origin"
_FORBIDDEN: Final = 403
_RELOAD: Final = 409
_READS: Final = ("GET", "HEAD")
_HEAD: Final = "HEAD"
_COMPONENTS: PageCache[Path, tuple[tuple[object, ...], _Found]] = PageCache(depends=())
_GUARDS: PageCache[Path, tuple[ContextGraph, frozenset[int]]] = PageCache(depends=())
RELOAD_HEADER: Final = "X-Next-Reload"
"""The response header of a reload signal, on a 409 and on every 403 of a reload."""

EXPIRED_SIGNAL: Final = "token_expired"
"""The reload signal of a token past its lifetime, epoch, claims version or entry."""

REFUSED_SIGNAL: Final = "refused"
"""The reload signal of any 403, a forgery and a rotated session looking alike."""

REJECTED_HEADER: Final = "X-Next-Token-Rejected"
"""The response header naming why a token was refused, sent under `DEBUG` alone."""

RETRY_WITH: Final = "token"
"""The wire key of a fresh retry token in the morph op of a failed reload."""

_UNNAMED: Final = DeferMode.LOAD


class IsolatedRenderer(CallRenderer, TripSource, Protocol):
    """A renderer of one component alone that names the input of its retry token."""


def private[R: HttpResponseBase](response: R) -> R:
    """Send `response` never stored, with no referrer beyond the origin and `Vary`.

    Every answer of a reload goes out through it, a stream keeping its `Cache-Control`.
    """
    if response.status_code == _FORBIDDEN:
        response[RELOAD_HEADER] = REFUSED_SIGNAL
    if not isinstance(response, PartialStream):
        never_store(response)
    response["Referrer-Policy"] = _SAME_ORIGIN
    set_partial_vary(response)
    return response


def _answer(status: int, error: BoundaryTokenError | None) -> HttpResponse:
    """Return an empty answer, the token refusal explained under `DEBUG` alone."""
    if error is None or not settings.DEBUG:
        return HttpResponse(b"", status=status)
    response = HttpResponse(
        f"{error}\n{error.detail}\n", status=status, content_type="text/plain"
    )
    response[REJECTED_HEADER] = str(error.reason)
    return response


def refused(error: BoundaryTokenError | None = None) -> HttpResponse:
    """Return the one 403 every refused reload answers, explained under `DEBUG`."""
    return private(_answer(_FORBIDDEN, error))


def expired(error: BoundaryTokenError | None = None) -> HttpResponse:
    """Return the empty 409 a stale token answers, so the client reloads the page."""
    response = _answer(_RELOAD, error)
    response[RELOAD_HEADER] = EXPIRED_SIGNAL
    return private(response)


def methods() -> tuple[str, ...]:
    """Return the methods a reload answers, those of the configured carrier."""
    return defer_manager.tokens().carrier.methods


def not_allowed(permitted: tuple[str, ...]) -> HttpResponseNotAllowed:
    """Return the 405 of a reload sent with a method it does not answer."""
    return private(HttpResponseNotAllowed(permitted))


def failed(request: HttpRequest, exc: Exception) -> HttpResponseBase:
    """Return what a page request answers a guard's 404, 403 or 400 with, private."""
    return private(response_for_exception(request, exc))


def cross_site(request: HttpRequest) -> bool:
    """Whether the browser reports the request as made from another site."""
    site = request_header(request, SEC_FETCH_SITE)
    return site is not None and site != _SAME_ORIGIN


def _addressed(request: HttpRequest) -> str | None:
    """Return the one wrapper id `X-Next-Zone` names, `None` for any other value."""
    zones = partial_intent(request).zones
    if len(zones) != 1 or IDENT.fullmatch(zones[0]) is None:
        return None
    return zones[0]


def _visible(path: Path) -> Mapping[str, ComponentInfo]:
    return collect_visible_components(path / "template.djx")


def _stamp() -> tuple[object, ...] | None:
    """Return what holds while the components a page can call stay the same.

    Watched template edits leave no stamp, and replaced backends are held only weakly.
    """
    if template_edits_watched():
        return None
    return (
        tuple(map(weakref.ref, components_manager.backends)),
        component.registry.version,
        defer_declarations.version,
        page.contexts.version,
    )


def page_components(page_path: Path) -> Mapping[tuple[str, str], ComponentInfo]:
    """Return every component a render of the page can call, by name and scope.

    It reads what each directory up to the page root and each component sees, once.
    """
    stamp = _stamp()
    held = _COMPONENTS.get(page_path)
    if held is not None and stamp is not None and held[0] == stamp:
        return held[1]
    found = _walked(page_path)
    if stamp is not None:
        _COMPONENTS[page_path] = (stamp, found)
    return found


def _walked(page_path: Path) -> _Found:
    """Return the components the directories of a page and its components see."""
    found: _Found = {}
    start = page_path.parent
    pending = [start, *start.parents][: page_tree_depth(start)]
    while pending:
        directory = pending.pop()
        for info in _visible(directory).values():
            key = (info.name, component_scope(info))
            if key in found:
                continue
            found[key] = info
            source = info.template_path or info.module_path
            if source is not None:
                pending.append(source.parent)
    return found


def _target(
    request: HttpRequest, info: ComponentInfo, kwargs: Mapping[str, object]
) -> BoundaryTarget:
    """Return what the server resolves a reload of `info` on this URL to."""
    return BoundaryTarget(
        info.name,
        component_scope(info),
        url_path=request.path,
        url_kwargs=url_pairs(kwargs),
        tokens=token_overrides(info),
    )


def _refuse(
    backend: BoundaryTokenBackend,
    request: HttpRequest,
    reason: TokenRejection,
    component: str,
) -> BoundaryTokenError:
    error = BoundaryTokenError(reason)
    backend.announce(request, error, component)
    return error


def _named(
    backend: BoundaryTokenBackend, request: HttpRequest, token: str, page_path: Path
) -> Pairs[object] | None:
    """Return the overrides the token names in its header, `None` when it names none.

    The name is a lookup among the page's components, never a trial of each one.
    """
    profile = backend.profile(token)
    if profile is None:
        return None
    if profile == override_profile(()):
        return ()
    for info in page_components(page_path).values():
        group = token_overrides(info)
        if group and override_profile(group) == profile:
            return group
    raise _refuse(backend, request, TokenRejection.URL_MISMATCH, "")


def redeem(
    request: HttpRequest, token: str, page_path: Path, kwargs: Mapping[str, object]
) -> tuple[BoundaryClaims, ComponentInfo]:
    """Return the claims of a token and the component the page resolves them to.

    A named token settles only under the overrides of the component it resolves to.
    """
    backend = defer_manager.tokens()
    named = _named(backend, request, token, page_path)
    try:
        claims = backend.open(request, token, tokens=named or ())
    except BoundaryTokenError as exc:
        backend.announce(request, exc)
        raise
    info = page_components(page_path).get((claims.component, claims.scope))
    if info is None:
        raise _refuse(backend, request, TokenRejection.URL_MISMATCH, claims.component)
    own = token_overrides(info)
    if named is not None and own != named:
        raise _refuse(backend, request, TokenRejection.URL_MISMATCH, info.name)
    try:
        if named is None and own:
            claims = backend.open(request, token, tokens=own)
        settled = backend.settle(request, claims, _target(request, info, kwargs))
    except BoundaryTokenError as exc:
        backend.announce(request, exc, info.name)
        raise
    return settled, info


def _context(request: HttpRequest, collector: StaticCollector, path: Path) -> Context:
    """Return the context of the processors alone, the props joining it later."""
    data: dict[str, object] = {}
    for processor in processors_of(Engine.get_default()):
        data.update(processor(request))
    data[REQUEST_KEY] = request
    data[COLLECTOR_KEY] = collector
    data[TEMPLATE_PATH_KEY] = path
    return page_context(data, request)


def _mode(info: ComponentInfo) -> DeferMode:
    options = defer_declarations.options(info.module_path)
    mode = None if options is None else options.mode
    return mode if mode is not None and client_mode(mode) else _UNNAMED


def _envelope(
    request: HttpRequest,
    ident: str,
    rendered: tuple[str, BoundaryFailure | None],
    *,
    collector: StaticCollector,
    retry: str | None,
    pending: Deferred | None,
    page_path: Path,
) -> HttpResponseBase:
    """Return the envelope morphing the content into the wrapper, with its state.

    Nested boundaries the render left stream after it when the client asks.
    """
    html, failure = rendered
    patches = Patches(request)
    patches.morph({keys.BOUNDARY: ident}, html)
    patches.absorb_zone_result(
        ZoneRenderResult(html={}, bodies={}, collector=collector)
    )
    envelope = patches.envelope()
    extras: dict[str, object] = {keys.CHUNK_STATE: "ready"}
    if failure is not None:
        extras = {keys.CHUNK_STATE: "error", keys.CHUNK_KIND: failure.kind.value}
        if retry is not None:
            extras[RETRY_WITH] = retry
    morph = envelope.ops[0]
    ops = (Patch(morph.op, morph.target, morph.html, extras), *envelope.ops[1:])
    envelope = replace(envelope, ops=ops)
    if pending is not None:
        stream = pending.respond(envelope, page_path=page_path)
        with owned(stream):
            return private(stream)
    backend = partial_backend_manager.get()
    body = backend.serialize_envelope(envelope)
    response = PatchResponse(
        body, content_type=backend.content_type, version=patches.version
    )
    if request.method in _READS:
        set_stream_vary(response)
    return private(response)


def _retry(request: HttpRequest, trip: TripInput | None) -> str | None:
    if trip is None:
        return None
    try:
        return mint(request, trip)
    except BoundaryMintError:
        return None


def _guards(page_path: Path) -> tuple[ContextGraph, frozenset[int]]:
    """Return the context graph of the page and its guard steps, read once a graph."""
    graph = page.contexts.graph(page_path)
    held = _GUARDS.get(page_path)
    if held is None or held[0] is not graph:
        held = _GUARDS[page_path] = (graph, guard_steps(graph))
    return held


def run_guards(
    page_path: Path,
    request: HttpRequest,
    kwargs: Mapping[str, object],
    dep_cache: dict[str, Any],
) -> None:
    """Run the context guards of the page and its layouts, as a deferred shell does.

    A guard raises what it raises in a page render, and a page without one runs nothing.
    """
    graph, steps = _guards(page_path)
    if not steps:
        return
    phase = Phase(steps, {})
    if any(graph.steps[index].is_async for index in steps):
        bridged_scope(
            dep_cache,
            partial(
                arun_phase,
                graph,
                page_path,
                phase,
                request=request,
                url_kwargs=kwargs,
                dep_cache=dep_cache,
            ),
        )
        return
    run_phase(
        graph, page_path, phase, request=request, url_kwargs=kwargs, dep_cache=dep_cache
    )


def _headers() -> HttpResponse:
    """Return the empty answer of a `HEAD` reload that passed every check."""
    content_type = partial_backend_manager.get().content_type
    return private(HttpResponse(b"", content_type=content_type))


@dataclass(frozen=True, slots=True)
class _Reached:
    """The page a reload reached, its URL kwargs and the cache its guards share."""

    page_path: Path
    kwargs: Mapping[str, object]
    dep_cache: dict[str, Any]


type _Authorized = tuple[Mapping[str, object], Http404 | None]


def _authorize(
    request: HttpRequest, claims: BoundaryClaims, info: ComponentInfo, reached: _Reached
) -> _Authorized | HttpResponseBase:
    """Return the decoded props and a vanished row, or the answer that stops a reload.

    Under an object rule a vanished row refuses, so no uncleared caller learns of it.
    """
    try:
        run_guards(reached.page_path, request, reached.kwargs, reached.dep_cache)
    except GUARD_RAISES as exc:
        return failed(request, exc)
    user = getattr(request, "user", None)
    shell = decide(info, request)
    if (claims.perms and not holds(user, claims.perms)) or shell.denial is not None:
        return refused()
    try:
        limit = defer_manager.tokens().prop_limit(token_overrides(info))
        props = decode_props(request, claims.props, prop_types(info), limit=limit)
    except Http404 as exc:
        return refused() if shell is PENDING else ({}, exc)
    except BoundaryTokenError as exc:
        defer_manager.tokens().announce(request, exc, info.name)
        return refused(exc)
    if not decide(info, request, props_lookup(props)).passed:
        return refused()
    return props, None


def answer(
    request: HttpRequest,
    page_path: Path,
    kwargs: Mapping[str, object],
    token: str,
    dep_cache: dict[str, Any],
) -> HttpResponseBase:
    """Answer a reload of one boundary, the page access already passed.

    The page guards run once the token opened, and the rule before any prop decodes.
    """
    ident = _addressed(request)
    if ident is None:
        return refused()
    try:
        claims, info = redeem(request, token, page_path, kwargs)
    except BoundaryTokenError as exc:
        return expired(exc) if exc.expired else refused(exc)
    authorized = _authorize(
        request, claims, info, _Reached(page_path, kwargs, dep_cache)
    )
    if isinstance(authorized, HttpResponseBase):
        return authorized
    if request.method == _HEAD:
        return _headers()
    props, gone = authorized
    collector = StaticCollector(bundle_url=get_static_manager().chunk_url)
    context = _context(request, collector, page_path)
    trip = TripInput(info, _mode(info), props, claims.perms)
    renderer = isolated_renderer(info, page_path, trip)
    with streamable(request) as block, wrapping(ident):
        rendered = _render(request, context, collector, renderer, gone)
    pending = None if block is None else block.deferred
    failure = rendered[1]
    try:
        retry = (
            None if failure is None else _retry(request, renderer.retry_input(failure))
        )
        return _envelope(
            request,
            ident,
            rendered,
            collector=collector,
            retry=retry,
            pending=pending,
            page_path=page_path,
        )
    finally:
        if pending is not None:
            pending.release()


def _render(
    request: HttpRequest,
    context: Context,
    collector: StaticCollector,
    renderer: IsolatedRenderer,
    gone: Exception | None,
) -> tuple[str, BoundaryFailure | None]:
    """Render the component inside its error boundary, nested boundaries in place.

    A failure the boundary lets through, a vanished row among them, shows its error UI.
    """
    if gone is not None:
        return renderer.error(context, kind_of(gone))
    failure: BoundaryFailure | None = None

    def draw() -> str:
        nonlocal failure
        html, failure = renderer.body(context)
        return html

    caught = Capture()
    html = ""
    with caught, render_boundaries():
        html = assembled(draw, collector=collector, request=request)
    if caught.error is not None:
        return renderer.error(context, kind_of(caught.error))
    return html, failure


__all__ = [
    "EXPIRED_SIGNAL",
    "REFUSED_SIGNAL",
    "REJECTED_HEADER",
    "RELOAD_HEADER",
    "RETRY_WITH",
    "answer",
    "cross_site",
    "expired",
    "failed",
    "methods",
    "not_allowed",
    "page_components",
    "private",
    "redeem",
    "refused",
    "run_guards",
]
