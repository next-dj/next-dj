import logging

import pytest
from django.core.exceptions import (
    BadRequest,
    PermissionDenied,
    RequestAborted,
    SuspiciousOperation,
)
from django.http import Http404
from django.http.multipartparser import MultiPartParserError

from next.boundaries import Boundary, contain, scope
from next.defer import Addressing, BoundaryFailure, ErrorKind
from next.diagnostics import degraded
from next.static import StaticCollector
from next.static.assets import StaticAsset
from tests.support.boundaries import RecordingFallback, defer_errors, raising


class TestContain:
    """A guarded subtree renders, or its failure is contained behind the error UI."""

    def test_a_success_returns_the_render(self) -> None:
        fallback = RecordingFallback()
        assert contain(
            Boundary("card", Addressing.COMPONENT), lambda: "<b>ok</b>", fallback
        ) == ("<b>ok</b>")
        assert fallback.calls == []
        assert degraded() is False

    def test_a_failure_renders_the_error_ui_in_its_wrapper(self) -> None:
        fallback = RecordingFallback()
        html = contain(
            Boundary("card", Addressing.COMPONENT), raising(ValueError("x")), fallback
        )
        ((error, kind),) = fallback.calls
        assert kind is ErrorKind.ERROR
        assert type(error) is BoundaryFailure
        assert error.kind is ErrorKind.ERROR
        assert error.status is None
        assert error.retry is True
        assert len(error.digest) == 12
        assert int(error.digest, 16) >= 0
        assert html.startswith('<div id="b-')
        assert (
            'data-next-state="error" data-next-error="error"><p>sorry</p></div>' in html
        )
        assert "data-next-error-digest" not in html
        assert degraded() is True

    def test_retry_follows_the_fallback(self) -> None:
        fallback = RecordingFallback(retry=False)
        contain(Boundary("card", Addressing.COMPONENT), raising(ValueError()), fallback)
        assert fallback.calls[0][0].retry is False

    @pytest.mark.parametrize(
        "exc",
        [Http404(), PermissionDenied(), BadRequest(), RequestAborted()],
        ids=lambda exc: type(exc).__name__,
    )
    def test_an_intended_exception_passes_before_the_first_byte(
        self, exc: Exception
    ) -> None:
        fallback = RecordingFallback()
        with pytest.raises(type(exc)):
            contain(Boundary("card", Addressing.COMPONENT), raising(exc), fallback)
        assert fallback.calls == []
        assert degraded() is False

    def test_a_base_exception_passes_and_undoes_the_journal(self) -> None:
        collector = StaticCollector()

        def render() -> str:
            collector.add(StaticAsset(url="/failed.css", kind="css"))
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            contain(
                Boundary("card", Addressing.COMPONENT, collector=collector),
                render,
                RecordingFallback(),
            )
        assert list(collector.assets_in_slot("styles")) == []
        assert collector._journal is None

    def test_an_intended_exception_after_the_first_byte_maps_to_its_kind(self) -> None:
        fallback = RecordingFallback()
        boundary = Boundary("card", Addressing.COMPONENT, started=True)
        html = contain(boundary, raising(Http404()), fallback)
        error, kind = fallback.calls[0]
        assert kind is ErrorKind.NOT_FOUND
        assert error.kind is ErrorKind.NOT_FOUND
        assert error.status == 404
        assert 'data-next-error="not_found"' in html

    def test_opaque_kinds_hide_the_kind_but_the_fallback_reads_it(self) -> None:
        fallback = RecordingFallback()
        with defer_errors(KINDS="opaque"):
            html = contain(
                Boundary("card", Addressing.COMPONENT, started=True),
                raising(PermissionDenied()),
                fallback,
            )
        error, kind = fallback.calls[0]
        assert kind is ErrorKind.FORBIDDEN
        assert error.kind is ErrorKind.ERROR
        assert error.status is None
        assert 'data-next-error="error"' in html

    @pytest.mark.parametrize(
        "exc", [SuspiciousOperation("odd"), MultiPartParserError("odd")]
    )
    def test_a_suspicious_operation_after_the_first_byte_is_logged_as_django_does(
        self, exc: Exception, caplog: pytest.LogCaptureFixture
    ) -> None:
        name = f"django.security.{type(exc).__name__}"
        with caplog.at_level(logging.ERROR, name):
            contain(
                Boundary("card", Addressing.COMPONENT, started=True),
                raising(exc),
                RecordingFallback(),
            )
        records = [record for record in caplog.records if record.name == name]
        assert [record.getMessage() for record in records] == ["odd"]
        assert records[0].status_code == 400

    def test_a_bad_request_is_not_a_security_event(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR):
            contain(
                Boundary("card", Addressing.COMPONENT, started=True),
                raising(BadRequest("odd")),
                RecordingFallback(),
            )
        assert not any(r.name.startswith("django.security") for r in caplog.records)

    def test_no_tag_stamps_the_root_of_the_error_ui(self) -> None:
        html = contain(
            Boundary("row", Addressing.COMPONENT),
            raising(ValueError()),
            RecordingFallback("<tr><td>x</td></tr>", tag=None),
        )
        assert html.startswith('<tr id="b-')
        assert html.endswith('data-next-error="error"><td>x</td></tr>')


