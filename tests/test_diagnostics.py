import logging

import pytest
from django.core.exceptions import PermissionDenied
from django.http import Http404

from next.conf.signals import settings_reloaded
from next.diagnostics import INTENDED_EXCEPTIONS, BackendReadLog, FailureLog


class _Backend:
    """Stand-in for the third-party backend a watch layer reads."""


_BOOM = RuntimeError("boom")


def _raise() -> list[int]:
    raise _BOOM


@pytest.fixture()
def log() -> BackendReadLog:
    return BackendReadLog(logging.getLogger("next.tests.diagnostics"))


class TestBackendReadLog:
    """A read answers the backend, or the default plus one report."""

    def test_a_sound_answer_reaches_the_caller(self, log: BackendReadLog) -> None:
        answer = log.read(
            _Backend(), "watched trees", lambda: [1, 2], valid=bool, default=[]
        )

        assert answer == [1, 2]

    def test_a_raising_read_answers_the_default(
        self, log: BackendReadLog, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR, logger="next.tests.diagnostics"):
            answer = log.read(
                _Backend(), "watched trees", _raise, valid=bool, default=[]
            )

        assert answer == []
        assert "failed to report its watched trees" in caplog.text
        assert "boom" in caplog.text

    def test_a_malformed_answer_is_reported_without_a_traceback(
        self, log: BackendReadLog, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR, logger="next.tests.diagnostics"):
            answer = log.read(
                _Backend(),
                "watched trees",
                lambda: ["nope"],
                valid=lambda roots: all(isinstance(r, int) for r in roots),
                default=[],
            )

        assert answer == []
        assert "reported watched trees of the wrong type" in caplog.text
        assert caplog.records[0].exc_info is None

    def test_a_raising_check_counts_as_a_failing_read(
        self, log: BackendReadLog, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Inspecting what came back is itself a read of the backend's own code."""
        with caplog.at_level(logging.ERROR, logger="next.tests.diagnostics"):
            answer = log.read(
                _Backend(), "watched trees", lambda: [1], valid=_raise, default=[]
            )

        assert answer == []
        assert "failed to report its watched trees" in caplog.text

    def test_the_same_failure_is_reported_once(
        self, log: BackendReadLog, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR, logger="next.tests.diagnostics"):
            for _ in range(3):
                log.read(_Backend(), "watched trees", _raise, valid=bool, default=[])

        assert caplog.text.count("failed to report its watched trees") == 1

    def test_the_same_malformed_answer_is_reported_once(
        self, log: BackendReadLog, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR, logger="next.tests.diagnostics"):
            for _ in range(3):
                answer = log.read(
                    _Backend(),
                    "watched trees",
                    lambda: ["nope"],
                    valid=lambda roots: all(isinstance(r, int) for r in roots),
                    default=[],
                )

        assert answer == []
        assert caplog.text.count("reported watched trees of the wrong type") == 1

    def test_another_subject_of_one_source_reports_again(
        self, log: BackendReadLog, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR, logger="next.tests.diagnostics"):
            log.read(_Backend(), "watched trees", _raise, valid=bool, default=[])
            log.read(_Backend(), "page roots", _raise, valid=bool, default=[])

        assert caplog.text.count("failed to report its") == 2

    def test_a_reconfigure_re_arms_the_diagnostic(
        self, log: BackendReadLog, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR, logger="next.tests.diagnostics"):
            log.read(_Backend(), "watched trees", _raise, valid=bool, default=[])
            settings_reloaded.send(sender=None)
            log.read(_Backend(), "watched trees", _raise, valid=bool, default=[])

        assert caplog.text.count("failed to report its watched trees") == 2


@pytest.fixture()
def failures() -> FailureLog:
    return FailureLog(logging.getLogger("next.tests.diagnostics"))


def _contained(failures: FailureLog, key: object) -> str:
    """Run a failing call through `contain`, answering the caller's fallback."""
    try:
        _raise()
    except RuntimeError as exc:
        failures.contain(exc, key, "items() in %s raised", "sitemap.py")
    return "fallback"


class TestFailureLog:
    """User code that raises is loud under DEBUG and logged once otherwise."""

    def test_production_logs_the_first_failure_with_its_traceback(
        self, failures: FailureLog, caplog: pytest.LogCaptureFixture, settings
    ) -> None:
        settings.DEBUG = False
        with caplog.at_level(logging.ERROR, logger="next.tests.diagnostics"):
            first = _contained(failures, "items")
            second = _contained(failures, "items")

        assert first == second == "fallback"
        assert caplog.text.count("items() in sitemap.py raised") == 1
        assert caplog.records[0].exc_info is not None

    def test_debug_reraises_with_the_source_as_a_note(
        self, failures: FailureLog, settings
    ) -> None:
        settings.DEBUG = True
        with pytest.raises(RuntimeError) as info:
            _contained(failures, "items")

        assert "items() in sitemap.py raised" in info.value.__notes__
        del info.value.__notes__

    def test_a_reconfigure_rearms_the_report(
        self, failures: FailureLog, caplog: pytest.LogCaptureFixture, settings
    ) -> None:
        settings.DEBUG = False
        with caplog.at_level(logging.ERROR, logger="next.tests.diagnostics"):
            _contained(failures, "items")
            settings_reloaded.send(sender=None)
            _contained(failures, "items")

        assert caplog.text.count("items() in sitemap.py raised") == 2

    def test_warn_reports_once_per_key(
        self, failures: FailureLog, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="next.tests.diagnostics"):
            failures.warn("a", "missing %s", "x.js")
            failures.warn("a", "missing %s", "x.js")
            failures.warn("b", "missing %s", "y.js")

        assert caplog.text.count("missing x.js") == 1
        assert "missing y.js" in caplog.text

    def test_intended_exceptions_name_the_http_answers(self) -> None:
        assert Http404 in INTENDED_EXCEPTIONS
        assert PermissionDenied in INTENDED_EXCEPTIONS
