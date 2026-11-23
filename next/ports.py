"""Runtime ports letting an area call another one without importing it."""

from __future__ import annotations

from concurrent.futures import wait
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, Protocol

from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured


if TYPE_CHECKING:
    import contextvars
    from collections.abc import (
        Awaitable,
        Callable,
        Collection,
        Iterator,
        Mapping,
        MutableMapping,
    )
    from concurrent.futures import Future
    from contextlib import AbstractContextManager
    from pathlib import Path

    from django.http import HttpRequest
    from django.http.response import HttpResponseBase
    from django.template import Context
    from django.urls import URLPattern

    from next.components.info import ComponentInfo
    from next.components.trip import TripInput
    from next.defer import DeferredCall
    from next.static import StaticCollector
    from next.tasks import ContextSnapshot
    from next.urls import RouterBackend, RouterManager, URLPatternParser
    from next.utils import PageRoot


class PortSlot[T]:
    """Holds the port implementation `AppConfig.ready` binds, which a test may swap."""

    __slots__ = ("_impl", "_subject")

    def __init__(self, subject: str) -> None:
        """Start unbound under the name an early read is reported against."""
        self._impl: T | None = None
        self._subject = subject

    def set(self, impl: T) -> None:
        """Bind the implementation composed in `AppConfig.ready`."""
        self._impl = impl

    def get(self) -> T:
        """Return the bound implementation."""
        if self._impl is None:
            raise ImproperlyConfigured(self._unbound_message())
        return self._impl

    def peek(self) -> T | None:
        """Return the bound implementation, `None` before the app is ready."""
        return self._impl

    def _unbound_message(self) -> str:
        """Spell the failure of a slot read before the app finished starting."""
        return (
            f"The {self._subject} is unbound, which means the next app never "
            "finished starting. NextFrameworkConfig.ready() binds it."
        )


class ActionOwners(Protocol):
    """The `page.py` files an `@action` of their directory answers to.

    `next.forms` imports `next.pages`, so the page checks read the actions through this.
    """

    def page_files(self) -> Collection[Path]:
        """Return the `page.py` path of each directory whose files declare an action."""


class PageScan(Protocol):
    """The page-tree scan the checks and the discovery helpers run.

    `next.pages.scan` imports `next.discovery`, so discovery reaches the scan here.
    """

    def load_scanned_page_modules(
        self, router_manager: RouterManager
    ) -> list[tuple[str, Path]]:
        """Execute every routed `page.py`, answering the ones that loaded."""


class RouterAccess(Protocol):
    """The import seam `next.urls` opens for an area that cannot import it.

    `next.urls` imports `next.pages`, so the watcher and checks reach back through this.
    """

    def create_backend(self, config: dict[str, Any]) -> RouterBackend:
        """Return the router one `PAGE_BACKENDS` entry names."""

    def create_manager(self) -> RouterManager:
        """Return a fresh manager over every configured router."""

    def url_parser(self) -> URLPatternParser:
        """Return the parser the file router routes bracket segments through."""

    def page_kwargs(self, root: PageRoot, trail: str) -> frozenset[str] | None:
        """Return the kwargs a page at `trail` of `root` answers, `None` if refused."""


class StaticAssets(Protocol):
    """The static-manager surface one page render calls.

    `next.static` imports `next.pages`, so the render path reaches back through this.
    """

    def create_collector(self) -> StaticCollector:
        """Return a fresh sink for the assets one render references."""

    def discover_page_assets(self, file_path: Path, collector: StaticCollector) -> None:
        """Collect the assets co-located with one page."""

    def collect_component_assets(
        self, info: ComponentInfo, collector: StaticCollector | None
    ) -> None:
        """Collect the assets co-located with one composite component."""

    def inject(
        self,
        html: str,
        collector: StaticCollector,
        *,
        page_path: Path,
        request: HttpRequest | None,
    ) -> str:
        """Replace every placeholder token in `html` with rendered tags."""


class SeoRoutes(Protocol):
    """The routes the seo area adds to the lazy urlpatterns.

    `next.seo` imports `next.urls`, so `next.urls` reads the SEO routes through this.
    """

    def patterns(self) -> list[URLPattern]:
        """Return the SEO routes whose source exists, placed after every page route."""


