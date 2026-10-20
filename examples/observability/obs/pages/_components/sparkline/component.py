from typing import Any

from obs.serializers import WrappedJsContextSerializer

from next import component


# React and Babel sit in the page, because its scripts land before this one.


@component.context(
    "totals_chart", serialize=True, serializer=WrappedJsContextSerializer()
)
def totals_chart(totals: dict[str, int]) -> dict[str, Any]:
    """Expose chart-ready data under `window.Next.context.totals_chart`.

    Its own serializer wraps the value in a `{"v": 1, "data": ...}` envelope.
    """
    return {
        "bars": [{"name": name, "value": int(value)} for name, value in totals.items()]
    }
