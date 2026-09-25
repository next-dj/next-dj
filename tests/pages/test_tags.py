import re
from pathlib import Path
from unittest.mock import patch

import pytest
from django.template import Context, TemplateSyntaxError
from django.template.base import Template
from django.test import RequestFactory
from django.utils import translation
from django.utils.functional import lazy

from next.components import ComponentInfo, components_manager
from next.pages.metadata import MetadataThunk, PageMetadataRegistry
from next.seeding import METADATA_KEY, TEMPLATE_PATH_KEY
from tests.support import write_page_chain


def _render(source: str, **ctx) -> str:
    return Template(source).render(Context(ctx))


def _thunk(
    tmp_path: Path, source: str, calls: list[int] | None = None
) -> MetadataThunk:
    """Return a thunk over a fresh registry, counting the callable's runs in `calls`."""
    (leaf,) = write_page_chain(tmp_path, [("leaf", source)])
    registry = PageMetadataRegistry()
    if calls is not None:

        def meta() -> dict[str, object]:
            calls.append(1)
            return {"title": lazy(translation.get_language, str)()}

        registry.register(leaf, meta)
    return MetadataThunk(registry, leaf, RequestFactory().get("/leaf/"), {}, {}, {})


class TestTemplatePlaceholderTag:
    """``{% template %}`` marks the hole a composed layout fills."""

    @pytest.mark.parametrize(
        "source",
        ["a{% template %}b", "a{%  template  %}b", "a{%\n  template\n%}b"],
        ids=["tight", "padded", "multiline"],
    )
    def test_single_form_renders_nothing(self, source) -> None:
        assert _render(source) == "ab"

    def test_paired_form_renders_its_fallback(self) -> None:
        source = "a{% #template %}<p>{{ msg }}</p>{% /template %}b"
        assert _render(source, msg="none yet") == "a<p>none yet</p>b"

    def test_paired_form_renders_an_empty_body_as_nothing(self) -> None:
        assert _render("a{% #template %}{% /template %}b") == "ab"

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            ('{% template "main" %}', "{% template %} tag takes no arguments"),
            (
                '{% #template "main" %}x{% /template %}',
                "{% #template %} tag takes no arguments",
            ),
        ],
        ids=["single", "paired"],
    )
    def test_arguments_are_rejected(self, source, message) -> None:
        with pytest.raises(TemplateSyntaxError, match=re.escape(message)):
            Template(source)


class TestMetadataTag:
    """``{% metadata %}`` renders the head of the page whose thunk it finds."""

    def test_no_thunk_renders_nothing(self) -> None:
        assert _render("a{% metadata %}b") == "ab"

    def test_a_foreign_value_under_the_key_renders_nothing(self) -> None:
        assert _render("a{% metadata %}b", **{METADATA_KEY: {"title": "T"}}) == "ab"

    def test_a_thunk_renders_the_head_lines(self, tmp_path: Path) -> None:
        thunk = _thunk(tmp_path, 'metadata = {"title": "T", "description": "D"}\n')
        assert _render("{% metadata %}", **{METADATA_KEY: thunk}) == (
            '<title>T</title>\n<meta name="description" content="D">'
        )

    def test_the_request_of_the_thunk_resolves_the_canonical(
        self, tmp_path: Path
    ) -> None:
        source = 'metadata = {"canonical": True, "base": "https://acme.example"}\n'
        thunk = _thunk(tmp_path, source)
        assert _render("{% metadata %}", **{METADATA_KEY: thunk}) == (
            '<link rel="canonical" href="https://acme.example/leaf/">'
        )

    @pytest.mark.parametrize(
        "source", ['{% metadata "x" %}', "{% metadata x=1 %}"], ids=["arg", "kwarg"]
    )
    def test_arguments_are_rejected(self, source: str) -> None:
        message = "{% metadata %} tag takes no arguments"
        with pytest.raises(TemplateSyntaxError, match=re.escape(message)):
            Template(source)

    def test_two_tags_resolve_the_thunk_once(self, tmp_path: Path) -> None:
        calls: list[int] = []
        thunk = _thunk(tmp_path, "x = 1\n", calls)
        with translation.override("en"):
            html = _render("{% metadata %}|{% metadata %}", **{METADATA_KEY: thunk})
        assert html == "<title>en</title>|<title>en</title>"
        assert calls == [1]

    def test_a_lazy_title_renders_under_the_language_of_each_render(
        self, tmp_path: Path
    ) -> None:
        thunk = _thunk(tmp_path, "x = 1\n", [])
        template = Template("{% metadata %}")
        context = Context({METADATA_KEY: thunk})
        with translation.override("de"):
            assert template.render(context) == "<title>de</title>"
        with translation.override("en"):
            assert template.render(context) == "<title>en</title>"

    def test_the_tag_reaches_a_component_render(self, tmp_path: Path) -> None:
        (tmp_path / "head.djx").write_text("<head>{% metadata %}</head>")
        info = ComponentInfo(
            name="page_head",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "head.djx",
            module_path=None,
            is_simple=True,
        )
        thunk = _thunk(tmp_path, 'metadata = {"title": "T"}\n')
        with patch.object(components_manager, "get_component", return_value=info):
            html = _render(
                '{% component "page_head" %}',
                **{
                    TEMPLATE_PATH_KEY: str(tmp_path / "template.djx"),
                    METADATA_KEY: thunk,
                },
            )
        assert html == "<head><title>T</title></head>"
