import logging

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from next.boundaries import Boundary, contain
from next.defer import Addressing, DebugBoundaryFailure, ErrorKind
from next.static import StaticCollector
from next.static.assets import StaticAsset
from next.testing import override_next_settings
from tests.support.boundaries import RecordingFallback, defer_errors, raising


REPORTED: list[tuple[Exception, dict[str, object]]] = []
NOT_CALLABLE = "a string"
LOUD = "loud"


def report(exc: Exception, **kwargs: object) -> str:
    REPORTED.append((exc, kwargs))
    return "custom-digest"


def report_raising(exc: Exception, **kwargs: object) -> str:
    raise RuntimeError(LOUD)


def report_none(exc: Exception, **kwargs: object) -> None:
    return None


def report_empty(exc: Exception, **kwargs: object) -> str:
    return ""


@pytest.fixture(autouse=True)
def _reported():
    REPORTED.clear()
    yield
    REPORTED.clear()


class TestSettings:
    """`DEFER["ERRORS"]` switches raising, reporting and the DEBUG detail."""

    def test_raise_reraises_after_the_rollback(self) -> None:
        collector = StaticCollector()

        def render() -> str:
            collector.add(StaticAsset(url="/failed.css", kind="css"))
            raise ValueError(LOUD)

        fallback = RecordingFallback()
        with defer_errors(RAISE=True), pytest.raises(ValueError, match="loud"):
            contain(
                Boundary("card", Addressing.COMPONENT, collector=collector),
                render,
                fallback,
            )
        assert fallback.calls == []
        assert list(collector.assets_in_slot("styles")) == []

    def test_a_misfit_value_reads_as_its_default(self) -> None:
        fallback = RecordingFallback()
        with defer_errors(RAISE="yes"):
            contain(
                Boundary("card", Addressing.COMPONENT), raising(ValueError()), fallback
            )
        assert len(fallback.calls) == 1

    def test_errors_that_are_no_mapping_read_as_the_defaults(self) -> None:
        fallback = RecordingFallback()
        with override_next_settings(DEFER={"ERRORS": "loud"}):
            contain(
                Boundary("card", Addressing.COMPONENT), raising(ValueError()), fallback
            )
            contain(
                Boundary("card", Addressing.COMPONENT), raising(ValueError()), fallback
            )
        assert len(fallback.calls) == 2

    def test_the_reporter_replaces_the_log_and_names_the_digest(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fallback = RecordingFallback()
        with (
            defer_errors(REPORTER=f"{__name__}.report"),
            caplog.at_level(logging.ERROR, "next.boundaries"),
        ):
            for _ in range(2):
                contain(
                    Boundary("card", Addressing.COMPONENT),
                    raising(ValueError("r")),
                    fallback,
                )
        assert [error.digest for error, _kind in fallback.calls] == [
            "custom-digest",
            "custom-digest",
        ]
        exc, kwargs = REPORTED[0]
        assert str(exc) == "r"
        assert kwargs == {
            "name": "card",
            "addressing": Addressing.COMPONENT,
            "kind": ErrorKind.ERROR,
            "request": None,
        }
        assert not [r for r in caplog.records if r.name == "next.boundaries"]

    @pytest.mark.parametrize(
        ("dotted", "logged"),
        [
            (f"{__name__}.missing", ImproperlyConfigured),
            (f"{__name__}.NOT_CALLABLE", ImproperlyConfigured),
            (f"{__name__}.report_raising", RuntimeError),
        ],
    )
    def test_a_failing_reporter_falls_back_to_the_default_digest_and_log(
        self, dotted: str, logged: type[Exception], caplog: pytest.LogCaptureFixture
    ) -> None:
        fallback = RecordingFallback()
        with (
            defer_errors(REPORTER=dotted),
            caplog.at_level(logging.ERROR, "next.boundaries"),
        ):
            contain(
                Boundary("card", Addressing.COMPONENT),
                raising(ValueError("x")),
                fallback,
            )
        reporter_record, failure_record = [
            r for r in caplog.records if r.name == "next.boundaries"
        ]
        assert reporter_record.exc_info is not None
        assert reporter_record.exc_info[0] is logged
        assert "default digest and logging stand in" in reporter_record.getMessage()
        digest = fallback.calls[0][0].digest
        assert failure_record.digest == digest
        assert len(digest) == 12

    def test_a_reporter_answering_a_misfit_digest_is_logged_and_falls_back(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fallback = RecordingFallback()
        with (
            defer_errors(REPORTER=f"{__name__}.report_empty"),
            caplog.at_level(logging.ERROR, "next.boundaries"),
        ):
            contain(
                Boundary("card", Addressing.COMPONENT), raising(ValueError()), fallback
            )
        answer_record, failure_record = [
            r for r in caplog.records if r.name == "next.boundaries"
        ]
        assert "rather than a digest string" in answer_record.getMessage()
        assert failure_record.digest == fallback.calls[0][0].digest
        assert len(fallback.calls[0][0].digest) == 12

    def test_a_reporter_answering_none_has_no_reference_and_the_default_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fallback = RecordingFallback()
        with (
            defer_errors(REPORTER=f"{__name__}.report_none"),
            caplog.at_level(logging.ERROR, "next.boundaries"),
        ):
            contain(
                Boundary("card", Addressing.COMPONENT), raising(ValueError()), fallback
            )
        (failure_record,) = [r for r in caplog.records if r.name == "next.boundaries"]
        assert "failed and rendered its error.djx" in failure_record.getMessage()
        assert failure_record.exc_info is not None
        assert failure_record.digest == fallback.calls[0][0].digest
        assert len(fallback.calls[0][0].digest) == 12

    def test_debug_adds_the_message_the_traceback_and_the_detail(self) -> None:
        fallback = RecordingFallback()
        with override_settings(DEBUG=True):
            html = contain(
                Boundary("card", Addressing.COMPONENT),
                raising(ValueError("<script>x</script>")),
                fallback,
            )
        error = fallback.calls[0][0]
        assert isinstance(error, DebugBoundaryFailure)
        assert error.message == "ValueError: <script>x</script>"
        assert "Traceback" in error.traceback
        assert f'data-next-error-digest="{error.digest}"' in html
        assert html.endswith("</pre></details></div>")
        assert "<script>x" not in html
        assert html.count("Traceback (most recent call last)") == 1

    def test_debug_detail_off_keeps_only_the_author_markup(self) -> None:
        with override_settings(DEBUG=True), defer_errors(DEBUG_DETAIL=False):
            html = contain(
                Boundary("card", Addressing.COMPONENT),
                raising(ValueError()),
                RecordingFallback(),
            )
        assert "data-next-error-detail" not in html
        assert html.endswith("<p>sorry</p></div>")
