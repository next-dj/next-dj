from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from django.core.cache import cache
from django.core.paginator import Paginator

from .models import Product


if TYPE_CHECKING:
    from django.db.models import QuerySet

    from .models import Category
    from .providers import Filters


CACHE_KEY_PREFIX = "catalog.search."
CACHE_TTL = 60


def _cache_key(
    filters: Filters,
    page: int,
    per_page: int,
    category_pk: int | None,
) -> str:
    """Build a stable hash-based cache key for the given filter set."""
    payload: dict[str, Any] = {
        "q": filters.q,
        "brands": list(filters.brands),
        "price_min": str(filters.price_min) if filters.price_min is not None else None,
        "price_max": str(filters.price_max) if filters.price_max is not None else None,
        "in_stock": filters.in_stock,
        "sort": filters.sort,
        "page": page,
        "per_page": per_page,
        "category": category_pk,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return CACHE_KEY_PREFIX + hashlib.blake2b(blob, digest_size=16).hexdigest()


def cached_search(
    filters: Filters,
    page: int,
    per_page: int,
    *,
    category: Category | None = None,
) -> dict[str, Any]:
    """Return a paginated search payload, reading from the cache when possible.

    The function materialises the page slice into a list so the
    cached payload does not retain a lazy queryset that could be
    re-evaluated later under different conditions. The returned
    payload uses a stable shape so templates can iterate
    `payload['products']` and read pagination flags directly.
    """
    key = _cache_key(filters, page, per_page, category.pk if category else None)
    cached = cache.get(key)
    if cached is not None:
        return cached
    qs = Product.objects.select_related("category")
    if category is not None:
        qs = qs.filter(category=category)
    if filters.q:
        qs = qs.filter(name__icontains=filters.q)
    if filters.brands:
        qs = qs.filter(brand__in=filters.brands)
    if filters.price_min is not None:
        qs = qs.filter(price__gte=filters.price_min)
    if filters.price_max is not None:
        qs = qs.filter(price__lte=filters.price_max)
    if filters.in_stock:
        qs = qs.filter(in_stock=True)
    qs = _apply_sort(qs, filters.sort)
    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(page)
    payload: dict[str, Any] = {
        "products": list(page_obj.object_list),
        "total": paginator.count,
        "page": page_obj.number,
        "num_pages": paginator.num_pages,
        "per_page": per_page,
        "has_next": page_obj.has_next(),
        "has_prev": page_obj.has_previous(),
    }
    cache.set(key, payload, CACHE_TTL)
    return payload


_SORT_ORDERINGS = {
    "price_asc": ("price",),
    "price_desc": ("-price",),
    "name": ("name",),
    "newest": ("-created_at",),
}


def _apply_sort(qs: QuerySet[Product], sort: str) -> QuerySet[Product]:
    """Apply a known ordering to the queryset and fall back to newest."""
    ordering = _SORT_ORDERINGS.get(sort, _SORT_ORDERINGS["newest"])
    return qs.order_by(*ordering)
