from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.middleware.locale import LocaleMiddleware
from django.template import Template
from django.test import override_settings

from next import utils
from next.errors import InvalidDirsError
from next.urls.dispatcher import scan_pages_tree
from next.utils import (
    INTERCEPT_PATTERN,
    RACY_MTIME_WINDOW_NS,
    ROUTE_GROUP_PATTERN,
    UNSETTLED_MTIME_NS,
    InterceptPage,
    PageRoot,
    TreeSource,
    after_fork,
    child_nodelists,
    classify_dirs_entries,
    exec_module_file,
    int_between,
    is_dynamic_trail,
    is_int,
    is_intercept_name,
    is_middleware,
    is_route_group,
    is_within,
    join_trail,
    load_tree_source,
    middleware_listed,
    non_negative_number,
    page_roots_shape_error,
    parse_intercept,
    positive_number,
    prefix_trail,
    redirect_or_fallback,
    regex_trail,
    resolve_base_dir,
    resolve_intercept,
    settled_mtime_ns,
    snapshot_mtime_ns,
    stat_mtime_ns,
    strip_route_groups,
    template_edits_watched,
    walk_page_tree,
)
from tests.support import write_page
from tests.support.cases.grammar import (
    INTERCEPT_NAME_CASES,
    INTERCEPT_RESOLVE_CASES,
    InterceptNameCase,
    InterceptResolveCase,
)


_OVERSIZE_TARGET = "/search/?q=" + "\u044f" * 3000


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


_TICK_NS = 1_700_000_000 * 10**9


class TestSettledMtimeNs:
    """Tests for ``settled_mtime_ns`` and ``snapshot_mtime_ns``."""

    @pytest.mark.parametrize(
        "now_ns",
        [
            _TICK_NS,
            _TICK_NS + RACY_MTIME_WINDOW_NS - 1,
            _TICK_NS - RACY_MTIME_WINDOW_NS + 1,
        ],
        ids=["same-tick", "just-before-settling", "slightly-in-the-future"],
    )
    def test_an_mtime_inside_the_window_is_unsettled(self, monkeypatch, now_ns) -> None:
        monkeypatch.setattr(utils, "time_ns", lambda: now_ns)
        assert settled_mtime_ns(_TICK_NS) == UNSETTLED_MTIME_NS

    @pytest.mark.parametrize(
        "now_ns",
        [_TICK_NS + RACY_MTIME_WINDOW_NS, _TICK_NS - RACY_MTIME_WINDOW_NS],
        ids=["aged", "far-in-the-future"],
    )
    def test_an_mtime_outside_the_window_is_kept(self, monkeypatch, now_ns) -> None:
        monkeypatch.setattr(utils, "time_ns", lambda: now_ns)
        assert settled_mtime_ns(_TICK_NS) == _TICK_NS

    def test_a_snapshot_in_the_tick_of_the_path_is_unsettled(
        self, tmp_path, monkeypatch
    ) -> None:
        os.utime(tmp_path, ns=(_TICK_NS, _TICK_NS))
        monkeypatch.setattr(utils, "time_ns", lambda: _TICK_NS)
        assert snapshot_mtime_ns(tmp_path) == UNSETTLED_MTIME_NS

    def test_a_snapshot_after_the_window_keeps_the_mtime(
        self, tmp_path, monkeypatch
    ) -> None:
        os.utime(tmp_path, ns=(_TICK_NS, _TICK_NS))
        monkeypatch.setattr(utils, "time_ns", lambda: _TICK_NS + RACY_MTIME_WINDOW_NS)
        assert snapshot_mtime_ns(tmp_path) == _TICK_NS

    def test_a_snapshot_of_a_path_that_does_not_stat_is_nothing(self, tmp_path) -> None:
        assert snapshot_mtime_ns(tmp_path / "never-written") is None


