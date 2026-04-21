"""Benchmarks for ``next.components.registry``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.components.info import ComponentInfo
from next.components.registry import (
    ComponentRegistry,
    ComponentVisibilityResolver,
)


if TYPE_CHECKING:
    from pathlib import Path


_BULK = 500


def _make_components(root: Path, count: int) -> list[ComponentInfo]:
    return [
        ComponentInfo(
            name=f"c_{i}",
            scope_root=root,
            scope_relative="",
            template_path=root / f"c_{i}.djx",
            module_path=None,
            is_simple=True,
        )
        for i in range(count)
    ]


class TestBenchComponentRegistry:
    @pytest.mark.benchmark(group="components.registry")
    def test_register_bulk(self, tmp_path: Path, benchmark) -> None:
        components = _make_components(tmp_path, _BULK)

        def run() -> None:
            registry = ComponentRegistry()
            registry.register_many(components)

        benchmark(run)

    @pytest.mark.benchmark(group="components.registry")
    def test_lookup_by_name_hit(self, tmp_path: Path, benchmark) -> None:
        registry = ComponentRegistry()
        registry.register_many(_make_components(tmp_path, _BULK))
        benchmark(registry.__contains__, "c_250")

    @pytest.mark.benchmark(group="components.registry")
    def test_lookup_miss(self, tmp_path: Path, benchmark) -> None:
        registry = ComponentRegistry()
        registry.register_many(_make_components(tmp_path, _BULK))
        benchmark(registry.__contains__, "not_registered")


class TestBenchComponentVisibility:
    @pytest.mark.benchmark(group="components.visibility")
    def test_visibility_resolve_cold(self, tmp_path: Path, benchmark) -> None:
        registry = ComponentRegistry()
        registry.mark_as_root(tmp_path)
        registry.register_many(_make_components(tmp_path, _BULK))
        template_path = tmp_path / "leaf" / "page.djx"
        template_path.parent.mkdir()
        template_path.touch()

        def run() -> None:
            resolver = ComponentVisibilityResolver(registry)
            resolver.resolve_visible(template_path)

        benchmark(run)

    @pytest.mark.benchmark(group="components.visibility")
    def test_visibility_resolve_cached(self, tmp_path: Path, benchmark) -> None:
        registry = ComponentRegistry()
        registry.mark_as_root(tmp_path)
        registry.register_many(_make_components(tmp_path, _BULK))
        template_path = tmp_path / "leaf" / "page.djx"
        template_path.parent.mkdir()
        template_path.touch()
        resolver = ComponentVisibilityResolver(registry)
        resolver.resolve_visible(template_path)
        benchmark(resolver.resolve_visible, template_path)

    @pytest.mark.benchmark(group="components.visibility")
    def test_version_bump_invalidation(self, tmp_path: Path, benchmark) -> None:
        registry = ComponentRegistry()
        registry.mark_as_root(tmp_path)
        registry.register_many(_make_components(tmp_path, _BULK))
        template_path = tmp_path / "leaf" / "page.djx"
        template_path.parent.mkdir()
        template_path.touch()
        resolver = ComponentVisibilityResolver(registry)
        resolver.resolve_visible(template_path)
        extra = _make_components(tmp_path, 1)

        def run() -> None:
            registry.register(extra[0])
            resolver.resolve_visible(template_path)

        benchmark(run)
