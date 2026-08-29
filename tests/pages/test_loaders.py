import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Never
from unittest.mock import MagicMock, patch

import pytest
from django.http import HttpRequest
from django.test import override_settings

import next.pages.loaders as loaders_module
from next.conf import next_framework_settings
from next.pages.loaders import (
    DjxTemplateLoader,
    LayoutTemplateLoader,
    PageModuleImportError,
    PythonTemplateLoader,
    TemplateLoader,
    _load_python_module,
    _load_python_module_memo,
    _page_roots,
    build_registered_loaders,
    has_load_errors,
    last_load_error,
    read_module_string_lists,
    reset_module_memo,
)
from next.pages.processors import _get_context_processors, _import_context_processor
from tests.support import default_page_router_config, file_router_config_entry


class TestPythonTemplateLoader:
    """``PythonTemplateLoader`` reading a ``template`` attribute out of ``page.py``."""

    @pytest.mark.parametrize(
        ("file_content", "expected_can_load", "expected_load_result"),
        [
            ('template = "Hello {{ name }}!"', True, "Hello {{ name }}!"),
            ('print("test")', False, None),
            ("invalid python syntax !!!", False, None),
        ],
        ids=["template_attr", "no_template_attr", "invalid_syntax"],
    )
    def test_can_load_and_load_template(
        self,
        python_template_loader,
        tmp_path,
        file_content,
        expected_can_load,
        expected_load_result,
    ) -> None:
        """Only a ``page.py`` defining ``template`` loads, a broken or bare one does not."""
        page_file = tmp_path / "page.py"
        page_file.write_text(file_content)

        can_load_result = python_template_loader.can_load(page_file)
        load_result = python_template_loader.load_template(page_file)

        assert can_load_result is expected_can_load
        assert load_result == expected_load_result


class TestDjxTemplateLoader:
    """``DjxTemplateLoader`` reading a sibling ``template.djx``."""

    @pytest.mark.parametrize(
        ("create_djx_file", "djx_content", "expected_result"),
        [
            (
                True,
                "<h1>{{ title }}</h1><p>{{ content }}</p>",
                "<h1>{{ title }}</h1><p>{{ content }}</p>",
            ),
            (False, None, None),
        ],
        ids=["with_djx", "without_djx"],
    )
    def test_load_djx_template(
        self,
        djx_template_loader,
        tmp_path,
        create_djx_file,
        djx_content,
        expected_result,
    ) -> None:
        """A sibling ``template.djx`` loads verbatim, its absence yields ``None``."""
        page_file = tmp_path / "page.py"
        page_file.write_text('print("test")')

        if create_djx_file:
            djx_file = tmp_path / "template.djx"
            djx_file.write_text(djx_content)

        result = djx_template_loader.load_template(page_file)

        assert result == expected_result

    @pytest.mark.parametrize(
        ("test_case", "page_content", "create_djx", "djx_content", "expected_template"),
        [
            (
                "djx_template_only",
                'print("test")',
                True,
                "<h1>{{ title }}</h1><p>Hello {{ name }}!</p>",
                "<h1>{{ title }}</h1><p>Hello {{ name }}!</p>",
            ),
            (
                "template_priority",
                'template = "Python template: {{ name }}"',
                True,
                "<h1>DJX template: {{ name }}</h1>",
                "Python template: {{ name }}",
            ),
        ],
        ids=["djx_template_only", "template_priority"],
    )
    def test_create_url_pattern_template_scenarios(
        self,
        page_instance,
        tmp_path,
        url_parser,
        test_case,
        page_content,
        create_djx,
        djx_content,
        expected_template,
    ) -> None:
        """A ``template`` attribute wins over a sibling ``template.djx`` at render time."""
        page_file = tmp_path / "page.py"
        page_file.write_text(page_content)

        if create_djx:
            djx_file = tmp_path / "template.djx"
            djx_file.write_text(djx_content)

        pattern = page_instance.create_url_pattern("test", page_file, url_parser)

        assert pattern is not None
        # Template is loaded lazily at first render, not at create_url_pattern
        result = page_instance.render(page_file, title="Title", name="World")
        expected_rendered = expected_template.replace("{{ title }}", "Title").replace(
            "{{ name }}", "World"
        )
        assert expected_rendered in result

    def test_render_djx_template_with_context(self, page_instance, tmp_path) -> None:
        """A ``template.djx`` body interpolates the keyword arguments passed to render."""
        page_file = tmp_path / "page.py"
        page_file.write_text('print("test")')

        djx_file = tmp_path / "template.djx"
        djx_content = "<h1>{{ title }}</h1><p>Hello {{ name }}!</p>"
        djx_file.write_text(djx_content)

        loader = DjxTemplateLoader()
        if loader.can_load(page_file):
            template_content = loader.load_template(page_file)
            if template_content:
                page_instance.register_template(page_file, template_content)

        result = page_instance.render(page_file, title="Welcome", name="World")

        assert result == "<h1>Welcome</h1><p>Hello World!</p>"

    def test_render_djx_template_with_django_tags(
        self, page_instance, tmp_path
    ) -> None:
        """Django ``if`` and ``for`` tags inside ``template.djx`` execute normally."""
        page_file = tmp_path / "page.py"
        page_file.write_text('print("test")')

        djx_file = tmp_path / "template.djx"
        djx_content = """
        <h1>{{ title }}</h1>
        {% if items %}
            <ul>
            {% for item in items %}
                <li>{{ item }}</li>
            {% endfor %}
            </ul>
        {% else %}
            <p>No items</p>
        {% endif %}
        """
        djx_file.write_text(djx_content)

        loader = DjxTemplateLoader()
        if loader.can_load(page_file):
            template_content = loader.load_template(page_file)
            if template_content:
                page_instance.register_template(page_file, template_content)

        result = page_instance.render(
            page_file, title="Items", items=["Apple", "Banana"]
        )

        assert "Items" in result
        assert "Apple" in result
        assert "Banana" in result
        assert "<li>" in result

    def test_djx_template_with_context_functions(self, page_instance, tmp_path) -> None:
        """A registered ``@context`` key resolves inside a ``template.djx`` body."""
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from next.pages import context

