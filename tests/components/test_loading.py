import threading
import types
from pathlib import Path

import pytest

from next.caches import LruCache
from next.components.loading import (
    _CACHE_MISS,
    Loaded,
    ModuleCache,
    ModuleLoader,
    last_load_error,
)
from next.errors import AsyncCallableError


BODIES_THAT_RAISE = [
    pytest.param("MISSPELLED_NAME\n", NameError, id="undefined-name"),
    pytest.param("raise RuntimeError('boom')\n", RuntimeError, id="bare-raise"),
    pytest.param("1 / 0\n", ZeroDivisionError, id="arithmetic"),
    pytest.param("def broken(\n", SyntaxError, id="syntax"),
]


class TestAFailingComponentModuleDegradesTheRender:
    """A `component.py` body runs arbitrary user code and may raise anything.

    The loader catches and records it, so the render degrades instead of a 500.
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


class TestAsyncComponentRender:
    """The load records an `async def render`, and refuses an async generator one."""

    def test_async_render_loads_and_is_recorded(self, tmp_path: Path) -> None:
        path = tmp_path / "component.py"
        path.write_text("async def render():\n    return 'hi'\n")
        loader = ModuleLoader()

        module, awaiting, _resolves = loader.loaded(path)
        assert module is not None
        assert last_load_error(path) is None
        assert awaiting is True

    def test_async_generator_render_is_recorded_as_the_load_error(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "component.py"
        path.write_text("async def render():\n    yield 'hi'\n")

        assert ModuleLoader().loaded(path) == Loaded(None)
        error = last_load_error(path)
        assert isinstance(error, AsyncCallableError)
        assert "is an async generator" in str(error)

    def test_sync_render_loads(self, tmp_path: Path) -> None:
        path = tmp_path / "component.py"
        path.write_text("def render():\n    return 'hi'\n")

        module, awaiting, resolves = ModuleLoader().loaded(path)
        assert module is not None
        assert last_load_error(path) is None
        assert awaiting is False
        assert resolves is True

    @pytest.mark.parametrize(
        ("source", "resolves"),
        [
            (
                (
                    "from next.components import context\n\n"
                    "@context('title')\ndef title():\n    return 'hi'\n"
                ),
                True,
            ),
            ("value = 1\n", False),
        ],
        ids=["context", "plain"],
    )
    def test_the_entry_records_whether_a_render_resolves(
        self, tmp_path: Path, source: str, *, resolves: bool
    ) -> None:
        path = tmp_path / "component.py"
        path.write_text(source)
        assert ModuleLoader().loaded(path).resolves is resolves

    def test_a_reload_without_the_async_render_forgets_it(self, tmp_path: Path) -> None:
        path = tmp_path / "component.py"
        path.write_text("async def render():\n    return 'hi'\n")
        cache = ModuleCache()
        ModuleLoader(cache).load(path)
        assert cache.entry(path).awaiting is True

        cache.clear()
        assert cache.entry(path) is _CACHE_MISS
        path.write_text("def render():\n    return 'hi'\n")
        ModuleLoader(cache).load(path)
        assert cache.entry(path).awaiting is False

    def test_a_store_over_an_async_entry_drops_its_mark(self, tmp_path: Path) -> None:
        path = tmp_path / "component.py"
        cache = ModuleCache()
        cache.set(path, None, awaiting=True)
        cache.set(path, None)
        assert cache.entry(path) == Loaded(None)


class _PausingOrder(LruCache[Path, Loaded]):
    """An LRU order that pauses its writer right after an entry is published."""

    def __init__(self) -> None:
        """Start with the writer free to publish."""
        super().__init__(128)
        self.published = threading.Event()
        self.read = threading.Event()

    def __setitem__(self, key: Path, value: Loaded) -> None:
        super().__setitem__(key, value)
        self.published.set()
        assert self.read.wait(5)


class TestAPublishedModuleCarriesItsFlag:
    """A render on another thread never sees an async module without its flag."""

    def test_a_reader_between_publish_and_return_sees_the_flag(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "component.py"
        path.write_text("async def render():\n    return 'hi'\n")
        cache = ModuleCache()
        order = _PausingOrder()
        cache._order = order
        loader = ModuleLoader(cache)
        writer = threading.Thread(target=loader.load, args=(path,))
        writer.start()
        try:
            assert order.published.wait(5)
            module, awaiting, _resolves = loader.loaded(path)
        finally:
            order.read.set()
            writer.join(5)

        assert module is not None
        assert awaiting is True


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
