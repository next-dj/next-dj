from django.utils.safestring import SafeString
from markup import render_markdown
from wiki.models import Article
from wiki.providers import DArticle

from next import context, page
from next.pages import MetadataDict


@context("article")
def article(item: DArticle[Article]) -> Article:
    """Inject the article addressed by the URL slug into the template."""
    return item


@context("rendered_html")
def rendered_html(item: DArticle[Article]) -> SafeString:
    """Markdown body of the article rendered to safe HTML."""
    return render_markdown(item.body_md)


@page.metadata
def article_meta(article: Article) -> MetadataDict:
    """Title and describe the tab from the row the `article` context already fetched.

    A body without a paragraph keeps the description the index declares for the site.
    """
    meta: MetadataDict = {"title": article.title}
    if article.summary:
        meta["description"] = article.summary
    return meta
