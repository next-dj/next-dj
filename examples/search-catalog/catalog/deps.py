import inspect
from decimal import Decimal, InvalidOperation

from django.http import HttpRequest

from next.deps import (
    DDependencyBase,
    Loader,
    RegisteredParameterProvider,
    ResolutionContext,
)
from next.partial import NumberPager
from next.urls import get_multi_values

from .models import Filters, Product


listing = NumberPager(per_page=6)

product_cards = Loader(Product.objects.cards)


class DFilters(DDependencyBase["Filters"]):
    """DI marker that resolves to a `Filters` snapshot."""

    __slots__ = ()


def _decimal_or_none(raw: str | None) -> Decimal | None:
    """Parse a string into a `Decimal` and return `None` on failure."""
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def parse_filters(request: HttpRequest) -> Filters:
    """Build a `Filters` snapshot from `request.GET`."""
    g = request.GET
    return Filters(
        q=g.get("q", "").strip(),
        brands=tuple(get_multi_values(request, "brand")),
        price_min=_decimal_or_none(g.get("price_min")),
        price_max=_decimal_or_none(g.get("price_max")),
        in_stock=g.get("in_stock") in {"1", "true", "on"},
        sort=g.get("sort") or "newest",
    )


class FiltersProvider(RegisteredParameterProvider):
    """Resolve `DFilters`-annotated parameters into a `Filters` snapshot."""

    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Match the bare `DFilters` annotation when a request is attached."""
        if param.annotation is not DFilters:
            return False
        return getattr(context, "request", None) is not None

    def static_can_handle(self, param: inspect.Parameter) -> bool | None:
        """Rule out a foreign annotation. A match still needs an attached request."""
        return None if param.annotation is DFilters else False

    def resolve(self, _param: inspect.Parameter, context: ResolutionContext) -> Filters:
        """Return a `Filters` snapshot derived from the current request."""
        return parse_filters(context.request)
