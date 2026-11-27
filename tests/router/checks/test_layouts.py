from pathlib import Path
from unittest import mock

import pytest

from next.pages.loaders import forget_layout_texts
from next.router.checks import (
    check_inline_scripts,
    check_layout_page_reads,
    check_layout_slots,
    check_no_store_pages,
    check_root_layout_paths,
    layouts as router_checks,
)
from next.testing import override_next_settings
from tests.support import patch_checks_route_manager, write_page
from tests.support.checks import write_file


_ROOT_LAYOUT = (
    "<html><head>{% metadata %}</head><body>"
    '<nav class="{{ active_tab }}">{{ brand }} {{ user }} {{ csrf_token }}</nav>'
    '{% zone "flash" %}{{ notice }}{% endzone %}{% template %}</body></html>'
)
_SECTION_LAYOUT = (
    "<section>{{ section }}{% #template %}{{ hint }}{% /template %}</section>"
)
_HOME = """
from next.pages import context


@context("brand", inherit_context=True)
def brand() -> str:
    return "b"


@context("active_tab")
def active_tab() -> str:
    return "home"

template = "home"
"""
_SECTION = """
from next.pages import context


@context("section", inherit_context=True)
def section() -> str:
    return "ops"


@context("active_tab")
def active_tab() -> str:
    return "ops"


@context("notice")
def notice() -> str:
    return "n"

template = "ops"
"""
_ITEM = """
from next.pages import context


@context("hint")
def hint() -> str:
    return "h"


@context("title")
def title() -> str:
    return "t"

template = "item"
"""
_WRAPPING = """
from next.pages import context


@context("active_tab")
def active_tab() -> str:
    return "none"
"""


@pytest.mark.usefixtures("router_on")
class TestLayoutSlots:
    """`next.E239` names a placeholder the slot comments cannot wrap."""

    def test_a_title_placeholder(self, tmp_path: Path) -> None:
        bad = write_file(tmp_path / "layout.djx", "<title>{% template %}</title>")
        good = write_file(tmp_path / "a" / "layout.djx", "<main>{% template %}</main>")
        gone = tmp_path / "gone" / "layout.djx"
        with mock.patch.object(
            router_checks, "layouts", return_value=[bad, good, gone]
        ):
            (message,) = check_layout_slots()
        assert message.id == "next.E239"
        assert "inside a <title>" in message.msg

    def test_the_layouts_of_the_trees(self, tmp_path: Path) -> None:
        first = write_file(tmp_path / "a" / "layout.djx", "<main>{% template %}</main>")
        second = write_file(
            tmp_path / "b" / "layout.djx", "<main>{% template %}</main>"
        )
        third = write_file(tmp_path / "c" / "layout.djx", "<main>{% template %}</main>")
        with (
            mock.patch.object(
                router_checks,
                "get_layout_djx_paths_for_watch",
                return_value=[second, first],
            ),
            mock.patch.object(
                router_checks, "shared_layouts", return_value=[first, third]
            ),
        ):
            assert router_checks.layouts() == [first, second, third]


@pytest.mark.usefixtures("router_on")
class TestLayoutPageReads:
    """`next.W183` names a layout chrome reading a key the pages below it set."""

    def _tree(self, root: Path, home: str = _HOME) -> None:
        (root / "layout.djx").write_text(_ROOT_LAYOUT)
        write_page(root, "", home)
        write_page(root, "ops", _SECTION)
        (root / "ops" / "layout.djx").write_text(_SECTION_LAYOUT)
        write_page(root, "ops/item", _ITEM)
        (root / "ops" / "item" / "layout.djx").write_text("<p>{{ title }}</p>")
        forget_layout_texts()

    def test_a_key_the_pages_set_differently_is_named(self, tmp_path: Path) -> None:
        self._tree(tmp_path)

        with patch_checks_route_manager(pages_directory=tmp_path):
            (message,) = check_layout_page_reads()

        assert message.id == "next.W183"
        assert message.obj == str(tmp_path / "layout.djx")
        assert "reads 'active_tab' outside a zone" in message.msg
        assert f"as {tmp_path / 'page.py'} does for 'active_tab'" in message.msg
        assert "{% zone %}" in message.hint

    def test_shared_keys_zones_and_the_slot_are_quiet(self, tmp_path: Path) -> None:
        self._tree(tmp_path)
        (tmp_path / "layout.djx").write_text(
            _ROOT_LAYOUT.replace("{{ active_tab }}", "")
        )
        forget_layout_texts()

        with patch_checks_route_manager(pages_directory=tmp_path):
            assert check_layout_page_reads() == []

    def test_a_page_py_without_a_body_is_not_read(self, tmp_path: Path) -> None:
        self._tree(tmp_path, home=_WRAPPING)
        (tmp_path / "ops" / "page.py").write_text(
            _SECTION.replace('"active_tab"', '"other"')
        )

        with patch_checks_route_manager(pages_directory=tmp_path):
            assert check_layout_page_reads() == []

    def test_a_layout_off_the_page_path_shares_no_callable(self) -> None:
        assert (
            router_checks._distance(Path("/a/layout.djx"), Path("/b/page.py")) is None
        )


@pytest.mark.parametrize(
    "check",
    [
        check_layout_slots,
        check_root_layout_paths,
        check_inline_scripts,
        check_no_store_pages,
        check_layout_page_reads,
    ],
    ids=lambda check: check.__name__,
)
def test_off_checks_nothing(check) -> None:
    with override_next_settings(ROUTER={"ENABLED": False}):
        assert check() == []
