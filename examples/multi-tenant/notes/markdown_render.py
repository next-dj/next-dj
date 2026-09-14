import re

import markdown
from django.utils.html import escape
from django.utils.safestring import SafeString


EMPTY_PREVIEW = "<p class='text-slate-400 italic'>Nothing to preview yet.</p>"
UNSAFE_HREF = re.compile(
    r'href="\s*(?:javascript|data|vbscript):[^"]*"', flags=re.IGNORECASE
)


def render_markdown(text: str) -> SafeString:
    """Render a note body to safe HTML for the preview pane.

    Escaping the body before the Markdown renderer leaves `<script>` and friends as text
    while headings, lists, code and links still resolve. `javascript:` and similar
    `href` values are stripped afterwards because auto-link parsing accepts them.
    """
    body = text or ""
    if not body.strip():
        return SafeString(EMPTY_PREVIEW)
    renderer = markdown.Markdown(extensions=["fenced_code", "tables"])
    rendered = renderer.convert(escape(body))
    cleaned = UNSAFE_HREF.sub('href="#"', rendered)
    return SafeString(cleaned)
