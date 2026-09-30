from pathlib import Path

from next.scripts import Script
from next.scripts.discovery import ScriptsSource
from next.scripts.registry import ScriptsRegistry


def _source(root: Path) -> ScriptsSource:
    return ScriptsSource(root, root / "scripts.py", 1, (Script("a", init="1"),))


class TestScriptsRegistry:
    """One source per tree root, in tree order, every write bumping the version."""

    def test_a_registration_is_held(self, tmp_path: Path) -> None:
        registry = ScriptsRegistry()
        source = _source(tmp_path)
        registry.register(tmp_path, source)
        assert registry.source(tmp_path) is source
        assert registry.roots() == (tmp_path,)
        assert registry.sources() == (source,)
        assert registry.version == 1

    def test_a_tree_without_a_source_holds_its_place(self, tmp_path: Path) -> None:
        registry = ScriptsRegistry()
        registry.register(tmp_path / "a", None)
        registry.register(tmp_path / "b", _source(tmp_path / "b"))
        registry.register(tmp_path / "a", None)
        assert registry.roots() == (tmp_path / "a", tmp_path / "b")
        assert [source.root for source in registry.sources()] == [tmp_path / "b"]
        assert registry.source(tmp_path / "a") is None

    def test_a_reset_forgets_every_tree(self, tmp_path: Path) -> None:
        registry = ScriptsRegistry()
        registry.register(tmp_path, _source(tmp_path))
        registry.reset()
        assert registry.roots() == ()
        assert registry.version == 2
