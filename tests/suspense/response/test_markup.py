import pytest
from django.test import override_settings

from next.defer import BoundaryFailure, DebugBoundaryFailure, DeferMode, ErrorKind
from next.suspense.assembly.inline import assemble
from next.suspense.markers import Outcome, Rendered
from next.suspense.render import Delta
from next.suspense.response import idle, in_place, shell, strip, wrappers
from tests.support.boundaries import make_call, open_session


def _ready(html: str, **kwargs: object) -> Rendered[Delta]:
    return Rendered(html, Delta(), **kwargs)


def _wrapper(ident: str, tag: str | None = "div", loading: str = "L") -> str:
    return shell(ident, make_call(tag), loading)


class TestMarkup:
    """The loading and settled wrappers carry one id under both names."""

    def test_a_settled_error_names_its_kind(self) -> None:
        failure = BoundaryFailure(ErrorKind.TIMEOUT, "d1", None, retry=False)
        assert in_place("b-1-0", make_call("div", "k"), "E", failure) == (
            '<div id="b-1-0" data-next-boundary="b-1-0" data-next-boundary-key="k" '
            'data-next-state="error" data-next-error="timeout">E</div>'
        )

    @override_settings(DEBUG=True)
    def test_a_debug_error_shows_its_digest_and_detail_as_inline_ones_do(self) -> None:
        failure = DebugBoundaryFailure(
            ErrorKind.ERROR, "d1", None, False, "ValueError: x", "Traceback x"
        )
        html = in_place("b-1-0", make_call("div", "k"), "E", failure)
        assert html == (
            '<div id="b-1-0" data-next-boundary="b-1-0" data-next-boundary-key="k" '
            'data-next-state="error" data-next-error="error" '
            'data-next-error-digest="d1">E<details open data-next-error-detail>'
            "<summary>ValueError: x</summary><pre>Traceback x</pre></details></div>"
        )
        assert 'data-next-error-detail="Traceback x"' in in_place(
            "b-1-0", make_call("span", "k"), "E", failure
        )

    def test_an_adopted_error_root_takes_the_error_attributes(self) -> None:
        failure = BoundaryFailure(ErrorKind.ERROR, "d1", None, retry=False)
        html = in_place("b-1-0", make_call(None, "k"), "<tr><td>E</td></tr>", failure)
        assert html == (
            '<tr id="b-1-0" data-next-boundary="b-1-0" data-next-boundary-key="k" '
            'data-next-state="error" data-next-error="error"><td>E</td></tr>'
        )

    def test_the_source_is_escaped(self) -> None:
        html = in_place("b-1-0", make_call("div", "k:<x>", source='a"b'), "", None)
        assert 'data-next-boundary-key="k:&lt;x&gt;"' in html
        assert 'data-next-boundary-source="a&quot;b"' in html

    def test_an_adopted_error_with_no_content_is_a_template(self) -> None:
        failure = BoundaryFailure(ErrorKind.ERROR, "d1", None, retry=False)
        assert in_place("b-1-0", make_call(None, "k"), "", failure) == (
            '<template id="b-1-0" data-next-boundary="b-1-0" '
            'data-next-boundary-key="k" data-next-state="error" '
            'data-next-error="error"></template>'
        )

    def test_an_adopted_root_with_no_content_is_a_template(self) -> None:
        assert in_place("b-1-0", make_call(None, "k"), "", None).startswith(
            '<template id="b-1-0"'
        )

    @override_settings(DEBUG=True)
    @pytest.mark.parametrize(
        ("detail", "tail"),
        [
            pytest.param(
                "why",
                'data-next-error-digest="d1" data-next-error-detail="why"></template>',
                id="with-a-message",
            ),
            pytest.param(
                "", 'data-next-error-digest="d1"></template>', id="without-a-message"
            ),
        ],
    )
    def test_a_debug_adopted_error_with_no_content_shows_its_digest(
        self, detail: str, tail: str
    ) -> None:
        failure = DebugBoundaryFailure(ErrorKind.ERROR, "d1", None, False, detail, "")
        markup = in_place("b-1-0", make_call(None, "k"), "", failure)
        assert markup.endswith('data-next-error="error" ' + tail)

    def test_a_refused_loading_ui_settles_the_shell_as_an_error(self) -> None:
        failure = BoundaryFailure(ErrorKind.ERROR, "d1", None, retry=False)
        html = shell("b-1-0", make_call(None), "", failure)
        assert html == (
            '<!--b-1-0:--><template id="b-1-0" data-next-boundary="b-1-0" '
            'data-next-boundary-key="k" data-next-state="error" '
            'data-next-error="error"></template><!--/b-1-0-->'
        )
        settled_html = assemble(
            html, [Outcome("b-1-0", _ready("<li>R</li>"))], None, seal="1"
        )
        assert settled_html.startswith('<li id="b-1-0"')

    def test_strip_takes_out_the_markers_of_its_response_alone(self) -> None:
        echo = "<!--b-2-0:div--><!--/b-2-0-->"
        assert strip(echo + _wrapper("b-1-0"), "1") == echo + (
            '<div id="b-1-0" data-next-boundary="b-1-0" data-next-boundary-key="k" '
            'data-next-state="loading" aria-busy="true">L</div>'
        )


