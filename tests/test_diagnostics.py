import logging

import pytest
from django.core.exceptions import BadRequest, PermissionDenied, SuspiciousOperation
from django.http import Http404

from next.caches import DEFAULT_CACHE_SIZE
from next.conf.signals import settings_reloaded
from next.diagnostics import (
    INTENDED_EXCEPTIONS,
    QUIET_PERIOD,
    BackendReadLog,
    FailureLog,
    degraded,
    reset_failure_logs,
    watch_degraded,
)
from next.testing import override_next_settings


class _Backend:
    """Stand-in for the third-party backend a watch layer reads."""


_BOOM = RuntimeError("boom")


def _raise(error: Exception = _BOOM) -> list[int]:
    raise error


class _Clock:
    """A monotonic clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Replace the clock the failure logs read with one the test moves."""
    held = _Clock()
    monkeypatch.setattr("next.diagnostics.monotonic", held)
    return held


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

    def test_a_failure_that_keeps_failing_is_reported_after_the_quiet_period(
        self, log: BackendReadLog, clock: _Clock, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR, logger="next.tests.diagnostics"):
            for _ in range(3):
                log.read(_Backend(), "watched trees", _raise, valid=bool, default=[])
            clock.now += QUIET_PERIOD
            log.read(_Backend(), "watched trees", _raise, valid=bool, default=[])

        assert [record.suppressed for record in caplog.records] == [0, 2]
        assert "occurred 2 more times" in caplog.records[1].getMessage()
        assert caplog.records[1].exc_info is not None


@pytest.fixture()
def failures() -> FailureLog:
    return FailureLog(logging.getLogger("next.tests.diagnostics"))


def _contained(failures: FailureLog, key: object, error: Exception = _BOOM) -> str:
    """Run a call raising `error` through `contain`, answering the caller's fallback."""
    try:
        _raise(error)
    except Exception as exc:  # noqa: BLE001 - every failure goes to `contain`
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

    def test_strict_loading_reraises_outside_debug(
        self, failures: FailureLog, settings
    ) -> None:
        settings.DEBUG = False
        with (
            override_next_settings(STRICT_LOADING=True),
            pytest.raises(RuntimeError) as info,
        ):
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

    def test_an_intended_exception_propagates_in_production(
        self, failures: FailureLog, caplog: pytest.LogCaptureFixture, settings
    ) -> None:
        """A 404 a user callable raises stays a 404, not a logged degradation."""
        settings.DEBUG = False
        watch_degraded()
        with (
            caplog.at_level(logging.ERROR, logger="next.tests.diagnostics"),
            pytest.raises(Http404),
        ):
            _contained(failures, "items", Http404())

        assert caplog.records == []
        assert degraded() is False

    def test_an_empty_pass_through_contains_an_intended_exception(
        self, failures: FailureLog, caplog: pytest.LogCaptureFixture, settings
    ) -> None:
        """A call site outside any view contains a 404 like any other failure."""
        settings.DEBUG = False
        watch_degraded()
        with caplog.at_level(logging.ERROR, logger="next.tests.diagnostics"):
            try:
                _raise(Http404())
            except Http404 as exc:
                failures.contain(exc, "items", "items() raised", pass_through=())

        assert [record.getMessage() for record in caplog.records] == ["items() raised"]
        assert degraded() is True
        watch_degraded()

    def test_warn_degrades_nothing(self, failures: FailureLog) -> None:
        watch_degraded()
        failures.warn("a", "missing %s", "x.js")
        assert degraded() is False

    def test_intended_exceptions_name_the_http_answers(self) -> None:
        assert set(INTENDED_EXCEPTIONS) == {
            Http404,
            PermissionDenied,
            SuspiciousOperation,
            BadRequest,
        }


