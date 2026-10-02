import pytest
from django.template import Context, Template, TemplateSyntaxError
from django.test import RequestFactory

from next.pages.responses import cookie_varies, mark_shared_render
from next.scripts.manager import CONSENT_NOTE, SCRIPT_NOTE
from next.seeding import COLLECTOR_KEY, REQUEST_KEY
from next.static import StaticCollector
from next.testing import override_next_settings


CONSENTED = '{% #consented "marketing" %}<video>{% else %}<p>ask</p>{% /consented %}'
END = "<!--/next-consented-->"
MARKETING = {"CATEGORIES": ["marketing"]}


def _render(source: str, *, cookie: str | None = None, shared: bool = False):
    request = RequestFactory().get("/")
    if cookie is not None:
        request.COOKIES["next_consent"] = cookie
    if shared:
        mark_shared_render(request)
    collector = StaticCollector()
    out = Template(source).render(
        Context({REQUEST_KEY: request, COLLECTOR_KEY: collector})
    )
    return out, collector, request


class TestScriptTag:
    """`{% script %}` names a script for the head of this render."""

    def test_the_name_is_noted(self) -> None:
        out, collector, _request = _render('{% script "chat" %}{% script "" %}')
        assert out == ""
        assert list(collector.notes(SCRIPT_NOTE)) == ["chat"]

    def test_without_a_collector_nothing_is_noted(self) -> None:
        assert Template('{% script "chat" %}').render(Context({})) == ""


class TestConsentedTag:
    """`{% #consented %}` renders a branch by the consent of the visitor."""

    def test_a_granted_category_renders_the_body(self) -> None:
        with override_next_settings(CONSENT=MARKETING):
            out, collector, request = _render(CONSENTED, cookie="1:marketing:1")
        assert out == "<video>"
        assert list(collector.notes(CONSENT_NOTE)) == ["marketing"]
        assert cookie_varies(request)

    def test_a_denied_category_renders_the_else_branch(self) -> None:
        out, _collector, _request = _render(CONSENTED)
        assert out == "<p>ask</p>"

    def test_without_an_else_branch_a_denial_renders_nothing(self) -> None:
        out, _collector, _request = _render(
            '{% #consented "marketing" %}<video>{% /consented %}'
        )
        assert out == ""

    def test_a_shared_page_renders_both_the_body_inert(self) -> None:
        out, _collector, request = _render(
            CONSENTED, cookie="1:marketing:1", shared=True
        )
        assert out == (
            '<template data-next-consented="marketing"><video></template><p>ask</p>'
            f"{END}"
        )
        assert not cookie_varies(request)

    def test_a_shared_page_closes_an_empty_else_with_the_marker(self) -> None:
        out, _collector, _request = _render(
            '{% #consented "marketing" %}<video>{% /consented %}', shared=True
        )
        assert (
            out == f'<template data-next-consented="marketing"><video></template>{END}'
        )

    def test_nested_blocks_nest_their_markers(self) -> None:
        out, collector, _request = _render(
            '{% #consented "marketing" %}<a>'
            '{% #consented "stats" %}<b>{% else %}<c>{% /consented %}'
            "{% /consented %}",
            shared=True,
        )
        assert out == (
            '<template data-next-consented="marketing"><a>'
            f'<template data-next-consented="stats"><b></template><c>{END}'
            f"</template>{END}"
        )
        assert list(collector.notes(CONSENT_NOTE)) == ["marketing", "stats"]

    def test_a_server_render_carries_no_marker(self) -> None:
        with override_next_settings(CONSENT=MARKETING):
            granted, _collector, _request = _render(CONSENTED, cookie="1:marketing:1")
            denied, _collector, _request = _render(CONSENTED)
        assert granted == "<video>"
        assert END not in granted + denied

    def test_without_a_collector_it_still_renders(self) -> None:
        assert Template(CONSENTED).render(Context({})) == "<p>ask</p>"

    @pytest.mark.parametrize(
        "source",
        [
            "{% #consented %}x{% /consented %}",
            '{% #consented "a" "b" %}x{% /consented %}',
        ],
    )
    def test_anything_but_one_category_is_a_syntax_error(self, source: str) -> None:
        with pytest.raises(TemplateSyntaxError, match="exactly one category"):
            Template(source)