class FormRoutes(Protocol):
    """The URL patterns the form backends serve.

    The forms area imports the pages package, which loads the URL checks that read this.
    """

    def __iter__(self) -> Iterator[URLPattern]:
        """Yield the URL patterns of every form backend."""


class PageScripts(Protocol):
    """The third-party scripts one page render adds to its head and init payload.

    `next.scripts` imports the static manager, so the injector reads it through this.
    """

    def render(
        self,
        collector: StaticCollector,
        *,
        page_path: Path | None,
        request: HttpRequest | None,
        nonce: Callable[[], str | None],
    ) -> tuple[str, Mapping[str, object]]:
        """Return the head tags and the reserved payload entries of one render.

        `nonce` mints the request nonce, so it is called only when a tag is written.
        """


class RealtimeNegotiation(Protocol):
    """The transports and partial content types every page negotiates its stream on.

    `next.realtime` and `next.partial` import the static runtime, so it reads them here.
    """

    def transports(self) -> tuple[str, ...]:
        """Return the enabled realtime transports in preference order."""

    def content_types(self) -> tuple[str, str]:
        """Return the content type of the active partial backend and the stock one."""


class AsyncBridge(Protocol):
    """Runs async code to completion from sync code, on the thread that asks.

    `next.suspense` sits above the areas that bridge, so they reach its driver here.
    """

    def __call__[T](self, work: Awaitable[T] | Callable[[], Awaitable[T]], /) -> T:
        """Run `work`, an awaitable or a factory of one, and return its result."""


class DeferralSink(Protocol):
    """Where a deferred component call goes, and how a render settles what it queued.

    `next.suspense` sits above the tags and the render paths, so they reach it here.
    """

    def queue(self, context: Context, call: DeferredCall) -> str:
        """Take one deferred call and return the text the render holds in its place."""

    def inline(self, context: Context, call: DeferredCall) -> str:
        """Render one call in place inside its settled wrapper."""

    def retry(self, request: object, trip: TripInput) -> str:
        """Return the token attributes a failed plain call retries with, or nothing."""

    def assembled(
        self,
        render: Callable[[], str],
        *,
        collector: StaticCollector | None,
        request: HttpRequest | None,
        context: MutableMapping[str, object] | None = None,
    ) -> str:
        """Return what `render` renders with every deferred call it queued settled."""


class ThreadWait(Protocol):
    """Blocks the calling thread on a future, serving the hops sent to that thread.

    `next.suspense` sits above `next.deps`, so a waiting loader reaches its server here.
    """

    def __call__(self, future: Future[Any], /) -> None:
        """Return once `future` is done, running the hops queued for this thread."""


class ContextPropagator(Protocol):
    """Restores a context snapshot on the sync thread or the loop that runs its work.

    `next.deps` sits below the default in `next.suspense`, and a project binds its own.
    """

    def restore(self, captured: ContextSnapshot, /) -> contextvars.Context:
        """Return `captured` restored on this sync thread, itself if nothing moved."""

    async def arestore(self, captured: ContextSnapshot, /) -> contextvars.Context:
        """Return `captured` restored on the running loop, itself if nothing moved."""


class RouterStamp(Protocol):
    """Sets the client router directives, a login or logout among them, on a response.

    `next.router` sits above `next.forms`, so a form or action response reaches it here.
    """

    def __call__(
        self, response: HttpResponseBase, request: HttpRequest, /
    ) -> HttpResponseBase:
        """Return `response` carrying the `X-Next-Router` directives of `request`."""


class ZoneMarking(Protocol):
    """Binds the active-link pass over the zone HTML an action response renders.

    `next.router` sits above `next.forms`, so the action dispatch reaches it here.
    """

    def __call__(self, request: HttpRequest, /) -> AbstractContextManager[None]:
        """Return the block whose zone HTML is marked for the page of the form."""


class LayerLoading(Protocol):
    """Stashes the loading UI of each layer zone the links of a rendered page open.

    `next.partial` sits above `next.static`, so the injector reaches it here.
    """

    def __call__(
        self, html: str, collector: StaticCollector, request: HttpRequest | None, /
    ) -> str:
        """Return `html` with the loading UI of each layer zone its links open."""


