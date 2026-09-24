import re
import threading

import markdown
from django.utils.html import escape, format_html
from django.utils.safestring import SafeString


EMPTY_PREVIEW = format_html(
    "<p class='text-slate-400 italic'>{}</p>", "Nothing to preview yet."
)
UNSAFE_HREF = re.compile(
    r'href="\s*(?:javascript|data|vbscript):[^"]*"', flags=re.IGNORECASE
)
_LOCAL = threading.local()


def _renderer() -> markdown.Markdown:
    """Return this thread's renderer, since building one costs more than a render."""
    renderer = getattr(_LOCAL, "renderer", None)
    if renderer is None:
        renderer = markdown.Markdown(extensions=["fenced_code", "tables"])
        _LOCAL.renderer = renderer
    return renderer


def render_markdown(text: str) -> SafeString:
    """Render Markdown to safe HTML for a page body or a live preview pane.

    Escaping neutralizes `<script>` before rendering, and stripping
    `javascript:`/`data:` hrefs after closes the auto-link gap.
    """
    body = text or ""
    if not body.strip():
        return EMPTY_PREVIEW
    rendered = _renderer().reset().convert(escape(body))
    return SafeString(UNSAFE_HREF.sub('href="#"', rendered))
