import pytest
from django.dispatch import Signal

from next.testing import SignalEvent, SignalRecorder


class TestSignalRecorder:
    """Recorder captures emissions between start and stop."""

    def test_rejects_empty_signal_list(self) -> None:
        with pytest.raises(ValueError, match="at least one signal"):
            SignalRecorder()

    def test_captures_single_signal(self) -> None:
        sig = Signal()
        with SignalRecorder(sig) as rec:
            sig.send(sender="s", payload=1)
        assert len(rec) == 1
        event = rec.events[0]
        assert isinstance(event, SignalEvent)
        assert event.sender == "s"
        assert event.kwargs["payload"] == 1
        assert event.signal is sig

    def test_captures_multiple_signals(self) -> None:
        a = Signal()
        b = Signal()
        with SignalRecorder(a, b) as rec:
            a.send(sender="sa", value=1)
            b.send(sender="sb", value=2)
            a.send(sender="sa2", value=3)
        assert len(rec) == 3
        assert len(rec.events_for(a)) == 2
        assert len(rec.events_for(b)) == 1

    def test_does_not_capture_after_stop(self) -> None:
        sig = Signal()
        rec = SignalRecorder(sig).start()
        sig.send(sender="s", x=1)
        rec.stop()
        sig.send(sender="s", x=2)
        assert len(rec) == 1

    def test_start_is_idempotent(self) -> None:
        sig = Signal()
        rec = SignalRecorder(sig)
        rec.start()
        rec.start()
        sig.send(sender="s")
        rec.stop()
        assert len(rec) == 1

    def test_stop_is_idempotent(self) -> None:
        sig = Signal()
        rec = SignalRecorder(sig).start()
        rec.stop()
        rec.stop()
        sig.send(sender="s")
        assert len(rec) == 0

    def test_iteration_yields_events(self) -> None:
        sig = Signal()
        with SignalRecorder(sig) as rec:
            sig.send(sender="s", n=1)
            sig.send(sender="s", n=2)
        values = [event.kwargs["n"] for event in rec]
        assert values == [1, 2]

    def test_clear_drops_events_but_keeps_connection(self) -> None:
        sig = Signal()
        rec = SignalRecorder(sig).start()
        sig.send(sender="s", n=1)
        rec.clear()
        assert len(rec) == 0
        sig.send(sender="s", n=2)
        rec.stop()
        assert [event.kwargs["n"] for event in rec] == [2]
