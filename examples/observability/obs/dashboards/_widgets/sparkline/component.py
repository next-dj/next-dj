from typing import Any

from obs.serializers import WrappedJsContextSerializer

from next import component


# React and Babel-standalone are declared in `obs/dashboards/page.py`, because
# a page-level script lands before this widget's own `component.jsx`.


@component.context(
    "totals_chart", serialize=True, serializer=WrappedJsContextSerializer()
)
def totals_chart(totals: dict[str, int]) -> dict[str, Any]:
    """Expose chart-ready data under `window.Next.context.totals_chart`.

    The override demonstrates that a single `@component.context` key
    can route through a different serializer than the global default.
    The sparkline reads `window.Next.context.totals_chart.data.bars`
    because the envelope is `{"v": 1, "data": ...}`.
    """
    return {
        "bars": [{"name": name, "value": int(value)} for name, value in totals.items()]
    }
