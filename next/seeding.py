"""The render-context keys every area shares and the seed of the render collector.

It sits outside `next.static` because the page render reaches that area through a port.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from next.ports import static_assets_slot


if TYPE_CHECKING:
    from pathlib import Path

    from next.static import StaticCollector


COLLECTOR_KEY: Final = "_static_collector"
"""Context key the render binds its `StaticCollector` under."""

JS_CONTEXT_KEY: Final = "_next_js_context"
"""Context key carrying the `serialize=True` subset a render collected."""

JS_SERIALIZERS_KEY: Final = "_next_js_context_serializers"
"""Context key carrying the serializer chosen per `JS_CONTEXT_KEY` entry."""


def seed_collector(page_path: Path, context_data: dict[str, object]) -> StaticCollector:
    """Return a collector hydrated from `context_data` and bound back into it.

    A page render and a standalone zone render each seed one collector the same way.
    """
    assets = static_assets_slot.get()
    collector = assets.create_collector()
    js_context = context_data.pop(JS_CONTEXT_KEY, None)
    js_serializers = context_data.pop(JS_SERIALIZERS_KEY, None)
    if isinstance(js_context, dict):
        # A zone render is handed whatever context its caller kept, so neither
        # key is trusted to hold what `build_render_context` left behind.
        serializers = js_serializers if isinstance(js_serializers, dict) else {}
        for key, value in js_context.items():
            collector.add_js_context(key, value, serializer=serializers.get(key))
    assets.discover_page_assets(page_path, collector)
    context_data[COLLECTOR_KEY] = collector
    return collector


__all__ = ["COLLECTOR_KEY", "JS_CONTEXT_KEY", "JS_SERIALIZERS_KEY", "seed_collector"]
