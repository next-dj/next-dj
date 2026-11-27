"""The page view the URL router mounts, a skeleton the lifecycle participants fill.

Each feature takes part through the points its participant names.
"""

from __future__ import annotations

import time
from contextlib import ExitStack
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Final, Protocol, cast

from django.http import Http404, HttpResponse

from next.bridge import drive
from next.deps.cache import render_dep_cache
from next.deps.runner import run_segment
from next.deps.scope import render_scope
from next.diagnostics import watch_degraded
from next.introspect import is_coroutine_callable
from next.lifecycle import PageCall, ResponseSource, lifecycle
from next.pages.access import page_access, refusal, routed
from next.pages.boundaries import Caught, boundary_scope
from next.pages.loaders import load_page_module, load_python_module_memo, serves
from next.pages.manager import Built
from next.pages.responses import (
    finish_response,
    finish_zone_response,
    prepare_page_render,
    response_policy,
    zone_policy,
)
from next.partial.headers import set_partial_vary
from next.partial.stream import owned
from next.requests import RequestKind
from next.transactions import skip_atomic_requests
from next.transactions.policy import SAFE_METHODS

from .kinds import route_request
from .plan import PagePlans


if TYPE_CHECKING:
    import types
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from pathlib import Path

    from django.http import HttpRequest
    from django.http.response import HttpResponseBase
    from django.template import Template

    from next.lifecycle import Gates, Points
    from next.pages.loaders import BoundaryChain
    from next.pages.manager import Page
    from next.pages.manager.bodies import BodyResolution
    from next.pages.responses import ResponsePolicy


_SUCCESS: Final = range(200, 300)
_PAIR: Final = 2


@dataclass(frozen=True, slots=True)
class _Prepared:
    """What resolving a `render()` body hands the template render.

    A `Caught` outcome stands for a body or a context that failed inside a boundary,
    and an async page resolves its policy and context in the same drive.
    """

    outcome: BodyResolution | Caught
    policy: ResponsePolicy | None = None
    built: Built | None = None
    started: float | None = None


class _RoutedPageView(Protocol):
    """A page view carrying the `page.py` path form dispatch maps an origin URL to.

    `login_required` is the attribute `LoginRequiredMiddleware` reads off a view.
    """

    next_page_path: Path
    login_required: bool

    def __call__(self, request: HttpRequest, **kwargs: object) -> HttpResponseBase: ...


def _inside[R](
    openings: tuple[Callable[[PageCall], AbstractContextManager[object]], ...],
    call: PageCall,
    run: Callable[[], R],
) -> R:
    """Return what `run` returns inside every block of `openings`, first outermost.

    One or two blocks nest as plain `with` statements, which costs no stack object.
    """
    count = len(openings)
    if count == 0:
        return run()
    if count == 1:
        with openings[0](call):
            return run()
    if count == _PAIR:
        first, second = openings
        with first(call), second(call):
            return run()
    with ExitStack() as stack:
        for opening in openings:
            stack.enter_context(opening(call))
        return run()


def _called(
    request: HttpRequest, file_path: Path, kwargs: dict[str, object]
) -> PageCall:
    """Return the call of one request, its kind and purpose noted on it."""
    watch_degraded()
    kind = route_request(request)
    return PageCall(request, file_path, kwargs, kind, render_dep_cache(request))


def _admission(call: PageCall, gates: Gates) -> HttpResponseBase | None:
    """Settle every access decision of a request, `None` to serve it.

    It runs before the page is read and before the transaction opens, so a refused
    request composes and opens nothing. A kind a gate answers decides its access itself.
    """
    for admit in gates.admit:
        early = admit(call)
        if early is not None:
            return early
    if gates.answers:
        return None
    denied = refusal(call.file_path, call.request, call.kwargs, call.dep_cache)
    if denied is None:
        return None
    denial: HttpResponseBase = denied
    for refuse in gates.refuse:
        denial = refuse(call, denial)
    return denial


def _policy(
    page: Page, call: PageCall, response: HttpResponseBase | None = None
) -> ResponsePolicy:
    """Return the response policy of a full page and prepare its render by it."""
    request = call.request
    policy = response_policy(
        page,
        call.file_path,
        request,
        url_kwargs=call.kwargs,
        dep_cache=call.dep_cache,
        response=response,
    )
    prepare_page_render(policy, request)
    return policy


def _finish(
    page: Page, call: PageCall, points: Points, response: HttpResponseBase
) -> HttpResponseBase:
    """Set the page headers on `response`, then pass it through every finish point.

    A response `render()` built varies on the partial headers only once it is stored.
    """
    request, kind, policy = call.request, call.kind, call.policy
    built = call.source is ResponseSource.BUILT
    if kind is RequestKind.ZONE:
        finish_zone_response(response, zone_policy(page, call.file_path), request)
    elif policy is None:
        pass
    elif kind is RequestKind.NAVIGATE and not built:
        finish_zone_response(response, policy, request)
    else:
        finish_response(response, policy, request, call.file_path)
        if kind is RequestKind.PAGE and (not built or _stored(response)):
            set_partial_vary(response)
    for finish in points.finish:
        response = finish(call, response)
    return response


