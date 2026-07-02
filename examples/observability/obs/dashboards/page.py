from obs import metrics

from next.pages import context


scripts = [
    "https://unpkg.com/react@18/umd/react.production.min.js",
    "https://unpkg.com/react-dom@18/umd/react-dom.production.min.js",
    "https://unpkg.com/@babel/standalone@7/babel.min.js",
]


@context("totals")
def totals() -> dict[str, int]:
    """Return the headline counters every overview tile reads from."""
    return {
        "pages_rendered": metrics.total_for_kind("pages.rendered"),
        "components_rendered": metrics.total_for_kind("components.rendered"),
        "actions_dispatched": metrics.total_for_kind("forms.action_dispatched"),
        "html_injections": metrics.read_kind("static").get("html_injected", 0),
    }


@context("busiest_pages")
def busiest_pages() -> list[tuple[str, int]]:
    """Return the five most rendered page modules for the lazy widget."""
    return metrics.top_by_kind("pages.rendered", limit=5)
