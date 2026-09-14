import logging

import pytest

from next.conf.signals import settings_reloaded
from next.diagnostics import BackendReadLog


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
