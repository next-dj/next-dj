import logging
from pathlib import Path

import pytest

from next.scripts import Script, ScriptsSourceImportError
from next.scripts.discovery import SCRIPTS_MODULE, load_scripts, source_stale
from tests.support import touch_later


def _write(root: Path, source: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / SCRIPTS_MODULE
    path.write_text(source)
    return path


class TestLoadScripts:
    """A `scripts.py` loads through its own loader, a failure kept on the source."""

    def test_a_tree_without_one_has_no_source(self, tmp_path: Path) -> None:
        assert load_scripts(tmp_path) is None

    def test_the_declared_scripts_are_read(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "from next.scripts import Script\n"
            "scripts = (Script('a', init='1'), Script('b', init='2'))\n",
        )
        source = load_scripts(tmp_path)
        assert source is not None
        assert source.root == tmp_path
        assert source.path == path
        assert source.stamp == path.stat().st_mtime_ns
        assert [script.name for script in source.scripts] == ["a", "b"]
        assert source.error is None
        assert source.problem is None

    def test_a_module_without_scripts_declares_none(self, tmp_path: Path) -> None:
        _write(tmp_path, "x = 1\n")
        source = load_scripts(tmp_path)
        assert source is not None
        assert source.scripts == ()
        assert source.problem is None

    def test_a_broken_module_keeps_its_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write(tmp_path, "raise RuntimeError('boom')\n")
        with caplog.at_level(logging.ERROR, "next.scripts.discovery"):
            source = load_scripts(tmp_path)
        assert source is not None
        assert isinstance(source.error, ScriptsSourceImportError)
        assert isinstance(source.error.__cause__, RuntimeError)
        assert source.scripts == ()
        assert "failed to import" in caplog.text

    @pytest.mark.parametrize(
        ("value", "problem"),
        [
            ("'x'", "scripts is 'str', not an iterable of Script"),
            ("3", "scripts is 'int', not an iterable of Script"),
            ("Script('a', init='1')", "scripts is 'Script', not an iterable"),
        ],
    )
    def test_a_value_that_is_no_iterable_is_a_problem(
        self, tmp_path: Path, value: str, problem: str
    ) -> None:
        _write(tmp_path, f"from next.scripts import Script\nscripts = {value}\n")
        source = load_scripts(tmp_path)
        assert source is not None
        assert source.problem is not None
        assert source.problem.startswith(problem)

    def test_a_stray_item_is_a_problem_the_rest_still_loads(
        self, tmp_path: Path
    ) -> None:
        _write(
            tmp_path,
            "from next.scripts import Script\nscripts = [Script('a', init='1'), 3]\n",
        )
        source = load_scripts(tmp_path)
        assert source is not None
        assert source.scripts == (Script("a", init="1"),)
        assert source.problem == "scripts holds 'int', not Script"


class TestSourceStale:
    """A source is stale once its file moves, appears or goes."""

    def test_an_untouched_source_is_fresh(self, tmp_path: Path) -> None:
        _write(tmp_path, "scripts = ()\n")
        assert not source_stale(load_scripts(tmp_path), tmp_path)
        assert not source_stale(None, tmp_path / "empty")

    def test_an_edit_a_new_file_and_a_removal_are_stale(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "scripts = ()\n")
        source = load_scripts(tmp_path)
        touch_later(path)
        assert source_stale(source, tmp_path)
        assert source_stale(None, tmp_path)
        path.unlink()
        assert source_stale(source, tmp_path)
