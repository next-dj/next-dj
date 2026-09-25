from collections.abc import Iterator

from wiki.models import Article

from next.seo import Entry, sitemap


exclude = ["search", "articles/**"]


@sitemap.items("wiki/[slug]")
def articles() -> Iterator[Entry]:
    """List every article row, stamped with the time it was last saved."""
    for article in Article.objects.only("slug", "updated_at"):
        yield Entry(kwargs={"slug": article.slug}, lastmod=article.updated_at)
