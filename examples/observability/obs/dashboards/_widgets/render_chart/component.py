from typing import Any

from next.components import component


# Chart.js loads from a CDN. The framework collects this URL through
# `{% collect_scripts %}` and dedupes it across renders, so listing it
# once in the component module is enough for every page that mounts
# the chart.
scripts = ["https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"]


@component.context("render_rates", serialize=True)
def render_rates(live_stats: dict[str, Any]) -> dict[str, Any]:
    """Expose the per-source totals under `window.Next.context.render_rates`.

    No `serializer=` override here. The value travels through the
    process-wide `JS_CONTEXT_SERIALIZER`, so the rendered HTML carries
    a flat object. Compare against the wrapped `live_stats` and
    `totals_chart` keys to see the per-key override semantics.
    """
    totals = live_stats["totals"]
    return {
        "window": live_stats["window"],
        "bars": [
            {"name": "pages", "value": int(totals["pages"])},
            {"name": "components", "value": int(totals["components"])},
            {"name": "actions", "value": int(totals["actions"])},
        ],
    }
