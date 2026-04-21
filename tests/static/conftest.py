from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.components import ComponentInfo
from next.static import (
    AssetDiscovery,
    StaticBackend,
    StaticCollector,
    StaticFilesBackend,
    StaticManager,
    default_kinds,
    reset_default_manager,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path


CSS_URL = "https://example.com/a.css"
JS_URL = "https://example.com/a.js"


class DeterministicBackend(StaticFilesBackend):
    """Backend with stable deterministic URLs for discovery-order tests."""

    def register_file(
        self,
        _source_path: Path,
        logical_name: str,
        kind: str,
    ) -> str:
        return f"/static/next/{logical_name}{default_kinds.extension(kind)}"


@pytest.fixture()
def fresh_manager() -> StaticManager:
    return StaticManager()


@pytest.fixture()
def collector() -> StaticCollector:
    return StaticCollector()


@pytest.fixture()
def file_backend() -> StaticBackend:
    return DeterministicBackend()


@pytest.fixture()
def reset_default() -> Generator[None, None, None]:
    yield
    reset_default_manager()


@pytest.fixture()
def simple_component(tmp_path: Path) -> ComponentInfo:
    template_path = tmp_path / "card.djx"
    template_path.write_text("<div>card</div>")
    return ComponentInfo(
        name="card",
        scope_root=tmp_path,
        scope_relative="",
        template_path=template_path,
        module_path=None,
        is_simple=True,
    )


@pytest.fixture()
def composite_component(tmp_path: Path) -> ComponentInfo:
    comp_dir = tmp_path / "_components" / "widget"
    comp_dir.mkdir(parents=True)
    template_path = comp_dir / "component.djx"
    template_path.write_text("<div>widget</div>")
    module_path = comp_dir / "component.py"
    module_path.write_text(
        'styles = ["https://cdn.example.com/extra.css"]\n'
        'scripts = ["https://cdn.example.com/extra.js"]\n'
    )
    (comp_dir / "component.css").write_text(".widget {}")
    (comp_dir / "component.js").write_text("/* widget */")
    return ComponentInfo(
        name="widget",
        scope_root=tmp_path,
        scope_relative="",
        template_path=template_path,
        module_path=module_path,
        is_simple=False,
    )


@pytest.fixture()
def make_discovery() -> Callable[..., tuple[AssetDiscovery, StaticManager]]:
    """Build an ``AssetDiscovery`` wired to a given backend and page roots."""

    def _factory(
        backend: StaticBackend,
        page_roots: tuple[Path, ...] = (),
    ) -> tuple[AssetDiscovery, StaticManager]:
        manager = StaticManager()
        manager._backends = [backend]
        manager._cached_page_roots = page_roots
        return AssetDiscovery(manager), manager

    return _factory
