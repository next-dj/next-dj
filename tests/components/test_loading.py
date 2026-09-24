import types
from pathlib import Path

import pytest

from next.components.loading import (
    _CACHE_MISS,
    ModuleCache,
    ModuleLoader,
    last_load_error,
)


BODIES_THAT_RAISE = [
    pytest.param("MISSPELLED_NAME\n", NameError, id="undefined-name"),
    pytest.param("raise RuntimeError('boom')\n", RuntimeError, id="bare-raise"),
    pytest.param("1 / 0\n", ZeroDivisionError, id="arithmetic"),
    pytest.param("def broken(\n", SyntaxError, id="syntax"),
]


class TestAFailingComponentModuleDegradesTheRender:
    """A `component.py` body runs arbitrary user code and may raise anything.

    Every failure has to come back as the bare template instead of a 500, so the
    loader catches the lot and records what it caught for the deployment check.
    """

    @pytest.mark.parametrize(("body", "raised"), BODIES_THAT_RAISE)
    def test_a_module_body_that_raises_loads_as_none(
        self, tmp_path: Path, body: str, raised: type[BaseException]
    ) -> None:
        path = tmp_path / "component.py"
        path.write_text(body)

        assert ModuleLoader().load(path) is None
        assert isinstance(last_load_error(path), raised)

    def test_a_body_ending_the_process_is_left_to_end_it(self, tmp_path: Path) -> None:
        """`SystemExit` is no render failure, so the guard never swallows it."""
        path = tmp_path / "component.py"
        path.write_text("import sys\n\nsys.exit(2)\n")

        with pytest.raises(SystemExit):
            ModuleLoader().load(path)

    def test_the_recorded_error_carries_what_the_body_raised(
        self, tmp_path: Path
    ) -> None:
        """The check quotes the message, so the exception itself is what is kept."""
        path = tmp_path / "component.py"
        path.write_text("raise RuntimeError('the card needs a title')\n")

        ModuleLoader().load(path)

        assert str(last_load_error(path)) == "the card needs a title"

    def test_the_record_keeps_no_frames_of_the_module_that_failed(
        self, tmp_path: Path
    ) -> None:
        """A live traceback would pin the module globals for the life of the entry."""
        path = tmp_path / "component.py"
        path.write_text(
            "PAYLOAD = bytearray(1024)\n"
            "try:\n"
            "    1 / 0\n"
            "except ZeroDivisionError as e:\n"
            "    raise RuntimeError('the card needs a title') from e\n"
        )

        ModuleLoader().load(path)

        error = last_load_error(path)
        assert isinstance(error, RuntimeError)
        assert str(error) == "the card needs a title"
        assert error.__traceback__ is None
        assert error.__cause__ is None
        assert error.__context__ is None

    def test_a_failure_is_logged_for_the_developer_reading_the_console(
        self, tmp_path: Path, caplog
    ) -> None:
        path = tmp_path / "component.py"
        path.write_text("MISSPELLED_NAME\n")

        with caplog.at_level("ERROR", logger="next.components.loading"):
            ModuleLoader().load(path)

        assert any(str(path) in record.message for record in caplog.records)
        assert any(record.exc_info is not None for record in caplog.records)

    def test_a_module_that_imports_cleanly_records_no_error(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "component.py"
        path.write_text("TITLE = 'card'\n")

        assert ModuleLoader().load(path) is not None
        assert last_load_error(path) is None


class TestModuleCacheSentinel:
    """A cached `None` is a result, so the miss needs a sentinel of its own."""

    def test_an_absent_path_answers_the_miss_sentinel(self, tmp_path: Path) -> None:
        assert ModuleCache().get(tmp_path / "absent.py") is _CACHE_MISS

    def test_a_stored_none_answers_none_rather_than_the_sentinel(
        self, tmp_path: Path
    ) -> None:
        cache = ModuleCache()
        path = tmp_path / "component.py"
        cache.set(path, None)

        assert cache.get(path) is None

    def test_a_stored_module_answers_that_module(self, tmp_path: Path) -> None:
        cache = ModuleCache()
        path = tmp_path / "component.py"
        module = types.ModuleType("component")
        cache.set(path, module)

        assert cache.get(path) is module

    def test_a_failed_import_is_not_retried_while_the_cache_holds_it(
        self, tmp_path: Path
    ) -> None:
        """The cached `None` is what spares every later render the same failure."""
        path = tmp_path / "component.py"
        path.write_text("MISSPELLED_NAME\n")
        loader = ModuleLoader(ModuleCache())
        assert loader.load(path) is None

        path.write_text("TITLE = 'card'\n")

        assert loader.load(path) is None
