from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.template import Context, Template, engines
from django.template.engine import Engine

from next.components import components_manager
from next.components.backends import FileComponentsBackend


if TYPE_CHECKING:
    from pathlib import Path

    from django.template.base import Template as CompiledTemplate


# Touching the engines triggers the global init that registers the next tags,
# so warm it up here and keep the bench body on render cost alone.
engines.all()

_CARD = '<article class="card"><h2>{{ title }}</h2><p>{{ body }}</p></article>'

_COMPONENT_TAG = '{% component "card" title="Bench" body="Body text" %}'
_INCLUDE_TAG = (
    '{% with title="Bench" body="Body text" %}{% include "card.html" %}{% endwith %}'
)
_INCLUSION_TAG = '{% bench_card "Bench" "Body text" %}'

_PAGE_TAG_COUNT = 10

_CACHED_LOADER = (
    "django.template.loaders.cached.Loader",
    ["django.template.loaders.filesystem.Loader"],
)


def _component_page(
    root: Path, monkeypatch: pytest.MonkeyPatch, count: int
) -> tuple[CompiledTemplate, Context]:
    """Serve `card` from a real file backend and compile a page of `count` tags."""
    components = root / "components"
    components.mkdir()
    (components / "card.djx").write_text(_CARD)
    backend = FileComponentsBackend({"DIRS": [str(components)], "COMPONENTS_DIR": "_c"})
    backend.discover()
    monkeypatch.setattr(components_manager, "_backends", [backend])
    monkeypatch.setattr(components_manager, "_loaded", True)
    monkeypatch.setattr(components_manager, "_walk_registered_folders", set())

    page = root / "page.djx"
    page.write_text("")
    template = Template("{% load components %}" + _COMPONENT_TAG * count)
    return template, Context({"current_template_path": str(page)})


def _vanilla_page(root: Path, source: str, count: int) -> CompiledTemplate:
    """Compile a page of `count` vanilla tags against a cached-loader engine."""
    (root / "card.html").write_text(_CARD)
    engine = Engine(
        dirs=[str(root)],
        loaders=[_CACHED_LOADER],
        builtins=["tests.benchmarks.templatetags.vanilla"],
    )
    return engine.from_string(source * count)


class TestBenchComponentTag:
    """`{% component %}` on a page beside the vanilla tags it competes with.

    One tag prices the whole path, ten tags the marginal tag, and the ``{% include %}``
    and ``inclusion_tag`` rows price the same work in plain Django.
    """

    @pytest.mark.benchmark(group="templatetags.component")
    def test_one_component_tag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, benchmark
    ) -> None:
        template, context = _component_page(tmp_path, monkeypatch, 1)
        template.render(context)

        benchmark(template.render, context)

    @pytest.mark.benchmark(group="templatetags.component")
    def test_ten_component_tags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, benchmark
    ) -> None:
        template, context = _component_page(tmp_path, monkeypatch, _PAGE_TAG_COUNT)
        template.render(context)

        benchmark(template.render, context)

    @pytest.mark.benchmark(group="templatetags.component")
    def test_one_vanilla_include(self, tmp_path: Path, benchmark) -> None:
        template = _vanilla_page(tmp_path, _INCLUDE_TAG, 1)
        context = Context({})
        template.render(context)

        benchmark(template.render, context)

    @pytest.mark.benchmark(group="templatetags.component")
    def test_ten_vanilla_includes(self, tmp_path: Path, benchmark) -> None:
        template = _vanilla_page(tmp_path, _INCLUDE_TAG, _PAGE_TAG_COUNT)
        context = Context({})
        template.render(context)

        benchmark(template.render, context)

    @pytest.mark.benchmark(group="templatetags.component")
    def test_one_vanilla_inclusion_tag(self, tmp_path: Path, benchmark) -> None:
        template = _vanilla_page(tmp_path, _INCLUSION_TAG, 1)
        context = Context({})
        template.render(context)

        benchmark(template.render, context)
