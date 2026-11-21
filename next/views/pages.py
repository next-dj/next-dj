"""The static and resolving page views and the factory the URL router mounts."""

from __future__ import annotations

import time
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Final, Protocol, Self, cast

from django.http import Http404, HttpRequest, HttpResponse

from next.conf.router import router_enabled
from next.deps.cache import render_dep_cache
from next.deps.runner import run_segment
from next.diagnostics import watch_degraded
from next.intercept import intercepts
from next.introspect import is_coroutine_callable
from next.pages.access import page_access, refusal, routed
from next.pages.boundaries import Caught, boundary_scope
from next.pages.guards import GUARD_RAISES
from next.pages.loaders import load_page_module, load_python_module_memo, serves
from next.pages.manager import Built, defers, page as zone_pages
from next.pages.navigation import page_router
from next.pages.responses import (
    Purpose,
    finish_response,
    finish_zone_response,
    prepare_page_render,
    purpose_of,
    response_policy,
    zone_policy,
)
from next.pages.scope import render_scope
from next.pages.transactions import page_atomic
from next.partial.headers import PartialIntent, partial_intent, set_partial_vary
from next.partial.stream import owned, streamable
from next.partial.view import plain_zone_response, zone_response
from next.router.current import current_links
from next.router.manager import router_manager
from next.router.markers import CutKind
from next.router.navigation import (
    announce,
    answered,
    cleared,
    debug_cut,
    declaration,
    hostlike,
    intercepted,
    navigation_envelope,
    origin_vary,
    page_marking,
    passed,
    refused_prefetch,
    routing,
    shed,
    stale,
    stamp,
    visit_envelope,
    zone_marking,
)
from next.static.runtime import partial_streams
from next.suspense import drive
from next.suspense.assembly import PageRender, page_deferring, page_response
from next.suspense.manager import suspense_manager
from next.suspense.reload import (
    answer,
    cross_site,
    failed,
    methods,
    not_allowed,
    private,
    refused,
)
from next.suspense.response import StreamingPageResponse
from next.suspense.trip import note_route, tripped
from next.transactions import (
    SAFE_METHODS,
    ViewKind,
    atomic_aliases,
    keep_savepoints,
    request_atomic,
    skip_atomic_requests,
)

from .plan import PagePlan, PagePlans
from .routes import Route, boundary_token, route_request


if TYPE_CHECKING:
    import types
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from pathlib import Path
    from types import TracebackType

    from django.http.response import HttpResponseBase
    from django.template import Template

    from next.pages.loaders import BoundaryChain
    from next.pages.manager import Page, _BodyResolution
    from next.pages.responses import ResponsePolicy
    from next.partial.stream import Streamable
    from next.router.navigation import Routing


_SUCCESS: Final = range(200, 300)
_REDIRECTS: Final = range(300, 400)

_UNDEFERRED: Final[AbstractContextManager[PageRender | None]] = nullcontext()
_UNMARKED: Final[AbstractContextManager[None]] = nullcontext()
_PLANNED: Final = frozenset({Route.PAGE, Route.ZONE})

_KINDS: Final = {
    Route.PAGE: ViewKind.PAGE,
    Route.ZONE: ViewKind.ZONE,
    Route.BOUNDARY: ViewKind.BOUNDARY,
    Route.NAVIGATE: ViewKind.NAVIGATION,
}


@dataclass(frozen=True, slots=True)
class _Prepared:
    """What the driven part of an async page hands the sync template render.

    A `Caught` outcome stands for a body or a context that failed inside a boundary.
    """

    outcome: _BodyResolution | Caught
    policy: ResponsePolicy
    built: Built | None
    started: float | None


class _RoutedPageView(Protocol):
    """A page view carrying the `page.py` path form dispatch maps an origin URL to.

    `login_required` is the attribute `LoginRequiredMiddleware` reads off a view.
    """

    next_page_path: Path
    login_required: bool

    def __call__(self, request: HttpRequest, **kwargs: object) -> HttpResponseBase: ...


def _deferrals(
    request: HttpRequest, chain: BoundaryChain
) -> AbstractContextManager[PageRender | None]:
    """Return the block a page renders in, gathering its calls when any can defer.

    A page without a `loading.djx` in a project without a deferred component skips it.
    """
    if defers(chain):
        return page_deferring(request)
    return _UNDEFERRED


