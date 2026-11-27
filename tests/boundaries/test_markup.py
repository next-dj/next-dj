import logging

import pytest
from django.test import override_settings

from next.boundaries import contain
from next.boundaries.calls import Addressing
from next.boundaries.failures import (
    Boundary,
    BoundaryFailure,
    DebugBoundaryFailure,
    ErrorKind,
    Trace,
)
from next.boundaries.markup import (
    adopt,
    debug_detail,
    failed,
    identity,
    note_retry,
    wrapped,
    wrapper_tag,
)
from next.static import StaticCollector
from next.static.runtime import BUNDLE_NOTE
from tests.support.boundaries import RecordingErrorUI, defer_errors, raising


STAMP = 'id="b" data-next-boundary="b" data-next-state="error" data-next-error="error"'


class TestWrapperTag:
    """No wrapper element is offered where the markup before the site forbids one."""

    @pytest.mark.parametrize(
        ("preceding", "tag"),
        [
            ("", "div"),
            ("<section><p>", "div"),
            ("<table>", None),
            ("<table><tbody>", None),
            ("<table><tr><td>", "div"),
            ("<table></table>", "div"),
            ("<ul>", None),
            ("<ul><li>", "div"),
            ("<select><optgroup>", None),
            ("<svg><g>", None),
            ("<svg></svg>", "div"),
            ("<!-- <table> -->", "div"),
            ("<table><br>", None),
            ("<table><col/>", None),
            ("<div/><ol>", None),
            ("</p><div>", "div"),
            ("<TABLE><TR>", None),
            ("<table><tr><td></td></tr></table><p>", "div"),
            ("<table><script>if (a<b && c>d) {}</script>", None),
            ("<script>var t = '<table>';</script>", "div"),
            ("<div title='<table>'>", "div"),
            ('<div title="a>b"><ul>', None),
            ("<svg><foreignObject><div>", "div"),
            ("<svg><foreignObject></foreignObject><g>", None),
            ("<table><tr><td><svg></svg></td>", None),
            ("<ul><path />", "div"),
            ("<title-bar></title-bar><table><tbody>", None),
            ("<script-loader></script-loader><ul>", None),
            ("<style-switcher><ul>", None),
            ("<!--><table>", None),
            ("<!---><ul>", None),
            ("<!-- a --!><ul>", None),
            ("<svg><path/></svg><ul>", None),
            ("<svg><foreignObject><div/><table>", None),
        ],
    )
    def test_wrapper_tag(self, preceding: str, tag: str | None) -> None:
        assert wrapper_tag(preceding) == tag

    def test_the_tag_given_is_returned(self) -> None:
        assert wrapper_tag("<p>", "span") == "span"


class TestMarkup:
    """The wrapper and the DEBUG detail escape everything they print."""

    def test_the_trace_formats_the_line_and_the_traceback(self) -> None:
        try:
            raising(ValueError("x"))()
        except ValueError as exc:
            trace = Trace.of(exc)
        assert trace.line == "ValueError: x"
        assert trace.text.startswith("Traceback (most recent call last):")
        assert trace.text.endswith("ValueError: x\n")

    def test_wrapped_carries_the_state_and_the_kind(self) -> None:
        html = wrapped("div", "b-1-1", ErrorKind.ERROR, "<p>x</p>")
        assert html == (
            '<div id="b-1-1" data-next-boundary="b-1-1" data-next-state="error" '
            'data-next-error="error"><p>x</p></div>'
        )

    def test_wrapped_prints_the_digest_when_given(self) -> None:
        html = wrapped("div", "b", ErrorKind.ERROR, "", '"x"')
        assert 'data-next-error-digest="&quot;x&quot;"' in html

    def test_wrapped_names_the_source_when_given(self) -> None:
        html = wrapped("div", "b", ErrorKind.ERROR, "", source="pages/<x>/error.djx")
        assert 'data-next-boundary-source="pages/&lt;x&gt;/error.djx"' in html

    def test_a_contained_failure_names_its_source_under_debug(self) -> None:
        boundary = Boundary("a", Addressing.PAGE, source="pages/error.djx")
        with override_settings(DEBUG=True):
            html = contain(boundary, raising(ValueError()), RecordingErrorUI())
        assert 'data-next-boundary-source="pages/error.djx"' in html

    def test_a_contained_failure_hides_its_source_outside_debug(self) -> None:
        boundary = Boundary("a", Addressing.PAGE, source="pages/error.djx")
        with override_settings(DEBUG=False):
            html = contain(boundary, raising(ValueError()), RecordingErrorUI())
        assert "data-next-boundary-source" not in html

    def test_debug_detail_escapes_the_exception_and_the_traceback(self) -> None:
        try:
            raising(ValueError("<script>alert(1)</script>"))()
        except ValueError as exc:
            detail = debug_detail(Trace.of(exc))
        assert detail.startswith("<details open data-next-error-detail><summary>")
        assert "<script>" not in detail
        assert "ValueError: &lt;script&gt;alert(1)&lt;/script&gt;" in detail
        assert "<pre>Traceback (most recent call last):" in detail


