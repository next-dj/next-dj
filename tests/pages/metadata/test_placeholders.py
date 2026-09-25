import pytest
from django.utils import translation
from django.utils.functional import Promise, lazy
from django.utils.translation import gettext

from next.pages.errors import PageMetadataTemplateError
from next.pages.metadata import (
    PLACEHOLDERS,
    parse_template,
    substitute_title,
    template_has_title,
    title_lazy,
)
from tests.support import TEMPLATE_CASES, TemplateCase


class TestTemplates:
    """Only bare `{title}` and `{site_name}` survive the parser."""

    def test_the_placeholders_are_the_two_names(self) -> None:
        assert frozenset({"title", "site_name"}) == PLACEHOLDERS

    @pytest.mark.parametrize(
        "case", TEMPLATE_CASES, ids=[case.id for case in TEMPLATE_CASES]
    )
    def test_substitution_or_error(self, case: TemplateCase) -> None:
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


class TestLazyTitle:
    """`title_lazy` answers a `Promise` that evaluates under the active language."""

    def test_answers_a_promise_without_evaluating(self) -> None:
        calls: list[str] = []

        def build() -> str:
            calls.append("evaluated")
            return "{title}"

        title = title_lazy(lazy(build, str)(), {"title": "Wallet"})
        assert isinstance(title, Promise)
        assert calls == []
        assert str(title) == "Wallet"
        assert calls == ["evaluated"]

    def test_evaluates_under_the_active_language(self) -> None:
        template = lazy(lambda: "{title} · " + gettext("Yes"), str)()
        title = title_lazy(template, {"title": "Wallet"})
        with translation.override("de"):
            assert str(title) == "Wallet · Ja"
        with translation.override("en"):
            assert str(title) == "Wallet · Yes"

    def test_a_broken_translation_fails_only_when_evaluated(self) -> None:
        template = lazy(lambda: "{x.__class__}", str)()
        title = title_lazy(template, {"title": "Wallet"})
        with pytest.raises(PageMetadataTemplateError, match=r"x\.__class__"):
            str(title)