def _stored(response: HttpResponseBase) -> bool:
    """Whether a browser may store a successful `response` a full page returned."""
    return (
        isinstance(response, HttpResponse)
        and response.status_code in _SUCCESS
        and response.has_header("Cache-Control")
    )


def _finished(
    page: Page, call: PageCall, points: Points, response: HttpResponseBase
) -> HttpResponseBase:
    """Finish `response` in one place, closing a stream a failing step leaves behind."""
    if not response.streaming:
        return _finish(page, call, points, response)
    with owned(response):
        return _finish(page, call, points, response)


def _rendered(
    page: Page, call: PageCall, points: Points, render: Callable[[], str]
) -> HttpResponseBase:
    """Render inside the blocks, answer through the respond point, then finish."""
    html = _inside(points.block, call, render)
    respond = points.respond
    response = HttpResponse(html) if respond is None else respond(call, html)
    return _finished(page, call, points, response)


def _built(
    page: Page, call: PageCall, points: Points, response: HttpResponseBase
) -> HttpResponseBase:
    """Finish the response the `render()` of the page returned in place of a render."""
    call.source = ResponseSource.BUILT
    return _finished(page, call, points, response)


def _served(page: Page, call: PageCall, points: Points) -> HttpResponseBase:
    """Answer with the serve point inside the blocks, the page rendering nothing."""
    serve = cast("Callable[[PageCall], HttpResponseBase]", points.serve)
    response = _inside(points.block, call, partial(serve, call))
    return _finished(page, call, points, response)


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


def _static_view(page: Page, file_path: Path) -> Callable[..., HttpResponseBase]:
    """Return the view of a page whose body comes from files on disk.

    The body is request-independent, so the view renders the compiled template.
    An async context runs inside the render, through the bridge the server binds.
    """
    plans = PagePlans(page, file_path)

    def view(request: HttpRequest, **kwargs: object) -> HttpResponseBase:
        call = _called(request, file_path, kwargs)
        gates = lifecycle.gates(call.kind)
        denied = _admission(call, gates)
        if denied is not None:
            return denied
        safe = request.method in SAFE_METHODS
        if gates.answers:
            points = plans.blind().points(call.kind, safe=safe)
            return _inside(points.around, call, partial(_served, page, call, points))
        template = page.composed_template_for(file_path)
        plan = plans.of(template)
        points = plan.points(call.kind, safe=safe)
        plain = plan.plain and safe
        if not points.around:
            return _static(page, template, call, points, plain=plain)
        serve = partial(_static, page, template, call, points, plain=plain)
        return _inside(points.around, call, serve)

    return view


def _static(
    page: Page, template: Template, call: PageCall, points: Points, *, plain: bool
) -> HttpResponseBase:
    """Serve a page whose body is its template, straight when its plan is `plain`."""
    if points.serve is not None:
        return _served(page, call, points)
    call.policy = _policy(page, call)
    render = partial(_straight if plain else _whole, page, template, call)
    return _rendered(page, call, points, render)


def _straight(page: Page, template: Template, call: PageCall) -> str:
    """Render the template straight, or the part of it a block narrowed the call to."""
    part = template if call.part is None else call.part
    return page.render_plain(
        call.file_path, part, call.request, call.kwargs, dep_cache=call.dep_cache
    )


def _whole(page: Page, template: Template, call: PageCall) -> str:
    """Render the page in its boundaries, or the part a block narrowed the call to."""
    part = template if call.part is None else call.part
    return page.render(
        call.file_path,
        call.request,
        call.kwargs,
        dep_cache=call.dep_cache,
        template=part,
    )


def _resolving_view(
    page: Page,
    file_path: Path,
    module: types.ModuleType | None,
    *,
    broken_at_build: bool,
    driven: bool,
) -> Callable[..., HttpResponseBase]:
    """Return the view that resolves a per-request body before composing.

    `broken_at_build` re-reads the module per request, a 404 once it has no body. An
    async `render()` and a broken module are planned blind, as their colour may move.
    """
    plans = PagePlans(page, file_path)

    def view(request: HttpRequest, **kwargs: object) -> HttpResponseBase:
        if broken_at_build and not routed(file_path):
            raise Http404
        call = _called(request, file_path, kwargs)
        gates = lifecycle.gates(call.kind)
        denied = _admission(call, gates)
        if denied is not None:
            return denied
        safe = request.method in SAFE_METHODS
        if gates.answers:
            points = plans.blind().points(call.kind, safe=safe)
            return _inside(points.around, call, partial(_served, page, call, points))
        skeleton = None if driven else page.layout_skeleton(file_path)
        plan = plans.blind() if skeleton is None else plans.of(skeleton[1])
        points = plan.points(call.kind, safe=safe)
        active = load_python_module_memo(file_path) if broken_at_build else module
        plain = plan.plain and safe
        if not points.around:
            return _resolved(page, active, skeleton, call, points, plain=plain)
        serve = partial(_resolved, page, active, skeleton, call, points, plain=plain)
        return _inside(points.around, call, serve)

    return view


