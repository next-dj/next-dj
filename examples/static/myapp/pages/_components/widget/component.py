from next.components import context


styles = [
    "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css",
]
scripts: list[str] = []


@context("subtitle")
def widget_subtitle() -> str:
    """Static subtitle exposed to the widget template as ``subtitle``."""
    return "Composite widget example"
