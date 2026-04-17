from next.components import context


styles: list[str] = []
scripts = [
    "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js",
]


@context("labels")
def chart_labels() -> list[str]:
    """Static x-axis labels rendered by the chart template."""
    return ["Jan", "Feb", "Mar", "Apr"]


@context("values")
def chart_values() -> list[int]:
    """Static bar heights matching the labels list."""
    return [40, 70, 55, 90]