@context("landing")
def get_landing_data(*args, **kwargs):
    return {
        "title": "Test Title",
        "description": "Test Description"
    }
        """)

        djx_file = tmp_path / "template.djx"
        djx_content = "<h1>{{ landing.title }}</h1><p>{{ landing.description }}</p>"
        djx_file.write_text(djx_content)

        loader = DjxTemplateLoader()
        if loader.can_load(page_file):
            template_content = loader.load_template(page_file)
            if template_content:
                page_instance.register_template(page_file, template_content)

        page_instance._context_manager.register_context(
            page_file,
            "landing",
            lambda *args, **kwargs: {
                "title": "Test Title",
                "description": "Test Description",
            },
        )

        result = page_instance.render(page_file)

        assert "<h1>Test Title</h1>" in result
        assert "<p>Test Description</p>" in result


class TestLayoutTemplateLoader:
    """``LayoutTemplateLoader`` discovering and composing the ``layout.djx`` chain."""

    @pytest.mark.parametrize(
        ("create_layout", "create_template", "expected_can_load"),
        [
            (True, True, True),
            (False, True, False),
            (True, False, True),
            (False, False, False),
        ],
        ids=["layout_and_template", "template_only", "layout_only", "neither"],
    )
    def test_can_load_with_layout_files(
        self, tmp_path, create_layout, create_template, expected_can_load
    ) -> None:
        """An ancestor ``layout.djx`` alone makes the page loadable, a lone template does not."""
        loader = LayoutTemplateLoader()

        sub_dir = tmp_path / "sub" / "nested"
        sub_dir.mkdir(parents=True)

        if create_layout:
            layout_file = tmp_path / "layout.djx"
            layout_file.write_text(
                "<html><body>{% block template %}{% endblock template %}</body></html>"
            )

        if create_template:
            template_file = sub_dir / "template.djx"
            template_file.write_text("<h1>Test Content</h1>")

        page_file = sub_dir / "page.py"

        result = loader.can_load(page_file)
        assert result is expected_can_load

    def test_get_additional_layout_files_with_next_pages_config(self, tmp_path) -> None:
        """Layout roots configured on a page backend contribute their ``layout.djx``."""
        loader = LayoutTemplateLoader()

        layout_file = tmp_path / "layout.djx"
        layout_file.write_text("layout content")

        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": default_page_router_config(tmp_path)}
        ):
            result = loader._get_additional_layout_files()

        assert len(result) == 1
        assert layout_file in result

    def test_get_additional_layout_files_when_routers_not_list(self) -> None:
        """When ``ROUTERS`` is not a list, skip scanning (defensive)."""
        loader = LayoutTemplateLoader()

        mock_nf = SimpleNamespace(
            PAGE_BACKENDS="not-a-list", URL_NAME_TEMPLATE="page_{name}"
        )
        with patch("next.pages.loaders.next_framework_settings", mock_nf):
            assert loader._get_additional_layout_files() == []

    @pytest.mark.parametrize(
        ("test_case", "config", "expected_result"),
        [
            (
                "invalid_config",
                [
                    "invalid_config",
                    file_router_config_entry(pages_dir="/nonexistent/path"),
                ],
                [],
            ),
            ("app_dirs_true", [file_router_config_entry(app_dirs=True)], []),
        ],
        ids=["invalid_config", "app_dirs_true"],
    )
    def test_get_additional_layout_files_scenarios(
        self, tmp_path, test_case, config, expected_result
    ) -> None:
        """A malformed entry or a missing directory contributes no layout files."""
        loader = LayoutTemplateLoader()

        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": config}):
            result = loader._get_additional_layout_files()

        assert result == expected_result

    @pytest.mark.parametrize(
        ("test_case", "config", "expected_list"),
        [
            (
                "with_pages_dir",
                file_router_config_entry(pages_dir="test_dir"),
                ["test_dir"],
            ),
            ("with_app_dirs", file_router_config_entry(app_dirs=True), []),
            ("no_options", file_router_config_entry(), []),
        ],
        ids=["with_pages_dir", "with_app_dirs", "no_options"],
    )
    def test_get_pages_dirs_for_config_scenarios(
        self, tmp_path, test_case, config, expected_list
    ) -> None:
        """Only existing ``DIRS`` paths become page roots, ``APP_DIRS`` alone yields none."""
        loader = LayoutTemplateLoader()

        if test_case == "with_pages_dir":
            config["DIRS"] = [str(tmp_path)]
            expected_list = [Path(tmp_path).resolve()]

        result = loader._get_pages_dirs_for_config(config)
        assert result == expected_list

    def test_get_pages_dirs_for_config_empty_when_dirs_missing(self, tmp_path) -> None:
        """Missing ``DIRS`` behaves like an empty list."""
        loader = LayoutTemplateLoader()
        result = loader._get_pages_dirs_for_config({})
        assert result == []

    def test_get_pages_dirs_for_config_string_base_dir(self, tmp_path) -> None:
        """String ``BASE_DIR`` is normalized like in the file router."""
        loader = LayoutTemplateLoader()
        with patch("next.utils.settings") as mock_settings:
            mock_settings.BASE_DIR = str(tmp_path)
            out = loader._get_pages_dirs_for_config({"DIRS": []})
        assert out == []

    def test_get_pages_dirs_for_config_dirs_list(self, tmp_path) -> None:
        """Existing directory paths in ``DIRS`` are resolved."""
        loader = LayoutTemplateLoader()
        config = {"DIRS": [str(tmp_path)]}
        result = loader._get_pages_dirs_for_config(config)
        assert len(result) == 1
        assert result[0] == Path(tmp_path).resolve()

    @pytest.mark.parametrize(
        (
            "test_case",
            "create_layout",
            "create_template",
            "template_content",
            "expected_result",
        ),
        [
            (
                "with_local_layout",
                True,
                True,
                "<h1>Test Content</h1>",
                "<h1>Test Content</h1>",
            ),
            (
                "without_local_layout",
                False,
                True,
                "<h1>Test Content</h1>",
                "{% block template %}<h1>Test Content</h1>{% endblock template %}",
            ),
            (
                "no_template_file",
                False,
                False,
                None,
                "{% block template %}{% endblock template %}",
            ),
        ],
        ids=["with_local_layout", "without_local_layout", "no_template_file"],
    )
    def test_wrap_in_template_block_scenarios(
        self,
        tmp_path,
        test_case,
        create_layout,
        create_template,
        template_content,
        expected_result,
    ) -> None:
        """A body is wrapped in a ``template`` block only when no local layout owns it."""
        loader = LayoutTemplateLoader()

        if create_layout:
            layout_file = tmp_path / "layout.djx"
            layout_file.write_text("layout content")

        if create_template:
            template_file = tmp_path / "template.djx"
            template_file.write_text(template_content)

        page_file = tmp_path / "page.py"
        result = loader._wrap_in_template_block(page_file)

        assert result == expected_result

    def test_find_layout_files_with_duplicate_additional_layouts(
        self, tmp_path
    ) -> None:
        """A configured root that repeats the local layout is not counted twice."""
        loader = LayoutTemplateLoader()

        layout_file = tmp_path / "layout.djx"
        layout_file.write_text("layout content")

        template_file = tmp_path / "template.djx"
        template_file.write_text("template content")

        page_file = tmp_path / "page.py"

        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": default_page_router_config(tmp_path)}
        ):
            result = loader._find_layout_files(page_file)

        assert len(result) == 1
        assert layout_file in result

    def test_get_additional_layout_files_with_duplicate_layouts(self, tmp_path) -> None:
        """Two backends pointing at one root yield that root's layout once."""
        loader = LayoutTemplateLoader()

        layout_file = tmp_path / "layout.djx"
        layout_file.write_text("layout content")

        config = default_page_router_config(tmp_path) + default_page_router_config(
            tmp_path
        )

        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": config}):
            result = loader._get_additional_layout_files()

        assert len(result) == 1
        assert layout_file in result

    def test_find_layout_files_with_additional_layouts_already_present(
        self, tmp_path
    ) -> None:
        """A configured root above the page adds nothing the local walk already found."""
        loader = LayoutTemplateLoader()

        parent_dir = tmp_path / "parent"
        parent_dir.mkdir()
        local_layout = parent_dir / "layout.djx"
        local_layout.write_text("local layout")

        child_dir = parent_dir / "child"
        child_dir.mkdir()
        template_file = child_dir / "template.djx"
        template_file.write_text("template content")

        page_file = child_dir / "page.py"

        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": default_page_router_config(parent_dir)}
        ):
            result = loader._find_layout_files(page_file)

        assert len(result) == 1
        assert local_layout in result

    def test_find_layout_files_with_different_additional_layouts(
        self, tmp_path
    ) -> None:
        """A configured root outside the page hierarchy adds its own layout to the chain."""
        loader = LayoutTemplateLoader()

        local_layout = tmp_path / "layout.djx"
        local_layout.write_text("local layout")

        template_file = tmp_path / "template.djx"
        template_file.write_text("template content")

        page_file = tmp_path / "page.py"

        additional_dir = tmp_path / "additional"
        additional_dir.mkdir()
        additional_layout = additional_dir / "layout.djx"
        additional_layout.write_text("additional layout")

        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": default_page_router_config(additional_dir)}
        ):
            result = loader._find_layout_files(page_file)

        assert len(result) == 2
        assert local_layout in result
        assert additional_layout in result

    def test_load_template_with_single_layout(self, tmp_path) -> None:
        """One ancestor layout wraps the body and keeps its ``template`` block."""
        loader = LayoutTemplateLoader()

        layout_file = tmp_path / "layout.djx"
        layout_content = (
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )
        layout_file.write_text(layout_content)

        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        template_file = sub_dir / "template.djx"
        template_content = "<h1>Test Content</h1>"
        template_file.write_text(template_content)

        page_file = sub_dir / "page.py"
        result = loader.load_template(page_file)

        assert result is not None
        assert template_content in result
        assert "<html><body>" in result
        assert "</body></html>" in result
        assert "{% block template %}" in result

    def test_load_template_with_multiple_layouts(self, tmp_path) -> None:
        """Nested layouts compose outermost first, with the body innermost."""
        loader = LayoutTemplateLoader()

        root_layout = tmp_path / "layout.djx"
        root_layout.write_text(
            "<html><head><title>Root</title></head><body>{% block template %}{% endblock template %}</body></html>"
        )

        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        sub_layout = sub_dir / "layout.djx"
        sub_layout.write_text(
            "<div class='sub-layout'>{% block template %}{% endblock template %}</div>"
        )

        nested_dir = sub_dir / "nested"
        nested_dir.mkdir()
        template_file = nested_dir / "template.djx"
        template_content = "<h1>Test Content</h1>"
        template_file.write_text(template_content)

        page_file = nested_dir / "page.py"
        result = loader.load_template(page_file)

        assert result is not None
        assert template_content in result
        assert "<html><head><title>Root</title></head>" in result
        assert "<div class='sub-layout'>" in result
        assert "{% block template %}" in result

    def test_load_template_without_template_djx(self, tmp_path) -> None:
        """A layout with no body behind it composes to an empty ``template`` block."""
        loader = LayoutTemplateLoader()

        layout_file = tmp_path / "layout.djx"
        layout_file.write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>"
        )

        page_file = tmp_path / "page.py"

        result = loader.load_template(page_file)

        assert result is not None
        assert "<html><body>" in result
        assert "</body></html>" in result
        assert "{% block template %}{% endblock template %}" in result

    def test_load_template_layout_accepts_unnamed_endblock(self, tmp_path) -> None:
        """Compose works when layout uses {% endblock %} instead of {% endblock template %}."""
        loader = LayoutTemplateLoader()
        layout_file = tmp_path / "layout.djx"
        layout_file.write_text(
            "<html><body>{% block template %}{% endblock %}</body></html>"
        )
        page_file = tmp_path / "page.py"
        result = loader.load_template(page_file)
        assert result is not None
        assert "<html><body>" in result
        assert "</body></html>" in result
        assert "{% block template %}" in result
        assert "{% block template %}{% endblock template %}" in result

    def test_find_layout_files(self, tmp_path) -> None:
        """The walk collects every ``layout.djx`` from the page up to the tree root."""
        loader = LayoutTemplateLoader()

        sub_dir = tmp_path / "sub" / "nested"
        sub_dir.mkdir(parents=True)

        root_layout = tmp_path / "layout.djx"
        root_layout.write_text("root layout")

        sub_layout = tmp_path / "sub" / "layout.djx"
        sub_layout.write_text("sub layout")

        page_file = sub_dir / "page.py"
        layout_files = loader._find_layout_files(page_file)

        assert len(layout_files) == 2
        assert sub_layout in layout_files
        assert root_layout in layout_files

    def test_layout_sources_reports_every_walked_directory(self, tmp_path) -> None:
        """A page under no routed tree hands back every directory it visited."""
        loader = LayoutTemplateLoader()

        sub_dir = tmp_path / "sub" / "nested"
        sub_dir.mkdir(parents=True)
        layout = tmp_path / "sub" / "layout.djx"
        layout.write_text("sub layout")

        layout_files, watched_dirs = loader.layout_sources(sub_dir / "page.py")

        assert layout_files == [layout]
        assert watched_dirs[:3] == [sub_dir, tmp_path / "sub", tmp_path]
        assert watched_dirs[-1].parent == watched_dirs[-1].parent.parent

    def test_the_watched_directories_stop_at_the_page_root(self, tmp_path) -> None:
        """A page inside a routed tree watches no directory above that tree.

        The layout above still joins the chain, only the mtime watch stops, so
        an unrelated write to a shared parent evicts no composition.
        """
        loader = LayoutTemplateLoader()

        root = tmp_path / "site"
        page_dir = root / "sub"
        page_dir.mkdir(parents=True)
        outer_layout = tmp_path / "layout.djx"
        outer_layout.write_text("layout above the tree")
        root_layout = root / "layout.djx"
        root_layout.write_text("root layout")

        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": default_page_router_config(root)}
        ):
            layout_files, watched_dirs = loader.layout_sources(page_dir / "page.py")

        assert layout_files == [root_layout, outer_layout]
        assert watched_dirs == [page_dir, root]

    def test_the_nearest_page_root_bounds_the_watched_directories(
        self, tmp_path
    ) -> None:
        """Two routed trees on one path watch only up to the inner one."""
        loader = LayoutTemplateLoader()

        outer = tmp_path / "site"
        inner = outer / "admin"
        inner.mkdir(parents=True)
        config = default_page_router_config(outer) + default_page_router_config(inner)

        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": config}):
            _, watched_dirs = loader.layout_sources(inner / "page.py")

        assert watched_dirs == [inner]

    def test_the_memoised_page_roots_survive_a_second_walk(self, tmp_path) -> None:
        """The routers are built once, so the second walk reads the memo."""
        root = tmp_path / "site"
        root.mkdir()

        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": default_page_router_config(root)}
        ):
            first = _page_roots()
            second = _page_roots()

        assert first == second == (root,)

    def test_an_app_list_change_drops_the_memoised_page_roots(self) -> None:
        """An `APP_DIRS` router routes new trees when the app list moves."""
        _page_roots()

        with override_settings(INSTALLED_APPS=["django.contrib.contenttypes"]):
            assert loaders_module._PAGE_ROOTS_CACHE["value"] is None

    @pytest.mark.parametrize(
        "layouts",
        [[], ["."], [".."], [".", "..", "../.."]],
        ids=["none", "sibling", "ancestor", "chain"],
    )
    @pytest.mark.parametrize(
        "body",
        ["<p>b</p>", "", "{% block template %}{% endblock template %}"],
        ids=["body", "empty", "placeholder"],
    )
    def test_filled_skeleton_equals_a_direct_compose(
        self, tmp_path, layouts, body
    ) -> None:
        """Filling the cached skeleton reproduces `compose_body` character for character."""
        loader = LayoutTemplateLoader()

        page_dir = tmp_path / "a" / "b"
        page_dir.mkdir(parents=True)
        page_file = page_dir / "page.py"
        page_file.write_text("x = 1")
        for index, relative in enumerate(layouts):
            target = (page_dir / relative).resolve() / "layout.djx"
            target.write_text(
                f'<div class="l{index}">'
                "{% block template %}{% endblock template %}</div>"
            )

        skeleton = loader.compose_skeleton(page_file)

        assert loader.fill_skeleton(skeleton, body) == loader.compose_body(
            body, page_file
        )

    def test_compose_layout_hierarchy_exception_handling(self, tmp_path) -> None:
        """A layout that cannot be read leaves the body unwrapped instead of raising."""
        loader = LayoutTemplateLoader()

        layout_file = tmp_path / "layout.djx"
        layout_file.write_text("test")

        template_file = tmp_path / "template.djx"
        template_file.write_text("test")

        with patch("pathlib.Path.read_text", side_effect=OSError("Mocked error")):
            result = loader._compose_layout_hierarchy("test content", [layout_file])
            assert result == "test content"

    def test_load_template_no_layout_files(self, tmp_path) -> None:
        """A page with no layout above it yields ``None``."""
        loader = LayoutTemplateLoader()

        page_file = tmp_path / "page.py"
        page_file.write_text("template = 'test'")

        result = loader.load_template(page_file)
        assert result is None


