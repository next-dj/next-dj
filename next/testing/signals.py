"""Signal capture utility for tests.

`SignalRecorder` is a context manager that connects to one or more
Django signals, stores every emission as a `SignalEvent`, and
disconnects on exit. It works with plain Django `TestCase`, the stdlib
`unittest.TestCase`, and pytest without any framework-specific code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self


if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import TracebackType

    from django.dispatch import Signal


@dataclass(frozen=True, slots=True)
class SignalEvent:
    """Single captured emission of a Django signal."""

    signal: Signal
    sender: Any
    kwargs: dict[str, Any]


class SignalRecorder:
    """Record every emission of the given signals until `stop` is called.

    Use as a context manager for scoped capture or call `start` and
    `stop` explicitly when the lifecycle spans multiple helpers.
    """

    def __init__(self, *signals: Signal) -> None:
        """Accept one or more Django signals to record."""
        if not signals:
            msg = "SignalRecorder requires at least one signal"
            raise ValueError(msg)
        self.signals: tuple[Signal, ...] = signals
        self.events: list[SignalEvent] = []
        self._started: bool = False

    def start(self) -> Self:
        """Connect receivers for every tracked signal and return self."""
        if self._started:
            return self
        for signal in self.signals:
            signal.connect(self._receiver, weak=False)
        self._started = True
        return self

    def stop(self) -> None:
        """Disconnect receivers for every tracked signal."""
        if not self._started:
            return
        for signal in self.signals:
            signal.disconnect(self._receiver)
        self._started = False

    def _receiver(
        self,
        sender: Any,  # noqa: ANN401
        signal: Signal,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        self.events.append(
            SignalEvent(signal=signal, sender=sender, kwargs=dict(kwargs))
        )

    def __enter__(self) -> Self:
        """Start recording on context entry."""
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stop recording on context exit."""
        self.stop()

    def __iter__(self) -> Iterator[SignalEvent]:
        """Iterate over captured events in emission order."""
        return iter(self.events)

    def __len__(self) -> int:
        """Return the number of captured events."""
        return len(self.events)

    def events_for(self, signal: Signal) -> list[SignalEvent]:
        """Return every captured event emitted by the given signal."""
        return [event for event in self.events if event.signal is signal]

    def first_for(self, signal: Signal) -> SignalEvent:
        """Return the first captured event for `signal` or raise `LookupError`."""
        for event in self.events:
            if event.signal is signal:
                return event
        msg = f"No captured events for signal {signal!r}"
        raise LookupError(msg)

    def last_for(self, signal: Signal) -> SignalEvent:
        """Return the last captured event for `signal` or raise `LookupError`."""
        for event in reversed(self.events):
            if event.signal is signal:
                return event
        msg = f"No captured events for signal {signal!r}"
        raise LookupError(msg)

    def clear(self) -> None:
        """Drop every captured event without disconnecting."""
        self.events.clear()


def capture_signals(*signals: Signal) -> SignalRecorder:
    """Return a started `SignalRecorder` for use as a context manager.

    Equivalent to `SignalRecorder(*signals).start()` but reads like a
    verb at the call site: `with capture_signals(sig) as rec: ...`.
    """
    return SignalRecorder(*signals).start()


def capture_framework_signals() -> SignalRecorder:
    """Return a recorder connected to every signal in `next.signals.__all__`.

    Handy when a test wants to verify that nothing unexpected fires
    without wiring each signal by hand.
    """
    # Lazy-imported to keep `next.testing` from pulling in every framework
    # subsystem at import time.
    from next import signals as framework_signals  # noqa: PLC0415

    tracked = tuple(
        getattr(framework_signals, name) for name in framework_signals.__all__
    )
    return SignalRecorder(*tracked).start()


__all__ = [
    "SignalEvent",
    "SignalRecorder",
    "capture_framework_signals",
    "capture_signals",
]
