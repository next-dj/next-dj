from typing import Any

from next.components import component


# The Chart.js CDN URL lives in the page-level `scripts` list of
# `stats/page.py`, not here, because page scripts are injected before
# this widget's co-located `component.js` that needs `window.Chart`.


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
