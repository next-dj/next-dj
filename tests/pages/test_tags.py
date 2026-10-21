import re

import pytest
from django.template import Context, TemplateSyntaxError
from django.template.base import Template

from next.pages.metadata.nodes import MetadataNode
from next.templatetags.pages import RootLayoutNode


def _render(source: str, **ctx) -> str:
    return Template(source).render(Context(ctx))


class TestTemplatePlaceholderTag:
    """``{% template %}`` marks the placeholder a composed layout fills."""

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


class TestRootLayoutTag:
    """``{% layout root %}`` marks a root layout and renders nothing."""

    @pytest.mark.parametrize(
        "source",
        ["a{% layout root %}b", "a{%  layout   root  %}b", "a{%\n layout\n root\n%}b"],
        ids=["tight", "padded", "multiline"],
    )
    def test_the_marker_renders_nothing(self, source) -> None:
        assert _render(source) == "ab"

    def test_the_marker_compiles_to_its_node(self) -> None:
        template = Template("{% layout root %}")

        assert template.nodelist.get_nodes_by_type(RootLayoutNode)

    @pytest.mark.parametrize(
        "source",
        [
            "{% layout %}",
            "{% layout leaf %}",
            '{% layout "root" %}',
            "{% layout root extra %}",
            "{% layout root root %}",
            "{% layout x=1 %}",
        ],
        ids=["none", "wrong", "quoted", "extra", "twice", "kwarg"],
    )
    def test_any_other_form_is_rejected(self, source) -> None:
        message = "{% layout %} tag takes the single argument 'root'"
        with pytest.raises(TemplateSyntaxError, match=re.escape(message)):
            Template(source)


class TestMetadataTagCompiles:
    """``{% metadata %}`` compiles to its node and takes no arguments."""

    @pytest.mark.parametrize(
        "source", ['{% metadata "x" %}', "{% metadata x=1 %}"], ids=["arg", "kwarg"]
    )
    def test_arguments_are_rejected(self, source: str) -> None:
        message = "{% metadata %} tag takes no arguments"
        with pytest.raises(TemplateSyntaxError, match=re.escape(message)):
            Template(source)

    def test_the_tag_compiles_to_the_metadata_node(self) -> None:
        assert Template("{% metadata %}").nodelist.get_nodes_by_type(MetadataNode)