class TestContextProcessors:
    """Resolving context processors from page backends and from ``TEMPLATES``."""

    def test_get_context_processors_empty_config(self, page_instance) -> None:
        """No backends and no ``TEMPLATES`` resolve to no processors."""
        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": []}, TEMPLATES=[]):
            processors = _get_context_processors()
            assert processors == []

    def test_get_context_processors_routers_not_list(self, page_instance) -> None:
        """When ``PAGE_BACKENDS`` is not a list, treat as no router config."""
        mock_nf = SimpleNamespace(PAGE_BACKENDS={})
        with (
            patch("next.pages.processors.next_framework_settings", mock_nf),
            override_settings(TEMPLATES=[]),
        ):
            processors = _get_context_processors()
            assert processors == []

    def test_get_context_processors_no_context_processors(self, page_instance) -> None:
        """A backend that declares no processors contributes none."""
        config = [file_router_config_entry(app_dirs=True)]
        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": config}, TEMPLATES=[]):
            processors = _get_context_processors()
            assert processors == []

    def test_get_context_processors_inherits_from_templates(
        self, page_instance
    ) -> None:
        """A backend silent on processors falls back to the ``TEMPLATES`` list."""

        def test_processor(request):
            return {"test_var": "test_value"}

        def auth_processor(request):
            return {"user": MagicMock()}

        templates_config = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "context_processors": [
                        "test_app.context_processors.test_processor",
                        "test_app.context_processors.auth_processor",
                    ]
                },
            }
        ]

        next_pages_config = [file_router_config_entry(app_dirs=True)]

        with patch("next.pages.processors.import_string") as mock_import:
            mock_import.side_effect = [test_processor, auth_processor]

            with override_settings(
                TEMPLATES=templates_config,
                NEXT_FRAMEWORK={"PAGE_BACKENDS": next_pages_config},
            ):
                processors = _get_context_processors()
                assert len(processors) == 2
                assert processors[0] == test_processor
                assert processors[1] == auth_processor

    def test_get_context_processors_merges_next_pages_and_templates(
        self, page_instance
    ) -> None:
        """When both routers and TEMPLATES set context_processors, merge (routers first)."""

        def template_processor(request):
            return {"template_var": "template_value"}

        def next_pages_processor(request):
            return {"next_var": "next_value"}

        templates_config = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "context_processors": [
                        "test_app.context_processors.template_processor"
                    ]
                },
            }
        ]
        next_pages_config = [
            file_router_config_entry(
                app_dirs=True,
                options={
                    "context_processors": [
                        "test_app.context_processors.next_pages_processor"
                    ]
                },
            )
        ]

        with patch("next.pages.processors.import_string") as mock_import:
            mock_import.side_effect = [next_pages_processor, template_processor]
            with override_settings(
                TEMPLATES=templates_config,
                NEXT_FRAMEWORK={"PAGE_BACKENDS": next_pages_config},
            ):
                processors = _get_context_processors()
                assert len(processors) == 2
                assert processors[0] == next_pages_processor
                assert processors[1] == template_processor

    def test_get_context_processors_deduplicates_by_path(self, page_instance) -> None:
        """Same path in routers and TEMPLATES appears once (first occurrence wins)."""
        shared_path = "test_app.context_processors.shared_processor"

        def shared_processor(request):
            return {"shared": True}

        templates_config = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"context_processors": [shared_path]},
            }
        ]
        next_pages_config = [
            file_router_config_entry(
                app_dirs=True, options={"context_processors": [shared_path]}
            )
        ]
        with (
            patch("next.pages.processors.import_string", return_value=shared_processor),
            override_settings(
                TEMPLATES=templates_config,
                NEXT_FRAMEWORK={"PAGE_BACKENDS": next_pages_config},
            ),
        ):
            processors = _get_context_processors()
            assert len(processors) == 1
            assert processors[0] == shared_processor

    def test_get_context_processors_fallback_empty_templates(
        self, page_instance
    ) -> None:
        """With empty TEMPLATES and no router processors, result is empty."""
        with override_settings(TEMPLATES=[], NEXT_FRAMEWORK={"PAGE_BACKENDS": []}):
            result = _get_context_processors()
            assert result == []

    def test_get_context_processors_fallback_non_list(self, page_instance) -> None:
        """When TEMPLATES context_processors is not a list, fallback yields empty."""
        templates_config = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"context_processors": "not_a_list"},
            }
        ]
        with override_settings(
            TEMPLATES=templates_config, NEXT_FRAMEWORK={"PAGE_BACKENDS": []}
        ):
            result = _get_context_processors()
            assert result == []

    def test_get_context_processors_with_valid_processors(self, page_instance) -> None:
        """Declared processors are imported and kept in declaration order."""

        def test_processor(request):
            return {"test_var": "test_value"}

        def another_processor(request):
            return {"another_var": "another_value"}

        with patch("next.pages.processors.import_string") as mock_import:
            mock_import.side_effect = [test_processor, another_processor]

            config = [
                file_router_config_entry(
                    app_dirs=True,
                    options={
                        "context_processors": [
                            "test_app.context_processors.test_processor",
                            "test_app.context_processors.another_processor",
                        ]
                    },
                )
            ]

            with override_settings(
                NEXT_FRAMEWORK={"PAGE_BACKENDS": config}, TEMPLATES=[]
            ):
                processors = _get_context_processors()
                assert len(processors) == 2
                assert processors[0] == test_processor
                assert processors[1] == another_processor

    def test_get_context_processors_with_invalid_processor(self, page_instance) -> None:
        """An unimportable path is warned about and dropped, the rest still load."""
        config = [
            file_router_config_entry(
                app_dirs=True,
                options={
                    "context_processors": [
                        "invalid.module.path",
                        "django.template.context_processors.request",
                    ]
                },
            )
        ]

        with (
            override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": config}),
            patch("next.pages.processors.import_string") as mock_import,
            patch("next.pages.processors.logger.warning") as mock_warning,
        ):
            mock_import.side_effect = [
                ImportError("No module named 'invalid'"),
                lambda request: {"request": request},
            ]
            processors = _get_context_processors()
            real_processors = [
                p for p in processors if callable(p) and not hasattr(p, "_mock_name")
            ]
            assert len(real_processors) == 1
            mock_warning.assert_called_once()

    def test_import_context_processor_non_callable(self, page_instance) -> None:
        """A dotted path resolving to a non-callable yields ``None``."""
        with patch("next.pages.processors.import_string") as mock_import:
            mock_import.return_value = "not a callable"

            processor = _import_context_processor("some.module.path")
            assert processor is None

    def test_render_with_context_processors(self, page_instance, tmp_path) -> None:
        """Processor output reaches the template alongside the render keyword arguments."""
        page_file = tmp_path / "page.py"
        template_str = "<h1>{{ title }}</h1><p>{{ request_var }}</p>"
        page_instance.register_template(page_file, template_str)

        mock_request = HttpRequest()
        mock_request.META = {}

        def test_processor(request):
            return {"request_var": "from_processor"}

        with patch(
            "next.pages.manager._get_context_processors", return_value=[test_processor]
        ):
            result = page_instance.render(page_file, mock_request, title="Test Title")

            assert "Test Title" in result
            assert "from_processor" in result

    def test_render_without_request_object(self, page_instance, tmp_path) -> None:
        """Without a request the processors never run."""
        page_file = tmp_path / "page.py"
        template_str = "<h1>{{ title }}</h1>"
        page_instance.register_template(page_file, template_str)

        def test_processor(request):
            return {"request_var": "from_processor"}

        with patch(
            "next.pages.manager._get_context_processors", return_value=[test_processor]
        ):
            result = page_instance.render(page_file, title="Test Title")

            assert result == "<h1>Test Title</h1>"
            assert "from_processor" not in result

    def test_render_without_context_processors(self, page_instance, tmp_path) -> None:
        """An empty processor list renders through a plain context."""
        page_file = tmp_path / "page.py"
        template_str = "<h1>{{ title }}</h1>"
        page_instance.register_template(page_file, template_str)

        mock_request = HttpRequest()
        mock_request.META = {}

        with patch("next.pages.manager._get_context_processors", return_value=[]):
            result = page_instance.render(page_file, mock_request, title="Test Title")

            assert result == "<h1>Test Title</h1>"

    def test_render_with_context_processor_error(self, page_instance, tmp_path) -> None:
        """A raising processor is warned about and skipped, later ones still apply."""
        page_file = tmp_path / "page.py"
        template_str = "<h1>{{ title }}</h1><p>{{ good_var }}</p>"
        page_instance.register_template(page_file, template_str)

        mock_request = HttpRequest()
        mock_request.META = {}

        def error_processor(request) -> Never:
            msg = "Test error"
            raise ValueError(msg)

        def good_processor(request):
            return {"good_var": "good_value"}

        with (
            patch(
                "next.pages.manager._get_context_processors",
                return_value=[error_processor, good_processor],
            ),
            patch("next.pages.manager.logger") as mock_logger,
        ):
            result = page_instance.render(page_file, mock_request, title="Test Title")

            assert "Test Title" in result
            assert "good_value" in result
            mock_logger.warning.assert_called_once()

    def test_strict_context_reraises_processor_error(
        self, page_instance, tmp_path
    ) -> None:
        """`STRICT_CONTEXT=True` turns processor errors into hard failures."""
        page_file = tmp_path / "page.py"
        page_instance.register_template(page_file, "<h1>{{ title }}</h1>")

        mock_request = HttpRequest()
        mock_request.META = {}

        def error_processor(request) -> Never:
            msg = "boom"
            raise ValueError(msg)

        with (
            override_settings(NEXT_FRAMEWORK={"STRICT_CONTEXT": True}),
            patch(
                "next.pages.manager._get_context_processors",
                return_value=[error_processor],
            ),
            pytest.raises(ValueError, match="boom"),
        ):
            page_instance.render(page_file, mock_request, title="Test Title")

    def test_render_with_context_processor_non_dict_return(
        self, page_instance, tmp_path
    ) -> None:
        """A processor returning a non-mapping is ignored, later ones still apply."""
        page_file = tmp_path / "page.py"
        template_str = "<h1>{{ title }}</h1><p>{{ good_var }}</p>"
        page_instance.register_template(page_file, template_str)

        mock_request = HttpRequest()
        mock_request.META = {}

        def non_dict_processor(request) -> str:
            return "not a dict"

        def good_processor(request):
            return {"good_var": "good_value"}

        with patch(
            "next.pages.manager._get_context_processors",
            return_value=[non_dict_processor, good_processor],
        ):
            result = page_instance.render(page_file, mock_request, title="Test Title")

            assert "Test Title" in result
            assert "good_value" in result


