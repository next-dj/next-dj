from typing import Any

from next import component


# The Chart.js URL lives in the `scripts` list of `stats/page.py` so it loads first.


@component.context("render_rates", serialize=True)
def render_rates(live_stats: dict[str, Any]) -> dict[str, Any]:
    """Expose the per-source totals under `window.Next.context.render_rates`.

    With no `serializer=` it goes through the global `JS_CONTEXT_SERIALIZER` flat.
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
