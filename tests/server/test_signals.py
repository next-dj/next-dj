from collections.abc import Generator
from unittest.mock import patch

import pytest

from next.server import iter_all_autoreload_watch_specs
from next.server.signals import watch_specs_ready
from next.server.watcher import _registered_extra_watch_specs
from next.testing import SignalRecorder, capture_signals


@pytest.fixture()
def capture_watch_specs_ready() -> Generator[SignalRecorder, None, None]:
    """Record ``watch_specs_ready`` emissions and drop the registered specs after."""
    with capture_signals(watch_specs_ready) as recorder:
        yield recorder
    _registered_extra_watch_specs.clear()


class TestWatchSpecsReadySignal:
    """``watch_specs_ready`` fires after ``iter_all_autoreload_watch_specs``."""

    def test_fires_when_specs_are_resolved(
        self, capture_watch_specs_ready: SignalRecorder
    ) -> None:
        """Calling ``iter_all_autoreload_watch_specs()`` emits ``watch_specs_ready``."""
        with patch(
            "next.server.watcher._iter_default_autoreload_watch_specs", return_value=[]
        ):
            iter_all_autoreload_watch_specs()
        assert len(capture_watch_specs_ready) == 1

    def test_sender_is_iter_all_autoreload_watch_specs(
        self, capture_watch_specs_ready: SignalRecorder
    ) -> None:
        """Sender is the ``iter_all_autoreload_watch_specs`` function."""
        with patch(
            "next.server.watcher._iter_default_autoreload_watch_specs", return_value=[]
        ):
            iter_all_autoreload_watch_specs()
        assert (
            capture_watch_specs_ready.events[0].sender
            is iter_all_autoreload_watch_specs
        )

    def test_specs_kwarg_contains_resolved_list(
        self, capture_watch_specs_ready: SignalRecorder
    ) -> None:
        """``specs`` kwarg is the final deduplicated list."""
        with patch(
            "next.server.watcher._iter_default_autoreload_watch_specs", return_value=[]
        ):
            result = iter_all_autoreload_watch_specs()
        assert capture_watch_specs_ready.events[0].kwargs["specs"] == result
