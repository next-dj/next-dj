from django.utils.safestring import SafeString

from next.components import component


scripts = ["https://cdn.jsdelivr.net/npm/marked/marked.min.js"]

EMPTY_HTML = SafeString("")


@component.context("label")
def label(label: str = "Live preview") -> str:
    """Caption shown above the preview pane, overridable through the prop."""
    return label


@component.context("rendered_html")
def rendered_html(rendered_html: SafeString = EMPTY_HTML) -> SafeString:
    """Server-rendered HTML for first paint, empty when the caller renders later."""
    return rendered_html
