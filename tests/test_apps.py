from django.utils import autoreload
from django.utils.autoreload import autoreload_started

from next.pages import get_pages_directories_for_watch
from next.utils import NextStatReloader


class TestNextFrameworkConfig:
    """Tests for NextFrameworkConfig.ready() behavior."""

    def test_ready_patches_stat_reloader(self) -> None:
        """After app load, django.utils.autoreload.StatReloader is NextStatReloader."""
        assert autoreload.StatReloader is NextStatReloader

    def test_autoreload_started_watches_each_pages_directory(self) -> None:
        """Sending autoreload_started runs watch_dir once per pages dir for page.py globs."""
        watch_calls = []

        class MockSender:
            def watch_dir(self, path, glob):
                watch_calls.append((path, glob))

        autoreload_started.send(sender=MockSender())
        pages_dirs = get_pages_directories_for_watch()
        next_watch_calls = [(p, g) for p, g in watch_calls if g == "**/page.py"]
        assert len(next_watch_calls) == len(pages_dirs)
        for path in pages_dirs:
            assert any(p == path and g == "**/page.py" for p, g in watch_calls)
