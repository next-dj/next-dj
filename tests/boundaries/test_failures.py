import asyncio
import logging
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from django.core.exceptions import (
    BadRequest,
    ImproperlyConfigured,
    PermissionDenied,
    RequestAborted,
    SuspiciousOperation,
)
from django.http import Http404
from django.http.multipartparser import MultiPartParserError
from django.template import Context, Template
from django.test import override_settings

from next.boundaries import contain
from next.boundaries.calls import Addressing
from next.boundaries.failures import (
    Boundary,
    BoundaryFailure,
    DebugBoundaryFailure,
    ErrorKind,
    contained,
    kind_of,
    retry_loads,
    shown_kind,
    shown_source,
    unrun_error,
)
from next.static import ScriptInjectionPolicy, StaticCollector
from next.static.assets import StaticAsset
from next.testing import override_next_settings
from tests.support.boundaries import RecordingErrorUI, defer_errors, raising


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
    """`BOUNDARIES` switches raising, reporting and the DEBUG detail."""

    def test_raise_reraises_after_the_rollback(self) -> None:
        collector = StaticCollector()

        def render() -> str:
            collector.add(StaticAsset(url="/failed.css", kind="css"))
            raise ValueError(LOUD)

        error_ui = RecordingErrorUI()
        with defer_errors(RAISE=True), pytest.raises(ValueError, match="loud"):
            contain(
                Boundary("card", Addressing.COMPONENT, collector=collector),
                render,
                error_ui,
            )
        assert error_ui.calls == []
        assert list(collector.assets_in_slot("styles")) == []

    def test_a_misfit_raises_before_the_boundary_runs(self) -> None:
        error_ui = RecordingErrorUI()
        with (
            override_next_settings(BOUNDARIES={"RAISE": "yes"}),
            pytest.raises(ImproperlyConfigured, match=r"\['BOUNDARIES'\]\['RAISE'\]"),
        ):
            contain(
                Boundary("card", Addressing.COMPONENT), raising(ValueError()), error_ui
            )
        assert error_ui.calls == []

    def test_the_reporter_replaces_the_log_and_names_the_digest(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        error_ui = RecordingErrorUI()
        with (
            defer_errors(REPORTER=f"{__name__}.report"),
            caplog.at_level(logging.ERROR, "next.boundaries"),
        ):
            for _ in range(2):
                contain(
                    Boundary("card", Addressing.COMPONENT),
                    raising(ValueError("r")),
                    error_ui,
                )
        assert [error.digest for error, _kind in error_ui.calls] == [
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
        error_ui = RecordingErrorUI()
        with (
            defer_errors(REPORTER=dotted),
            caplog.at_level(logging.ERROR, "next.boundaries"),
        ):
            contain(
                Boundary("card", Addressing.COMPONENT),
                raising(ValueError("x")),
                error_ui,
            )
        reporter_record, failure_record = [
            r for r in caplog.records if r.name == "next.boundaries"
        ]
        assert reporter_record.exc_info is not None
        assert reporter_record.exc_info[0] is logged
        assert "default digest and logging stand in" in reporter_record.getMessage()
        digest = error_ui.calls[0][0].digest
        assert failure_record.digest == digest
        assert len(digest) == 12

    def test_a_reporter_answering_a_misfit_digest_is_logged_and_falls_back(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        error_ui = RecordingErrorUI()
        with (
            defer_errors(REPORTER=f"{__name__}.report_empty"),
            caplog.at_level(logging.ERROR, "next.boundaries"),
        ):
            contain(
                Boundary("card", Addressing.COMPONENT), raising(ValueError()), error_ui
            )
        answer_record, failure_record = [
            r for r in caplog.records if r.name == "next.boundaries"
        ]
        assert "rather than a digest string" in answer_record.getMessage()
        assert failure_record.digest == error_ui.calls[0][0].digest
        assert len(error_ui.calls[0][0].digest) == 12

    def test_a_reporter_answering_none_has_no_reference_and_the_default_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        error_ui = RecordingErrorUI()
        with (
            defer_errors(REPORTER=f"{__name__}.report_none"),
            caplog.at_level(logging.ERROR, "next.boundaries"),
        ):
            contain(
                Boundary("card", Addressing.COMPONENT), raising(ValueError()), error_ui
            )
        (failure_record,) = [r for r in caplog.records if r.name == "next.boundaries"]
        assert "failed and rendered its error.djx" in failure_record.getMessage()
        assert failure_record.exc_info is not None
        assert failure_record.digest == error_ui.calls[0][0].digest
        assert len(error_ui.calls[0][0].digest) == 12

    def test_debug_adds_the_message_the_traceback_and_the_detail(self) -> None:
        error_ui = RecordingErrorUI()
        with override_settings(DEBUG=True):
            html = contain(
                Boundary("card", Addressing.COMPONENT),
                raising(ValueError("<script>x</script>")),
                error_ui,
            )
        error = error_ui.calls[0][0]
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
                RecordingErrorUI(),
            )
        assert "data-next-error-detail" not in html
        assert html.endswith("<p>sorry</p></div>")


class TestCatchRules:
    """Every exception is contained except the ones Django answers and disconnects."""

    @pytest.mark.parametrize(
        ("exc", "before", "after"),
        [
            (ValueError("x"), True, True),
            (KeyError("x"), True, True),
            (Http404(), False, True),
            (PermissionDenied(), False, True),
            (SuspiciousOperation(), False, True),
            (BadRequest(), False, True),
            (MultiPartParserError(), False, True),
            (RequestAborted(), False, False),
            (KeyboardInterrupt(), False, False),
            (GeneratorExit(), False, False),
            (asyncio.CancelledError(), False, False),
        ],
        ids=lambda value: (
            type(value).__name__ if isinstance(value, BaseException) else None
        ),
    )
    def test_contained_before_and_after_the_first_byte(
        self, exc: BaseException, *, before: bool, after: bool
    ) -> None:
        assert contained(exc) is before
        assert contained(exc, started=True) is after

    @pytest.mark.parametrize(
        ("exc", "kind"),
        [
            (Http404(), ErrorKind.NOT_FOUND),
            (PermissionDenied(), ErrorKind.FORBIDDEN),
            (BadRequest(), ErrorKind.BAD_REQUEST),
            (SuspiciousOperation(), ErrorKind.BAD_REQUEST),
            (MultiPartParserError(), ErrorKind.BAD_REQUEST),
            (ValueError(), ErrorKind.ERROR),
        ],
    )
    def test_kind_of(self, exc: Exception, kind: ErrorKind) -> None:
        assert kind_of(exc) is kind

    @pytest.mark.parametrize(
        ("kind", "opaque", "shown"),
        [
            (ErrorKind.NOT_FOUND, True, ErrorKind.ERROR),
            (ErrorKind.FORBIDDEN, True, ErrorKind.ERROR),
            (ErrorKind.BAD_REQUEST, True, ErrorKind.ERROR),
            (ErrorKind.OVERLOADED, True, ErrorKind.ERROR),
            (ErrorKind.TIMEOUT, True, ErrorKind.TIMEOUT),
            (ErrorKind.NOT_FOUND, False, ErrorKind.NOT_FOUND),
        ],
    )
    def test_shown_kind(
        self, kind: ErrorKind, shown: ErrorKind, *, opaque: bool
    ) -> None:
        assert shown_kind(kind, opaque=opaque) is shown


@pytest.fixture()
def script_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[ScriptInjectionPolicy], None]:
    """Return a setter of the injection policy the static manager reports."""

    def use(policy: ScriptInjectionPolicy) -> None:
        builder = SimpleNamespace(policy=policy)
        monkeypatch.setattr(
            "next.static.ports.get_static_manager",
            lambda: SimpleNamespace(script_builder=lambda: builder),
        )

    return use


class TestRetryLoads:
    """A retry needs the runtime script, so it follows the injection policy."""

    @pytest.mark.parametrize(
        ("policy", "loads"),
        [
            (ScriptInjectionPolicy.AUTO, True),
            (ScriptInjectionPolicy.MANUAL, True),
            (ScriptInjectionPolicy.DISABLED, False),
        ],
    )
    def test_only_a_disabled_script_blocks_the_retry(
        self, script_policy, policy: ScriptInjectionPolicy, *, loads: bool
    ) -> None:
        script_policy(policy)
        assert retry_loads() is loads

    def test_the_default_policy_loads_the_script(self) -> None:
        assert retry_loads() is True


class TestUnrunError:
    """The error of a boundary that never ran carries no message and a fresh digest."""

    @pytest.mark.parametrize("kind", [ErrorKind.TIMEOUT, ErrorKind.OVERLOADED])
    def test_the_kind_is_shown_as_it_is(self, kind: ErrorKind) -> None:
        failure = unrun_error(kind)
        assert failure == BoundaryFailure(kind, failure.digest, kind.status, True)

    def test_every_error_has_its_own_digest(self) -> None:
        assert (
            unrun_error(ErrorKind.ERROR).digest != unrun_error(ErrorKind.ERROR).digest
        )

    def test_an_opaque_setting_shows_a_status_kind_as_an_error(self) -> None:
        with override_next_settings(BOUNDARIES={"KINDS": "opaque"}):
            failure = unrun_error(ErrorKind.OVERLOADED)
        assert failure.kind is ErrorKind.ERROR
        assert failure.status is None

    def test_the_retry_follows_the_script_policy(self, script_policy) -> None:
        script_policy(ScriptInjectionPolicy.DISABLED)
        assert unrun_error(ErrorKind.TIMEOUT).retry is False

    @override_settings(DEBUG=True)
    def test_debug_keeps_the_message_and_the_traceback_empty(self) -> None:
        failure = unrun_error(ErrorKind.ERROR)
        assert (failure.message, failure.traceback) == ("", "")


class TestShownSource:
    """DEBUG names the file that caught relative to the project where it can."""

    def test_a_file_inside_the_project_is_relative(self, tmp_path) -> None:
        with override_settings(BASE_DIR=tmp_path):
            assert shown_source(tmp_path / "pages" / "error.djx") == "pages/error.djx"

    def test_a_file_outside_the_project_stays_absolute(self, tmp_path) -> None:
        source = tmp_path / "error.djx"
        with override_settings(BASE_DIR=tmp_path / "project"):
            assert shown_source(source) == str(source)


class TestErrorKind:
    """The kinds a boundary settles as, each with the status it stands for."""

    def test_the_error_kinds_include_the_client_ones(self) -> None:
        assert list(ErrorKind) == [
            "error",
            "timeout",
            "overloaded",
            "not_found",
            "forbidden",
            "bad_request",
            "incomplete",
            "network",
        ]

    @pytest.mark.parametrize(
        ("kind", "status"),
        [
            (ErrorKind.NOT_FOUND, 404),
            (ErrorKind.FORBIDDEN, 403),
            (ErrorKind.BAD_REQUEST, 400),
            (ErrorKind.OVERLOADED, 503),
            (ErrorKind.ERROR, None),
            (ErrorKind.TIMEOUT, None),
            (ErrorKind.NETWORK, None),
        ],
    )
    def test_each_kind_names_its_status(self, kind: ErrorKind, status) -> None:
        assert kind.status == status


class TestBoundaryFailure:
    def test_the_detail_is_absent_outside_debug(self) -> None:
        error = BoundaryFailure.of(
            ErrorKind.NOT_FOUND, "a1b2c3d4e5f6", retry=True, message="x", traceback="tb"
        )
        assert error == BoundaryFailure(ErrorKind.NOT_FOUND, "a1b2c3d4e5f6", 404, True)
        assert not hasattr(error, "message")
        assert not hasattr(error, "traceback")

    def test_a_template_reads_the_absent_detail_as_empty(self) -> None:
        error = BoundaryFailure.of(
            ErrorKind.ERROR, "a1b2c3d4e5f6", retry=True, message="x", traceback="tb"
        )
        template = Template(
            "{{ error.kind }}:{{ error.message }}{% if error.traceback %}!{% endif %}"
        )
        assert template.render(Context({"error": error})) == "error:"

    @override_settings(DEBUG=True)
    def test_the_detail_is_kept_under_debug(self) -> None:
        error = BoundaryFailure.of(
            ErrorKind.TIMEOUT, "a1b2c3d4e5f6", retry=False, message="x", traceback="tb"
        )
        assert error == DebugBoundaryFailure(
            ErrorKind.TIMEOUT, "a1b2c3d4e5f6", None, False, "x", "tb"
        )
        assert "tb" not in repr(error)