def _resolved(
    page: Page,
    module: types.ModuleType | None,
    skeleton: tuple[str, BoundaryChain] | None,
    call: PageCall,
    points: Points,
    *,
    plain: bool,
) -> HttpResponseBase:
    """Resolve the body sync or in one drive, then answer it as the plan says.

    A plain body resolves outside the page boundaries, which its plan found none of.
    """
    file_path, request = call.file_path, call.request
    if skeleton is None:
        skeleton = page.layout_skeleton(file_path)
    if plain:
        body = page.resolve_page_body(
            file_path, module, request, call.kwargs, dep_cache=call.dep_cache
        )
        return _answered(page, call, points, _Prepared(body), skeleton, plain=True)
    chain = skeleton[1]
    with boundary_scope(chain):
        if is_coroutine_callable(getattr(module, "render", None)):
            whole = points.serve is None
            prepared = drive(partial(_prepare, page, module, chain, call, whole=whole))
        else:
            outcome = page.resolve_guarded(
                file_path,
                module,
                request,
                call.kwargs,
                dep_cache=call.dep_cache,
                chain=chain,
            )
            prepared = _Prepared(outcome)
        return _answered(page, call, points, prepared, skeleton, plain=False)


def _answered(
    page: Page,
    call: PageCall,
    points: Points,
    prepared: _Prepared,
    skeleton: tuple[str, BoundaryChain],
    *,
    plain: bool,
) -> HttpResponseBase:
    """Answer a resolved body, a caught one with the error UI of its boundary."""
    outcome = call.body = prepared.outcome
    built = None if isinstance(outcome, Caught) else outcome.http_response
    if points.serve is not None:
        if built is not None:
            return _built(page, call, points, built)
        return _served(page, call, points)
    policy = prepared.policy
    call.policy = _policy(page, call, built) if policy is None else policy
    if built is not None:
        return _built(page, call, points, built)
    file_path, request, kwargs = call.file_path, call.request, call.kwargs
    render: Callable[[], str]
    if isinstance(outcome, Caught):
        render = partial(
            page.render_caught,
            file_path,
            outcome,
            request,
            kwargs,
            dep_cache=call.dep_cache,
            started=prepared.started,
            chain=skeleton[1],
        )
    elif plain:
        render = partial(
            page.render_plain,
            file_path,
            skeleton[0],
            request,
            kwargs,
            dep_cache=call.dep_cache,
            body=outcome.body or "",
        )
    else:
        render = partial(
            page.render_composed,
            file_path,
            outcome.body or "",
            request,
            kwargs,
            dep_cache=call.dep_cache,
            built=prepared.built,
            skeleton=skeleton,
        )
    return _rendered(page, call, points, render)


async def _prepare(
    page: Page,
    module: types.ModuleType | None,
    chain: BoundaryChain,
    call: PageCall,
    *,
    whole: bool,
) -> _Prepared:
    """Resolve the body of an async page, its policy and context too for a whole page.

    The policy runs between the two on a blocking thread, in the order a sync page has.
    A failure a page boundary of `chain` contains ends the run with that boundary.
    """
    file_path, request, kwargs, dep_cache = (
        call.file_path,
        call.request,
        call.kwargs,
        call.dep_cache,
    )
    async with render_scope(dep_cache):
        body = page.aresolve_page_body(
            file_path, module, request, kwargs, dep_cache=dep_cache
        )
        outcome = (
            await page.acontain(chain, file_path, body) if chain.errors else await body
        )
        if not whole:
            return _Prepared(outcome)
        response = None if isinstance(outcome, Caught) else outcome.http_response
        policy = await run_segment(partial(_policy, page, call, response))
        if (
            isinstance(outcome, Caught)
            or response is not None
            or not page.awaits(file_path)
        ):
            return _Prepared(outcome, policy)
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


def page_view(page: Page, file_path: Path) -> Callable[..., HttpResponseBase] | None:
    """Return the view of a real or virtual page, or `None` when it has no body.

    A `page.py` whose import failed keeps a view, so each request surfaces the error.
    """
    module, error = load_page_module(file_path)
    if not serves(file_path, module, error):
        return None
    return unified_view(page, file_path, module, broken_at_build=error is not None)


__all__ = ["page_view", "unified_view"]
