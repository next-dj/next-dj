import pytest

from next.pages.checks.reads import chrome_contents, chrome_names, template_names


class TestTemplateNames:
    """The scan reads the names variables and tag arguments start with."""

    def test_variables_and_tag_arguments_are_read(self) -> None:
        names = template_names(
            "{% if a.b %}{{ c|default:'x y' }}{% for i in items %}{% endfor %}"
            "{% endif %}{# d #}text e{{ f | date:g }}"
        )

        assert names == {"a", "c", "f", "g", "i", "in", "items"}

    def test_a_comment_block_reads_nothing(self) -> None:
        text = "{% comment %}{{ hidden }}{% if x %}{% endcomment %}{{ shown }}"

        assert template_names(text) == {"shown"}

    def test_zones_and_the_slot_count_as_reads(self) -> None:
        text = '{% zone "z" %}{{ a }}{% endzone %}{% #template %}{{ b }}{% /template %}'

        assert template_names(text) == {"a", "b"}


class TestChromeNames:
    """The chrome scan leaves out what a kept layout re-renders or never keeps."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param(
                "<nav>{{ tab }}</nav>{% template %}{{ after }}",
                {"tab", "after"},
                id="around-the-slot",
            ),
            pytest.param(
                '{% zone "tabs" %}{{ tab }}{% zone "in" %}{{ deep }}{% endzone %}'
                "{% endzone %}{{ kept }}{% template %}",
                {"kept"},
                id="nested-zones",
            ),
            pytest.param(
                "{% #template %}{{ fallback }}{% /template %}{{ kept }}",
                {"kept"},
                id="slot-fallback",
            ),
            pytest.param(
                '{% zone "main" %}{{ wrapped }}{% template %}{% endzone %}',
                {"wrapped"},
                id="zone-around-the-slot",
            ),
            pytest.param(
                '{% zone "main" %}<p>{% #template %}x{% /template %}</p>{% endzone %}'
                '{% zone "flash" %}{{ messages }}{% endzone %}',
                set(),
                id="zone-around-the-fallback",
            ),
            pytest.param(
                "<head>{% metadata %}</head>{% comment %}{{ c }}{% endcomment %}"
                "{% endzone %}{% template %}",
                set(),
                id="metadata-comment-stray-end",
            ),
            pytest.param('{% zone "open" %}{{ a }}', {"a"}, id="unclosed-zone"),
        ],
    )
    def test_the_names_outside_zones_and_slot(
        self, text: str, expected: set[str]
    ) -> None:
        assert chrome_names(text) == expected

    def test_the_contents_are_the_raw_tokens(self) -> None:
        text = '{% zone "z" %}{{ a }}{% endzone %}{{ request.path }}{% template %}'

        assert list(chrome_contents(text)) == ["request.path", "template"]
