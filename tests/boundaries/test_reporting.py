import logging
from unittest.mock import patch

import pytest
from django.core.signals import got_request_exception

from next.boundaries import boundary_failed, contain
from next.boundaries.calls import Addressing
from next.boundaries.failures import Boundary, ErrorKind
from next.diagnostics import QUIET_PERIOD
from next.static import StaticCollector
from next.testing import capture_signals
from next.transactions import savepoints
from tests.support.boundaries import RecordingErrorUI, raising


LOUD = "loud"


class TestReporting:
    """A contained failure is logged at a bounded rate and announced to receivers."""

    def test_the_failure_is_logged_once_per_quiet_period_with_its_digest(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        error_ui = RecordingErrorUI()
        with caplog.at_level(logging.ERROR, "next.boundaries"):
            for _ in range(2):
                contain(
                    Boundary("card", Addressing.COMPONENT),
                    raising(ValueError()),
                    error_ui,
                )
        (record,) = [r for r in caplog.records if r.name == "next.boundaries"]
        assert record.digest == error_ui.calls[0][0].digest
        assert record.exc_info is not None
        assert record.exc_info[0] is ValueError
        assert "component 'card'" in record.getMessage()

    def test_every_digest_shown_is_logged_the_quiet_ones_at_debug(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = [1000.0]
        monkeypatch.setattr("next.diagnostics.monotonic", lambda: now[0])
        error_ui = RecordingErrorUI()
        with caplog.at_level(logging.DEBUG, "next.boundaries"):
            for _ in range(3):
                contain(
                    Boundary("card", Addressing.COMPONENT),
                    raising(ValueError()),
                    error_ui,
                )
            now[0] += QUIET_PERIOD
            contain(
                Boundary("card", Addressing.COMPONENT), raising(ValueError()), error_ui
            )
        records = [r for r in caplog.records if r.name == "next.boundaries"]
        assert [r.digest for r in records] == [e.digest for e, _k in error_ui.calls]
        assert [r.levelno for r in records] == [
            logging.ERROR,
            logging.DEBUG,
            logging.DEBUG,
            logging.ERROR,
        ]
        assert (
            records[3]
            .getMessage()
            .endswith(
                "The same failure occurred 2 more times since it was last logged."
            )
        )

    def test_a_contained_failure_is_handled_so_django_hears_of_no_exception(
        self,
    ) -> None:
        with capture_signals(got_request_exception) as recorder:
            contain(
                Boundary("card", Addressing.COMPONENT),
                raising(ValueError()),
                RecordingErrorUI(),
            )
        assert len(recorder) == 0

    def test_the_signal_carries_the_failure(self) -> None:
        request = object()
        with capture_signals(boundary_failed) as recorder:
            html = contain(
                Boundary("card", Addressing.COMPONENT, request=request),
                raising(ValueError("x")),
                RecordingErrorUI(),
            )
        event = recorder.first_for(boundary_failed)
        assert event.sender is contain
        assert event.kwargs["name"] == "card"
        assert event.kwargs["addressing"] is Addressing.COMPONENT
        assert event.kwargs["kind"] is ErrorKind.ERROR
        assert event.kwargs["request"] is request
        assert str(event.kwargs["exception"]) == "x"
        assert f'data-next-boundary="{event.kwargs["id"]}"' in html
        assert len(event.kwargs["digest"]) == 12

    def test_no_listener_sends_nothing(self) -> None:
        with (
            patch.object(boundary_failed, "has_listeners", return_value=False),
            patch.object(boundary_failed, "send_robust") as send,
        ):
            contain(
                Boundary("card", Addressing.COMPONENT),
                raising(ValueError()),
                RecordingErrorUI(),
            )
        send.assert_not_called()

    def test_a_raising_receiver_never_escapes_the_boundary(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def receiver(**kwargs: object) -> None:
            raise KeyError(LOUD)

        collector = StaticCollector()
        boundary_failed.connect(receiver, sender=contain, weak=False)
        try:
            with caplog.at_level(logging.ERROR, "django.dispatch"):
                html = contain(
                    Boundary("card", Addressing.COMPONENT, collector=collector),
                    raising(ValueError()),
                    RecordingErrorUI(),
                )
        finally:
            boundary_failed.disconnect(receiver, sender=contain)
        assert html.endswith("<p>sorry</p></div>")
        assert collector._journal is None
        assert savepoints._local.session is None
        assert any(r.exc_info and r.exc_info[0] is KeyError for r in caplog.records)
