import re

import markdown
from django.utils.html import escape, format_html
from django.utils.safestring import SafeString


EMPTY_PREVIEW = format_html(
    "<p class='text-slate-400 italic'>{}</p>", "Nothing to preview yet."
)
UNSAFE_HREF = re.compile(
    r'href="\s*(?:javascript|data|vbscript):[^"]*"', flags=re.IGNORECASE
)


def render_markdown(text: str) -> SafeString:
    """Render Markdown to safe HTML for a page body or a live preview pane.

    Escaping neutralizes `<script>` before rendering, and stripping
    `javascript:`/`data:` hrefs after closes the auto-link gap.
    """
    body = text or ""
    if not body.strip():
        return EMPTY_PREVIEW
    renderer = markdown.Markdown(extensions=["fenced_code", "tables"])
    rendered = renderer.convert(escape(body))
    return SafeString(UNSAFE_HREF.sub('href="#"', rendered))
