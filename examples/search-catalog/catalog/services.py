import hashlib
import json
from typing import Any

from django.core.cache import cache
from django.core.paginator import Paginator

from .models import Category, Filters, Lead, Product


CACHE_KEY_PREFIX = "catalog.search."
CACHE_TTL = 60


def _cache_key(
    filters: Filters, page: int, per_page: int, category_pk: int | None
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
    filters: Filters, page: int, per_page: int, *, category: Category | None = None
) -> dict[str, Any]:
    """Return a paginated search payload, reading from the cache when possible.

    The page slice is materialised into a list so the cached payload keeps no lazy
    queryset that a later evaluation could resolve differently.
    """
    key = _cache_key(filters, page, per_page, category.pk if category else None)
    cached = cache.get(key)
    if cached is not None:
        return cached
    qs = Product.objects.matching(filters)
    if category is not None:
        qs = qs.filter(category=category)
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


def featured_products(count: int) -> list[Product]:
    """Return the `count` newest products in stock, the grid of the home page."""
    in_stock = Product.objects.filter(in_stock=True).select_related("category")
    return list(in_stock.order_by("-created_at")[:count])


def list_categories() -> list[Category]:
    """Return every category in name order."""
    return list(Category.objects.all())


def brands(category: Category | None = None) -> list[str]:
    """Return the brands of the catalog, or of one category when it is given."""
    products = Product.objects.all()
    if category is not None:
        products = products.filter(category=category)
    return products.brand_list()


def product_in(category: Category, slug: str) -> Product:
    """Return the product `slug` names inside `category`, or raise `DoesNotExist`."""
    return Product.objects.select_related("category").get(category=category, slug=slug)


def category_slugs() -> list[str]:
    """Return the slug of every category, the one column a listing URL needs."""
    return list(Category.objects.values_list("slug", flat=True))


def product_addresses() -> list[tuple[str, str]]:
    """Return the category slug and slug of every product, the columns its URL needs."""
    return list(Product.objects.values_list("category__slug", "slug"))


def create_lead(*, category: Category, email: str) -> Lead:
    """Store a visitor who asked for the launch code of `category`."""
    lead = Lead(category=category, email=email)
    lead.full_clean()
    lead.save()
    return lead
