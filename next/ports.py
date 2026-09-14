"""Runtime ports letting an area call another one without importing it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from django.core.exceptions import ImproperlyConfigured


if TYPE_CHECKING:
    from pathlib import Path

    from django.forms import BaseForm, BaseFormSet
    from django.http import HttpRequest, HttpResponse

    from next.forms.backends import FormActionBackend
    from next.forms.dispatch.responses import ActionOutcome
    from next.forms.wizard import FormWizard
    from next.partial.headers import PartialIntent
    from next.static import StaticCollector
    from next.urls import RouterBackend, RouterManager


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


class RouterAccess(Protocol):
    """The import seam `next.urls` opens for an area that cannot import it.

    Both methods answer the concrete classes of that area rather than a routing
    abstraction, since `next.urls` imports `next.pages` and the watcher and checks
    reach back the other way.
    """

    def create_backend(self, config: dict[str, Any]) -> RouterBackend:
        """Return the router one `PAGE_BACKENDS` entry names."""
        ...

    def create_manager(self) -> RouterManager:
        """Return a fresh manager over every configured router."""
        ...


class StaticAssets(Protocol):
    """The static-manager surface one page render calls.

    `next.static` reads page trees and page modules and so imports `next.pages`, which
    leaves the render path reaching back the other way.
    """

    def create_collector(self) -> StaticCollector:
        """Return a fresh sink for the assets one render references."""
        ...

    def discover_page_assets(self, file_path: Path, collector: StaticCollector) -> None:
        """Collect the assets co-located with one page."""
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


partial_shaper_slot = PortSlot["PartialShaper"]("partial shaper")
router_access_slot = PortSlot["RouterAccess"]("router access port")
static_assets_slot = PortSlot["StaticAssets"]("static assets port")


__all__ = [
    "PartialShaper",
    "PortSlot",
    "RouterAccess",
    "StaticAssets",
    "partial_shaper_slot",
    "router_access_slot",
    "static_assets_slot",
]
