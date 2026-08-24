from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.components.info import ComponentInfo
from next.components.loading import ModuleLoader
from next.components.renderers import (
    COMPONENT_PROPS_CONTEXT_KEY,
    ComponentTemplateLoader,
    CompositeComponentRenderer,
    _guarded_keys,
    _reject_collisions,
)
from tests.benchmarks.factories import build_component_info


if TYPE_CHECKING:
    from pathlib import Path


_COMPONENT_MODULE = """from next.components import component


@component.context
def extra():
    return {"env": "prod", "version": "1"}
"""


def _build_composite_component(root: Path) -> ComponentInfo:
    """Write a component.py plus component.djx pair and describe it."""
    (root / "component.py").write_text(_COMPONENT_MODULE)
    (root / "component.djx").write_text(
        "<div>{{ title }} {{ env }} {{ version }}</div>"
    )
    return ComponentInfo(
        name="card",
        scope_root=root,
        scope_relative="",
        template_path=root / "component.djx",
        module_path=(root / "component.py").resolve(),
        is_simple=False,
    )


class TestBenchCompositeRender:
    @pytest.mark.benchmark(group="components.render")
    def test_render_end_to_end(self, tmp_path: Path, benchmark) -> None:
        """The whole composite render, from reading the template to the output.

        Disk and template compilation dominate this number, so it tracks the
        render as a whole rather than any single step of it.
        """
        info = _build_composite_component(tmp_path)
        module_loader = ModuleLoader()
        renderer = CompositeComponentRenderer(
            module_loader, ComponentTemplateLoader(module_loader)
        )
        context = {"title": "Hi", COMPONENT_PROPS_CONTEXT_KEY: frozenset({"title"})}
        # The first render imports component.py and registers its context
        # function, which must not happen once per warmup iteration.
        renderer.render(info, context, None)

        benchmark(renderer.render, info, context, None)


class TestBenchContextCollisionGuard:
    @pytest.mark.benchmark(group="components.render")
    def test_reject_collisions_on_a_clean_dict(self, tmp_path: Path, benchmark) -> None:
        """The guard cost a keyless context dict pays when nothing collides."""
        info = build_component_info(tmp_path)
        guarded = _guarded_keys({COMPONENT_PROPS_CONTEXT_KEY: frozenset({"title"})})
        data = {"env": "prod", "version": "1"}

        benchmark(_reject_collisions, info, data, guarded)