class TestAdoptedRoot:
    """Without a wrapper the boundary stamps the one root element of its error UI."""

    @pytest.mark.parametrize(
        ("body", "stamped"),
        [
            ("<tr><td>x</td></tr>", f"<tr {STAMP}><td>x</td></tr>"),
            ("  <tr class='a>b'></tr>\n", f"  <tr {STAMP} class='a>b'></tr>\n"),
            ("<!-- note --><li>x</li>", f"<!-- note --><li {STAMP}>x</li>"),
            ("<col span=2>", f"<col {STAMP} span=2>"),
            ("<rect/>", f"<rect {STAMP}/>"),
            ("<script>a<b</script>", f"<script {STAMP}>a<b</script>"),
            ("<li><br><img src=x></li>", f"<li {STAMP}><br><img src=x></li>"),
            ("<tr><script>a<b</script></tr>", f"<tr {STAMP}><script>a<b</script></tr>"),
        ],
    )
    def test_the_single_root_carries_the_wrapper_attributes(
        self, body: str, stamped: str
    ) -> None:
        assert wrapped(None, "b", ErrorKind.ERROR, body) == stamped

    @pytest.mark.parametrize(
        ("body", "found"),
        [
            ("", "0 root elements"),
            ("<!-- only a note -->", "0 root elements"),
            ("<tr></tr><tr></tr>", "2 root elements"),
            ("text", "loose text"),
            ("<tr></tr>tail", "loose text"),
            ("lead<tr></tr>", "loose text"),
            ("</div><tr></tr>", "loose text"),
        ],
    )
    def test_any_other_shape_leaves_an_empty_placeholder_and_a_warning(
        self, body: str, found: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, "next.boundaries"):
            html = wrapped(None, "b", ErrorKind.ERROR, body)
        assert html == f"<template {STAMP}></template>"
        (record,) = [r for r in caplog.records if r.name == "next.boundaries"]
        assert found in record.getMessage()
        assert "a boundary" in record.getMessage()

    def test_debug_names_the_site_in_the_placeholder(self) -> None:
        with override_settings(DEBUG=True):
            html = contain(
                Boundary(
                    "row", Addressing.COMPONENT, site="the call in page.djx, line 3"
                ),
                raising(ValueError("x")),
                RecordingErrorUI("<tr></tr><tr></tr>", tag=None),
            )
        assert html.startswith('<template id="b-')
        assert "data-next-error-digest=" in html
        assert (
            "The error UI of the call in page.djx, line 3 renders 2 root elements"
        ) in html
        assert "ValueError: x" in html.split("data-next-error-detail=")[1]

    def test_debug_without_the_detail_keeps_the_site_message(self) -> None:
        with override_settings(DEBUG=True), defer_errors(DEBUG_DETAIL=False):
            html = contain(
                Boundary("row", Addressing.COMPONENT),
                raising(ValueError("x")),
                RecordingErrorUI("", tag=None),
            )
        detail = html.split('data-next-error-detail="')[1]
        assert detail.startswith("The error UI of the component &#x27;row&#x27;")
        assert "Traceback" not in detail

    def test_debug_puts_the_detail_on_the_adopted_root(self) -> None:
        with override_settings(DEBUG=True):
            html = contain(
                Boundary("row", Addressing.COMPONENT),
                raising(ValueError("<b>")),
                RecordingErrorUI("<tr><td>E</td></tr>", tag=None),
            )
        assert html.startswith('<tr id="b-')
        assert html.endswith("><td>E</td></tr>")
        assert "<details" not in html
        assert "ValueError: &lt;b&gt;" in html

    def test_production_leaves_the_placeholder_without_a_message(self) -> None:
        html = contain(
            Boundary("row", Addressing.COMPONENT),
            raising(ValueError()),
            RecordingErrorUI("", tag=None),
        )
        assert html.startswith('<template id="b-')
        assert "data-next-error-detail" not in html


class TestDetailPlacement:
    """The DEBUG detail is an element only inside a wrapper that keeps it in place."""

    @pytest.mark.parametrize("tag", ["div", "li"])
    def test_a_flow_wrapper_holds_the_element(self, tag: str) -> None:
        with override_settings(DEBUG=True):
            html = contain(
                Boundary("card", Addressing.COMPONENT),
                raising(ValueError()),
                RecordingErrorUI(tag=tag),
            )
        assert "<details open data-next-error-detail>" in html

    @pytest.mark.parametrize("tag", ["tr", "span", "tbody"])
    def test_another_wrapper_carries_the_detail_as_an_attribute(self, tag: str) -> None:
        with override_settings(DEBUG=True):
            html = contain(
                Boundary("card", Addressing.COMPONENT),
                raising(ValueError("x")),
                RecordingErrorUI("<td>E</td>", tag=tag),
            )
        assert "<details" not in html
        assert html.startswith(f'<{tag} id="b-')
        assert 'data-next-error-detail="Traceback' in html
        assert html.endswith(f"><td>E</td></{tag}>")


