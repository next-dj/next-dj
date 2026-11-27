"""Runtime ports letting an area call another one without importing it."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from django.core.exceptions import ImproperlyConfigured


if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterator, Mapping, MutableMapping
    from contextlib import AbstractContextManager
    from pathlib import Path

    from django.http import HttpRequest
    from django.http.response import HttpResponseBase
    from django.template import Context
    from django.urls import URLPattern

    from next.boundaries.calls import DeferredCall
    from next.components.info import ComponentInfo
    from next.components.trip import TripInput
    from next.pages import Page
    from next.static import StaticCollector
    from next.urls import PageBackend, RouteManager, URLPatternParser
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
        self, route_manager: RouteManager
    ) -> list[tuple[str, Path]]:
        """Execute every routed `page.py`, answering the ones that loaded."""


class RouteAccess(Protocol):
    """The import seam `next.urls` opens for an area that cannot import it.

    `next.urls` imports `next.pages`, so the watcher and checks reach back through this.
    """

    def create_backend(self, config: dict[str, Any]) -> PageBackend:
        """Return the page backend one `PAGE_BACKENDS` entry names."""

    def create_manager(self) -> RouteManager:
        """Return a fresh manager over every configured page backend."""

    def url_parser(self) -> URLPatternParser:
        """Return the parser the file router routes bracket segments through."""

    def page_kwargs(self, root: PageRoot, trail: str) -> frozenset[str] | None:
        """Return the kwargs a page at `trail` of `root` answers, `None` if refused."""


class PageViews(Protocol):
    """Builds the view a routed page answers with.

    `next.views` sits above `next.urls`, so the file router reaches the factory here.
    """

    def __call__(
        self, page: Page, file_path: Path, /
    ) -> Callable[..., HttpResponseBase] | None:
        """Return the view of the page at `file_path`, `None` when it has no body."""


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

    def runtime_loads(self) -> bool:
        """Return whether a page loads the runtime script, which every retry needs."""


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


class ScriptRender(Protocol):
    """One page injection as the page script providers read it.

    `entries` holds the request entries every provider added, once all of them ran.
    """

    @property
    def request(self) -> HttpRequest | None:
        """Return the request the page answers, `None` for a render without one."""

    @property
    def page_path(self) -> Path | None:
        """Return the `page.py` the render belongs to, `None` outside a page."""

    @property
    def collector(self) -> StaticCollector:
        """Return the collector holding the assets and notes of the render."""

    @property
    def runtime(self) -> bool:
        """Return whether the page loads the runtime from its scripts placeholder."""

    @property
    def streams(self) -> bool:
        """Return whether the page writes its deferred boundaries after the shell."""

    @property
    def entries(self) -> Mapping[str, object]:
        """Return the request payload entries the providers added."""

    def nonce(self) -> str | None:
        """Return the request nonce, minted by the first call."""

    def bundle_url(self, name: str) -> str | None:
        """Return the URL of the declared bundle `name`, `None` if storage lacks it."""


_NO_ENTRIES: Mapping[str, object] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class ScriptShare:
    """What one provider adds to the head, init payload and bundles of one render.

    `shared` holds encoded entries every request of one configuration repeats, which
    the injector escapes once, and `entries` the values of this request alone.
    """

    head: str = ""
    entries: Mapping[str, object] = _NO_ENTRIES
    shared: str = ""
    after: Callable[[str | None], str] | None = None
    eager: tuple[str, ...] = ()
    bundles: tuple[str, ...] = ()
    listed: frozenset[str] = frozenset()
    enclose: Callable[[str], str] | None = None
    finish: Callable[[str], str] | None = None
    stylesheet: str = ""


class PageScripts(Protocol):
    """One area's part in the head, init payload and bundles of every page render.

    `next.static` sits below the features, so the injector reads them through this.
    """

    @property
    def name(self) -> str:
        """Return the name the list keys the provider by."""

    @property
    def priority(self) -> int:
        """Return the rank the injector runs it by, the lowest first."""

    @property
    def keys(self) -> frozenset[str]:
        """Return the init-payload keys it writes, which page context may not use."""

    def applies(self) -> bool:
        """Return whether it takes part in any render under the loaded settings."""

    def share(self, render: ScriptRender, /) -> ScriptShare | None:
        """Return what it adds to `render`, `None` for nothing."""


class Ranked(Protocol):
    """An implementation a `PortList` keys by name and orders by rank."""

    @property
    def name(self) -> str:
        """Return the name a later registration replaces it under."""

    @property
    def priority(self) -> int:
        """Return the rank it runs by, the lowest first."""


class PortList[T: Ranked]:
    """Holds the ranked implementations `AppConfig.ready` registers, in run order.

    Equal ranks keep their registration order, and `version` moves on every change.
    """

    __slots__ = ("_items", "_version")

    def __init__(self) -> None:
        """Start empty, as a render before the app is ready takes no part from any."""
        self._items: tuple[T, ...] = ()
        self._version = 0

    @property
    def version(self) -> int:
        """Return the counter every registration and reset moves."""
        return self._version

    def __iter__(self) -> Iterator[T]:
        """Yield the implementations in run order."""
        return iter(self._items)

    def register(self, impl: T) -> None:
        """Add `impl` in its rank, or put it in the place of the one of its name."""
        kept = [item for item in self._items if item.name != impl.name]
        kept.append(impl)
        self._items = tuple(sorted(kept, key=_rank))
        self._version += 1

    def reset(self) -> None:
        """Drop every implementation."""
        self._items = ()
        self._version += 1


def _rank(item: Ranked) -> int:
    """Return the rank of one listed implementation."""
    return item.priority


class DeferralSink(Protocol):
    """Where a deferred component call goes, and how a render settles what it queued.

    `next.defer` sits above the tags and the render paths, so they reach it here.
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