class TestTemplateLoaderContract:
    """`TemplateLoader` exposes `source_name` and a default `source_path`."""

    def test_built_in_source_names(self) -> None:
        assert DjxTemplateLoader.source_name == "template.djx"
        assert PythonTemplateLoader.source_name == "template"
        assert LayoutTemplateLoader.source_name == ""

    def test_djx_source_path_returns_sibling_when_exists(self, tmp_path: Path) -> None:
        page_file = tmp_path / "page.py"
        djx = tmp_path / "template.djx"
        djx.write_text("<h1>hi</h1>")
        assert DjxTemplateLoader().source_path(page_file) == djx

    def test_djx_source_path_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert DjxTemplateLoader().source_path(tmp_path / "page.py") is None

    def test_default_source_path_is_none(self, tmp_path: Path) -> None:
        """Custom loaders that do not back a file return None by default."""

        class Stub(TemplateLoader):
            source_name = "stub"

            def can_load(self, _: Path) -> bool:
                return False

            def load_template(self, _: Path) -> str | None:
                return None

        assert Stub().source_path(tmp_path / "page.py") is None


class TestReadModuleStringLists:
    """`read_module_string_lists` is the narrow read the static area needs."""

    def test_returns_one_list_per_requested_name(self, tmp_path: Path) -> None:
        module_file = tmp_path / "page.py"
        module_file.write_text('styles = ["a.css"]\nscripts = ["b.js", "c.js"]\n')
        assert read_module_string_lists(module_file, ["styles", "scripts"]) == {
            "styles": ["a.css"],
            "scripts": ["b.js", "c.js"],
        }

    def test_an_unknown_name_reads_as_an_empty_list(self, tmp_path: Path) -> None:
        module_file = tmp_path / "page.py"
        module_file.write_text("x = 1\n")
        assert read_module_string_lists(module_file, ["styles"]) == {"styles": []}

    def test_a_non_sequence_and_its_junk_entries_are_dropped(
        self, tmp_path: Path
    ) -> None:
        module_file = tmp_path / "page.py"
        module_file.write_text('styles = "a.css"\nscripts = ["b.js", 3, ""]\n')
        assert read_module_string_lists(module_file, ["styles", "scripts"]) == {
            "styles": [],
            "scripts": ["b.js"],
        }

    def test_a_module_that_does_not_load_answers_none(self, tmp_path: Path) -> None:
        """`None` tells an absent or broken module apart from an empty one."""
        assert read_module_string_lists(tmp_path / "missing.py", ["styles"]) is None

    def test_no_names_asked_for_still_reports_the_module_loaded(
        self, tmp_path: Path
    ) -> None:
        module_file = tmp_path / "page.py"
        module_file.write_text("x = 1\n")
        assert read_module_string_lists(module_file, []) == {}


