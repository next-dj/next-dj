import re

import pytest
from django.template import Context, TemplateSyntaxError
from django.template.base import Template


def _render(source: str, **ctx) -> str:
    return Template(source).render(Context(ctx))


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
