import logging
from unittest.mock import patch

import pytest
from django.template import Context, Template, TemplateSyntaxError
from django.test import RequestFactory, override_settings

from next.pages.responses import cookie_varies, mark_shared_render
from next.scripts.manager import CONSENT_NOTE, GATED_NOTE, SCRIPT_NOTE, GatedNote
from next.seeding import COLLECTOR_KEY, REQUEST_KEY
from next.static import StaticAsset, StaticCollector, default_kinds
from next.testing import override_next_settings
from tests.support.patches import restored_static_registries


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


HELD = (
    '{% load next_static %}{% #consented "marketing" %}<video>'
    '{% script "chat" %}{% use_script "https://cdn.example/x.js" %}'
    '{% use_style "https://cdn.example/a.css" %}'
    '{% #consented "stats" %}{% script "stats" %}{% /consented %}'
    "{% /consented %}"
)


def _seeded(*assets: StaticAsset) -> StaticCollector:
    shadow = StaticCollector()
    shadow.add_js_context("player", {"id": 7})
    for asset in assets:
        shadow.add(asset)
    return shadow


class TestHeldBody:
    """A client-rendered body loads no script before the visitor grants its category."""

    def test_scripts_wait_while_styles_pass(self) -> None:
        out, collector, _request = _render(HELD, shared=True)
        assert "<video>" in out
        assert list(collector.notes(SCRIPT_NOTE)) == []
        assert list(collector.notes(CONSENT_NOTE)) == ["marketing", "stats"]
        assert [asset.url for asset in collector.assets_in_slot("styles")] == [
            "https://cdn.example/a.css"
        ]
        assert collector.assets_in_slot("scripts") == ()
        assert list(collector.notes(GATED_NOTE)) == [
            GatedNote("marketing", StaticAsset("https://cdn.example/x.js", "js")),
            GatedNote("marketing", "chat"),
            GatedNote("stats", "stats"),
        ]

    def test_a_server_render_registers_the_granted_body_as_it_stands(self) -> None:
        with override_next_settings(CONSENT={"CATEGORIES": ["marketing", "stats"]}):
            _out, collector, _request = _render(HELD, cookie="2:marketing|stats:1")
        assert list(collector.notes(SCRIPT_NOTE)) == ["chat", "stats"]
        assert list(collector.notes(GATED_NOTE)) == []
        assert [asset.url for asset in collector.assets_in_slot("scripts")] == [
            "https://cdn.example/x.js"
        ]

    def test_the_js_context_passes(self) -> None:
        with patch("next.templatetags.scripts.get_static_manager") as manager:
            manager.return_value.create_collector.return_value = _seeded()
            _out, collector, _request = _render(CONSENTED, shared=True)
        assert collector.js_context() == {"player": {"id": 7}}

    def test_another_kind_is_dropped_and_logged_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with restored_static_registries():
            default_kinds.register(
                "font", extension=".woff2", slot="scripts", renderer="render_link_tag"
            )
            font = StaticAsset("https://cdn.example/f.woff2", "font")
            with (
                patch("next.templatetags.scripts.get_static_manager") as manager,
                caplog.at_level(logging.WARNING, "next.scripts"),
            ):
                manager.return_value.create_collector.side_effect = [
                    _seeded(font),
                    _seeded(font),
                ]
                for _ in range(2):
                    _out, collector, _request = _render(CONSENTED, shared=True)
        assert collector.assets_in_slot("scripts") == ()
        assert list(collector.notes(GATED_NOTE)) == []
        assert caplog.text.count("'font' asset") == 1

    def test_without_a_collector_the_body_still_renders(self) -> None:
        request = RequestFactory().get("/")
        mark_shared_render(request)
        out = Template(CONSENTED).render(Context({REQUEST_KEY: request}))
        assert out.startswith('<template data-next-consented="marketing"><video>')


class TestUnknownCategory:
    """A category the list leaves out is logged once under `DEBUG`."""

    @pytest.mark.parametrize(
        ("debug", "consent", "logged"),
        [
            (True, MARKETING, 1),
            (False, MARKETING, 0),
            (True, None, 0),
            (True, {"CATEGORIES": ["marketing", "ads"]}, 0),
        ],
        ids=["debug", "production", "unconfigured", "listed"],
    )
    def test_it_is_logged_once_under_debug(
        self,
        caplog: pytest.LogCaptureFixture,
        *,
        debug: bool,
        consent: object,
        logged: int,
    ) -> None:
        source = '{% #consented "ads" %}x{% /consented %}'
        framework = {} if consent is None else {"CONSENT": consent}
        with (
            override_next_settings(**framework),
            override_settings(DEBUG=debug),
            caplog.at_level(logging.WARNING, "next.scripts"),
        ):
            _render(source)
            _render(source)
        assert caplog.text.count("{% #consented 'ads' %}") == logged
