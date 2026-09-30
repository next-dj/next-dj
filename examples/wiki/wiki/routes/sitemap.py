from django.db.models import QuerySet
from wiki.models import Article

from next.seo import sitemap


exclude = ["search"]


@sitemap.items(
    "wiki/[slug]", kwargs=lambda article: {"slug": article.slug}, lastmod="updated_at"
)
def articles() -> QuerySet[Article]:
    """List every article row, paged by the database instead of loaded at once."""
    return Article.objects.only("slug", "updated_at").order_by("pk")
