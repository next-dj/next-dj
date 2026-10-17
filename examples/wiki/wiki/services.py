from django.db import transaction
from django.db.models import Q, QuerySet

from wiki.models import Article


def latest_articles() -> list[Article]:
    """Return every article, the freshest first."""
    return list(Article.objects.all())


def search_articles(needle: str) -> list[Article]:
    """Return the articles whose title or body contains `needle`, case aside."""
    text_match = Q(title__icontains=needle) | Q(body_md__icontains=needle)
    return list(Article.objects.filter(text_match))


def article_index() -> QuerySet[Article]:
    """Return the slug and stamp of every article as a lazy queryset in pk order."""
    return Article.objects.only("slug", "updated_at").order_by("pk")


def slug_taken(slug: str, *, exclude_pk: int | None = None) -> bool:
    """Return whether an article other than `exclude_pk` already uses `slug`."""
    return Article.objects.filter(slug=slug).exclude(pk=exclude_pk).exists()


def create_article(*, slug: str, title: str, body_md: str) -> Article:
    """Create an article, validated against the rules of the model."""
    article = Article(slug=slug, title=title, body_md=body_md)
    article.full_clean()
    article.save()
    return article


def update_article(*, article_id: int, slug: str, title: str, body_md: str) -> Article:
    """Rewrite an article, loading it under a lock."""
    with transaction.atomic():
        article = Article.objects.select_for_update().get(pk=article_id)
        article.slug = slug
        article.title = title
        article.body_md = body_md
        article.full_clean()
        article.save()
    return article