class TestIsWithin:
    """Tests for ``is_within``."""

    def test_a_path_under_the_root_is_within_it(self, tmp_path) -> None:
        assert is_within(tmp_path / "a" / "b", tmp_path) is True

    def test_the_root_itself_is_within_it(self, tmp_path) -> None:
        assert is_within(tmp_path, tmp_path) is True

    def test_a_sibling_sharing_a_name_prefix_is_not_within(self, tmp_path) -> None:
        assert is_within(tmp_path / "site-two", tmp_path / "site") is False

    def test_a_parent_of_the_root_is_not_within_it(self, tmp_path) -> None:
        assert is_within(tmp_path, tmp_path / "a") is False


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

    @pytest.mark.parametrize(
        ("value", "inside"),
        [(0, False), (1, True), (3, True), (4, False), (True, False), (2.0, False)],
        ids=["below", "low", "high", "above", "bool", "float"],
    )
    def test_int_between_takes_only_ints_inside_both_bounds(
        self, value: object, *, inside: bool
    ) -> None:
        assert int_between(value, 1, 3) is inside

    @pytest.mark.parametrize(
        ("value", "inside"),
        [
            (0, False),
            (0.5, True),
            (2, True),
            (2.5, False),
            (float("inf"), False),
            (float("nan"), False),
            (True, False),
            ("1", False),
            (10**400, False),
            (-(10**400), False),
        ],
        ids=[
            "zero",
            "float",
            "ceiling",
            "above",
            "inf",
            "nan",
            "bool",
            "str",
            "huge-int",
            "huge-negative",
        ],
    )
    def test_positive_number_takes_finite_numbers_up_to_the_ceiling(
        self, value: object, *, inside: bool
    ) -> None:
        assert positive_number(value, 2) is inside

    @pytest.mark.parametrize(
        ("value", "inside"),
        [
            (0, True),
            (2.0, True),
            (-0.5, False),
            (2.5, False),
            (float("nan"), False),
            (10**400, False),
        ],
        ids=["zero", "ceiling", "negative", "above", "nan", "huge-int"],
    )
    def test_non_negative_number_takes_zero_too(
        self, value: object, *, inside: bool
    ) -> None:
        assert non_negative_number(value, 2) is inside

    @pytest.mark.parametrize("check", [positive_number, non_negative_number])
    def test_an_unbounded_ceiling_takes_a_big_int_but_not_one_past_float(
        self, check
    ) -> None:
        assert check(2**1023, float("inf")) is True
        assert check(10**400, float("inf")) is False


