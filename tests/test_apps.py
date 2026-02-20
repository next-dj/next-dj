"""Tests for next.apps (NextFrameworkConfig)."""

from django.utils import autoreload
from django.utils.autoreload import autoreload_started

from next.urls import get_pages_directories_for_watch
from next.utils import NextStatReloader


class TestNextFrameworkConfig:
    """Tests for NextFrameworkConfig.ready() behavior."""

    def test_ready_patches_stat_reloader(self) -> None:
        """After app load, django.utils.autoreload.StatReloader is NextStatReloader."""
        assert autoreload.StatReloader is NextStatReloader

    def test_ready_connects_autoreload_started_handler(self) -> None:
        """ready() connects a handler that calls watch_dir with **/page.py for pages dirs."""
        watch_calls = []

        class Sender:
            def watch_dir(self, path, glob):
                watch_calls.append((path, glob))

        autoreload_started.send(sender=Sender())
        next_watch_calls = [(p, g) for p, g in watch_calls if g == "**/page.py"]
        expected_count = len(get_pages_directories_for_watch())
        assert len(next_watch_calls) == expected_count

    def test_autoreload_started_handler_calls_watch_dir_for_each_pages_dir(
        self,
    ) -> None:
        """The connected handler calls watch_dir once per path (page.py only)."""
        watch_calls = []

        class MockSender:
            def watch_dir(self, path, glob):
                watch_calls.append((path, glob))

        autoreload_started.send(sender=MockSender())
        next_watch_calls = [(p, g) for p, g in watch_calls if g == "**/page.py"]
        pages_dirs = get_pages_directories_for_watch()
        assert len(next_watch_calls) == len(pages_dirs)
        for path in pages_dirs:
            assert any(p == path and g == "**/page.py" for p, g in watch_calls)
