"""Runtime ports letting an area call another one without importing it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from django.core.exceptions import ImproperlyConfigured


if TYPE_CHECKING:
    from pathlib import Path

    from django.forms import BaseForm, BaseFormSet
    from django.http import HttpRequest, HttpResponse
    from django.template.base import NodeList
    from django.urls import URLPattern

    from next.components.info import ComponentInfo
    from next.forms.backends import FormActionBackend
    from next.forms.dispatch.responses import ActionOutcome
    from next.forms.wizard import FormWizard
    from next.partial.headers import PartialIntent
    from next.static import StaticCollector
    from next.urls import RouterBackend, RouterManager, URLPatternParser


class PortSlot[T]:
    """Holds the one implementation of a port composed at app startup.

    The binding is static, so unlike a settings-driven backend manager a
    slot never rebinds itself once the app is ready.
    """

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


class PartialShaper(Protocol):
    """Shapes page and form responses for partial requests.

    The caller decides through `intent` whether a request is partial, and that
    intent travels on as an argument so no shape method re-reads the request.
    """

    def intent(self, request: HttpRequest) -> PartialIntent:
        """Return what the request headers ask for."""
        ...

    def zone_response(
        self,
        page_path: Path,
        request: HttpRequest,
        intent: PartialIntent,
        *,
        dynamic_body: bool,
        url_kwargs: dict[str, object],
    ) -> HttpResponse:
        """Return the envelope for the zones the intent named."""
        ...

    def shape_response(
        self, backend: FormActionBackend, request: HttpRequest, outcome: ActionOutcome
    ) -> HttpResponse:
        """Return the envelope for one form action outcome."""
        ...

    def shape_validate(
        self,
        backend: FormActionBackend,
        request: HttpRequest,
        form: BaseForm | BaseFormSet,
        intent: PartialIntent,
        *,
        action_name: str,
        uid: str,
        wizard: FormWizard | None = None,
    ) -> HttpResponse:
        """Return the form morph envelope of a validate-only pass."""
        ...

    def set_vary(self, response: HttpResponse) -> None:
        """Declare the partial request headers `response` was negotiated on.

        A full page and a zone envelope answer the same URL, so a shared cache
        needs the same `Vary` set on both or it serves one where the other belongs.
        """
        ...


class PageScan(Protocol):
    """The page-tree scan the checks and the discovery helpers run.

    `next.pages.scan` reads the router manager from `next.discovery`, so the scan
    is reached through the port rather than through an import that closes the loop.
    """

    def load_scanned_page_modules(
        self, router_manager: RouterManager
    ) -> list[tuple[str, Path]]:
        """Execute every routed `page.py`, answering the ones that loaded."""
        ...


class RouterAccess(Protocol):
    """The import seam `next.urls` opens for an area that cannot import it.

    `next.urls` imports `next.pages`, so the watcher and checks reach back through this.
    """

    def create_backend(self, config: dict[str, Any]) -> RouterBackend:
        """Return the router one `PAGE_BACKENDS` entry names."""
        ...

    def create_manager(self) -> RouterManager:
        """Return a fresh manager over every configured router."""
        ...

    def url_parser(self) -> URLPatternParser:
        """Return the parser the file router routes bracket segments through."""
        ...


class StaticAssets(Protocol):
    """The static-manager surface one page render calls.

    `next.static` imports `next.pages`, so the render path reaches back through this.
    """

    def create_collector(self) -> StaticCollector:
        """Return a fresh sink for the assets one render references."""
        ...

    def discover_page_assets(self, file_path: Path, collector: StaticCollector) -> None:
        """Collect the assets co-located with one page."""
        ...

    def collect_component_assets(
        self, info: ComponentInfo, collector: StaticCollector | None
    ) -> None:
        """Collect the assets co-located with one composite component."""
        ...

    def inject(
        self,
        html: str,
        collector: StaticCollector,
        *,
        page_path: Path,
        request: HttpRequest | None,
    ) -> str:
        """Replace every placeholder token in `html` with rendered tags."""
        ...


class ComponentTags(Protocol):
    """What the component tag library answers about a compiled template.

    The library imports `next.pages`, so a pages check asks it through this.
    """

    def component_names(self, nodelist: NodeList) -> list[str]:
        """Return the name of every `{% component %}` tag the nodes hold."""
        ...


class SeoRoutes(Protocol):
    """The routes the seo area adds to the lazy urlpatterns.

    `next.seo` imports `next.urls`, so the pattern concat reaches back through this.
    """

    def patterns(self) -> list[URLPattern]:
        """Return the sitemap and robots routes, spliced after every page route."""
        ...


component_tags_slot = PortSlot["ComponentTags"]("component tags port")
page_scan_slot = PortSlot["PageScan"]("page scan port")
partial_shaper_slot = PortSlot["PartialShaper"]("partial shaper")
router_access_slot = PortSlot["RouterAccess"]("router access port")
seo_routes_slot = PortSlot["SeoRoutes"]("seo routes port")
static_assets_slot = PortSlot["StaticAssets"]("static assets port")


__all__ = [
    "ComponentTags",
    "PageScan",
    "PartialShaper",
    "PortSlot",
    "RouterAccess",
    "SeoRoutes",
    "StaticAssets",
    "component_tags_slot",
    "page_scan_slot",
    "partial_shaper_slot",
    "router_access_slot",
    "seo_routes_slot",
    "static_assets_slot",
]