class TestAfterFork:
    def test_registers_the_reset_for_a_forked_child(self) -> None:
        reset = Mock()
        with patch.object(os, "register_at_fork", create=True) as register:
            after_fork(reset)
        register.assert_called_once_with(after_in_child=reset)

    def test_does_nothing_where_the_platform_cannot_fork(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(os, "register_at_fork", raising=False)
        reset = Mock()
        after_fork(reset)
        reset.assert_not_called()


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


class TestPrefixTrail:
    """A Django `path()` prefix reads as the bracket trail of the page directories."""

    @pytest.mark.parametrize(
        ("prefix", "trail"),
        [
            ("", ""),
            ("/", ""),
            ("shop/", "shop"),
            ("/shop", "shop"),
            ("w/<int:wallet>/ops/", "w/[int:wallet]/ops"),
            ("<slug>/", "[slug]"),
            ("a/<int:x>/<str:y>/", "a/[int:x]/[str:y]"),
        ],
        ids=["empty", "slash", "static", "leading", "typed", "untyped", "two"],
    )
    def test_a_prefix_becomes_a_trail(self, prefix: str, trail: str) -> None:
        assert prefix_trail(prefix) == trail


class TestJoinTrail:
    """Two trails join with one slash, and an empty side adds nothing."""

    @pytest.mark.parametrize(
        ("head", "tail", "joined"),
        [
            ("", "", ""),
            ("", "ops", "ops"),
            ("w/[int:wallet]", "", "w/[int:wallet]"),
            ("w/[int:wallet]", "ops/[int:pk]", "w/[int:wallet]/ops/[int:pk]"),
        ],
        ids=["both_empty", "no_head", "no_tail", "both"],
    )
    def test_the_join(self, head: str, tail: str, joined: str) -> None:
        assert join_trail(head, tail) == joined


class TestRegexTrail:
    """A `re_path()` route reads as a trail the way Django reverses it."""

    @pytest.mark.parametrize(
        ("regex", "trail"),
        [
            ("^shop/", "shop"),
            (r"^blog/(?P<year>[0-9]{4})/", "blog/[year]"),
            (r"^v1\.0/", "v1.0"),
            (r"^x/(\d+)/", "x/[_0]"),
            ("^", ""),
            ("^(?:en|de)/", "[[route]]"),
            ("^(?i)shop/", "[[route]]"),
        ],
        ids=["static", "named", "escaped", "positional", "empty", "either", "flags"],
    )
    def test_a_regex_becomes_a_trail(self, regex: str, trail: str) -> None:
        assert regex_trail(regex) == trail


class TestPageRootTrails:
    """A tree answers routes under the prefix of the include that mounts it."""

    @pytest.mark.parametrize(
        ("prefix", "trail", "route"),
        [
            (None, "ops", "ops"),
            (None, "", ""),
            ("org/<int:org>/audit/", "log", "org/[int:org]/audit/log"),
            ("staff/", "", "staff"),
        ],
        ids=["assembled", "assembled_index", "included", "included_index"],
    )
    def test_route_trails(self, prefix: str | None, trail: str, route: str) -> None:
        root = PageRoot(path=Path(), label="x", prefix=prefix)

        assert root.route_trail(trail) == route

    def test_a_walked_trail_wins_over_the_prefix(self) -> None:
        root = PageRoot(
            path=Path(), label="x", prefix=r"^org/(?P<org>\d+)/", trail="org/[org]"
        )

        assert root.route_trail("log") == "org/[org]/log"

    def test_the_defaults_describe_an_auto_assembled_tree(self) -> None:
        root = PageRoot(path=Path(), label="Root")

        assert (root.prefix, root.namespace) == (None, "next")
        assert root.route_trail() == ""

    def test_a_root_built_without_a_key_has_none_and_is_well_shaped(self) -> None:
        root = PageRoot(Path(), "Third party")

        assert root.key is None
        assert page_roots_shape_error("Router", [root]) is None


class TestPrivateDirectories:
    """A directory whose name starts with `_` is a private folder, never a route."""

    def _tree(self, root: Path) -> None:
        for trail in ("", "about", "_drafts/wip", "about/_notes/x", "_components/card"):
            write_page(root, trail)

    def test_a_private_folder_routes_none_of_its_pages(self, tmp_path: Path) -> None:
        self._tree(tmp_path)

        assert [trail for trail, _page in walk_page_tree(tmp_path)] == ["", "about"]

    def test_every_private_folder_is_reported_where_it_sits(
        self, tmp_path: Path
    ) -> None:
        self._tree(tmp_path)
        skipped: list[tuple[str, Path, str]] = []

        list(
            walk_page_tree(
                tmp_path,
                on_skipped_dir=lambda folder, root, trail: skipped.append(
                    (folder.name, root, trail)
                ),
            )
        )

        assert sorted(skipped) == [
            ("_components", tmp_path, ""),
            ("_drafts", tmp_path, ""),
            ("_notes", tmp_path, "about"),
        ]

    def test_a_named_skip_and_a_private_folder_report_alike(
        self, tmp_path: Path
    ) -> None:
        self._tree(tmp_path)
        write_page(tmp_path, "drafts/wip")
        skipped: list[str] = []

        routes = [
            trail
            for trail, _page in walk_page_tree(
                tmp_path,
                {"drafts"},
                on_skipped_dir=lambda folder, _root, _trail: skipped.append(
                    folder.name
                ),
            )
        ]

        assert routes == ["", "about"]
        assert sorted(skipped) == ["_components", "_drafts", "_notes", "drafts"]

    def test_the_components_folder_still_registers(self, tmp_path: Path) -> None:
        self._tree(tmp_path)
        registered: list[tuple[str, Path, str]] = []

        with patch(
            "next.urls.dispatcher.register_components_folder_from_router_walk",
            lambda folder, root, trail: registered.append((folder.name, root, trail)),
        ):
            routes = [
                trail
                for trail, _page in scan_pages_tree(tmp_path, register_components=True)
            ]

        assert routes == ["", "about"]
        assert registered == [("_components", tmp_path, "")]


class TestRouteGroupNames:
    """A directory named as an identifier in parentheses is a route group."""

    @pytest.mark.parametrize(
        "name", ["(app)", "(a-b)", "(a_b)", "(x1)", "(_x)"], ids=str
    )
    def test_a_group_name(self, name: str) -> None:
        assert is_route_group(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "()",
            "(a b)",
            "((x))",
            "(x",
            "x)",
            "([id])",
            "(1x)",
            "(.)x",
            "(..)x",
            "(...)x",
            "(x)y",
            "x",
            "[id]",
            "",
        ],
        ids=repr,
    )
    def test_not_a_group_name(self, name: str) -> None:
        assert is_route_group(name) is False

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("(.)photo", True),
            ("(.x", True),
            ("(app)", False),
            ("photo", False),
            ("", False),
        ],
    )
    def test_a_name_opening_with_the_marker_prefix_is_read_as_an_intercept(
        self, name: str, *, expected: bool
    ) -> None:
        assert is_intercept_name(name) is expected

    def test_the_pattern_matches_a_whole_name_only(self) -> None:
        assert ROUTE_GROUP_PATTERN.fullmatch("(app)") is not None
        assert ROUTE_GROUP_PATTERN.fullmatch("(app)x") is None
        assert ROUTE_GROUP_PATTERN.fullmatch("x(app)") is None

    @pytest.mark.parametrize(
        ("raw", "stripped"),
        [
            ("", ""),
            ("dash/stats", "dash/stats"),
            ("(app)", ""),
            ("(app)/dash/(x)/y", "dash/y"),
            ("[id]/(g)/k", "[id]/k"),
            ("(1x)/y", "(1x)/y"),
        ],
    )
    def test_stripping_a_trail(self, raw: str, stripped: str) -> None:
        assert strip_route_groups(raw) == stripped

    def test_a_trail_without_parentheses_comes_back_as_is(self) -> None:
        raw = "dash/stats"

        assert strip_route_groups(raw) is raw