class TestSharedMarkup:
    """The helpers a deferred wrapper builds its markup with, as an inline one does."""

    def test_identity_carries_the_id_under_both_names(self) -> None:
        assert identity('b"1') == ' id="b&quot;1" data-next-boundary="b&quot;1"'

    def test_adopt_stamps_the_one_root(self) -> None:
        assert adopt(' id="b"', "<tr></tr>", site="s") == '<tr id="b"></tr>'

    def test_failed_outside_debug_shows_the_kind_alone(self) -> None:
        error = BoundaryFailure(ErrorKind.NOT_FOUND, "d1", 404, retry=False)
        assert failed("div", ' id="b"', error, "E", site="s") == (
            '<div id="b" data-next-state="error" data-next-error="not_found">E</div>'
        )

    @override_settings(DEBUG=True)
    def test_failed_under_debug_shows_the_digest_and_the_detail(self) -> None:
        error = DebugBoundaryFailure(ErrorKind.ERROR, "d1", None, False, "line", "text")
        assert failed("span", ' id="b"', error, "E", site="s") == (
            '<span id="b" data-next-state="error" data-next-error="error" '
            'data-next-error-digest="d1" data-next-error-detail="text">E</span>'
        )

    @override_settings(DEBUG=True)
    def test_the_detail_setting_hides_the_detail(self) -> None:
        error = DebugBoundaryFailure(ErrorKind.ERROR, "d1", None, False, "line", "text")
        with defer_errors(DEBUG_DETAIL=False):
            html = failed("span", "", error, "E", site="s")
        assert "data-next-error-digest" in html
        assert "data-next-error-detail" not in html


DEFER_URL = "/static/next/next.boundaries.min.js"


def _bundled() -> StaticCollector:
    return StaticCollector(bundle_url=lambda name: f"/static/next/next.{name}.min.js")


class TestRetryBundle:
    """An error UI offering a retry needs the `defer` bundle, a failed subtree's not."""

    def test_a_retry_control_in_the_error_ui_needs_it(self) -> None:
        collector = _bundled()
        html = contain(
            Boundary("card", Addressing.COMPONENT, collector=collector),
            raising(ValueError()),
            RecordingErrorUI("<b data-next-retry>again</b>"),
        )
        assert "data-next-retry" in html
        assert collector.notes(BUNDLE_NOTE) == [DEFER_URL]

    def test_a_retry_token_on_the_wrapper_needs_it(self) -> None:
        collector = _bundled()
        html = contain(
            Boundary("card", Addressing.COMPONENT, collector=collector),
            raising(ValueError()),
            RecordingErrorUI(token=' data-next-token="t"'),
        )
        assert ' data-next-token="t"' in html
        assert collector.notes(BUNDLE_NOTE) == [DEFER_URL]

    def test_an_error_ui_without_a_retry_needs_nothing(self) -> None:
        collector = _bundled()
        contain(
            Boundary("card", Addressing.COMPONENT, collector=collector),
            raising(ValueError()),
            RecordingErrorUI(),
        )
        assert collector.notes(BUNDLE_NOTE) == ()

    def test_the_need_of_a_failed_subtree_rolls_back(self) -> None:
        collector = _bundled()

        def render() -> str:
            collector.require("boundaries", urgent=True)
            raise ValueError

        contain(
            Boundary("card", Addressing.COMPONENT, collector=collector),
            render,
            RecordingErrorUI(),
        )
        assert collector.notes(BUNDLE_NOTE) == ()
        assert collector.assets_in_slot("scripts") == ()

    def test_an_outer_failure_takes_back_the_inner_retry(self) -> None:
        collector = _bundled()

        def outer() -> str:
            contain(
                Boundary("inner", Addressing.COMPONENT, collector=collector),
                raising(ValueError()),
                RecordingErrorUI("<b data-next-retry>again</b>"),
            )
            raise ValueError

        contain(
            Boundary("outer", Addressing.COMPONENT, collector=collector),
            outer,
            RecordingErrorUI(),
        )
        assert collector.notes(BUNDLE_NOTE) == ()

    @pytest.mark.parametrize(
        ("body", "needed"),
        [
            pytest.param("<button data-next-retry>again</button>", True, id="bare"),
            pytest.param('<a title="a>b" data-next-retry="1">x</a>', True, id="after"),
            pytest.param("<B class=x DATA-NEXT-RETRY/>", True, id="upper-case"),
            pytest.param("<p>Add data-next-retry to a button.</p>", False, id="prose"),
            pytest.param("<p title='data-next-retry'>x</p>", False, id="in-a-value"),
            pytest.param("<b data-next-retry-count>x</b>", False, id="longer-name"),
        ],
    )
    def test_only_a_start_tag_attribute_offers_a_retry(
        self, body: str, *, needed: bool
    ) -> None:
        collector = _bundled()
        note_retry(collector, body, "")
        assert bool(collector.notes(BUNDLE_NOTE)) is needed

    def test_a_render_without_a_collector_records_nothing(self) -> None:
        note_retry(None, "<b data-next-retry>again</b>", ' data-next-token="t"')
