from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING, Any

import pytest
from django.test import override_settings

from next.server import NextStatReloader


if TYPE_CHECKING:
    from collections.abc import Callable, Generator


class _MockAutoreloadSender:
    def __init__(self) -> None:
        self.watch_calls: list[tuple[object, str]] = []

    def watch_dir(self, path: object, glob: str) -> None:
        self.watch_calls.append((path, glob))


@pytest.fixture(autouse=True)
def _instant_reloader_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the one-second sleep a real tick performs.

    Django's ``StatReloader.tick`` sleeps ``SLEEP_TIME`` before every
    yield, so each tick a test drives costs a second of wall clock.
    """
    monkeypatch.setattr(NextStatReloader, "SLEEP_TIME", 0)


@pytest.fixture()
def mock_autoreload_sender() -> _MockAutoreloadSender:
    """Minimal sender stub for ``autoreload_started`` signal tests."""
    return _MockAutoreloadSender()


@pytest.fixture()
def apply_component_backends() -> Generator[Callable[[list[Any]], None], None, None]:
    """Install ``COMPONENT_BACKENDS`` entries with no page backends configured."""
    with ExitStack() as stack:

        def apply(entries: list[Any]) -> None:
            stack.enter_context(
                override_settings(
                    NEXT_FRAMEWORK={"PAGE_BACKENDS": [], "COMPONENT_BACKENDS": entries}
                )
            )

        yield apply
