from pathlib import Path
from unittest.mock import patch

import pytest

from next.checks import check_layout_templates, check_root_marker_outside_layouts
from next.checks.common import get_router_manager
from next.pages.checks.layouts import iter_layout_ancestry
from next.pages.placeholder import ROOT_MARKER
from tests.support import check_ids, routed, write_page


_SHELL = "<html>{% template %}</html>"
_NO_SLOT = "<html><body>no slot</body></html>"
_TWO_SLOTS = "<html>{% template %}{% template %}</html>"


def _layout(directory: Path, source: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    layout = directory / "layout.djx"
    layout.write_text(source)
    return layout


class TestLayoutAncestry:
    """W001 and W078 read every layout a scanned page sits under, once."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [(_NO_SLOT, "next.W001"), (_TWO_SLOTS, "next.W078")],
        ids=["missing", "repeated"],
    )
    def test_a_layout_of_a_folder_without_a_page_is_checked(
        self, tmp_path: Path, source: str, expected: str
    ) -> None:
        write_page(tmp_path / "pages", "empty/leaf")
        _layout(tmp_path / "pages" / "empty", source)

        with routed(tmp_path / "pages"):
            assert check_ids(check_layout_templates()) == [expected]

    @pytest.mark.parametrize(
        ("source", "expected"),
        [(_NO_SLOT, "next.W001"), (_TWO_SLOTS, "next.W078")],
        ids=["missing", "repeated"],
    )
    def test_a_dirs_root_layout_is_checked(
        self, tmp_path: Path, source: str, expected: str
    ) -> None:
        write_page(tmp_path / "pages", "")
        shared = tmp_path / "shared"
        _layout(shared, source)

        with routed(tmp_path / "pages", shared):
            assert check_ids(check_layout_templates()) == [expected]

    @pytest.mark.parametrize(
        ("source", "expected"),
        [(_NO_SLOT, "next.W001"), (_TWO_SLOTS, "next.W078")],
        ids=["missing", "repeated"],
    )
    def test_a_layout_above_the_tree_is_checked(
        self, tmp_path: Path, source: str, expected: str
    ) -> None:
        write_page(tmp_path / "site" / "pages", "")
        _layout(tmp_path / "site", source)

        with routed(tmp_path / "site" / "pages"):
            assert check_ids(check_layout_templates()) == [expected]

    def test_a_layout_a_root_cuts_from_every_page_is_not_checked(
        self, tmp_path: Path
    ) -> None:
        write_page(tmp_path / "pages", "embed")
        _layout(tmp_path / "pages", _NO_SLOT)
        _layout(tmp_path / "pages" / "embed", f"{ROOT_MARKER}{_SHELL}")

        with routed(tmp_path / "pages"):
            assert check_ids(check_layout_templates()) == []

    def test_a_layout_cut_for_one_page_is_checked_for_another(
        self, tmp_path: Path
    ) -> None:
        write_page(tmp_path / "pages", "embed")
        write_page(tmp_path / "pages", "home")
        _layout(tmp_path / "pages", _NO_SLOT)
        _layout(tmp_path / "pages" / "embed", f"{ROOT_MARKER}{_SHELL}")

        with routed(tmp_path / "pages"):
            assert check_ids(check_layout_templates()) == ["next.W001"]

    def test_a_layout_shared_by_many_pages_is_visited_once(
        self, tmp_path: Path
    ) -> None:
        layout = _layout(tmp_path / "pages", _SHELL)
        write_page(tmp_path / "pages", "a")
        write_page(tmp_path / "pages", "b")

        with routed(tmp_path / "pages"):
            manager, _errors = get_router_manager()
            visited = list(iter_layout_ancestry(manager))

        assert visited.count(layout.resolve()) == 1

    def test_a_failing_router_manager_checks_nothing(self) -> None:
        with patch(
            "next.pages.checks.layouts.get_router_manager", return_value=(None, [])
        ):
            assert check_layout_templates() == []


class TestMisplacedMarker:
    """E168 reports a marker the composition ignores."""

    def test_a_late_marker_is_e168(self, tmp_path: Path) -> None:
        write_page(tmp_path, "")
        layout = _layout(tmp_path, f"<p>x</p>{ROOT_MARKER}{_SHELL}")

        with routed(tmp_path):
            [error] = check_layout_templates()

        assert error.id == "next.E168"
        assert error.obj == str(layout.resolve())

    def test_a_repeated_marker_is_e168(self, tmp_path: Path) -> None:
        write_page(tmp_path, "")
        _layout(tmp_path, f"{ROOT_MARKER}{ROOT_MARKER}{_SHELL}")

        with routed(tmp_path):
            assert check_ids(check_layout_templates()) == ["next.E168"]

    def test_a_marker_at_the_top_is_silent(self, tmp_path: Path) -> None:
        write_page(tmp_path, "")
        _layout(tmp_path, f"{{% load static %}}{ROOT_MARKER}{_SHELL}")

        with routed(tmp_path):
            assert check_layout_templates() == []


class TestNestedDocument:
    """W133 warns about a second document shell inside an outer layout."""

    def test_a_shell_inside_an_outer_layout_is_w133(self, tmp_path: Path) -> None:
        write_page(tmp_path / "pages", "embed")
        _layout(tmp_path / "pages", _SHELL)
        inner = _layout(tmp_path / "pages" / "embed", _SHELL)

        with routed(tmp_path / "pages"):
            warnings = [w for w in check_layout_templates() if w.id == "next.W133"]

        assert [w.obj for w in warnings] == [str(inner.resolve())]
        assert "next.E168" not in warnings[0].msg

    def test_a_misplaced_marker_points_at_e168(self, tmp_path: Path) -> None:
        write_page(tmp_path / "pages", "embed")
        _layout(tmp_path / "pages", _SHELL)
        _layout(tmp_path / "pages" / "embed", f"<p>x</p>{ROOT_MARKER}{_SHELL}")

        with routed(tmp_path / "pages"):
            messages = check_layout_templates()

        assert check_ids(messages) == ["next.E168", "next.W133"]
        assert "next.E168" in messages[1].msg

    def test_a_root_marker_is_silent(self, tmp_path: Path) -> None:
        write_page(tmp_path / "pages", "embed")
        _layout(tmp_path / "pages", _SHELL)
        _layout(tmp_path / "pages" / "embed", f"{ROOT_MARKER}{_SHELL}")

        with routed(tmp_path / "pages"):
            assert check_layout_templates() == []

    def test_a_lone_layout_with_nothing_further_out_is_silent(
        self, tmp_path: Path
    ) -> None:
        write_page(tmp_path / "pages", "")
        _layout(tmp_path / "pages", _SHELL)

        with routed(tmp_path / "pages"):
            assert check_layout_templates() == []

    def test_an_outer_layout_without_a_shell_is_silent(self, tmp_path: Path) -> None:
        write_page(tmp_path / "pages", "embed")
        _layout(tmp_path / "pages", _SHELL)
        _layout(tmp_path / "pages" / "embed", "<main>{% template %}</main>")

        with routed(tmp_path / "pages"):
            assert check_layout_templates() == []


class TestStrayMarker:
    """W134 reports a marker where only a layout.djx would read it."""

    def test_a_page_template_with_a_marker_is_w134(self, tmp_path: Path) -> None:
        write_page(tmp_path, "about", body=f"{ROOT_MARKER}<p>hi</p>")

        with routed(tmp_path):
            [warning] = check_root_marker_outside_layouts()

        assert warning.id == "next.W134"
        assert warning.obj == str(tmp_path / "about" / "template.djx")
        assert "Page template" in warning.msg

    def test_a_component_template_with_a_marker_is_w134(self, tmp_path: Path) -> None:
        write_page(tmp_path, "")
        widget = tmp_path / "_components" / "widget"
        widget.mkdir(parents=True)
        (widget / "component.djx").write_text(f"{ROOT_MARKER}<b>x</b>")

        with routed(tmp_path):
            [warning] = check_root_marker_outside_layouts()

        assert warning.id == "next.W134"
        assert warning.obj == str(widget / "component.djx")
        assert "Component template" in warning.msg

    def test_templates_without_a_marker_are_silent(self, tmp_path: Path) -> None:
        write_page(tmp_path, "about", body="<p>hi</p>")
        widget = tmp_path / "_components" / "widget"
        widget.mkdir(parents=True)
        (widget / "component.djx").write_text("<b>x</b>")

        with routed(tmp_path):
            assert check_root_marker_outside_layouts() == []

    def test_a_marker_in_a_layout_is_silent(self, tmp_path: Path) -> None:
        write_page(tmp_path, "")
        _layout(tmp_path, f"{ROOT_MARKER}{_SHELL}")

        with routed(tmp_path):
            assert check_root_marker_outside_layouts() == []

    def test_a_failing_router_manager_checks_nothing(self) -> None:
        with patch(
            "next.pages.checks.layouts.get_router_manager", return_value=(None, [])
        ):
            assert check_root_marker_outside_layouts() == []