class TestBuildRegisteredLoaders:
    """`build_registered_loaders` reads `TEMPLATE_LOADERS` and caches."""

    def _reset_cache(self) -> None:
        # the cache is a single-slot holder mutated in place, never rebound,
        # so a stale None on this worker cannot break the production reads
        loaders_module._REGISTERED_LOADERS_CACHE["value"] = None

    def setup_method(self) -> None:
        self._reset_cache()

    def teardown_method(self) -> None:
        self._reset_cache()

    def test_default_list_loads_djx(self) -> None:
        loaders = build_registered_loaders()
        assert [type(loader) for loader in loaders] == [DjxTemplateLoader]

    @override_settings(
        NEXT_FRAMEWORK={
            "TEMPLATE_LOADERS": [
                "next.pages.loaders.DjxTemplateLoader",
                "next.pages.loaders.PythonTemplateLoader",
            ]
        }
    )
    def test_user_list_replaces_default(self) -> None:
        self._reset_cache()
        loaders = build_registered_loaders()
        assert [type(loader) for loader in loaders] == [
            DjxTemplateLoader,
            PythonTemplateLoader,
        ]

    @override_settings(
        NEXT_FRAMEWORK={
            "TEMPLATE_LOADERS": [
                123,
                "does.not.exist.Loader",
                "next.pages.registry.PageContextRegistry",
                "next.pages.loaders.DjxTemplateLoader",
            ]
        }
    )
    def test_invalid_entries_are_skipped(self) -> None:
        self._reset_cache()
        loaders = build_registered_loaders()
        assert [type(loader) for loader in loaders] == [DjxTemplateLoader]

    def test_settings_reload_resets_cache(self) -> None:
        build_registered_loaders()

        assert loaders_module._REGISTERED_LOADERS_CACHE["value"] is not None
        next_framework_settings.reload()
        assert loaders_module._REGISTERED_LOADERS_CACHE["value"] is None

    @override_settings(
        NEXT_FRAMEWORK={
            "TEMPLATE_LOADERS": [
                "next.pages.loaders.DjxTemplateLoader",
                "next.pages.loaders.DjxTemplateLoader",
            ]
        }
    )
    def test_duplicate_entries_registered_once(self) -> None:
        """A loader class appears at most once even when listed multiple times."""
        self._reset_cache()
        loaders = build_registered_loaders()
        assert [type(loader) for loader in loaders] == [DjxTemplateLoader]