class TestRouteGroupWalk:
    """A group page and children merge into the URL level of the folder holding it."""

    def _walk(self, root: Path, *skip: str) -> list[str]:
        return [trail for trail, _page in walk_page_tree(root, skip)]

    def test_a_group_at_the_root_serves_the_root_url(self, tmp_path: Path) -> None:
        write_page(tmp_path, "(app)")
        write_page(tmp_path, "(app)/dash")

        assert self._walk(tmp_path) == ["", "dash"]

    def test_a_group_page_yields_its_own_file_at_the_parent_trail(
        self, tmp_path: Path
    ) -> None:
        write_page(tmp_path, "docs")
        grouped = write_page(tmp_path, "docs/(g)")

        assert list(walk_page_tree(tmp_path)) == [
            ("docs", tmp_path / "docs" / "page.py"),
            ("docs", grouped),
        ]

    def test_a_lone_template_in_a_group_is_a_page_at_the_parent_trail(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "(g)").mkdir()
        (tmp_path / "(g)" / "template.djx").write_text("hi")

        assert list(walk_page_tree(tmp_path)) == [("", tmp_path / "(g)" / "page.py")]

    def test_nested_groups_flatten(self, tmp_path: Path) -> None:
        write_page(tmp_path, "(a)/(b)/bbb")
        write_page(tmp_path, "(a)/(b)")
        write_page(tmp_path, "(a)/(c)/ccc")

        assert self._walk(tmp_path) == ["", "bbb", "ccc"]

    def test_a_parameter_below_a_group_and_a_group_below_a_parameter(
        self, tmp_path: Path
    ) -> None:
        write_page(tmp_path, "(app)/[int:id]")
        write_page(tmp_path, "[slug]/(g)/edit")

        assert self._walk(tmp_path) == ["[int:id]", "[slug]/edit"]

    def test_a_private_folder_hides_the_groups_inside_it(self, tmp_path: Path) -> None:
        write_page(tmp_path, "_private/(x)/page")
        write_page(tmp_path, "about")

        assert self._walk(tmp_path) == ["about"]

    def test_a_private_folder_inside_a_group_is_skipped(self, tmp_path: Path) -> None:
        write_page(tmp_path, "(x)/_private/page")
        write_page(tmp_path, "(x)/about")
        skipped: list[str] = []

        routes = [
            trail
            for trail, _page in walk_page_tree(
                tmp_path,
                on_skipped_dir=lambda folder, _root, _trail: skipped.append(
                    folder.name
                ),
            )
        ]

        assert routes == ["about"]
        assert skipped == ["_private"]

    def test_the_merged_level_ranks_a_literal_before_a_parameter(
        self, tmp_path: Path
    ) -> None:
        write_page(tmp_path, "(app)/[id]")
        write_page(tmp_path, "new")

        assert self._walk(tmp_path) == ["new", "[id]"]

    def test_equal_names_follow_the_group_name_order_after_the_own_folder(
        self, tmp_path: Path
    ) -> None:
        own = write_page(tmp_path, "item")
        beta = write_page(tmp_path, "(beta)/item")
        alpha = write_page(tmp_path, "(alpha)/item")

        assert [page for _trail, page in walk_page_tree(tmp_path)] == [own, alpha, beta]

    @pytest.mark.parametrize(
        ("static", "dynamic"),
        [("(admin)/shop/new", "shop/[slug]"), ("shop/new", "(admin)/shop/[slug]")],
    )
    def test_a_segment_split_across_groups_ranks_its_children_together(
        self, tmp_path: Path, static: str, dynamic: str
    ) -> None:
        write_page(tmp_path, dynamic)
        write_page(tmp_path, static)

        assert self._walk(tmp_path) == ["shop/new", "shop/[slug]"]

    def test_a_catch_all_below_a_split_segment_ranks_last(self, tmp_path: Path) -> None:
        write_page(tmp_path, "docs/[[rest]]")
        write_page(tmp_path, "(a)/docs/[slug]")
        write_page(tmp_path, "(b)/docs/intro")

        assert self._walk(tmp_path) == ["docs/intro", "docs/[slug]", "docs/[[rest]]"]

    def test_a_segment_split_across_nested_groups_merges_at_every_depth(
        self, tmp_path: Path
    ) -> None:
        write_page(tmp_path, "shop/item/[id]")
        write_page(tmp_path, "(a)/(b)/shop/item/new")
        write_page(tmp_path, "(a)/shop/(c)/item/latest")

        assert self._walk(tmp_path) == [
            "shop/item/latest",
            "shop/item/new",
            "shop/item/[id]",
        ]

    def test_the_pages_of_a_split_segment_keep_the_folder_order(
        self, tmp_path: Path
    ) -> None:
        own = write_page(tmp_path, "shop")
        alpha = write_page(tmp_path, "(alpha)/shop")
        beta = write_page(tmp_path, "(beta)/shop")
        inner = write_page(tmp_path, "shop/(g)")

        assert list(walk_page_tree(tmp_path)) == [
            ("shop", own),
            ("shop", inner),
            ("shop", alpha),
            ("shop", beta),
        ]

    def test_a_split_segment_reports_each_skipped_folder_with_its_own_trail(
        self, tmp_path: Path
    ) -> None:
        write_page(tmp_path, "shop/new")
        write_page(tmp_path, "(admin)/shop/[slug]")
        for folder in ("shop/_components", "(admin)/shop/_components"):
            (tmp_path / folder).mkdir()
        skipped: list[str] = []

        list(
            walk_page_tree(
                tmp_path,
                on_skipped_dir=lambda _folder, _root, trail: skipped.append(trail),
            )
        )

        assert skipped == ["shop", "(admin)/shop"]

    def test_an_intercept_in_a_split_segment_targets_the_url_trail(
        self, tmp_path: Path
    ) -> None:
        write_page(tmp_path, "feed/photo")
        source = tmp_path / "(app)" / "feed" / "(.)photo"
        page = write_page(tmp_path, "(app)/feed/(.)photo")
        found: list[InterceptPage] = []

        list(walk_page_tree(tmp_path, on_intercept=found.append))

        assert found == [InterceptPage("feed/photo", page, source, "(.)")]

    def test_groups_without_anything_in_them_add_nothing(self, tmp_path: Path) -> None:
        write_page(tmp_path, "about")
        (tmp_path / "(empty)").mkdir()
        (tmp_path / "(other)" / "(inner)").mkdir(parents=True)

        assert self._walk(tmp_path) == ["about"]

    def test_a_tree_without_groups_keeps_its_order(self, tmp_path: Path) -> None:
        for trail in ("", "zeta", "[id]", "alpha", "alpha/[slug]", "alpha/new"):
            write_page(tmp_path, trail)

        assert self._walk(tmp_path) == [
            "",
            "alpha",
            "alpha/new",
            "alpha/[slug]",
            "zeta",
            "[id]",
        ]

    def test_a_skipped_folder_is_reported_with_its_file_system_trail(
        self, tmp_path: Path
    ) -> None:
        write_page(tmp_path, "(app)/dash")
        for folder in ("(app)/_components", "(app)/dash/_components"):
            (tmp_path / folder).mkdir()
        skipped: list[tuple[Path, str]] = []

        list(
            walk_page_tree(
                tmp_path,
                on_skipped_dir=lambda folder, root, trail: skipped.append(
                    (folder.relative_to(root), trail)
                ),
            )
        )

        assert sorted(skipped) == [
            (Path("(app)/_components"), "(app)"),
            (Path("(app)/dash/_components"), "(app)/dash"),
        ]


