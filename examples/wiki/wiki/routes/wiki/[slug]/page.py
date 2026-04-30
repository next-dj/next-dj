from django.utils.safestring import SafeString
from wiki.markdown_render import render_markdown
from wiki.models import Article
from wiki.providers import DArticle

from next.pages import context


@context("article")
def article(item: DArticle[Article]) -> Article:
    """Inject the article addressed by the URL slug into the template."""
    return item


@context("rendered_html")
def rendered_html(item: DArticle[Article]) -> SafeString:
    """Markdown body of the article rendered to safe HTML."""
    return render_markdown(item.body_md)
