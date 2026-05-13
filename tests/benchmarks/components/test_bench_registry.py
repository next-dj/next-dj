from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.components.registry import (
    ComponentRegistry,
    ComponentVisibilityResolver,
)
from tests.benchmarks.factories import build_component_info_list


if TYPE_CHECKING:
    from pathlib import Path


_BULK = 500


class TestBenchComponentRegistry:
    @pytest.mark.benchmark(group="components.registry")
    def test_register_bulk(self, tmp_path: Path, benchmark) -> None:
        components = build_component_info_list(tmp_path, _BULK)

        def run() -> None:
            registry = ComponentRegistry()
            registry.register_many(components)

        benchmark(run)

    @pytest.mark.benchmark(group="components.registry")
    def test_lookup_by_name_hit(
        self,
        populated_component_registry: tuple[ComponentRegistry, Path],
        benchmark,
    ) -> None:
        registry, _root = populated_component_registry
        benchmark(registry.__contains__, "c_250")

    @pytest.mark.benchmark(group="components.registry")
    def test_lookup_miss(
        self,
        populated_component_registry: tuple[ComponentRegistry, Path],
        benchmark,
    ) -> None:
        registry, _root = populated_component_registry
        benchmark(registry.__contains__, "not_registered")


class TestBenchComponentVisibility:
    @pytest.mark.benchmark(group="components.visibility")
    def test_visibility_resolve_cold(
        self,
        populated_component_registry: tuple[ComponentRegistry, Path],
        benchmark,
    ) -> None:
        registry, tmp_path = populated_component_registry
        template_path = tmp_path / "leaf" / "page.djx"
        template_path.parent.mkdir()
        template_path.touch()

        def run() -> None:
            resolver = ComponentVisibilityResolver(registry)
            resolver.resolve_visible(template_path)

        benchmark(run)

    @pytest.mark.benchmark(group="components.visibility")
    def test_visibility_resolve_cached(
        self,
        populated_component_registry: tuple[ComponentRegistry, Path],
        benchmark,
    ) -> None:
        registry, tmp_path = populated_component_registry
        template_path = tmp_path / "leaf" / "page.djx"
        template_path.parent.mkdir()
        template_path.touch()
        resolver = ComponentVisibilityResolver(registry)
        resolver.resolve_visible(template_path)
        benchmark(resolver.resolve_visible, template_path)

    @pytest.mark.benchmark(group="components.visibility")
    def test_version_bump_invalidation(
        self,
        populated_component_registry: tuple[ComponentRegistry, Path],
        benchmark,
    ) -> None:
        registry, tmp_path = populated_component_registry
        template_path = tmp_path / "leaf" / "page.djx"
        template_path.parent.mkdir()
        template_path.touch()
        resolver = ComponentVisibilityResolver(registry)
        resolver.resolve_visible(template_path)
        extra = build_component_info_list(tmp_path, 1)

        def run() -> None:
            registry.register(extra[0])
            resolver.resolve_visible(template_path)

        benchmark(run)
