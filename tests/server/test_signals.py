from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest

from next.server import iter_all_autoreload_watch_specs
from next.server.signals import watch_specs_ready
from next.server.watcher import _registered_extra_watch_specs


@pytest.fixture()
def capture_watch_specs_ready() -> Generator[list[dict[str, Any]], None, None]:
    events: list[dict[str, Any]] = []

    def _listener(sender, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    watch_specs_ready.connect(_listener)
    try:
        yield events
    finally:
        watch_specs_ready.disconnect(_listener)
        _registered_extra_watch_specs.clear()


class TestWatchSpecsReadySignal:
    """``watch_specs_ready`` fires after ``iter_all_autoreload_watch_specs``."""

    def test_fires_when_specs_are_resolved(
        self, capture_watch_specs_ready: list[dict[str, Any]]
    ) -> None:
        """Calling ``iter_all_autoreload_watch_specs()`` emits ``watch_specs_ready``."""
        with patch(
            "next.server.watcher.iter_default_autoreload_watch_specs",
            return_value=[],
        ):
            iter_all_autoreload_watch_specs()
        assert len(capture_watch_specs_ready) == 1

    def test_sender_is_iter_all_autoreload_watch_specs(
        self, capture_watch_specs_ready: list[dict[str, Any]]
    ) -> None:
        """Sender is the ``iter_all_autoreload_watch_specs`` function."""
        with patch(
            "next.server.watcher.iter_default_autoreload_watch_specs",
            return_value=[],
        ):
            iter_all_autoreload_watch_specs()
        assert capture_watch_specs_ready[0]["sender"] is iter_all_autoreload_watch_specs

    def test_specs_kwarg_contains_resolved_list(
        self, capture_watch_specs_ready: list[dict[str, Any]]
    ) -> None:
        """``specs`` kwarg is the final deduplicated list."""
        with patch(
            "next.server.watcher.iter_default_autoreload_watch_specs",
            return_value=[],
        ):
            result = iter_all_autoreload_watch_specs()
        assert capture_watch_specs_ready[0]["specs"] == result