def _loader_records(caplog, level: int) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == "next.pages.loaders" and r.levelno == level
    ]


class TestPageModuleImportErrors:
    """`_load_python_module` records broken imports for `last_load_error`."""

    def test_syntax_error_records_error_and_logs_exception(
        self, tmp_path, caplog
    ) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("def render( invalid syntax {\n")

        with caplog.at_level(logging.DEBUG, logger="next.pages.loaders"):
            result = _load_python_module(page_file)

        assert result is None
        assert len(_loader_records(caplog, logging.ERROR)) == 1
        error = last_load_error(page_file)
        assert isinstance(error, PageModuleImportError)
        assert error.file_path == page_file
        assert isinstance(error.__cause__, SyntaxError)
        assert str(error) == f"{page_file} failed to import"

    def test_missing_dependency_records_module_not_found_cause(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("import missing_dep_xyz\n")

        assert _load_python_module(page_file) is None
        error = last_load_error(page_file)
        assert isinstance(error, PageModuleImportError)
        assert type(error.__cause__) is ModuleNotFoundError
        assert "missing_dep_xyz" in str(error.__cause__)

    def test_attribute_error_in_module_body_is_recorded(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("import os\nos.definitely_missing_attribute\n")

        assert _load_python_module(page_file) is None
        error = last_load_error(page_file)
        assert isinstance(error, PageModuleImportError)
        assert isinstance(error.__cause__, AttributeError)

    @pytest.mark.parametrize(
        ("source", "cause_type"),
        [
            ("1 / 0\n", ZeroDivisionError),
            ("undefined_name_xyz\n", NameError),
            ('raise OSError("body io failure")\n', OSError),
        ],
        ids=["zero_division", "name_error", "os_error"],
    )
    def test_any_body_exception_is_recorded(
        self, tmp_path, source: str, cause_type: type[Exception]
    ) -> None:
        # The exec boundary is not a closed exception list, so exotic body
        # failures record instead of drowning as an absent module.
        page_file = tmp_path / "page.py"
        page_file.write_text(source)

        assert _load_python_module(page_file) is None
        error = last_load_error(page_file)
        assert isinstance(error, PageModuleImportError)
        assert isinstance(error.__cause__, cause_type)

    def test_spec_creation_oserror_stays_quiet_on_debug_level(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1\n")

        def raising(name: str, location: object) -> Never:
            raise OSError(name)

        monkeypatch.setattr(
            loaders_module.importlib.util, "spec_from_file_location", raising
        )
        with caplog.at_level(logging.DEBUG, logger="next.pages.loaders"):
            result = _load_python_module(page_file)

        assert result is None
        assert last_load_error(page_file) is None
        assert _loader_records(caplog, logging.ERROR) == []
        assert len(_loader_records(caplog, logging.DEBUG)) == 1

    def test_nonexistent_file_stays_quiet_on_debug_level(
        self, tmp_path, caplog
    ) -> None:
        missing = tmp_path / "page.py"

        with caplog.at_level(logging.DEBUG, logger="next.pages.loaders"):
            result = _load_python_module(missing)

        assert result is None
        assert last_load_error(missing) is None
        assert _loader_records(caplog, logging.ERROR) == []
        assert len(_loader_records(caplog, logging.DEBUG)) == 1

    def test_successful_reload_after_fix_clears_error(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("def render( invalid syntax {\n")
        assert _load_python_module_memo(page_file) is None
        assert last_load_error(page_file) is not None

        stamp = page_file.stat().st_mtime + 10
        page_file.write_text('template = "fixed"\n')
        os.utime(page_file, (stamp, stamp))

        module = _load_python_module_memo(page_file)
        assert module is not None
        assert module.template == "fixed"
        assert last_load_error(page_file) is None
        assert page_file not in loaders_module._LAST_LOAD_ERROR

    def test_memo_does_not_reexec_broken_module(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("def render( invalid syntax {\n")

        real_load = loaders_module._load_python_module
        calls: list[Path] = []

        def counting(file_path: Path) -> object:
            calls.append(file_path)
            return real_load(file_path)

        monkeypatch.setattr(loaders_module, "_load_python_module", counting)

        with caplog.at_level(logging.ERROR, logger="next.pages.loaders"):
            assert _load_python_module_memo(page_file) is None
            assert _load_python_module_memo(page_file) is None

        assert calls == [page_file]
        assert len(_loader_records(caplog, logging.ERROR)) == 1
        assert last_load_error(page_file) is not None

    def test_memo_reexecutes_on_mtime_change_and_updates_error(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("def render( invalid syntax {\n")
        assert _load_python_module_memo(page_file) is None
        first = last_load_error(page_file)
        assert isinstance(first.__cause__, SyntaxError)

        stamp = page_file.stat().st_mtime + 10
        page_file.write_text("import missing_dep_xyz\n")
        os.utime(page_file, (stamp, stamp))

        assert _load_python_module_memo(page_file) is None
        second = last_load_error(page_file)
        assert type(second.__cause__) is ModuleNotFoundError

    def test_last_load_error_stale_mtime_returns_none(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("def render( invalid syntax {\n")
        assert _load_python_module(page_file) is None
        assert last_load_error(page_file) is not None

        # The file is fixed on disk but the memo has not re-read it yet.
        stamp = page_file.stat().st_mtime + 10
        page_file.write_text('template = "fixed"\n')
        os.utime(page_file, (stamp, stamp))

        assert last_load_error(page_file) is None

    def test_last_load_error_missing_file_returns_none(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("def render( invalid syntax {\n")
        assert _load_python_module(page_file) is None
        assert last_load_error(page_file) is not None

        page_file.unlink()
        assert last_load_error(page_file) is None

    def test_a_record_the_file_outlived_stops_arming_the_probe(self, tmp_path) -> None:
        """A dead record is dropped, so the per-request gate goes quiet again."""
        # The gate reads a process-wide store, so it answers for this file only
        # once nothing else is on record.
        reset_module_memo()
        page_file = tmp_path / "page.py"
        page_file.write_text("def render( invalid syntax {\n")
        assert _load_python_module(page_file) is None
        assert has_load_errors() is True

        page_file.unlink()
        assert last_load_error(page_file) is None
        assert has_load_errors() is False

    def test_record_load_error_without_mtime_drops_entry(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("def render( invalid syntax {\n")
        assert _load_python_module(page_file) is None
        assert page_file in loaders_module._LAST_LOAD_ERROR

        # A file gone before the pre-exec stat has no mtime to key an entry by.
        loaders_module._record_load_error(page_file, ValueError("boom"), None)
        assert page_file not in loaders_module._LAST_LOAD_ERROR

    def test_reset_module_memo_clears_recorded_errors(self, tmp_path) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("def render( invalid syntax {\n")
        assert _load_python_module_memo(page_file) is None
        assert last_load_error(page_file) is not None

        reset_module_memo()

        assert loaders_module._LAST_LOAD_ERROR == {}
        assert page_file not in loaders_module._MODULE_MEMO
        assert last_load_error(page_file) is None

    def test_last_load_error_returns_fresh_instance_per_call(self, tmp_path) -> None:
        # A shared instance would grow its traceback on every re-raise, so
        # each call must wrap the one recorded cause in a new error object.
        page_file = tmp_path / "page.py"
        page_file.write_text("def render( invalid syntax {\n")
        assert _load_python_module(page_file) is None

        first = last_load_error(page_file)
        second = last_load_error(page_file)

        assert isinstance(first, PageModuleImportError)
        assert isinstance(second, PageModuleImportError)
        assert first is not second
        assert first.__cause__ is second.__cause__