class ServerLoad(Protocol):
    """How far the deferred work of this process lags, which a prefetch shed reads.

    `next.defer` sits beside `next.router`, so the router reads the pool here.
    """

    def backlog(self) -> float | None:
        """Return the wait of the oldest queued job, `None` while within its timeout."""


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
deferrals_slot = PortSlot["DeferralSink"]("deferrals port")
form_routes_slot = PortSlot["FormRoutes"]("form routes port")
layer_loading_slot = PortSlot["LayerLoading"]("layer loading port")
page_scan_slot = PortSlot["PageScan"]("page scan port")
page_views_slot = PortSlot["PageViews"]("page views port")
route_access_slot = PortSlot["RouteAccess"]("route access port")
router_stamp_slot = PortSlot["RouterStamp"]("router stamp port")
zone_marking_slot = PortSlot["ZoneMarking"]("zone marking port")
seo_routes_slot = PortSlot["SeoRoutes"]("seo routes port")
server_load_slot = PortSlot["ServerLoad"]("server load port")
static_assets_slot = PortSlot["StaticAssets"]("static assets port")

page_scripts = PortList["PageScripts"]()
"""The providers of every page head and init payload, ranked."""


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


def server_backlog() -> float | None:
    """Return the backlog the bound server load reports, `None` when none is bound."""
    load = server_load_slot.peek()
    return None if load is None else load.backlog()


def marked_zones(request: HttpRequest) -> AbstractContextManager[None]:
    """Return the block the bound zone marking opens, a plain one when unbound."""
    marking = zone_marking_slot.peek()
    return nullcontext() if marking is None else marking(request)


__all__ = [
    "ActionOwners",
    "DeferralSink",
    "FormRoutes",
    "LayerLoading",
    "PageScan",
    "PageScripts",
    "PageViews",
    "PortList",
    "PortSlot",
    "Ranked",
    "RouteAccess",
    "RouterStamp",
    "ScriptRender",
    "ScriptShare",
    "SeoRoutes",
    "ServerLoad",
    "StaticAssets",
    "ZoneMarking",
    "action_owners_slot",
    "deferrals_slot",
    "form_routes_slot",
    "layer_loading_slot",
    "marked_zones",
    "page_scan_slot",
    "page_scripts",
    "page_views_slot",
    "render_deferred",
    "route_access_slot",
    "router_stamp_slot",
    "seo_routes_slot",
    "server_backlog",
    "server_load_slot",
    "stamp_router",
    "stash_layer_loading",
    "static_assets_slot",
    "zone_marking_slot",
]
