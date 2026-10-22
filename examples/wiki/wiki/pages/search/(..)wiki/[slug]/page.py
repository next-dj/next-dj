from django.utils.safestring import SafeString
from markup import render_markdown
from wiki.deps import DArticle
from wiki.models import Article

from next import context, page
from next.pages import MetadataDict


@context("peek")
def peek(item: DArticle[Article]) -> Article:
    """Inject the article the preview covers."""
    return item


@context("peek_html")
def peek_html(item: DArticle[Article]) -> SafeString:
    """Markdown body of the article rendered to safe HTML."""
    return render_markdown(item.body_md)


@page.metadata
def peek_meta(peek: Article) -> MetadataDict:
    """Title the tab after the article while the preview covers the results."""
    return {"title": f"Preview · {peek.title}"}
