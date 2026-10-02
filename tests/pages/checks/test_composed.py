from pathlib import Path
from unittest.mock import patch

from django.core.checks import Error
from django.template import Context
from django.test import override_settings

from next.checks import reset_check_caches
from next.pages.checks import composed
from next.pages.checks.composed import iter_composed_pages
from next.pages.manager import page
from tests.support import file_router_config_entry, routed, write_page


LAYOUT = "<main>{% template %}</main>"


def _tree(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "layout.djx").write_text(LAYOUT)
    write_page(root, "", 'template = "<p>home</p>"\n')
    write_page(root, "about", "x = 1\n", body="<p>about</p>")
    return root


class TestIterComposedPages:
    """Each templated page comes back once, compiled through its layouts."""

    def test_every_templated_page_is_composed_through_its_layout(
        self, tmp_path: Path
    ) -> None:
        root = _tree(tmp_path / "pages")
        with routed(root):
            rendered = {
                path.parent.name: template.render(Context())
                for path, template in iter_composed_pages()
            }
        assert rendered == {
            "pages": "<main><p>home</p></main>",
            "about": "<main><p>about</p></main>",
        }

    def test_a_template_attribute_needs_no_layout(self, tmp_path: Path) -> None:
        root = tmp_path / "pages"
        write_page(root, "", 'template = "<p>home</p>"\n')
        with routed(root):
            rendered = [
                template.render(Context()) for _path, template in iter_composed_pages()
            ]
        assert rendered == ["<p>home</p>"]

    def test_a_page_without_a_template_is_left_out(self, tmp_path: Path) -> None:
        root = tmp_path / "pages"
        write_page(root, "", "x = 1\n", body="<p>home</p>")
        write_page(root, "bare", "x = 1\n")
        with routed(root):
            names = [path.parent.name for path, _template in iter_composed_pages()]
        assert names == ["pages"]

    def test_a_template_that_fails_to_compile_is_left_out(self, tmp_path: Path) -> None:
        root = _tree(tmp_path / "pages")
        write_page(root, "broken", "x = 1\n", body="{% if %}")
        with routed(root):
            names = [path.parent.name for path, _template in iter_composed_pages()]
        assert sorted(names) == ["about", "pages"]

    def test_a_tree_two_routers_share_is_composed_once(self, tmp_path: Path) -> None:
        root = _tree(tmp_path / "pages")
        entries = [file_router_config_entry(pages_dir=root)] * 2
        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": entries}):
            paths = [path for path, _template in iter_composed_pages()]
        assert len(paths) == len(set(paths)) == 2

    def test_no_router_manager_yields_nothing(self) -> None:
        error = Error("boom", id="next.E007")
        with patch.object(composed, "get_router_manager", return_value=(None, [error])):
            assert list(iter_composed_pages()) == []


class TestOncePerRun:
    """The walk runs once per check run and again after a reset."""

    def test_a_repeat_reuses_the_walk_until_a_reset(self, tmp_path: Path) -> None:
        root = _tree(tmp_path / "pages")
        with (
            routed(root),
            patch.object(
                page, "composed_template_for", wraps=page.composed_template_for
            ) as compose,
        ):
            first = list(iter_composed_pages())
            assert list(iter_composed_pages()) == first
            assert compose.call_count == 2
            reset_check_caches()
            list(iter_composed_pages())
            assert compose.call_count == 4
