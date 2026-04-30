import re

import markdown
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

from next.components import component


scripts = ["https://cdn.jsdelivr.net/npm/marked/marked.min.js"]


EMPTY_PREVIEW = "<p class='text-slate-400 italic'>Nothing to preview yet.</p>"
UNSAFE_HREF = re.compile(
    r'href="\s*(?:javascript|data|vbscript):[^"]*"',
    flags=re.IGNORECASE,
)


@component.context("rendered_html")
def render_html(body: str = "") -> SafeString:
    """Return the note body rendered as Markdown for the preview pane.

    The pipeline is conservative on purpose. Inline HTML in the
    source is neutralised by escaping the body before it reaches the
    Markdown renderer, so `<script>` and friends survive only as
    escaped text. Markdown syntax such as headings, lists, fenced
    code, and links still resolves. After rendering, `href` values
    that point at `javascript:`, `data:`, or `vbscript:` URLs are
    stripped because Markdown auto-link parsing accepts them.
    """
    text = body or ""
    if not text.strip():
        return mark_safe(EMPTY_PREVIEW)  # noqa: S308 - static content, no user input
    renderer = markdown.Markdown(extensions=["fenced_code", "tables"])
    rendered = renderer.convert(escape(text))
    cleaned = UNSAFE_HREF.sub('href="#"', rendered)
    return mark_safe(cleaned)  # noqa: S308 - escape() and regex above strip vectors
