from __future__ import annotations

import functools
from collections import OrderedDict
from pathlib import Path
from typing import override
from unittest.mock import patch

import pytest
from django.test import override_settings

from next.pages.loaders import _load_python_module
from next.utils import (
    callable_name,
    classify_dirs_entries,
    defining_file,
    resolve_base_dir,
    stat_mtime_ns,
    store_bounded,
    template_edits_watched,
)
from tests.support import attribution, unwrapped_decorator, wraps_decorator


class _EmptiedCache(OrderedDict[str, int]):
    """A cache another thread emptied between the write and the eviction."""

    @override
    def popitem(self, last: bool = True) -> tuple[str, int]:
        """Report the cache as empty, whatever it holds."""
        raise KeyError(last)


class _UnreorderableCache(OrderedDict[str, int]):
    """A cache another thread evicted the written key from before the reorder."""

    @override
    def move_to_end(self, key: str, last: bool = True) -> None:
        """Report the key as gone, whatever the cache holds."""
        raise KeyError(key)


class TestStoreBounded:
    """Tests for ``store_bounded``."""

    def test_a_rewrite_keeps_the_key_and_makes_it_the_freshest(self) -> None:
        """A key already held stays readable while it moves to the end."""
        cache: OrderedDict[str, int] = OrderedDict(first=1, second=2)
        store_bounded(cache, "first", 10, 2)
        assert list(cache.items()) == [("second", 2), ("first", 10)]

    def test_a_new_key_past_the_bound_drops_the_stalest(self) -> None:
        """The bound is what a cache of one-off keys never grows past."""
        cache: OrderedDict[str, int] = OrderedDict(first=1, second=2)
        store_bounded(cache, "third", 3, 2)
        assert list(cache.items()) == [("second", 2), ("third", 3)]

    def test_a_cache_emptied_in_between_costs_no_error(self) -> None:
        """A concurrent eviction leaves the write standing rather than raising."""
        cache = _EmptiedCache()
        store_bounded(cache, "only", 1, 0)
        assert cache["only"] == 1

    def test_a_key_evicted_before_the_reorder_costs_no_error(self) -> None:
        """The reorder is no place to raise, because the write already landed."""
        cache = _UnreorderableCache()
        store_bounded(cache, "only", 1, 8)
        assert cache["only"] == 1

    def test_the_write_lands_before_anything_can_go_wrong(self) -> None:
        """A key already held never goes missing for a reader without the lock."""
        cache = _UnreorderableCache(first=1, second=2)
        store_bounded(cache, "first", 10, 1)
        assert cache["first"] == 10
        assert len(cache) == 2


class TestStatMtimeNs:
    """Tests for ``stat_mtime_ns``."""

    def test_a_file_reports_the_nanoseconds_its_stat_carries(self, tmp_path) -> None:
        """The value is the one a caller comparing snapshots would take itself."""
        target = tmp_path / "page.py"
        target.write_text("")
        assert stat_mtime_ns(target) == target.stat().st_mtime_ns

    def test_a_path_that_does_not_stat_reports_nothing(self, tmp_path) -> None:
        """Nothing rather than a sentinel, because no real mtime equals nothing."""
        assert stat_mtime_ns(tmp_path / "never-written") is None


