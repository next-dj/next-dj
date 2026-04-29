from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from next.deps import DDependencyBase, RegisteredParameterProvider
from next.urls import get_multi_values


if TYPE_CHECKING:
    import inspect

    from django.http import HttpRequest

    from next.deps.context import ResolutionContext


DEFAULT_PER_PAGE = 6
MAX_PER_PAGE = 60


@dataclass(frozen=True, slots=True)
class Filters:
    """Snapshot of the filter set that drives a catalog listing."""

    q: str = ""
    brands: tuple[str, ...] = ()
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    in_stock: bool = False
    sort: str = "newest"

    def is_active(self) -> bool:
        """Return True when at least one filter is non-default."""
        return bool(
            self.q
            or self.brands
            or self.price_min is not None
            or self.price_max is not None
            or self.in_stock
            or self.sort != "newest",
        )


@dataclass(frozen=True, slots=True)
class PageRequest:
    """Validated paging descriptor consumed by `cached_search`."""

    number: int
    per_page: int


class DFilters(DDependencyBase["Filters"]):
    """DI marker that resolves to a `Filters` snapshot."""

    __slots__ = ()


class DPage(DDependencyBase["PageRequest"]):
    """DI marker that resolves to a clamped `PageRequest`."""

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

    def can_handle(
        self,
        param: inspect.Parameter,
        context: ResolutionContext,
    ) -> bool:
        """Match the bare `DFilters` annotation when a request is attached."""
        if param.annotation is not DFilters:
            return False
        return getattr(context, "request", None) is not None

    def resolve(
        self,
        _param: inspect.Parameter,
        context: ResolutionContext,
    ) -> Filters:
        """Return a `Filters` snapshot derived from the current request."""
        return parse_filters(context.request)


class PageProvider(RegisteredParameterProvider):
    """Resolve `DPage[T]`-annotated parameters into a `PageRequest`."""

    def can_handle(
        self,
        param: inspect.Parameter,
        context: ResolutionContext,
    ) -> bool:
        """Match `DPage` annotations when a request is attached."""
        if param.annotation is not DPage:
            return False
        return getattr(context, "request", None) is not None

    def resolve(
        self,
        _param: inspect.Parameter,
        context: ResolutionContext,
    ) -> PageRequest:
        """Return a clamped `PageRequest` derived from `?page` and `?per_page`."""
        g = context.request.GET
        try:
            number = max(1, int(g.get("page") or 1))
        except ValueError:
            number = 1
        try:
            per_page = int(g.get("per_page") or DEFAULT_PER_PAGE)
        except ValueError:
            per_page = DEFAULT_PER_PAGE
        return PageRequest(
            number=number,
            per_page=min(MAX_PER_PAGE, max(1, per_page)),
        )
