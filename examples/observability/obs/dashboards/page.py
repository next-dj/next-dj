from obs import metrics

from next import context


scripts = [
    "https://unpkg.com/react@18/umd/react.production.min.js",
    "https://unpkg.com/react-dom@18/umd/react-dom.production.min.js",
    "https://unpkg.com/@babel/standalone@7/babel.min.js",
]


@context("totals", zone="overview-totals")
def totals() -> dict[str, int]:
    """Return the headline counters every overview tile reads from.

    The `zone=` binding keeps the four aggregations off the lazy `busiest-pages` GET.
    """
    return {
        "pages_rendered": metrics.total_for_kind("pages.rendered"),
        "components_rendered": metrics.total_for_kind("components.rendered"),
        "actions_dispatched": metrics.total_for_kind("forms.action_dispatched"),
        "html_injections": metrics.read_kind("static").get("html_injected", 0),
    }
