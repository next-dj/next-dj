from collections.abc import Iterator

from catalog.models import Category, Product

from next.seo import SitemapEntry, sitemap


cache = 300


@sitemap.items("catalog/[category]")
def categories() -> Iterator[SitemapEntry]:
    """One listing per category."""
    for slug in Category.objects.values_list("slug", flat=True):
        yield SitemapEntry(kwargs={"category": slug})


@sitemap.items("shop/[category]")
def landings() -> Iterator[SitemapEntry]:
    """One landing per category, weighted above the listing it leads into."""
    for slug in Category.objects.values_list("slug", flat=True):
        yield SitemapEntry(kwargs={"category": slug}, priority=0.8)


@sitemap.items("catalog/[category]/[slug]")
def products() -> Iterator[SitemapEntry]:
    """One detail page per product, addressed through its category."""
    rows = Product.objects.values_list("category__slug", "slug")
    for category, slug in rows:
        yield SitemapEntry(kwargs={"category": category, "slug": slug})
