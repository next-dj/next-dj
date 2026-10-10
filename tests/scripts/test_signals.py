from pathlib import Path

from next.scripts import Script
from next.scripts.discovery import ScriptsSource
from next.scripts.registry import ScriptsRegistry
from next.scripts.signals import scripts_registered
from next.testing import capture_signals


class TestScriptsRegisteredSignal:
    """``scripts_registered`` fires once per tree the registry takes a source for."""

    def test_a_source_announces_its_root_source_and_scripts(
        self, tmp_path: Path
    ) -> None:
        scripts = (Script("a", init="1"),)
        source = ScriptsSource(tmp_path, tmp_path / "scripts.py", 1, scripts)
        with capture_signals(scripts_registered) as recorder:
            ScriptsRegistry().register(tmp_path, source)
        (event,) = recorder.events
        assert event.sender is ScriptsRegistry
        assert event.kwargs == {"root": tmp_path, "source": source, "scripts": scripts}

    def test_a_tree_without_a_source_announces_nothing(self, tmp_path: Path) -> None:
        with capture_signals(scripts_registered) as recorder:
            ScriptsRegistry().register(tmp_path, None)
        assert recorder.events == []
