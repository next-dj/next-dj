from collections.abc import Iterator

from catalog.services import category_slugs, product_addresses

from next.seo import SitemapEntry, sitemap


cache = 300


@sitemap.items("catalog/[category]")
def categories() -> Iterator[SitemapEntry]:
    """One listing per category."""
    for slug in category_slugs():
        yield SitemapEntry(kwargs={"category": slug})


@sitemap.items("shop/[category]")
def landings() -> Iterator[SitemapEntry]:
    """One landing per category, weighted above the listing it leads into."""
    for slug in category_slugs():
        yield SitemapEntry(kwargs={"category": slug}, priority=0.8)


@sitemap.items("catalog/[category]/[slug]")
def products() -> Iterator[SitemapEntry]:
    """One detail page per product, addressed through its category."""
    for category, slug in product_addresses():
        yield SitemapEntry(kwargs={"category": category, "slug": slug})
