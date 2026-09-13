"""Runtime ports letting an area call another one without importing it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol


if TYPE_CHECKING:
    from pathlib import Path

    from django.forms import BaseForm, BaseFormSet
    from django.http import HttpRequest, HttpResponse

    from next.forms.backends import FormActionBackend
    from next.forms.dispatch.responses import ActionOutcome
    from next.static import StaticCollector
    from next.urls import RouterBackend, RouterManager


def _unbound(subject: str) -> str:
    """Spell the failure of a slot read before the app finished starting."""
    return (
        f"The {subject} is unbound, which means the next app never finished "
        "starting. NextFrameworkConfig.ready() binds it."
    )


class PortSlot[T]:
    """Holds the one implementation of a port composed at app startup.

    The binding is static, so unlike a settings-driven backend manager a
    slot never rebinds itself once the app is ready.
    """

    _unbound_message: ClassVar[str] = _unbound("port")

    def __init__(self) -> None:
        """Start unbound so a missing composition step fails loudly."""
        self._impl: T | None = None

    def set(self, impl: T) -> None:
        """Bind the implementation composed in `AppConfig.ready`."""
        self._impl = impl

    def get(self) -> T:
        """Return the bound implementation."""
        if self._impl is None:
            raise RuntimeError(self._unbound_message)
        return self._impl


class PartialIntentView(Protocol):
    """What a caller reads off a parsed partial-request intent."""

    @property
    def partial(self) -> bool:
        """Whether the request asks for a partial response at all."""
        ...

    @property
    def zones(self) -> tuple[str, ...]:
        """Names of the zones the request asks to re-render."""
        ...

    @property
    def validate_fields(self) -> tuple[str, ...]:
        """Names of the fields the request asks to validate only."""
        ...


class PartialShaper(Protocol):
    """Shapes page and form responses for partial requests.

    The caller decides through `intent` whether a request is partial and
    only then enters a shape method, so a full render never pays for one.
    That intent travels on as an argument, so a shape method never re-reads
    the request to learn what was asked.
    """

    def intent(self, request: HttpRequest) -> PartialIntentView:
        """Return what the request headers ask for."""
        ...

    def zone_response(
        self,
        page_path: Path,
        request: HttpRequest,
        intent: PartialIntentView,
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
        intent: PartialIntentView,
        *,
        action_name: str,
        uid: str,
    ) -> HttpResponse:
        """Return the form morph envelope of a validate-only pass."""
        ...


class RouterAccess(Protocol):
    """Builds the routers of `next.urls` for an area that cannot import it.

    `next.urls` routes to pages and so imports `next.pages`, which leaves the
    watcher and the system checks reaching back the other way. They ask here
    instead and the composition root supplies the one implementation.
    """

    def create_backend(self, config: dict[str, Any]) -> RouterBackend:
        """Return the router one `PAGE_BACKENDS` entry names."""
        ...

    def create_manager(self) -> RouterManager:
        """Return a fresh manager over every configured router."""
        ...


class StaticAssets(Protocol):
    """The static-manager surface one page render calls.

    `next.static` reads page trees and page modules and so imports
    `next.pages`, which leaves the render path reaching back the other way.
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


class PartialShaperSlot(PortSlot["PartialShaper"]):
    """Holds the one shaper implementation composed at app startup."""

    _unbound_message: ClassVar[str] = _unbound("partial shaper")


class RouterAccessSlot(PortSlot["RouterAccess"]):
    """Holds the one router builder composed at app startup."""

    _unbound_message: ClassVar[str] = _unbound("router access port")


class StaticAssetsSlot(PortSlot["StaticAssets"]):
    """Holds the one static manager composed at app startup."""

    _unbound_message: ClassVar[str] = _unbound("static assets port")


partial_shaper_slot = PartialShaperSlot()
router_access_slot = RouterAccessSlot()
static_assets_slot = StaticAssetsSlot()


__all__ = [
    "PartialIntentView",
    "PartialShaper",
    "PartialShaperSlot",
    "PortSlot",
    "RouterAccess",
    "RouterAccessSlot",
    "StaticAssets",
    "StaticAssetsSlot",
    "partial_shaper_slot",
    "router_access_slot",
    "static_assets_slot",
]