class TestQuietPeriod:
    """A key that keeps failing is logged again once per quiet period, with a count."""

    def test_repeats_within_the_period_stay_quiet(
        self,
        failures: FailureLog,
        clock: _Clock,
        caplog: pytest.LogCaptureFixture,
        settings,
    ) -> None:
        settings.DEBUG = False
        with caplog.at_level(logging.ERROR, logger="next.tests.diagnostics"):
            _contained(failures, "items")
            clock.now += QUIET_PERIOD - 1
            _contained(failures, "items")

        assert len(caplog.records) == 1
        assert caplog.records[0].suppressed == 0

    def test_the_first_repeat_after_the_period_carries_the_count(
        self,
        failures: FailureLog,
        clock: _Clock,
        caplog: pytest.LogCaptureFixture,
        settings,
    ) -> None:
        settings.DEBUG = False
        with caplog.at_level(logging.ERROR, logger="next.tests.diagnostics"):
            for _ in range(4):
                _contained(failures, "items")
            clock.now += QUIET_PERIOD
            _contained(failures, "items")
            _contained(failures, "items")

        assert [record.suppressed for record in caplog.records] == [0, 3]
        assert caplog.records[1].getMessage() == (
            "items() in sitemap.py raised The same failure occurred 3 more times "
            "since it was last logged."
        )
        assert caplog.records[1].exc_info is not None

    def test_a_repeat_after_a_silent_period_carries_no_count(
        self, failures: FailureLog, clock: _Clock, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="next.tests.diagnostics"):
            failures.warn("a", "missing %s", "x.js")
            clock.now += QUIET_PERIOD * 3
            failures.warn("a", "missing %s", "x.js")

        assert [record.getMessage() for record in caplog.records] == [
            "missing x.js",
            "missing x.js",
        ]

    def test_a_message_without_arguments_keeps_its_percent_sign(
        self, failures: FailureLog, clock: _Clock, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="next.tests.diagnostics"):
            failures.warn("a", "100% of x.js is missing")
            failures.warn("a", "100% of x.js is missing")
            clock.now += QUIET_PERIOD
            failures.warn("a", "100% of x.js is missing")

        assert caplog.records[1].getMessage() == (
            "100% of x.js is missing The same failure occurred 1 more times since it "
            "was last logged."
        )

    def test_first_failure_answers_at_the_same_rate(
        self, failures: FailureLog, clock: _Clock
    ) -> None:
        answers = [failures.first_failure("a", "serves")]
        answers.append(failures.first_failure("a", "serves"))
        clock.now += QUIET_PERIOD
        answers.append(failures.first_failure("a", "serves"))
        answers.append(failures.first_failure("a", "serves"))

        assert answers == [True, False, True, False]

    def test_the_held_keys_are_bounded(self, failures: FailureLog) -> None:
        """A caller that mints keys without end cannot grow the log without end."""
        for number in range(DEFAULT_CACHE_SIZE + 10):
            failures.first_failure(number)

        assert failures.first_failure(0) is True
        assert failures.first_failure(DEFAULT_CACHE_SIZE + 9) is False


class TestResetFailureLogs:
    """One call re-arms every failure log of the process."""

    def test_every_log_reports_again(self, caplog: pytest.LogCaptureFixture) -> None:
        first = FailureLog(logging.getLogger("next.tests.diagnostics"))
        second = BackendReadLog(logging.getLogger("next.tests.diagnostics"))
        with caplog.at_level(logging.WARNING, logger="next.tests.diagnostics"):
            first.warn("a", "missing %s", "x.js")
            second.warn("a", "missing %s", "y.js")
            reset_failure_logs()
            first.warn("a", "missing %s", "x.js")
            second.warn("a", "missing %s", "y.js")

        assert caplog.text.count("missing x.js") == 2
        assert caplog.text.count("missing y.js") == 2


class TestDegraded:
    """A containment marks the render it degrades, a new render starts clean."""

    def test_a_contained_failure_degrades_the_render(
        self, failures: FailureLog, settings
    ) -> None:
        settings.DEBUG = False
        watch_degraded()
        assert degraded() is False
        _contained(failures, "items")
        assert degraded() is True
        watch_degraded()
        assert degraded() is False

    def test_a_loud_failure_degrades_nothing(
        self, failures: FailureLog, settings
    ) -> None:
        settings.DEBUG = True
        watch_degraded()
        with pytest.raises(RuntimeError) as info:
            _contained(failures, "items")
        del info.value.__notes__
        assert degraded() is False
