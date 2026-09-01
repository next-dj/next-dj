from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.template import Context, Template
from django.test import override_settings

from next.static.collector import default_placeholders
from next.static.manager import StaticManager
from tests.benchmarks.factories import build_layout_page
from tests.support import component_info, file_router_config_entry


if TYPE_CHECKING:
    from pathlib import Path

    from next.components import ComponentInfo
    from next.static.collector import StaticCollector


_ASSET_COUNTS = (5, 20)
_COLOCATED_ASSETS = 4
_COMPONENT_INSTANCES = 50


def _page_html() -> str:
    tokens = {slot.name: slot.token for slot in default_placeholders}
    return (
        f"<html><head><title>Bench</title>{tokens['styles']}</head>"
        f"<body><main>row</main>{tokens['scripts']}</body></html>"
    )


def _module_lists(count: int) -> str:
    """Return the `styles` and `scripts` declarations sharing `count` module URLs."""
    styles = [f"/static/bench_{i}.css" for i in range((count + 1) // 2)]
    scripts = [f"/static/bench_{i}.js" for i in range(count // 2)]
    return f"styles = {styles!r}\nscripts = {scripts!r}\n"


def _write_role_files(directory: Path, stem: str) -> None:
    (directory / f"{stem}.css").write_text("body{}")
    (directory / f"{stem}.js").write_text("/* js */")


def _build_pipeline_page(root: Path, assets: int) -> Path:
    """Write a page under one layout carrying `assets` assets in total."""
    page_file = build_layout_page(
        root,
        layouts=1,
        template="<h1>x</h1>",
        page_body=_module_lists(assets - _COLOCATED_ASSETS),
    )
    _write_role_files(root, "layout")
    _write_role_files(page_file.parent, "template")
    return page_file


def _build_pipeline_component(root: Path) -> ComponentInfo:
    """Write a composite component with both co-located asset kinds."""
    component_dir = root / "_components" / "widget"
    component_dir.mkdir(parents=True)
    _write_role_files(component_dir, "component")
    return component_info(component_dir, template="<div>widget</div>")


def _vanilla_template(assets: int) -> Template:
    """Return a vanilla Django template naming `assets` files through `static`."""
    tags = "".join(
        f'<link rel="stylesheet" href="{{% static \'bench_{i}.css\' %}}">'
        for i in range(assets)
    )
    return Template("{% load static %}" + tags)


class TestBenchStaticPipeline:
    """The whole per-request static path, from discovery to the injected HTML."""

    @pytest.mark.parametrize("assets", _ASSET_COUNTS, ids=["n5", "n20"])
    @pytest.mark.benchmark(group="static.pipeline")
    def test_page_pipeline(self, tmp_path: Path, assets: int, benchmark) -> None:
        """Collector, page discovery and injection, the shape a page GET walks."""
        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [file_router_config_entry(pages_dir=tmp_path)]
            }
        ):
            page_file = _build_pipeline_page(tmp_path, assets)
            manager = StaticManager()
            html = _page_html()

            def run() -> str:
                collector = manager.create_collector()
                manager.discover_page_assets(page_file, collector)
                return manager.inject(html, collector)

            run()
            benchmark(run)

    @pytest.mark.benchmark(group="static.pipeline")
    def test_page_pipeline_with_component_instances(
        self, tmp_path: Path, benchmark
    ) -> None:
        """One page mounting the same component fifty times, on one collector."""
        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [file_router_config_entry(pages_dir=tmp_path)]
            }
        ):
            page_file = _build_pipeline_page(tmp_path, _ASSET_COUNTS[0])
            info = _build_pipeline_component(tmp_path)
            manager = StaticManager()
            html = _page_html()

            def run() -> str:
                collector: StaticCollector = manager.create_collector()
                manager.discover_page_assets(page_file, collector)
                for _instance in range(_COMPONENT_INSTANCES):
                    manager.discover_component_assets(info, collector)
                return manager.inject(html, collector)

            run()
            benchmark(run)

    @pytest.mark.parametrize("assets", _ASSET_COUNTS, ids=["n5", "n20"])
    @pytest.mark.benchmark(group="static.pipeline")
    def test_vanilla_static_tags(self, assets: int, benchmark) -> None:
        """Reference line, a vanilla template naming the same number of files."""
        template = _vanilla_template(assets)
        context = Context()
        template.render(context)
        benchmark(template.render, context)
