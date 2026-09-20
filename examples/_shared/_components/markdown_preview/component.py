from django.utils.safestring import SafeString
from markup import render_markdown

from next import component


scripts = ["https://cdn.jsdelivr.net/npm/marked/marked.min.js"]


@component.context("label")
def label(label: str = "Live preview") -> str:
    """Caption shown above the preview pane, overridable through the prop."""
    return label


@component.context("rendered_html")
def rendered_html(source: str | None = None) -> SafeString:
    """Render the Markdown source for first paint, before the client takes over."""
    return render_markdown(source or "")
