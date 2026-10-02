from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from django.middleware.locale import LocaleMiddleware
from django.test import override_settings

from next.errors import InvalidDirsError
from next.utils import (
    TreeSource,
    classify_dirs_entries,
    exec_module_file,
    is_dynamic_trail,
    is_int,
    is_middleware,
    load_tree_source,
    middleware_index,
    middleware_listed,
    resolve_base_dir,
    stat_mtime_ns,
    template_edits_watched,
    tree_label,
    unique_labels,
)


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
        """A slashed path that exists as a file is read as a segment name."""
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

    @pytest.mark.parametrize(
        "entries",
        [
            pytest.param(5, id="scalar"),
            pytest.param("src/pages", id="string"),
            pytest.param(b"src/pages", id="bytes"),
        ],
    )
    def test_a_value_that_is_no_sequence_of_trees(self, entries: object) -> None:
        """A string splits into characters, so it is refused with the scalars."""
        with pytest.raises(InvalidDirsError):
            classify_dirs_entries(entries, Path("/tmp"))


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


class TestTypePredicates:
    @pytest.mark.parametrize(
        ("value", "integer"),
        [(True, False), (0, True), (1.5, False), ("1", False)],
        ids=["bool", "int", "float", "str"],
    )
    def test_a_bool_is_never_an_int(self, value: object, *, integer: bool) -> None:
        assert is_int(value) is integer


class TestIsDynamicTrail:
    @pytest.mark.parametrize(
        ("trail", "expected"),
        [
            ("", False),
            ("about", False),
            ("posts/[slug]", True),
            ("posts/[int:id]/edit", True),
            ("docs/[[path]]", True),
            ("odd[", False),
        ],
        ids=["root", "static", "param", "typed", "wildcard", "unclosed"],
    )
    def test_a_bracket_segment_makes_a_trail_dynamic(
        self, trail: str, *, expected: bool
    ) -> None:
        assert is_dynamic_trail(trail) is expected


class TestTreeLabels:
    """A page tree is named by its app, else its directory, and made unique."""

    def test_a_repeated_label_takes_a_numeric_suffix(self) -> None:
        assert unique_labels(["pages"] * 3) == ["pages", "pages-2", "pages-3"]

    def test_a_suffix_never_takes_the_label_of_another_tree(self) -> None:
        assert unique_labels(["blog", "blog", "blog-2"]) == ["blog", "blog-3", "blog-2"]
        assert unique_labels(["blog-2", "blog", "blog"]) == ["blog-2", "blog", "blog-3"]

    def test_every_tree_is_labelled_in_order(self, tmp_path: Path) -> None:
        roots = [tmp_path / "a" / "pages", tmp_path / "b" / "Pages", tmp_path / "!"]
        assert [tree_label(root) for root in roots] == ["pages", "pages", "root"]


class _SourceError(Exception):
    def __init__(self, path: Path) -> None:
        super().__init__(f"{path} failed")
        self.path = path


class TestLoadTreeSource:
    """A file at the top of a tree runs once, its failure kept on the source."""

    def test_an_absent_file_answers_none(self, tmp_path) -> None:
        assert load_tree_source(tmp_path / "x.py", "probe", _SourceError) is None

    def test_a_module_that_runs_answers_itself_and_its_stamp(self, tmp_path) -> None:
        path = tmp_path / "x.py"
        path.write_text("value = 1\n")
        source = load_tree_source(path, "probe", _SourceError)
        assert source is not None
        assert source.module is not None
        assert source.module.value == 1
        assert source.error is None
        assert source.stamp == path.stat().st_mtime_ns

    def test_a_failure_rides_the_source_with_its_cause(self, tmp_path, caplog) -> None:
        path = tmp_path / "x.py"
        path.write_text("raise RuntimeError('boom')\n")
        source = load_tree_source(path, "probe", _SourceError)
        assert source is not None
        assert source.module is None
        assert isinstance(source.error, _SourceError)
        assert isinstance(source.error.__cause__, RuntimeError)
        assert f"{path} failed to import" in caplog.text


class TestExecModuleFile:
    """A source file runs as a fresh module, its bytecode cache never older than it."""

    def test_a_same_size_rewrite_within_one_second_runs_the_new_source(
        self, tmp_path
    ) -> None:
        path = tmp_path / "x.py"
        second = path.parent.stat().st_mtime_ns // 10**9 * 10**9
        with patch.object(sys, "dont_write_bytecode", False):
            for value, offset in ((1, 100), (2, 200)):
                path.write_text(f"value = {value}\n")
                os.utime(path, ns=(second + offset, second + offset))
                module = exec_module_file(path, "probe")
                assert module is not None
                assert module.value == value

    def test_a_suffix_without_a_loader_answers_none(self, tmp_path) -> None:
        path = tmp_path / "x.unknown"
        path.write_text("value = 1\n")
        assert exec_module_file(path, "probe") is None


class TestTreeSource:
    """A source is stale once its file moves or goes."""

    def test_an_unmoved_file_is_fresh(self, tmp_path) -> None:
        path = tmp_path / "x.py"
        path.write_text("")
        source = TreeSource[Exception](path, stamp=path.stat().st_mtime_ns)
        assert not source.stale()

    def test_a_removed_file_is_stale(self, tmp_path) -> None:
        path = tmp_path / "x.py"
        path.write_text("")
        source = TreeSource[Exception](path, stamp=path.stat().st_mtime_ns)
        path.unlink()
        assert source.stale()


class _Locale(LocaleMiddleware):
    """A project subclass of `LocaleMiddleware`."""


class TestMiddlewareDetection:
    """A `MIDDLEWARE` entry matches its class, a subclass, or its exact dotted path."""

    LOCALE = "django.middleware.locale.LocaleMiddleware"

    @pytest.mark.parametrize(
        ("entry", "matches"),
        [
            (LOCALE, True),
            (f"{__name__}._Locale", True),
            ("django.middleware.common.CommonMiddleware", False),
            ("missing.module.Middleware", False),
            (f"{__name__}.TestMiddlewareDetection.LOCALE", False),
            (42, False),
        ],
        ids=["exact", "subclass", "other", "unimportable", "not_a_class", "not_text"],
    )
    def test_is_middleware(self, entry, matches) -> None:
        assert is_middleware(entry, self.LOCALE) is matches

    def test_a_base_that_does_not_import_matches_by_its_path_alone(self) -> None:
        assert is_middleware("csp.missing.CSPMiddleware", "csp.missing.CSPMiddleware")
        assert not is_middleware(self.LOCALE, "csp.missing.CSPMiddleware")

    def test_the_index_is_the_first_match(self) -> None:
        middleware = ["a.B", f"{__name__}._Locale", self.LOCALE]
        assert middleware_index(middleware, self.LOCALE) == 1
        assert middleware_listed(middleware, self.LOCALE)
        assert middleware_index(["a.B"], self.LOCALE) is None
        assert not middleware_listed([], self.LOCALE)