class TestIdle:
    """A client-trip wrapper carries its mode, its token and an idle state."""

    _TOKEN = ' data-next-token="t"'

    def test_a_tagged_wrapper_holds_the_loading_ui(self) -> None:
        html = idle("b-1-0", make_call(), "L", mode=DeferMode.LOAD, token=self._TOKEN)
        assert html == (
            '<div id="b-1-0" data-next-boundary="b-1-0" data-next-boundary-key="k" '
            'data-next-defer="load" data-next-token="t" data-next-state="idle">L</div>'
        )

    def test_an_adopted_root_is_marked(self) -> None:
        html = idle(
            "b-1-0",
            make_call(None),
            "<li>L</li>",
            mode=DeferMode.MANUAL,
            token=self._TOKEN,
        )
        assert html == (
            '<li id="b-1-0" data-next-boundary="b-1-0" data-next-boundary-key="k" '
            'data-next-defer="manual" data-next-token="t" data-next-adopted '
            'data-next-state="idle">L</li>'
        )

    def test_a_refused_loading_ui_settles_as_an_error(self) -> None:
        failure = BoundaryFailure(ErrorKind.ERROR, "d1", None, retry=False)
        html = idle(
            "b-1-0",
            make_call(),
            "",
            mode=DeferMode.LOAD,
            token=self._TOKEN,
            failure=failure,
        )
        assert 'data-next-state="error"' in html
        assert "idle" not in html


class TestRetryAttributes:
    def test_a_failed_wrapper_carries_its_retry_token(self) -> None:
        failure = BoundaryFailure(ErrorKind.ERROR, "d1", None, retry=True)
        html = in_place(
            "b-1-0", make_call("div", "k"), "E", failure, ' data-next-token="t"'
        )
        assert 'data-next-token="t" data-next-state="error"' in html

    def test_a_ready_wrapper_drops_the_retry_token(self) -> None:
        html = in_place(
            "b-1-0", make_call("div", "k"), "R", None, ' data-next-token="t"'
        )
        assert "data-next-token" not in html


class TestSealedMarkers:
    """The markers of one response are found by its seal, read as plain text."""

    def test_a_lead_without_a_marker_after_it_is_text(self) -> None:
        html = "<!--b-1-x:div--><!--b-1-:div--><!--/b-1-x-->" + _wrapper("b-1-0")
        assert list(wrappers(html, "1")) == ["b-1-0"]
        assert strip(html, "1").startswith(
            "<!--b-1-x:div--><!--b-1-:div--><!--/b-1-x-->"
        )
        assert "<!--b-1-0" not in strip(html, "1")

    def test_a_seal_is_matched_as_text(self) -> None:
        html = _wrapper("b-a.c-0") + "<!--b-abc-1:div--><!--/b-abc-1-->"
        assert list(wrappers(html, "a.c")) == ["b-a.c-0"]
        assert strip(html, "a.c").endswith("<!--b-abc-1:div--><!--/b-abc-1-->")

    def test_a_shell_without_markers_comes_back_as_the_object_passed(self) -> None:
        html = "<main>plain</main>"
        assert strip(html, "1") is html


class TestWrappers:
    """The markers of a response name the tag and the identity of each wrapper."""

    def test_a_tagged_and_an_adopted_wrapper_are_read(self) -> None:
        session = open_session()
        tagged = shell("b-" + session.seal + "-0", make_call("div", "k0"), "<p>l</p>")
        adopted = shell("b-" + session.seal + "-1", make_call(None, "k1"), "<tr></tr>")
        found = wrappers(tagged + adopted, session.seal)
        assert found["b-" + session.seal + "-0"][0] == "div"
        assert found["b-" + session.seal + "-1"][0] is None
        assert 'data-next-boundary-key="k1"' in found["b-" + session.seal + "-1"][1]

    def test_a_marker_without_its_wrapper_is_skipped(self) -> None:
        session = open_session()
        html = f"<!--b-{session.seal}-0:div--><p>no wrapper</p>"
        assert wrappers(html, session.seal) == {}