class TestClassifyDirsEntries:
    """Tests for ``classify_dirs_entries``."""

    def test_segment_when_relative_name_only(self) -> None:
        """A bare name becomes a segment when it is not a path under base_dir."""
        roots, segs = classify_dirs_entries(["extras"], Path("/nonexistent"))
        assert roots == []
        assert "extras" in segs

    def test_resolves_existing_dir_under_base(self, tmp_path: Path) -> None:
        """A relative path that exists under base_dir is classified as a path root."""
        sub = tmp_path / "nest"
        sub.mkdir()
        roots, _segs = classify_dirs_entries([Path("nest")], tmp_path)
        assert roots == [sub.resolve()]

    def test_resolves_nested_relative_path(self, tmp_path: Path) -> None:
        """A path string with a slash can resolve under base_dir when it exists."""
        nested = tmp_path / "x" / "y"
        nested.mkdir(parents=True)
        roots, _segs = classify_dirs_entries([Path("x/y")], tmp_path)
        assert roots == [nested.resolve()]

    def test_a_relative_entry_is_resolved_before_it_is_probed(
        self, tmp_path: Path
    ) -> None:
        """A `..` reaching past a missing directory still names the tree it means."""
        pages = tmp_path / "pages"
        pages.mkdir()
        roots, segs = classify_dirs_entries(["missing/../pages"], tmp_path)
        assert roots == [pages.resolve()]
        assert segs == frozenset()

    def test_slash_path_that_is_file_becomes_segment(self, tmp_path: Path) -> None:
        """When a path with a slash exists but is a file, it is treated as a segment name."""
        f = tmp_path / "a" / "b"
        f.parent.mkdir(parents=True)
        f.write_text("x")
        roots, segs = classify_dirs_entries([Path("a/b")], tmp_path)
        assert roots == []
        assert "b" in segs

    def test_a_relative_windows_entry_reads_its_last_component(
        self, tmp_path: Path
    ) -> None:
        """A backslashed relative entry read on POSIX carries no separator of its own."""
        roots, segs = classify_dirs_entries(["drafts\\hidden"], tmp_path)
        assert roots == []
        assert segs == frozenset({"hidden"})

    def test_an_absolute_entry_keeps_a_backslash_as_part_of_its_name(self) -> None:
        """On POSIX an absolute entry already separates, so a backslash is a name."""
        roots, segs = classify_dirs_entries(["/srv/app\\pages"], None)
        assert roots == []
        assert segs == frozenset({"app\\pages"})

    def test_a_relative_entry_without_a_base_dir_becomes_a_segment(self) -> None:
        """Without a base dir a relative entry can only name a URL segment."""
        roots, segs = classify_dirs_entries(["shop"], None)
        assert roots == []
        assert segs == frozenset({"shop"})

    def test_skips_empty_and_dot_entries(self) -> None:
        """Empty strings and dot entries are ignored."""
        roots, segs = classify_dirs_entries(["", ".", None], Path("/tmp"))
        assert roots == []
        assert segs == frozenset()

    def test_an_entry_of_separators_alone_names_no_segment(self, tmp_path) -> None:
        """A separator-only entry reaches `skip_dir_names` as nothing at all."""
        roots, segs = classify_dirs_entries(["\\", "./"], tmp_path)
        assert roots == []
        assert segs == frozenset()


class TestTemplateEditsWatched:
    """Tests for ``template_edits_watched``."""

    def test_the_gate_follows_the_debug_setting(self, watched_template_edits) -> None:
        """The predicate is read per call, so an override takes effect at once."""
        assert template_edits_watched() is True
        with override_settings(DEBUG=False):
            assert template_edits_watched() is False


class TestResolveBaseDir:
    """Tests for ``resolve_base_dir``."""

    def test_returns_path_when_base_dir_is_path(self) -> None:
        """``BASE_DIR`` already a ``Path`` is returned unchanged."""
        p = Path("/some/project")
        with patch("next.utils.settings") as mock_settings:
            mock_settings.BASE_DIR = p
            result = resolve_base_dir()
        assert result == p
        assert isinstance(result, Path)

    def test_returns_path_when_base_dir_is_string(self) -> None:
        """String ``BASE_DIR`` is converted to a ``Path``."""
        with patch("next.utils.settings") as mock_settings:
            mock_settings.BASE_DIR = "/some/project"
            result = resolve_base_dir()
        assert result == Path("/some/project")

    def test_returns_none_when_base_dir_is_neither_path_nor_str(self) -> None:
        """When ``BASE_DIR`` is neither Path nor str, return None."""
        with patch("next.utils.settings") as mock_settings:
            mock_settings.BASE_DIR = object()
            assert resolve_base_dir() is None

    def test_returns_none_when_base_dir_attribute_missing(self) -> None:
        """When ``BASE_DIR`` is not configured at all, return None."""
        with patch("next.utils.settings") as mock_settings:
            del mock_settings.BASE_DIR
            assert resolve_base_dir() is None


