from __future__ import annotations

import pytest


class _MockAutoreloadSender:
    def __init__(self) -> None:
        self.watch_calls: list[tuple[object, str]] = []

    def watch_dir(self, path: object, glob: str) -> None:
        self.watch_calls.append((path, glob))


@pytest.fixture()
def mock_autoreload_sender() -> _MockAutoreloadSender:
    """Minimal sender stub for ``autoreload_started`` signal tests."""
    return _MockAutoreloadSender()