action_owners_slot = PortSlot["ActionOwners"]("action owners port")
async_bridge_slot = PortSlot["AsyncBridge"]("async bridge port")
context_propagator_slot = PortSlot["ContextPropagator"]("context propagator port")
deferrals_slot = PortSlot["DeferralSink"]("deferrals port")
form_routes_slot = PortSlot["FormRoutes"]("form routes port")
layer_loading_slot = PortSlot["LayerLoading"]("layer loading port")
page_scan_slot = PortSlot["PageScan"]("page scan port")
router_access_slot = PortSlot["RouterAccess"]("router access port")
router_stamp_slot = PortSlot["RouterStamp"]("router stamp port")
zone_marking_slot = PortSlot["ZoneMarking"]("zone marking port")
page_scripts_slot = PortSlot["PageScripts"]("page scripts port")
realtime_negotiation_slot = PortSlot["RealtimeNegotiation"]("realtime negotiation port")
seo_routes_slot = PortSlot["SeoRoutes"]("seo routes port")
static_assets_slot = PortSlot["StaticAssets"]("static assets port")
thread_wait_slot = PortSlot["ThreadWait"]("thread wait port")


async def _completed[T](work: Awaitable[T] | Callable[[], Awaitable[T]]) -> T:
    awaitable = work() if callable(work) else work
    return await awaitable


def run_bridged[T](work: Awaitable[T] | Callable[[], Awaitable[T]]) -> T:
    """Run `work` through the bound bridge, or through `async_to_sync` when unbound.

    The fallback is correct but slower, as outside a server it starts a loop per call.
    """
    bridge = async_bridge_slot.peek()
    if bridge is None:
        return async_to_sync(_completed)(work)
    return bridge(work)


def render_deferred(
    render: Callable[[], str],
    *,
    collector: StaticCollector | None,
    request: HttpRequest | None,
    context: MutableMapping[str, object] | None = None,
) -> str:
    """Run `render` through the bound deferral sink, plainly when none is bound.

    Unbound, every deferred call it meets renders in place, one after another.
    """
    sink = deferrals_slot.peek()
    if sink is None:
        return render()
    return sink.assembled(render, collector=collector, request=request, context=context)


def stash_layer_loading(
    html: str, collector: StaticCollector, request: HttpRequest | None
) -> str:
    """Return `html` through the bound layer loading pass, unchanged when unbound."""
    loading = layer_loading_slot.peek()
    return html if loading is None else loading(html, collector, request)


def stamp_router(response: HttpResponseBase, request: HttpRequest) -> HttpResponseBase:
    """Set the client router directives through the bound stamp, none when unbound."""
    stamp = router_stamp_slot.peek()
    return response if stamp is None else stamp(response, request)


def marked_zones(request: HttpRequest) -> AbstractContextManager[None]:
    """Return the block the bound zone marking opens, a plain one when unbound."""
    marking = zone_marking_slot.peek()
    return nullcontext() if marking is None else marking(request)


def wait_served(future: Future[Any]) -> None:
    """Block until `future` is done through the bound wait, or plainly when unbound.

    A thread that waits plainly serves no hop, so one queued for it waits as well.
    """
    serve = thread_wait_slot.peek()
    if serve is None:
        wait((future,))
        return
    serve(future)


__all__ = [
    "ActionOwners",
    "AsyncBridge",
    "ContextPropagator",
    "DeferralSink",
    "FormRoutes",
    "LayerLoading",
    "PageScan",
    "PageScripts",
    "PortSlot",
    "RealtimeNegotiation",
    "RouterAccess",
    "RouterStamp",
    "SeoRoutes",
    "StaticAssets",
    "ThreadWait",
    "ZoneMarking",
    "action_owners_slot",
    "async_bridge_slot",
    "context_propagator_slot",
    "deferrals_slot",
    "form_routes_slot",
    "layer_loading_slot",
    "marked_zones",
    "page_scan_slot",
    "page_scripts_slot",
    "realtime_negotiation_slot",
    "render_deferred",
    "router_access_slot",
    "router_stamp_slot",
    "run_bridged",
    "seo_routes_slot",
    "stamp_router",
    "stash_layer_loading",
    "static_assets_slot",
    "thread_wait_slot",
    "wait_served",
    "zone_marking_slot",
]
