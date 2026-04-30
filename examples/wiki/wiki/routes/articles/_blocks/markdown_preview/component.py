from __future__ import annotations

from typing import TYPE_CHECKING

from wiki.markdown_render import render_markdown

from next.components import component


if TYPE_CHECKING:
    from django.utils.safestring import SafeString


scripts = ["https://cdn.jsdelivr.net/npm/marked/marked.min.js"]


@component.context("rendered_html")
def render_html(body: str = "") -> SafeString:
    """Render the article body as Markdown for the preview pane.

    The pipeline escapes raw HTML before Markdown parses the text and
    neutralises ``javascript`` style hrefs after rendering. The same
    ``render_markdown`` helper powers the full article view so the
    preview matches what readers see.
    """
    return render_markdown(body)