class TestNesting:
    """The innermost boundary catches first, and the render shares one state."""

    def test_the_innermost_boundary_catches_and_the_outer_one_renders(self) -> None:
        outer, middle, inner = (
            RecordingFallback("O"),
            RecordingFallback("M"),
            RecordingFallback("I"),
        )

        leaf = raising(ValueError("leaf"))

        def middle_render() -> str:
            return (
                "["
                + contain(Boundary("inner", Addressing.COMPONENT), leaf, inner)
                + "]"
            )

        def outer_render() -> str:
            return (
                "("
                + contain(
                    Boundary("middle", Addressing.COMPONENT), middle_render, middle
                )
                + ")"
            )

        html = contain(Boundary("outer", Addressing.COMPONENT), outer_render, outer)
        assert html.startswith('([<div id="b-')
        assert html.endswith(">I</div>])")
        assert len(inner.calls) == 1
        assert middle.calls == []
        assert outer.calls == []

    def test_a_failing_error_ui_reaches_the_next_boundary_up(self) -> None:
        outer = RecordingFallback("O")
        inner = RecordingFallback(
            render=lambda _error, _kind: raising(KeyError("ui"))()
        )

        def outer_render() -> str:
            return contain(
                Boundary("inner", Addressing.COMPONENT), raising(ValueError()), inner
            )

        html = contain(Boundary("outer", Addressing.COMPONENT), outer_render, outer)
        assert html.endswith(">O</div>")
        assert len(outer.calls) == 1
        assert len(inner.calls) == 1

    def test_the_same_name_is_not_caught_by_itself_inside_its_error_ui(self) -> None:
        def own_error_ui(error: BoundaryFailure, kind: ErrorKind) -> str:
            return contain(
                Boundary("card", Addressing.COMPONENT), raising(KeyError("again")), own
            )

        own = RecordingFallback(render=own_error_ui)
        with pytest.raises(KeyError, match="again"):
            contain(Boundary("card", Addressing.COMPONENT), raising(ValueError()), own)
        assert len(own.calls) == 1

    def test_another_name_inside_an_error_ui_still_catches(self) -> None:
        def error_ui(error: BoundaryFailure, kind: ErrorKind) -> str:
            return contain(
                Boundary("other", Addressing.COMPONENT), raising(KeyError()), other
            )

        other = RecordingFallback("X")
        html = contain(
            Boundary("card", Addressing.COMPONENT),
            raising(ValueError()),
            RecordingFallback(render=error_ui),
        )
        assert ">X</div></div>" in html

    def test_one_render_probes_the_aliases_once_and_numbers_its_boundaries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probes: list[int] = []

        def probe() -> tuple[str, ...]:
            probes.append(1)
            return ()

        monkeypatch.setattr(scope, "probe_atomic_aliases", probe)

        def outer_render() -> str:
            first = contain(
                Boundary("a", Addressing.COMPONENT),
                raising(ValueError()),
                RecordingFallback(),
            )
            second = contain(
                Boundary("b", Addressing.COMPONENT),
                raising(ValueError()),
                RecordingFallback(),
            )
            return first + second

        html = contain(
            Boundary("outer", Addressing.COMPONENT), outer_render, RecordingFallback()
        )
        ids = [part.split('"')[0] for part in html.split('data-next-boundary="')[1:]]
        assert len(probes) == 1
        assert [identifier.rsplit("-", 1)[1] for identifier in ids] == ["1", "2"]
        assert ids[0].rsplit("-", 1)[0] == ids[1].rsplit("-", 1)[0]


class TestOwners:
    """A boundary rendering its own error UI is keyed by owner, not by name alone."""

    def test_the_same_owner_inside_its_error_ui_is_not_caught_again(self) -> None:
        owner = object()

        def own_error_ui(error: BoundaryFailure, kind: ErrorKind) -> str:
            return contain(
                Boundary("card", Addressing.COMPONENT, owner=owner),
                raising(KeyError()),
                own,
            )

        own = RecordingFallback(render=own_error_ui)
        with pytest.raises(KeyError):
            contain(
                Boundary("card", Addressing.COMPONENT, owner=owner),
                raising(ValueError()),
                own,
            )

    def test_a_same_named_component_of_another_scope_is_still_caught(self) -> None:
        other = RecordingFallback("X")

        def error_ui(error: BoundaryFailure, kind: ErrorKind) -> str:
            return contain(
                Boundary("card", Addressing.COMPONENT, owner="scoped"),
                raising(KeyError()),
                other,
            )

        html = contain(
            Boundary("card", Addressing.COMPONENT, owner="shared"),
            raising(ValueError()),
            RecordingFallback(render=error_ui),
        )
        assert ">X</div></div>" in html