class TestInterceptNames:
    """An intercept name is one marker kind followed by the segment it targets."""

    @pytest.mark.parametrize("case", INTERCEPT_NAME_CASES, ids=lambda c: repr(c.name))
    def test_parsing_a_name(self, case: InterceptNameCase) -> None:
        assert parse_intercept(case.name) == case.parsed

    def test_the_pattern_matches_a_whole_name_only(self) -> None:
        assert INTERCEPT_PATTERN.fullmatch("(.)photo") is not None
        assert INTERCEPT_PATTERN.fullmatch("x(.)photo") is None
        assert INTERCEPT_PATTERN.fullmatch("(.)photo)") is None

    @pytest.mark.parametrize(
        "case", INTERCEPT_RESOLVE_CASES, ids=lambda c: f"{c.scope}|{c.marker}{c.rest}"
    )
    def test_resolving_a_target(self, case: InterceptResolveCase) -> None:
        assert resolve_intercept(case.scope, case.marker, case.rest) == case.target


class TestInterceptWalk:
    """An intercept page is reported with the trail it serves over and never routed."""

    @staticmethod
    def _scan(
        root: Path, *skip: str
    ) -> tuple[list[tuple[str, Path]], list[InterceptPage]]:
        found: list[InterceptPage] = []
        routes = list(walk_page_tree(root, skip, on_intercept=found.append))
        return routes, found

    def test_a_page_below_an_intercept_targets_the_resolved_trail(
        self, tmp_path: Path
    ) -> None:
        feed = write_page(tmp_path, "feed")
        photo = write_page(tmp_path, "feed/(.)photo/[id]")

        routes, found = self._scan(tmp_path)

        assert routes == [("feed", feed)]
        assert found == [
            InterceptPage(
                "feed/photo/[id]", photo, tmp_path / "feed" / "(.)photo", "(.)"
            )
        ]

    def test_the_intercept_directory_page_targets_the_resolved_trail(
        self, tmp_path: Path
    ) -> None:
        own = write_page(tmp_path, "feed/(.)photo")

        routes, found = self._scan(tmp_path)

        assert routes == []
        assert found == [
            InterceptPage("feed/photo", own, tmp_path / "feed" / "(.)photo", "(.)")
        ]

    def test_a_virtual_page_inside_an_intercept_is_reported(
        self, tmp_path: Path
    ) -> None:
        folder = tmp_path / "feed" / "(.)photo"
        folder.mkdir(parents=True)
        (folder / "template.djx").write_text("hi")

        routes, found = self._scan(tmp_path)

        assert routes == []
        assert found == [InterceptPage("feed/photo", folder / "page.py", folder, "(.)")]

    @pytest.mark.parametrize(
        ("trail", "marker", "target"),
        [
            ("a/b/(..)x", "(..)", "a/x"),
            ("a/b/c/(..)(..)x", "(..)(..)", "a/x"),
            ("a/b/c/(...)x", "(...)", "x"),
            ("(.)x", "(.)", "x"),
            ("(...)x", "(...)", "x"),
        ],
    )
    def test_every_marker_resolves_from_its_directory(
        self, tmp_path: Path, trail: str, marker: str, target: str
    ) -> None:
        file = write_page(tmp_path, trail)

        _routes, found = self._scan(tmp_path)

        assert found == [InterceptPage(target, file, tmp_path / trail, marker)]

    def test_a_group_inside_an_intercept_flattens(self, tmp_path: Path) -> None:
        own = write_page(tmp_path, "feed/(.)photo/(g)")
        deep = write_page(tmp_path, "feed/(.)photo/(g)/(h)/[id]")

        _routes, found = self._scan(tmp_path)

        source = tmp_path / "feed" / "(.)photo"
        assert sorted(found, key=lambda hit: hit.target_trail) == [
            InterceptPage("feed/photo", own, source, "(.)"),
            InterceptPage("feed/photo/[id]", deep, source, "(.)"),
        ]

    @pytest.mark.parametrize(
        "trail", ["(app)/feed/(.)photo", "feed/(g)/(.)photo", "(app)/feed/(g)/(.)photo"]
    )
    def test_an_intercept_inside_a_group_leaves_the_group_out_of_the_scope(
        self, tmp_path: Path, trail: str
    ) -> None:
        write_page(tmp_path, trail)

        _routes, found = self._scan(tmp_path)

        assert [hit.target_trail for hit in found] == ["feed/photo"]

    def test_an_intercept_inside_a_root_group_resolves_from_the_root(
        self, tmp_path: Path
    ) -> None:
        write_page(tmp_path, "(g)/(..)x")
        write_page(tmp_path, "(g)/(.)y")

        _routes, found = self._scan(tmp_path)

        assert [hit.target_trail for hit in found] == ["y"]

    def test_a_nested_intercept_is_dropped(self, tmp_path: Path) -> None:
        own = write_page(tmp_path, "feed/(.)photo")
        write_page(tmp_path, "feed/(.)photo/(.)inner")
        write_page(tmp_path, "feed/(.)photo/[id]/(...)deep")
        write_page(tmp_path, "feed/(.)photo/(g)/(.)other")

        routes, found = self._scan(tmp_path)

        assert routes == []
        assert [hit.file for hit in found] == [own]

    def test_a_malformed_name_and_a_marker_past_the_root_are_dropped(
        self, tmp_path: Path
    ) -> None:
        write_page(tmp_path, "(.)")
        write_page(tmp_path, "(..)x")
        write_page(tmp_path, "(.)(..)x")
        write_page(tmp_path, "feed/(..)(..)x")

        routes, found = self._scan(tmp_path)

        assert routes == []
        assert found == []

    def test_a_parenthesised_name_that_is_no_group_is_routed(
        self, tmp_path: Path
    ) -> None:
        odd = write_page(tmp_path, "(1x)")
        spaced = write_page(tmp_path, "(a b)")

        routes, found = self._scan(tmp_path)

        assert sorted(routes) == [("(1x)", odd), ("(a b)", spaced)]
        assert found == []

    def test_without_a_callback_an_intercept_is_never_routed(
        self, tmp_path: Path
    ) -> None:
        about = write_page(tmp_path, "about")
        write_page(tmp_path, "feed/(.)photo/[id]")
        write_page(tmp_path, "(.)")

        assert list(walk_page_tree(tmp_path)) == [("about", about)]

    def test_a_skipped_folder_inside_an_intercept_keeps_the_file_system_trail(
        self, tmp_path: Path
    ) -> None:
        write_page(tmp_path, "feed/(.)photo/(g)/[id]")
        for folder in (
            "feed/(.)photo/_components",
            "feed/(.)photo/(g)/_components",
            "feed/(.)photo/(g)/[id]/_components",
        ):
            (tmp_path / folder).mkdir(parents=True, exist_ok=True)
        skipped: list[tuple[Path, str]] = []

        for callbacks in ({"on_intercept": lambda _hit: None}, {}):
            skipped.clear()
            list(
                walk_page_tree(
                    tmp_path,
                    ("_components",),
                    on_skipped_dir=lambda folder, root, trail: skipped.append(
                        (folder.relative_to(root), trail)
                    ),
                    **callbacks,
                )
            )

            assert sorted(skipped) == [
                (Path("feed/(.)photo/(g)/[id]/_components"), "feed/(.)photo/(g)/[id]"),
                (Path("feed/(.)photo/(g)/_components"), "feed/(.)photo/(g)"),
                (Path("feed/(.)photo/_components"), "feed/(.)photo"),
            ]

    def test_a_malformed_intercept_reports_nothing_but_the_walk_goes_on(
        self, tmp_path: Path
    ) -> None:
        write_page(tmp_path, "(.)")
        (tmp_path / "(.)" / "_components").mkdir()
        skipped: list[Path] = []

        list(
            walk_page_tree(
                tmp_path,
                ("_components",),
                on_skipped_dir=lambda folder, _root, _trail: skipped.append(folder),
            )
        )

        assert skipped == []

    def test_intercepts_do_not_change_the_sibling_order(self, tmp_path: Path) -> None:
        for trail in ("zeta", "[id]", "alpha", "alpha/new", "alpha/[slug]"):
            write_page(tmp_path, trail)
        plain = [trail for trail, _page in walk_page_tree(tmp_path)]
        for trail in ("(.)a", "(..)b", "(...)c", "alpha/(.)new/[x]", "(g)/(.)q"):
            write_page(tmp_path, trail)

        routes, _found = self._scan(tmp_path)

        assert [trail for trail, _page in routes] == plain


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

    def test_a_failure_is_kept_with_its_cause(self, tmp_path, caplog) -> None:
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
    """A source is stale once its file is rewritten or deleted."""

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

    def test_an_entry_fixed_after_a_failed_import_matches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.syspath_prepend(tmp_path)
        entry = "late_locale_middleware.Late"
        assert not is_middleware(entry, self.LOCALE)
        (tmp_path / "late_locale_middleware.py").write_text(
            "from django.middleware.locale import LocaleMiddleware\n\n"
            "class Late(LocaleMiddleware):\n    pass\n"
        )
        importlib.invalidate_caches()
        assert is_middleware(entry, self.LOCALE)
        sys.modules.pop("late_locale_middleware")

    def test_a_list_holding_a_match_lists_the_base(self) -> None:
        assert middleware_listed(["a.B", f"{__name__}._Locale"], self.LOCALE)
        assert not middleware_listed(["a.B"], self.LOCALE)
        assert not middleware_listed([], self.LOCALE)


class TestRedirectOrFallback:
    """`redirect_or_fallback` answers the target unless Django refuses the Location."""

    def test_accepted_target_carries_the_status(self) -> None:
        response = redirect_or_fallback("/items/", "/", status=303)
        assert response.status_code == 303
        assert response.url == "/items/"

    def test_default_status_is_found(self) -> None:
        assert redirect_or_fallback("/items/", "/").status_code == 302

    @pytest.mark.usefixtures("cap_redirects")
    def test_refused_target_redirects_to_the_fallback(self) -> None:
        response = redirect_or_fallback(_OVERSIZE_TARGET, "/safe/", status=303)
        assert response.status_code == 303
        assert response.url == "/safe/"

    def test_disallowed_scheme_redirects_to_the_fallback(self) -> None:
        assert redirect_or_fallback("javascript:alert(1)", "/safe/").url == "/safe/"


class TestChildNodelists:
    """A node list on `child_nodelists` is yielded only when the node holds it."""

    def test_a_missing_list_is_skipped(self) -> None:
        (node,) = Template('{% has_perm "a.b" %}x{% endhas_perm %}').nodelist
        node.nodelist_false = None
        assert list(child_nodelists(node)) == [node.nodelist_true]
