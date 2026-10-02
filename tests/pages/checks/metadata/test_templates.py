from pathlib import Path

import pytest
from django.conf import settings
from django.test import override_settings

from next.pages.checks import check_metadata_tag_rendered
from tests.pages.checks.metadata.trees import framework, metadata_page, templated_page
from tests.support import check_ids, patch_checks_router_manager


class TestTagRendered:
    """`check_metadata_tag_rendered` walks the composition and its components."""

    def _project(self, tmp_path: Path, layout: str) -> tuple[Path, Path]:
        pages = tmp_path / "pages"
        pages.mkdir()
        (pages / "layout.djx").write_text(layout)
        page_file = metadata_page(pages / "hello", '{"title": "Hello"}')
        return pages, page_file

    def _component(self, pages: Path, name: str, body: str) -> None:
        folder = pages / "_components" / name
        folder.mkdir(parents=True)
        (folder / "component.djx").write_text(body)

    def test_a_tag_in_the_layout_is_silent(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(
            tmp_path, "<html><head>{% metadata %}</head>{% template %}</html>"
        )
        with patch_checks_router_manager(pages_directory=pages):
            assert check_metadata_tag_rendered() == []

    def test_a_tag_inside_a_component_is_silent(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(
            tmp_path, '<html>{% component "head" %}{% template %}</html>'
        )
        self._component(pages, "head", "<head>{% metadata %}</head>")
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            assert check_metadata_tag_rendered() == []

    def test_a_tag_two_components_deep_is_silent(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(
            tmp_path,
            '<html>{% #component "head" %}x{% /component %}{% template %}</html>',
        )
        self._component(pages, "head", '<head>{% component "seo" %}</head>')
        self._component(pages, "seo", "{% metadata %}")
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            assert check_metadata_tag_rendered() == []

    def test_no_tag_anywhere_is_w085(self, tmp_path: Path) -> None:
        pages, page_file = self._project(
            tmp_path, '<html>{% component "head" %}{% template %}</html>'
        )
        self._component(pages, "head", '<head><meta charset="utf-8"></head>')
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            messages = check_metadata_tag_rendered()
        assert check_ids(messages) == ["next.W085"]
        assert "{% metadata %}" in messages[0].msg
        assert messages[0].obj == str(page_file)

    def test_no_tag_and_no_component_is_w085(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(tmp_path, "<html>{% template %}</html>")
        with patch_checks_router_manager(pages_directory=pages):
            assert check_ids(check_metadata_tag_rendered()) == ["next.W085"]

    def test_a_page_without_metadata_is_silent(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        (pages / "layout.djx").write_text("<html>{% template %}</html>")
        templated_page(pages / "hello", "x = 1\n")
        with patch_checks_router_manager(pages_directory=pages):
            assert check_metadata_tag_rendered() == []

    def test_metadata_on_an_ancestor_counts_as_declared(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        (pages / "layout.djx").write_text("<html>{% template %}</html>")
        metadata_page(pages, '{"title": "Root"}')
        child = templated_page(pages / "child", "x = 1\n")
        with patch_checks_router_manager(pages_directory=pages):
            messages = check_metadata_tag_rendered()
        assert sorted(m.obj for m in messages) == sorted(
            [str(pages / "page.py"), str(child)]
        )

    def test_an_unresolved_component_is_silent(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(
            tmp_path, '<html>{% component "head" %}{% template %}</html>'
        )
        with patch_checks_router_manager(pages_directory=pages):
            assert check_metadata_tag_rendered() == []

    def test_a_render_page_is_silent(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        (pages / "layout.djx").write_text("<html>{% template %}</html>")
        templated_page(
            pages / "hello",
            'metadata = {"title": "Hello"}\n\n'
            "def render(request):\n"
            "    return '<p>x</p>'\n",
            body=None,
        )
        with patch_checks_router_manager(pages_directory=pages):
            assert check_metadata_tag_rendered() == []

    def test_a_composition_that_does_not_compile_is_silent(
        self, tmp_path: Path
    ) -> None:
        pages, _page_file = self._project(tmp_path, "<html>{% if %}{% template %}")
        with patch_checks_router_manager(pages_directory=pages):
            assert check_metadata_tag_rendered() == []

    def test_a_component_that_does_not_compile_is_silent(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(
            tmp_path, '<html>{% component "head" %}{% template %}</html>'
        )
        self._component(pages, "head", "{% if %}")
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            assert check_metadata_tag_rendered() == []

    def test_a_component_cycle_is_silent(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(
            tmp_path, '<html>{% component "a" %}{% template %}</html>'
        )
        self._component(pages, "a", '{% component "b" %}')
        self._component(pages, "b", '{% component "a" %}')
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            assert check_metadata_tag_rendered() == []

    def test_a_descent_past_the_depth_cap_is_silent(self, tmp_path: Path) -> None:
        pages, _page_file = self._project(
            tmp_path, '<html>{% component "c0" %}{% template %}</html>'
        )
        for index in range(9):
            self._component(pages, f"c{index}", f'{{% component "c{index + 1}" %}}')
        self._component(pages, "c9", "<p>leaf</p>")
        with override_settings(NEXT_FRAMEWORK=framework(pages)):
            assert check_metadata_tag_rendered() == []


class TestTagRenderedThroughIncludes:
    """`check_metadata_tag_rendered` follows an include it can name statically."""

    def _project(
        self, tmp_path: Path, head: str, partials: dict[str, str]
    ) -> tuple[Path, dict[str, object]]:
        pages = tmp_path / "pages"
        pages.mkdir()
        (pages / "layout.djx").write_text(f"<html><head>{head}</head>{{% template %}}")
        metadata_page(pages / "hello", '{"title": "Hello"}')
        partial_dir = tmp_path / "partials"
        partial_dir.mkdir()
        for name, body in partials.items():
            (partial_dir / name).write_text(body)
        engine = dict(settings.TEMPLATES[0])
        engine["DIRS"] = [*engine.get("DIRS", []), str(partial_dir)]
        return pages, {"TEMPLATES": [engine]}

    def test_a_tag_in_an_included_head_is_silent(self, tmp_path: Path) -> None:
        pages, templates = self._project(
            tmp_path, '{% include "head.html" %}', {"head.html": "{% metadata %}"}
        )
        with (
            override_settings(**templates),
            patch_checks_router_manager(pages_directory=pages),
        ):
            assert check_metadata_tag_rendered() == []

    def test_a_tag_two_includes_deep_is_silent(self, tmp_path: Path) -> None:
        pages, templates = self._project(
            tmp_path,
            '{% include "head.html" %}',
            {"head.html": '{% include "seo.html" %}', "seo.html": "{% metadata %}"},
        )
        with (
            override_settings(**templates),
            patch_checks_router_manager(pages_directory=pages),
        ):
            assert check_metadata_tag_rendered() == []

    def test_an_included_head_without_the_tag_is_w085(self, tmp_path: Path) -> None:
        pages, templates = self._project(
            tmp_path, '{% include "head.html" %}', {"head.html": "<title>x</title>"}
        )
        with (
            override_settings(**templates),
            patch_checks_router_manager(pages_directory=pages),
        ):
            assert check_ids(check_metadata_tag_rendered()) == ["next.W085"]

    @pytest.mark.parametrize(
        ("head", "partials"),
        [
            ("{% include head_template %}", {}),
            ('{% include "head.html"|lower %}', {"head.html": "<title>x</title>"}),
            ('{% include "missing.html" %}', {}),
            ('{% include "broken.html" %}', {"broken.html": "{% if %}"}),
            ('{% include "loop.html" %}', {"loop.html": '{% include "loop.html" %}'}),
        ],
        ids=["variable", "filtered", "missing", "broken", "cycle"],
    )
    def test_an_include_it_cannot_follow_is_silent(
        self, tmp_path: Path, head: str, partials: dict[str, str]
    ) -> None:
        pages, templates = self._project(tmp_path, head, partials)
        with (
            override_settings(**templates),
            patch_checks_router_manager(pages_directory=pages),
        ):
            assert check_metadata_tag_rendered() == []
