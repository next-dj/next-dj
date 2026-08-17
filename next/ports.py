"""Runtime ports letting an area call another one without importing it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol


if TYPE_CHECKING:
    from pathlib import Path

    from django.forms import BaseForm, BaseFormSet
    from django.http import HttpRequest, HttpResponse


_UNBOUND_SHAPER = (
    "The partial shaper is unbound, which means the next app never finished "
    "starting. NextFrameworkConfig.ready() binds it."
)


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
        self, backend: object, request: HttpRequest, outcome: object
    ) -> HttpResponse:
        """Return the envelope for one form action outcome."""
        ...

    def shape_validate(
        self,
        backend: object,
        request: HttpRequest,
        form: BaseForm | BaseFormSet,
        intent: PartialIntentView,
        *,
        action_name: str,
        uid: str,
    ) -> HttpResponse:
        """Return the form morph envelope of a validate-only pass."""
        ...


class PartialShaperSlot:
    """Holds the one shaper implementation composed at app startup.

    The binding is static, so unlike a settings-driven backend manager the
    slot never rebinds itself once the app is ready.
    """

    def __init__(self) -> None:
        """Start unbound so a missing composition step fails loudly."""
        self._impl: PartialShaper | None = None

    def set(self, impl: PartialShaper) -> None:
        """Bind the implementation composed in `AppConfig.ready`."""
        self._impl = impl

    def get(self) -> PartialShaper:
        """Return the bound implementation."""
        if self._impl is None:
            raise RuntimeError(_UNBOUND_SHAPER)
        return self._impl


partial_shaper_slot = PartialShaperSlot()


__all__ = [
    "PartialIntentView",
    "PartialShaper",
    "PartialShaperSlot",
    "partial_shaper_slot",
]
