import pytest
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy

from next.pages.errors import PageMetadataTemplateError
from next.pages.metadata import template_has_title
from next.pages.metadata.placeholders import (
    PLACEHOLDERS,
    apply_title_template,
    parse_template,
    substitute_title,
    template_names,
)
from tests.support import TITLE_TEMPLATE_CASES, TitleTemplateCase


class TestTemplates:
    """Only bare `{title}` and `{site_name}` survive the parser."""

    def test_the_placeholders_are_the_two_names(self) -> None:
        assert frozenset({"title", "site_name"}) == PLACEHOLDERS

    @pytest.mark.parametrize(
        "case", TITLE_TEMPLATE_CASES, ids=[case.id for case in TITLE_TEMPLATE_CASES]
    )
    def test_substitution_or_error(self, case: TitleTemplateCase) -> None:
        if case.error_fragment is None:
            assert substitute_title(case.template, case.values) == case.expected
            return
        with pytest.raises(PageMetadataTemplateError) as excinfo:
            substitute_title(case.template, case.values)
        assert case.error_fragment in excinfo.value.detail
        assert excinfo.value.template == case.template
        assert str(excinfo.value) == (
            f"metadata title template {case.template!r} {excinfo.value.detail}"
        )

    def test_template_error_is_a_value_error(self) -> None:
        with pytest.raises(ValueError, match="names the placeholder"):
            parse_template("{nope}")

    def test_parse_yields_literal_and_field_parts(self) -> None:
        assert parse_template("A {title} B {site_name}") == (
            ("A ", "title"),
            (" B ", "site_name"),
        )

    def test_parse_is_memoised_on_the_evaluated_string(self) -> None:
        parse_template.cache_clear()
        parse_template("{title} memo")
        before = parse_template.cache_info().hits
        parse_template("{title} memo")
        assert parse_template.cache_info().hits == before + 1

    def test_no_str_format_runs_on_the_user_string(self) -> None:
        class Loud(str):
            __slots__ = ()

            def format(self, *args: object, **kwargs: object) -> str:
                raise AssertionError

        assert substitute_title(Loud("{title}"), {"title": "ok"}) == "ok"

    @pytest.mark.parametrize(
        ("template", "expected"),
        [("{title} · Acme", True), ("Acme", False), ("{site_name}", False)],
        ids=["with_title", "plain", "site_name_only"],
    )
    def test_template_has_title(self, template: str, *, expected: bool) -> None:
        assert template_has_title(template) is expected

    @pytest.mark.parametrize(
        ("template", "expected"),
        [
            ("{title} · {site_name}", {"title", "site_name"}),
            ("{title} {title}", {"title"}),
            ("Acme", set()),
        ],
        ids=["both", "repeated", "plain"],
    )
    def test_template_names(self, template: str, expected: set[str]) -> None:
        assert template_names(template) == expected


class TestApplyTitleTemplate:
    """The chain template can be applied to any text, lazily."""

    def test_without_a_template_the_text_passes_through(self) -> None:
        text = gettext_lazy("Yes")
        assert apply_title_template(None, text, site_name=None) is text

    def test_with_a_template_the_result_is_lazy(self) -> None:
        title = apply_title_template(
            "{title} · {site_name}", "Wallet", site_name="Acme"
        )
        assert isinstance(title, Promise)
        assert str(title) == "Wallet · Acme"
