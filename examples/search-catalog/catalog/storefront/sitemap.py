from collections.abc import Iterator

from catalog.models import Category, Product

from next.seo import Entry, sitemap


cache = 300


@sitemap.items("catalog/[category]")
def categories() -> Iterator[Entry]:
    """One listing per category."""
    for slug in Category.objects.values_list("slug", flat=True):
        yield Entry(kwargs={"category": slug})


@sitemap.items("catalog/[category]/[slug]")
def products() -> Iterator[Entry]:
    """One detail page per product, addressed through its category."""
    rows = Product.objects.values_list("category__slug", "slug")
    for category, slug in rows:
        yield Entry(kwargs={"category": category, "slug": slug})
