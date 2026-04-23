import tempfile
from pathlib import Path
from unittest import TestCase

from django.dispatch import Signal

from next.testing import SignalRecorder, clear_loaded_dirs, eager_load_pages


class SignalRecorderUnittestScenario(TestCase):
    """SignalRecorder works with plain stdlib unittest.TestCase."""

    def test_captures_in_unittest_testcase(self) -> None:
        sig = Signal()
        with SignalRecorder(sig) as recorder:
            sig.send(sender="s", payload=42)
        assert len(recorder) == 1
        assert recorder.events[0].kwargs["payload"] == 42


class EagerLoaderUnittestScenario(TestCase):
    """eager_load_pages works with plain stdlib unittest.TestCase."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        clear_loaded_dirs()

    def test_loads_pages_from_tmp_dir(self) -> None:
        page = self.tmp_path / "page.py"
        page.write_text("MARK = 'visible'\n")
        loaded = eager_load_pages(self.tmp_path)
        assert len(loaded) == 1
        assert loaded[0].name == "page.py"
