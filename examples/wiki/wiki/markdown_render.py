from __future__ import annotations

import re

import markdown
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe


EMPTY_PREVIEW = "<p class='text-slate-400 italic'>Nothing to preview yet.</p>"
UNSAFE_HREF = re.compile(
    r'href="\s*(?:javascript|data|vbscript):[^"]*"',
    flags=re.IGNORECASE,
)


def render_markdown(text: str) -> SafeString:
    """Render Markdown text to safe HTML for the page or preview pane.

    Inline HTML in the source is neutralised by escaping the body
    before it reaches the Markdown renderer. Markdown syntax such as
    headings, lists, fenced code, and links still resolves. After
    rendering, ``href`` values pointing at ``javascript:``, ``data:``,
    or ``vbscript:`` URLs are stripped because Markdown auto-link
    parsing accepts them.
    """
    body = text or ""
    if not body.strip():
        return mark_safe(EMPTY_PREVIEW)  # noqa: S308 - static placeholder, no input
    renderer = markdown.Markdown(extensions=["fenced_code", "tables"])
    rendered = renderer.convert(escape(body))
    cleaned = UNSAFE_HREF.sub('href="#"', rendered)
    return mark_safe(cleaned)  # noqa: S308 - escape() and regex above strip vectors
