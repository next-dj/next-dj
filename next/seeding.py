"""The render-context keys every area shares and the ambient frame a render inherits.

It sits outside `next.static` because the page render reaches that area through a port.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from next.ports import static_assets_slot


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from django.http import HttpRequest

    from next.static import StaticCollector


TEMPLATE_PATH_KEY: Final = "current_template_path"
"""Context key naming the template a component name is resolved from."""

PAGE_MODULE_PATH_KEY: Final = "current_page_module_path"
"""Context key naming the `page.py` a page-scoped action resolves against."""

COMPONENT_MODULE_PATH_KEY: Final = "current_component_module_path"
"""Context key naming the `component.py` of the component being rendered."""

ACTION_ANCHOR_KEY: Final = "current_action_anchor"
"""Context key naming the module the actions of the enclosing form resolve against."""

REQUEST_KEY: Final = "request"
"""Context key the render binds the live request under."""

COLLECTOR_KEY: Final = "_static_collector"
"""Context key the render binds its `StaticCollector` under."""

JS_CONTEXT_KEY: Final = "_next_js_context"
"""Context key carrying the `serialize=True` subset a render collected."""

JS_SERIALIZERS_KEY: Final = "_next_js_context_serializers"
"""Context key carrying the serializer chosen per `JS_CONTEXT_KEY` entry."""


@dataclass(frozen=True, slots=True)
class RenderFrame:
    """The ambient values a component render inherits from the page around it.

    A render that copies the surrounding context inherits them for free, so only a
    caller building its context from scratch carries the frame itself.
    """

    template_path: Path | str | None = None
    page_module_path: Path | str | None = None
    action_anchor: Path | str | None = None
    request: HttpRequest | None = None
    collector: StaticCollector | None = None

    def seed(self, context_data: dict[str, Any]) -> None:
        """Write the ambient keys into a context the caller is still building.

        The request stays out, because the render strategies stamp it themselves.
        """
        context_data[TEMPLATE_PATH_KEY] = self.template_path
        context_data[PAGE_MODULE_PATH_KEY] = self.page_module_path
        context_data[ACTION_ANCHOR_KEY] = self.action_anchor
        context_data[COLLECTOR_KEY] = self.collector


EMPTY_FRAME: Final = RenderFrame()
"""The frame a widget carries until a render binds the surrounding page to it."""


_ambient_frame: ContextVar[RenderFrame] = ContextVar(
    "next_ambient_frame", default=EMPTY_FRAME
)


@contextmanager
def ambient_frame(frame: RenderFrame) -> Iterator[None]:
    """Publish `frame` for the widgets a binder had no instance to reach.

    A formset builds `empty_form` on access, so its widgets are born after the bind.
    """
    token = _ambient_frame.set(frame)
    try:
        yield
    finally:
        _ambient_frame.reset(token)


def current_ambient_frame() -> RenderFrame:
    """Return the frame published around the render under way."""
    return _ambient_frame.get()


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


__all__ = [
    "ACTION_ANCHOR_KEY",
    "COLLECTOR_KEY",
    "COMPONENT_MODULE_PATH_KEY",
    "EMPTY_FRAME",
    "JS_CONTEXT_KEY",
    "JS_SERIALIZERS_KEY",
    "PAGE_MODULE_PATH_KEY",
    "REQUEST_KEY",
    "TEMPLATE_PATH_KEY",
    "RenderFrame",
    "ambient_frame",
    "current_ambient_frame",
    "seed_collector",
]