class _Block:
    """The blocks one page render runs in, deferral gathering, its stream, the router.

    A plain page with the router and active links off enters the deferral block alone.
    """

    __slots__ = (
        "_chain",
        "_entered",
        "_file_path",
        "_marked",
        "_request",
        "block",
        "rendering",
        "route",
        "routed",
    )

    def __init__(
        self, request: HttpRequest, file_path: Path, chain: BoundaryChain, route: Route
    ) -> None:
        """Hold what the blocks need, entering none yet."""
        self._request = request
        self._file_path = file_path
        self._chain = chain
        self.route = route
        self.rendering: PageRender | None = None
        self.block: Streamable | None = None
        self.routed: Routing | None = None
        self._entered: AbstractContextManager[object] = _UNDEFERRED
        self._marked: AbstractContextManager[None] | None = None

    def __enter__(self) -> Self:
        """Enter the deferral block, and the stream and router blocks when routed.

        With the router off the active-link pass is bound inside the deferral block.
        """
        request = self._request
        if self.route is not Route.NAVIGATE and not router_enabled():
            deferral = _deferrals(request, self._chain)
            self.rendering = deferral.__enter__()
            self._entered = deferral
            if current_links() is not None:
                marked = self._marked = page_marking(request)
                marked.__enter__()
            return self
        stack = ExitStack()
        self._entered = stack
        purpose = purpose_of(request)
        if self.route is Route.NAVIGATE and purpose is Purpose.NAVIGATION:
            self.block = stack.enter_context(streamable(request))
        self.rendering = stack.enter_context(_deferrals(request, self._chain))
        self.routed = stack.enter_context(routing(request, self._file_path, purpose))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Leave every block entered, innermost first."""
        marked = self._marked
        if marked is not None:
            marked.__exit__(exc_type, exc, traceback)
        return self._entered.__exit__(exc_type, exc, traceback)


def _answer(
    html: str,
    held: _Block,
    policy: ResponsePolicy,
    request: HttpRequest,
    file_path: Path,
) -> HttpResponseBase:
    """Return the response of a rendered page, a stream when its render left one.

    A stream sets its own cache and transport headers over those the page sets, and a
    stream that never leaves the view is discarded, so its slot is not held for good.
    """
    if held.route is Route.NAVIGATE and held.routed is not None:
        return _navigated(html, held, held.routed, policy, (request, file_path))
    response = page_response(html, held.rendering, request, file_path)
    if not isinstance(response, StreamingPageResponse):
        finish_response(response, policy, request, file_path)
        set_partial_vary(response)
        return _stamped(response, held, request, file_path)
    with owned(response):
        finish_response(response, policy, request, file_path)
        set_partial_vary(response)
        _stamped(response, held, request, file_path)
        response.settle()
    return response


def _stamped(
    response: HttpResponseBase, held: _Block, request: HttpRequest, file_path: Path
) -> HttpResponseBase:
    """Stamp the router directives on the document of a routed render."""
    if held.routed is None:
        return response
    return stamp(response, request, snapshot=page_router(file_path).snapshot)


def _complete(request: HttpRequest, rendering: PageRender | None) -> bool:
    """Whether a prefetch is the page a navigation would answer, no job left behind.

    A prefetch that skipped a deferred job or left a client-trip wrapper is refetched.
    """
    if purpose_of(request) is not Purpose.PREFETCH or tripped(request):
        return False
    return rendering is None or rendering.delivered is None


def _navigated(
    html: str,
    held: _Block,
    routed: Routing,
    policy: ResponsePolicy,
    target: tuple[HttpRequest, Path],
) -> HttpResponseBase:
    """Answer a navigation with the envelope of what changed, streamed when it defers.

    The page rendered whole, so the cut compares chrome a document load would show.
    """
    request, file_path = target
    shell = routed.settled(html)
    cut = cleared(request, router_manager.cut().cut(declaration(request), shell), shell)
    patches = navigation_envelope(
        request,
        routed,
        shell,
        cut,
        shared=policy.shared,
        complete=_complete(request, held.rendering),
    )
    visit = cut.kind is CutKind.VISIT
    deferred = None if held.block is None or visit else held.block.deferred
    streamed = deferred is not None
    if held.rendering is not None:
        held.rendering.release()
    if deferred is None:
        response: HttpResponseBase = answered(patches)
    else:
        response = deferred.respond(
            patches.envelope(), page_path=file_path, compress=policy.compress
        )
    with owned(response):
        if streamed:
            set_partial_vary(response)
        finish_zone_response(response, policy, request)
        debug_cut(response, cut)
        stamp(response, request, snapshot=page_router(file_path).snapshot)
        announce(request, file_path, cut, streamed=streamed)
    return response


def _gate(request: HttpRequest, file_path: Path) -> HttpResponseBase | None:
    """Answer a navigation that needs no render, `None` to render the page.

    Another site, a stale version, a layer, an opt-out, a busy pool or `//` stops it.
    """
    if cross_site(request):
        return refused()
    conflict: HttpResponseBase | None = stale(request)
    if conflict is None:
        conflict = intercepted(request, file_path)
    if conflict is not None:
        return conflict
    options = page_router(file_path)
    if partial_intent(request).prefetch:
        if not (options.prefetch and options.navigate):
            return refused_prefetch()
        lagging = shed(request, file_path)
        if lagging is not None:
            return lagging
    if not options.navigate or hostlike(request):
        return stamp(answered(visit_envelope(request)), request)
    return None


def _refused(
    denial: HttpResponseBase, route: Route, request: HttpRequest
) -> HttpResponseBase:
    """Return the refusal of a page, private, varied and stamped for a navigation."""
    return passed(denial, request) if route is Route.NAVIGATE else denial


def unified_view(
    page: Page,
    file_path: Path,
    module: types.ModuleType | None,
    *,
    broken_at_build: bool = False,
) -> Callable[..., HttpResponseBase]:
    """Return the static or the resolving view of a page, as its body source requires.

    Dispatch maps an origin by `next_page_path`, and a public page skips the middleware.
    """
    render = None if module is None else getattr(module, "render", None)
    view = (
        _resolving_view(
            page,
            file_path,
            module,
            broken_at_build=broken_at_build,
            driven=broken_at_build or is_coroutine_callable(render),
        )
        if callable(render) or broken_at_build
        else _static_view(page, file_path)
    )
    tagged = cast("_RoutedPageView", view)
    tagged.next_page_path = file_path
    tagged.login_required = not page_access(file_path).rule.public
    return skip_atomic_requests(view)


def _transaction(
    request: HttpRequest, route: Route, file_path: Path
) -> AbstractContextManager[None]:
    """Return the transaction the method rule or `page.atomic` gives one request."""
    aliases = atomic_aliases(request, _KINDS[route], page_atomic(file_path))
    return request_atomic(aliases)


def _boundary_refusal(
    file_path: Path,
    request: HttpRequest,
    kwargs: dict[str, object],
    dep_cache: dict[str, Any],
) -> HttpResponseBase | None:
    """Return the refusal of a boundary reload, the page access asked before its token.

    A method it does not answer and a request another site sent stop before the rest.
    """
    permitted = methods()
    if request.method not in permitted:
        return not_allowed(permitted)
    if cross_site(request):
        return refused()
    try:
        denial = refusal(file_path, request, kwargs, dep_cache)
    except GUARD_RAISES as exc:
        return failed(request, exc)
    return None if denial is None else private(denial)


def _admission(
    file_path: Path,
    request: HttpRequest,
    kwargs: dict[str, object],
    route: Route,
    dep_cache: dict[str, Any],
) -> HttpResponseBase | None:
    """Settle every access decision of a page request, `None` to serve it.

    It runs before the transaction opens, so a refused request opens none.
    """
    if route is Route.BOUNDARY:
        return _boundary_refusal(file_path, request, kwargs, dep_cache)
    if route is Route.NAVIGATE:
        early = _gate(request, file_path)
        if early is not None:
            return early
    denial = refusal(file_path, request, kwargs, dep_cache)
    return None if denial is None else _refused(denial, route, request)


def _static_view(page: Page, file_path: Path) -> Callable[..., HttpResponseBase]:
    """Return the view of a page whose body comes from files on disk.

    The body is request-independent, so the view renders the compiled template.
    An async context runs inside the render, through the bridge the server binds.
    """
    plans = PagePlans(page, file_path)

    def view(request: HttpRequest, **kwargs: object) -> HttpResponseBase:
        watch_degraded()
        intent = partial_intent(request)
        route = route_request(request, intent)
        dep_cache = render_dep_cache(request)
        call = (request, kwargs, intent, route, dep_cache)
        if route not in _PLANNED or request.method not in SAFE_METHODS:
            return _admitted(file_path, call, serve, admitted=False)
        denied = refusal(file_path, request, kwargs, dep_cache)
        if denied is not None:
            return denied
        if route is Route.ZONE:
            template = zone_pages.composed_template_for(file_path)
            plan = plans.of(template)
            if plan.plain and not intercepts.of(file_path):
                return _plain_zone(page, file_path, template, plan, call)
            return _admitted(file_path, call, serve, admitted=True)
        template = page.composed_template_for(file_path)
        plan = plans.of(template)
        if plan.plain:
            return _plain(page, file_path, (template, ""), plan, call)
        held = partial(serve, template=template)
        return _admitted(file_path, call, held, admitted=True)

    def serve(call: _Call, template: Template | None = None) -> HttpResponseBase:
        request, kwargs, intent, route, dep_cache = call
        if route is Route.ZONE:
            with zone_marking(request):
                zone = zone_response(
                    file_path,
                    intent,
                    request,
                    dynamic_body=False,
                    url_kwargs=dict(kwargs),
                    dep_cache=dep_cache,
                )
            return _zone_answer(zone, page, file_path, request)
        policy = response_policy(
            page, file_path, request, url_kwargs=kwargs, dep_cache=dep_cache
        )
        prepare_page_render(policy, request)
        chain = page.composed_chain(file_path)
        with _Block(request, file_path, chain, route) as held:
            html = page.render(
                file_path, request, kwargs, dep_cache=dep_cache, template=template
            )
        return _answer(html, held, policy, request, file_path)

    return view


def _admitted(
    file_path: Path,
    call: _Call,
    serve: Callable[[_Call], HttpResponseBase],
    *,
    admitted: bool,
) -> HttpResponseBase:
    """Run `serve` in the transaction of the request, a boundary reload answered here.

    The access decisions come first unless the caller `admitted` the request itself,
    and the boundaries of the render share one lazy savepoint session.
    """
    request, kwargs, _intent, route, dep_cache = call
    if not admitted:
        denied = _admission(file_path, request, kwargs, route, dep_cache)
        if denied is not None:
            return denied
    note_route(request, kwargs)
    with keep_savepoints(), _transaction(request, route, file_path):
        if route is Route.BOUNDARY:
            token = boundary_token(request) or ""
            return answer(request, file_path, kwargs, token, dep_cache)
        return serve(call)


type _Call = tuple[HttpRequest, dict[str, object], PartialIntent, Route, dict[str, Any]]


def _plain(
    page: Page,
    file_path: Path,
    source: tuple[Template | str, str],
    plan: PagePlan,
    call: _Call,
) -> HttpResponseBase:
    """Render and answer a page that uses none of the optional request features.

    The active-link pass is bound only for a page whose templates can open a container.
    """
    request, kwargs, _intent, _route, dep_cache = call
    template, body = source
    policy = response_policy(
        page, file_path, request, url_kwargs=kwargs, dep_cache=dep_cache
    )
    prepare_page_render(policy, request)
    if plan.marks or plan.streams:
        html = _bound_render(page, file_path, source, plan, call)
    else:
        html = page.render_plain(
            file_path, template, request, kwargs, dep_cache=dep_cache, body=body
        )
    response = HttpResponse(html)
    finish_response(response, policy, request, file_path)
    set_partial_vary(response)
    return response


def _bound_render(
    page: Page,
    file_path: Path,
    source: tuple[Template | str, str],
    plan: PagePlan,
    call: _Call,
) -> str:
    """Render a plain page under the two marks its plan asks for.

    The link pass runs for a template that can open a container, and the payload says
    partials may stream wherever the project holds a component that defers.
    """
    request, kwargs, _intent, _route, dep_cache = call
    template, body = source
    streams = plan.streams and suspense_manager.policy().streams_partials(request)
    token = partial_streams.set(streams)
    try:
        with page_marking(request) if plan.marks else _UNMARKED:
            return page.render_plain(
                file_path, template, request, kwargs, dep_cache=dep_cache, body=body
            )
    finally:
        partial_streams.reset(token)


def _plain_zone(
    page: Page, file_path: Path, template: Template, plan: PagePlan, call: _Call
) -> HttpResponseBase:
    """Answer a safe zone request for a plain page no intercept serves."""
    request, kwargs, intent, _route, _dep_cache = call
    origin = plan.links is not None
    if plan.marks:
        with zone_marking(request):
            zone = plain_zone_response(
                file_path, intent, request, dict(kwargs), template, origin=origin
            )
    else:
        zone = plain_zone_response(
            file_path, intent, request, dict(kwargs), template, origin=origin
        )
    return finish_zone_response(zone, zone_policy(page, file_path), request)


def _resolving_view(
    page: Page,
    file_path: Path,
    module: types.ModuleType | None,
    *,
    broken_at_build: bool,
    driven: bool,
) -> Callable[..., HttpResponseBase]:
    """Return the view that resolves a per-request body before composing.

    `broken_at_build` re-reads the module per request, a 404 once it has no body.
    """
    plans = PagePlans(page, file_path)
    planned = not (driven or broken_at_build)

    def view(request: HttpRequest, **kwargs: object) -> HttpResponseBase:
        watch_degraded()
        if broken_at_build and not routed(file_path):
            raise Http404
        intent = partial_intent(request)
        route = route_request(request, intent)
        dep_cache = render_dep_cache(request)
        call = (request, kwargs, intent, route, dep_cache)
        if not planned or route is not Route.PAGE or request.method not in SAFE_METHODS:
            return _admitted(file_path, call, dispatch, admitted=False)
        denied = refusal(file_path, request, kwargs, dep_cache)
        if denied is not None:
            return denied
        skeleton = page.layout_skeleton(file_path)
        plan = plans.of(skeleton[1])
        if plan.plain:
            return _plain_resolved(page, file_path, module, (skeleton[0], plan), call)
        held = partial(dispatch, skeleton=skeleton)
        return _admitted(file_path, call, held, admitted=True)

    def dispatch(
        call: _Call, skeleton: tuple[str, BoundaryChain] | None = None
    ) -> HttpResponseBase:
        if driven:
            return _serve_async(
                page,
                file_path,
                module,
                call,
                broken_at_build=broken_at_build,
                fallback=serve,
            )
        return serve(call, skeleton)

    def serve(
        call: _Call, skeleton: tuple[str, BoundaryChain] | None = None
    ) -> HttpResponseBase:
        active_module = (
            load_python_module_memo(file_path) if broken_at_build else module
        )
        if skeleton is None:
            skeleton = page.layout_skeleton(file_path)
        with boundary_scope(skeleton[1]):
            return _serve_sync(page, file_path, active_module, skeleton, call)

    return view


def _plain_resolved(
    page: Page,
    file_path: Path,
    module: types.ModuleType | None,
    planned: tuple[str, PagePlan],
    call: _Call,
) -> HttpResponseBase:
    """Resolve the sync body of a plain page and answer it, outside every block."""
    request, kwargs, _intent, _route, dep_cache = call
    resolution = page.resolve_page_body(
        file_path, module, request, kwargs, dep_cache=dep_cache
    )
    built = resolution.http_response
    if built is not None:
        policy = response_policy(
            page,
            file_path,
            request,
            url_kwargs=kwargs,
            dep_cache=dep_cache,
            response=built,
        )
        prepare_page_render(policy, request)
        return _finish_render_response(built, policy, request, file_path)
    skeleton, plan = planned
    body = resolution.body if resolution.body is not None else ""
    return _plain(page, file_path, (skeleton, body), plan, call)


def _serve_sync(
    page: Page,
    file_path: Path,
    module: types.ModuleType | None,
    skeleton: tuple[str, BoundaryChain],
    call: _Call,
) -> HttpResponseBase:
    """Resolve a sync body inside the page boundaries, then compose and render it."""
    request, kwargs, intent, route, dep_cache = call
    outcome = page.resolve_guarded(
        file_path, module, request, kwargs, dep_cache=dep_cache, chain=skeleton[1]
    )
    if route is Route.ZONE:
        return _zone(page, file_path, outcome, intent, request, kwargs, dep_cache)
    if isinstance(outcome, Caught):
        return _caught_response(
            page, file_path, outcome, (request, kwargs, route), dep_cache, skeleton[1]
        )
    built = outcome.http_response
    policy = response_policy(
        page, file_path, request, url_kwargs=kwargs, dep_cache=dep_cache, response=built
    )
    prepare_page_render(policy, request)
    if built is not None:
        return _finish_render_response(built, policy, request, file_path, route)
    body = outcome.body if outcome.body is not None else ""
    with _Block(request, file_path, skeleton[1], route) as held:
        html = page.render_composed(
            file_path, body, request, kwargs, dep_cache=dep_cache, skeleton=skeleton
        )
    return _answer(html, held, policy, request, file_path)


def _zone(
    page: Page,
    file_path: Path,
    outcome: _BodyResolution | Caught,
    intent: PartialIntent,
    request: HttpRequest,
    kwargs: dict[str, object],
    dep_cache: dict[str, Any],
) -> HttpResponseBase:
    """Answer a zone GET of a resolving page, a caught body with its error UI."""
    with zone_marking(request):
        if isinstance(outcome, Caught):
            zone = zone_response(
                file_path,
                intent,
                request,
                dynamic_body=False,
                url_kwargs=dict(kwargs),
                dep_cache=dep_cache,
                caught=outcome,
            )
        elif outcome.http_response is not None:
            zone = outcome.http_response
        else:
            zone = zone_response(
                file_path,
                intent,
                request,
                dynamic_body=outcome.dynamic,
                url_kwargs=dict(kwargs),
                dep_cache=dep_cache,
            )
    return _zone_answer(zone, page, file_path, request)


def _zone_answer(
    zone: HttpResponseBase, page: Page, file_path: Path, request: HttpRequest
) -> HttpResponseBase:
    """Finish the response of a zone GET, closing a stream a later step fails."""
    with owned(zone):
        finished = finish_zone_response(zone, zone_policy(page, file_path), request)
        return stamp(origin_vary(finished), request)


def _serve_async(
    page: Page,
    file_path: Path,
    module: types.ModuleType | None,
    call: _Call,
    *,
    broken_at_build: bool,
    fallback: Callable[[_Call], HttpResponseBase],
) -> HttpResponseBase:
    """Drive a page whose `render()` awaits, else serve it sync.

    A module broken at build is read again, so its colour is the one now on disk.
    """
    active_module = load_python_module_memo(file_path) if broken_at_build else module
    if _renders_async(active_module):
        return _serve_rendered(page, file_path, active_module, call)
    return fallback(call)


def _serve_rendered(
    page: Page, file_path: Path, module: types.ModuleType | None, call: _Call
) -> HttpResponseBase:
    """Serve a page whose `render()` awaits, one drive running the async part.

    The template renders on this thread after the drive.
    """
    request, kwargs, intent, route, dep_cache = call
    skeleton = page.layout_skeleton(file_path)
    chain = skeleton[1]
    with boundary_scope(chain):
        if route is Route.ZONE:
            outcome = drive(
                partial(
                    _scoped_body,
                    page,
                    file_path,
                    module,
                    request,
                    kwargs,
                    dep_cache,
                    chain,
                )
            )
            return _zone(page, file_path, outcome, intent, request, kwargs, dep_cache)
        prepared = drive(
            partial(
                _prepare, page, file_path, module, request, kwargs, dep_cache, chain
            )
        )
        outcome = prepared.outcome
        if not isinstance(outcome, Caught) and outcome.http_response is not None:
            return _finish_render_response(
                outcome.http_response, prepared.policy, request, file_path, route
            )
        with _Block(request, file_path, chain, route) as held:
            if isinstance(outcome, Caught):
                html = page.render_caught(
                    file_path,
                    outcome,
                    request,
                    kwargs,
                    dep_cache=dep_cache,
                    started=prepared.started,
                    chain=chain,
                )
            else:
                html = page.render_composed(
                    file_path,
                    outcome.body or "",
                    request,
                    kwargs,
                    dep_cache=dep_cache,
                    built=prepared.built,
                    skeleton=skeleton,
                )
    return _answer(html, held, prepared.policy, request, file_path)


def _renders_async(module: types.ModuleType | None) -> bool:
    """Return whether the `render` of `module` is a coroutine function."""
    return is_coroutine_callable(getattr(module, "render", None))


async def _scoped_body(
    page: Page,
    file_path: Path,
    module: types.ModuleType | None,
    request: HttpRequest,
    kwargs: dict[str, object],
    dep_cache: dict[str, Any],
    chain: BoundaryChain,
) -> _BodyResolution | Caught:
    """Resolve the body of an async page for a zone GET in its request scope."""
    async with render_scope(dep_cache):
        body = page.aresolve_page_body(
            file_path, module, request, kwargs, dep_cache=dep_cache
        )
        return (
            await page.acontain(chain, file_path, body) if chain.errors else await body
        )


def _policy(
    page: Page,
    file_path: Path,
    request: HttpRequest,
    kwargs: dict[str, object],
    dep_cache: dict[str, Any],
    response: HttpResponseBase | None,
) -> ResponsePolicy:
    """Return the response policy of a full page and prepare its render by it."""
    policy = response_policy(
        page,
        file_path,
        request,
        url_kwargs=kwargs,
        dep_cache=dep_cache,
        response=response,
    )
    prepare_page_render(policy, request)
    return policy


async def _prepare(
    page: Page,
    file_path: Path,
    module: types.ModuleType | None,
    request: HttpRequest,
    kwargs: dict[str, object],
    dep_cache: dict[str, Any],
    chain: BoundaryChain,
) -> _Prepared:
    """Resolve the body, the policy and the context of an async page in one coroutine.

    The policy runs between the two on a blocking thread, in the order a sync page has.
    A failure a page boundary of `chain` contains ends the run with that boundary.
    """
    async with render_scope(dep_cache):
        body = page.aresolve_page_body(
            file_path, module, request, kwargs, dep_cache=dep_cache
        )
        outcome = (
            await page.acontain(chain, file_path, body) if chain.errors else await body
        )
        response = None if isinstance(outcome, Caught) else outcome.http_response
        policy = await run_segment(
            partial(_policy, page, file_path, request, kwargs, dep_cache, response)
        )
        if (
            isinstance(outcome, Caught)
            or response is not None
            or not page.awaits(file_path)
        ):
            return _Prepared(outcome, policy, None, None)
        started = time.perf_counter()
        if chain.deferred:
            built = await page.agated_context(
                file_path, request, kwargs, dep_cache=dep_cache, chain=chain
            )
        elif chain.errors:
            built = await page.aguarded_context(
                file_path, request, kwargs, dep_cache=dep_cache, chain=chain
            )
        else:
            built = await page.arender_context(
                file_path, request, kwargs, dep_cache=dep_cache
            )
        if isinstance(built, Caught):
            return _Prepared(built, policy, None, started)
        return _Prepared(outcome, policy, Built(built, started), started)


def _caught_response(
    page: Page,
    file_path: Path,
    caught: Caught,
    call: tuple[HttpRequest, dict[str, object], Route],
    dep_cache: dict[str, Any],
    chain: BoundaryChain,
) -> HttpResponseBase:
    """Answer with the error UI of the boundary that caught a failed page body."""
    request, kwargs, route = call
    policy = response_policy(
        page, file_path, request, url_kwargs=kwargs, dep_cache=dep_cache
    )
    prepare_page_render(policy, request)
    with _Block(request, file_path, chain, route) as held:
        html = page.render_caught(
            file_path, caught, request, kwargs, dep_cache=dep_cache, chain=chain
        )
    return _answer(html, held, policy, request, file_path)


def _finish_render_response(
    response: HttpResponseBase,
    policy: ResponsePolicy,
    request: HttpRequest,
    file_path: Path,
    route: Route = Route.PAGE,
) -> HttpResponseBase:
    """Finish the full-page response `render()` returned, varying a cacheable one.

    A browser may serve a stored full page for a zone fetch, so `Vary` is added. A
    navigation passes a redirect to the router's fetch and loads anything else hard.
    """
    if route is Route.NAVIGATE:
        return _navigated_response(response, policy, request, file_path)
    finish_response(response, policy, request, file_path)
    if (
        isinstance(response, HttpResponse)
        and response.status_code in _SUCCESS
        and response.has_header("Cache-Control")
    ):
        set_partial_vary(response)
    return response


def _navigated_response(
    response: HttpResponseBase,
    policy: ResponsePolicy,
    request: HttpRequest,
    file_path: Path,
) -> HttpResponseBase:
    """Answer a navigation whose `render()` returned a response of its own."""
    finish_response(response, policy, request, file_path)
    if response.status_code in _REDIRECTS and response.has_header("Location"):
        return passed(response, request)
    return stamp(answered(visit_envelope(request)), request)


def page_view(page: Page, file_path: Path) -> Callable[..., HttpResponseBase] | None:
    """Return the view of a real or virtual page, or `None` when it has no body.

    A `page.py` whose import failed keeps a view, so each request surfaces the error.
    """
    module, error = load_page_module(file_path)
    if not serves(file_path, module, error):
        return None
    return unified_view(page, file_path, module, broken_at_build=error is not None)


__all__ = ["page_view", "unified_view"]