class TestDefiningFile:
    """Tests for ``defining_file``."""

    def test_plain_function_returns_its_own_file(self) -> None:
        """A function declared here is attributed to this test module."""

        def handler() -> None:
            pass

        assert defining_file(handler) == Path(__file__)

    def test_function_wrapped_with_functools_wraps_returns_user_file(self) -> None:
        """A wrapper built with ``functools.wraps`` is unwrapped to the user file."""

        @wraps_decorator
        def handler() -> None:
            pass

        assert defining_file(handler) == Path(__file__)

    def test_function_wrapped_without_functools_wraps_returns_wrapper_file(
        self,
    ) -> None:
        """Without ``functools.wraps`` the wrapper's own file wins."""

        @unwrapped_decorator
        def handler() -> None:
            pass

        assert defining_file(handler) == Path(attribution.__file__)

    def test_class_returns_the_file_it_was_declared_in(self) -> None:
        """A class is attributed to the file declaring it."""

        class Marker:
            pass

        assert defining_file(Marker) == Path(__file__)

    def test_partial_returns_the_file_of_the_wrapped_function(self) -> None:
        """A ``functools.partial`` is attributed to the function it binds."""

        def handler(value: int) -> int:
            return value

        assert defining_file(functools.partial(handler, 1)) == Path(__file__)

    def test_nested_partial_unwraps_to_the_innermost_function(self) -> None:
        """Stacked partials resolve through to the declaring file."""

        def handler(first: int, second: int) -> int:
            return first + second

        stacked = functools.partial(functools.partial(handler, 1), 2)
        assert defining_file(stacked) == Path(__file__)

    def test_partial_of_a_builtin_raises_type_error(self) -> None:
        """A partial binding a builtin has no declaring file either."""
        with pytest.raises(TypeError, match=r"could not determine the file where"):
            defining_file(functools.partial(len))

    def test_callable_instance_returns_the_file_of_its_class(self) -> None:
        """An instance with ``__call__`` is attributed through that method."""

        class Handler:
            def __call__(self) -> None:
                pass

        assert defining_file(Handler()) == Path(__file__)

    def test_callable_without_code_object_raises_type_error(self) -> None:
        """A builtin has no ``__code__`` and raises ``TypeError``."""
        with pytest.raises(TypeError, match=r"could not determine the file where"):
            defining_file(len)

    def test_non_callable_object_raises_type_error_naming_the_object(self) -> None:
        """A non-callable value raises ``TypeError`` naming the value."""
        with pytest.raises(TypeError, match=r"'not a callable'"):
            defining_file("not a callable")

    def test_wrapper_loop_falls_back_to_the_outer_function(self, tmp_path) -> None:
        """A ``__wrapped__`` cycle answers with the file of the outer wrapper."""
        module_file = tmp_path / "page.py"
        module_file.write_text("def inner() -> None:\n    pass\n")
        module = _load_python_module(module_file)

        def outer() -> None:
            pass

        outer.__wrapped__ = module.inner
        module.inner.__wrapped__ = outer

        assert defining_file(outer) == Path(__file__)

    @pytest.mark.parametrize("member", ["__call__", "__init__"], ids=["call", "init"])
    def test_class_without_a_registered_module_reads_its_own_body(
        self, tmp_path, member
    ) -> None:
        """A class the loader execs without sys.modules answers through its body."""
        module_file = tmp_path / "page.py"
        module_file.write_text(
            f"class Declared:\n    def {member}(self) -> None:\n        pass\n"
        )
        module = _load_python_module(module_file)

        assert defining_file(module.Declared) == module_file

    def test_class_without_a_module_or_body_raises_type_error(self, tmp_path) -> None:
        """A body-less class outside sys.modules names no file at all."""
        module_file = tmp_path / "page.py"
        module_file.write_text("class Declared:\n    marker = 1\n")
        module = _load_python_module(module_file)

        with pytest.raises(TypeError, match=r"could not determine the file where"):
            defining_file(module.Declared)


class TestCallableName:
    """Tests for ``callable_name``."""

    def test_function_keeps_its_own_name(self) -> None:
        """A plain function reports ``__name__``."""

        def handler() -> None:
            pass

        assert callable_name(handler) == "handler"

    def test_partial_reports_the_wrapped_function(self) -> None:
        """A partial has no ``__name__``, so the bound function names it."""

        def handler(value: int) -> int:
            return value

        stacked = functools.partial(functools.partial(handler, 1))
        assert callable_name(stacked) == "handler"

    def test_callable_instance_reports_its_class(self) -> None:
        """An instance with ``__call__`` reports the class name."""

        class Handler:
            def __call__(self) -> None:
                pass

        assert callable_name(Handler()) == "Handler"
