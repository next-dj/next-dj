from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import override_settings

from next.errors import InvalidDirsError
from next.utils import (
    classify_dirs_entries,
    resolve_base_dir,
    stat_mtime_ns,
    template_edits_watched,
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
