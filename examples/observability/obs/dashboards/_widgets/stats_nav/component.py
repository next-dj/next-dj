from next.components import component


NAV_ITEMS = (
    ("/stats/", "Live"),
    ("/stats/pages/", "Pages"),
    ("/stats/components/", "Components"),
    ("/stats/forms/", "Forms"),
    ("/stats/static/", "Static"),
)


@component.context("nav_items")
def nav_items() -> tuple[tuple[str, str], ...]:
    """Return the static nav-link table rendered by the stats layout."""
    return NAV_ITEMS
