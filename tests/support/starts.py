import contextvars
import threading

import pytest


held: contextvars.ContextVar[object] = contextvars.ContextVar("held")


class Request:
    """A stand-in for the request object a caller's context holds."""


def spied_starts(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Record what `held` reads in the context each thread is started from."""
    seen: list[object] = []
    start = threading.Thread.start

    def spy(thread: threading.Thread) -> None:
        seen.append(held.get(None))
        start(thread)

    monkeypatch.setattr(threading.Thread, "start", spy)
    return seen
