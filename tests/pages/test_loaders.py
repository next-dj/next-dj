"""Tests for next.pages.loaders."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.http import HttpRequest
from django.test import override_settings

from next.conf import next_framework_settings
from next.pages.loaders import (
    DjxTemplateLoader,
    LayoutTemplateLoader,
    PythonTemplateLoader,
    TemplateLoader,
    build_registered_loaders,
)
from next.pages.processors import _get_context_processors, _import_context_processor
from tests.support import (
    default_page_router_config,
    file_router_config_entry,
)


class TestPythonTemplateLoader:
    """Test cases for Python template loader."""

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
        """Test can_load and load_template with different file contents."""
        page_file = tmp_path / "page.py"
        page_file.write_text(file_content)

        can_load_result = python_template_loader.can_load(page_file)
        load_result = python_template_loader.load_template(page_file)

        assert can_load_result is expected_can_load
        assert load_result == expected_load_result


class TestDjxTemplateLoader:
    """Test cases for DJX template loader."""

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
        """Test loading of template.djx template with different scenarios."""
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
        """Test create_url_pattern with different template scenarios."""
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
        """Test rendering template.djx template with context."""
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
        """Test rendering template.djx template with Django tags."""
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
            page_file,
            title="Items",
            items=["Apple", "Banana"],
        )

        assert "Items" in result
        assert "Apple" in result
        assert "Banana" in result
        assert "<li>" in result

    def test_djx_template_with_context_functions(self, page_instance, tmp_path) -> None:
        """Test template.djx template with context functions."""
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
    """Test cases for LayoutTemplateLoader."""

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
        self,
        tmp_path,
        create_layout,
        create_template,
        expected_can_load,
    ) -> None:
        """Test can_load with different layout and template combinations."""
        loader = LayoutTemplateLoader()

        sub_dir = tmp_path / "sub" / "nested"
        sub_dir.mkdir(parents=True)

        if create_layout:
            layout_file = tmp_path / "layout.djx"
            layout_file.write_text(
                "<html><body>{% block template %}{% endblock template %}</body></html>",
            )

        if create_template:
            template_file = sub_dir / "template.djx"
            template_file.write_text("<h1>Test Content</h1>")

        page_file = sub_dir / "page.py"

        result = loader.can_load(page_file)
        assert result is expected_can_load

    def test_get_additional_layout_files_with_next_pages_config(self, tmp_path) -> None:
        """Test _get_additional_layout_files with ``NEXT['PAGES']['ROUTERS']``."""
        loader = LayoutTemplateLoader()

        layout_file = tmp_path / "layout.djx"
        layout_file.write_text("layout content")

        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": default_page_router_config(tmp_path)
            },
        ):
            next_framework_settings.reload()
            result = loader._get_additional_layout_files()

        assert len(result) == 1
        assert layout_file in result

    def test_get_additional_layout_files_when_routers_not_list(self) -> None:
        """When ``ROUTERS`` is not a list, skip scanning (defensive)."""
        loader = LayoutTemplateLoader()

        mock_nf = SimpleNamespace(
            DEFAULT_PAGE_BACKENDS="not-a-list",
            URL_NAME_TEMPLATE="page_{name}",
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
            (
                "app_dirs_true",
                [file_router_config_entry(app_dirs=True)],
                [],
            ),
        ],
        ids=["invalid_config", "app_dirs_true"],
    )
    def test_get_additional_layout_files_scenarios(
        self,
        tmp_path,
        test_case,
        config,
        expected_result,
    ) -> None:
        """Test _get_additional_layout_files with different configuration scenarios."""
        loader = LayoutTemplateLoader()

        with override_settings(NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": config}):
            next_framework_settings.reload()
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
            (
                "with_app_dirs",
                file_router_config_entry(app_dirs=True),
                [],
            ),
            (
                "no_options",
                file_router_config_entry(),
                [],
            ),
        ],
        ids=["with_pages_dir", "with_app_dirs", "no_options"],
    )
    def test_get_pages_dirs_for_config_scenarios(
        self,
        tmp_path,
        test_case,
        config,
        expected_list,
    ) -> None:
        """Test _get_pages_dirs_for_config with different configuration scenarios."""
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
        """Test _wrap_in_template_block with different file scenarios."""
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
        """Test _find_layout_files when additional layouts are already in local hierarchy."""
        loader = LayoutTemplateLoader()

        layout_file = tmp_path / "layout.djx"
        layout_file.write_text("layout content")

        template_file = tmp_path / "template.djx"
        template_file.write_text("template content")

        page_file = tmp_path / "page.py"

        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": default_page_router_config(tmp_path)
            },
        ):
            next_framework_settings.reload()
            result = loader._find_layout_files(page_file)

        assert result is not None
        assert len(result) == 1
        assert layout_file in result

    def test_get_additional_layout_files_with_duplicate_layouts(self, tmp_path) -> None:
        """Test _get_additional_layout_files with duplicate layout files."""
        loader = LayoutTemplateLoader()

        layout_file = tmp_path / "layout.djx"
        layout_file.write_text("layout content")

        config = default_page_router_config(tmp_path) + default_page_router_config(
            tmp_path
        )

        with override_settings(NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": config}):
            next_framework_settings.reload()
            result = loader._get_additional_layout_files()

        assert len(result) == 1
        assert layout_file in result

    def test_find_layout_files_with_additional_layouts_already_present(
        self, tmp_path
    ) -> None:
        """Test _find_layout_files when additional layouts are already in layout_files."""
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
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": default_page_router_config(parent_dir)
            },
        ):
            next_framework_settings.reload()
            result = loader._find_layout_files(page_file)

        assert result is not None
        assert len(result) == 1
        assert local_layout in result

    def test_find_layout_files_with_different_additional_layouts(
        self, tmp_path
    ) -> None:
        """Test _find_layout_files when additional layouts are different from local ones."""
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
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": default_page_router_config(additional_dir),
            },
        ):
            next_framework_settings.reload()
            result = loader._find_layout_files(page_file)

        assert result is not None
        assert len(result) == 2
        assert local_layout in result
        assert additional_layout in result

    def test_load_template_with_single_layout(self, tmp_path) -> None:
        """Test load_template with single layout file."""
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
        """Test load_template with multiple layout files in hierarchy."""
        loader = LayoutTemplateLoader()

        root_layout = tmp_path / "layout.djx"
        root_layout.write_text(
            "<html><head><title>Root</title></head><body>{% block template %}{% endblock template %}</body></html>",
        )

        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        sub_layout = sub_dir / "layout.djx"
        sub_layout.write_text(
            "<div class='sub-layout'>{% block template %}{% endblock template %}</div>",
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
        """Test load_template when template.djx doesn't exist."""
        loader = LayoutTemplateLoader()

        layout_file = tmp_path / "layout.djx"
        layout_file.write_text(
            "<html><body>{% block template %}{% endblock template %}</body></html>",
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
            "<html><body>{% block template %}{% endblock %}</body></html>",
        )
        page_file = tmp_path / "page.py"
        result = loader.load_template(page_file)
        assert result is not None
        assert "<html><body>" in result
        assert "</body></html>" in result
        assert "{% block template %}" in result
        assert "{% block template %}{% endblock template %}" in result

    def test_find_layout_files(self, tmp_path) -> None:
        """Test _find_layout_files method."""
        loader = LayoutTemplateLoader()

        sub_dir = tmp_path / "sub" / "nested"
        sub_dir.mkdir(parents=True)

        root_layout = tmp_path / "layout.djx"
        root_layout.write_text("root layout")

        sub_layout = tmp_path / "sub" / "layout.djx"
        sub_layout.write_text("sub layout")

        page_file = sub_dir / "page.py"
        layout_files = loader._find_layout_files(page_file)

        assert layout_files is not None
        assert len(layout_files) == 2
        assert sub_layout in layout_files
        assert root_layout in layout_files

    def test_compose_layout_hierarchy_exception_handling(self, tmp_path) -> None:
        """Test _compose_layout_hierarchy handles exceptions gracefully."""
        loader = LayoutTemplateLoader()

        layout_file = tmp_path / "layout.djx"
        layout_file.write_text("test")

        template_file = tmp_path / "template.djx"
        template_file.write_text("test")

        with patch("pathlib.Path.read_text", side_effect=OSError("Mocked error")):
            result = loader._compose_layout_hierarchy("test content", [layout_file])
            assert result == "test content"

    def test_load_template_no_layout_files(self, tmp_path) -> None:
        """Test load_template when no layout files exist."""
        loader = LayoutTemplateLoader()

        page_file = tmp_path / "page.py"
        page_file.write_text("template = 'test'")

        result = loader.load_template(page_file)
        assert result is None


class TestContextProcessors:
    """Test context_processors functionality."""

    def test_get_context_processors_empty_config(self, page_instance) -> None:
        """Test _get_context_processors with empty ``ROUTERS`` list."""
        with override_settings(
            NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": []},
            TEMPLATES=[],
        ):
            next_framework_settings.reload()
            processors = _get_context_processors()
            assert processors == []

    def test_get_context_processors_routers_not_list(self, page_instance) -> None:
        """When ``DEFAULT_PAGE_BACKENDS`` is not a list, treat as no router config."""
        mock_nf = SimpleNamespace(DEFAULT_PAGE_BACKENDS={})
        with (
            patch("next.pages.processors.next_framework_settings", mock_nf),
            override_settings(TEMPLATES=[]),
        ):
            processors = _get_context_processors()
            assert processors == []

    def test_get_context_processors_no_context_processors(self, page_instance) -> None:
        """Test _get_context_processors with routers but no context_processors."""
        config = [file_router_config_entry(app_dirs=True)]
        with override_settings(
            NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": config},
            TEMPLATES=[],
        ):
            next_framework_settings.reload()
            processors = _get_context_processors()
            assert processors == []

    def test_get_context_processors_inherits_from_templates(
        self, page_instance
    ) -> None:
        """Test _get_context_processors inherits from TEMPLATES when routers omit processors."""

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
                    ],
                },
            },
        ]

        next_pages_config = [file_router_config_entry(app_dirs=True)]

        with patch("next.pages.processors.import_string") as mock_import:
            mock_import.side_effect = [test_processor, auth_processor]

            with override_settings(
                TEMPLATES=templates_config,
                NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": next_pages_config},
            ):
                next_framework_settings.reload()
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
                        "test_app.context_processors.template_processor",
                    ],
                },
            },
        ]
        next_pages_config = [
            file_router_config_entry(
                app_dirs=True,
                options={
                    "context_processors": [
                        "test_app.context_processors.next_pages_processor",
                    ],
                },
            ),
        ]

        with patch("next.pages.processors.import_string") as mock_import:
            mock_import.side_effect = [next_pages_processor, template_processor]
            with override_settings(
                TEMPLATES=templates_config,
                NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": next_pages_config},
            ):
                next_framework_settings.reload()
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
            },
        ]
        next_pages_config = [
            file_router_config_entry(
                app_dirs=True,
                options={"context_processors": [shared_path]},
            ),
        ]
        with (
            patch("next.pages.processors.import_string", return_value=shared_processor),
            override_settings(
                TEMPLATES=templates_config,
                NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": next_pages_config},
            ),
        ):
            next_framework_settings.reload()
            processors = _get_context_processors()
            assert len(processors) == 1
            assert processors[0] == shared_processor

    def test_get_context_processors_fallback_empty_templates(
        self, page_instance
    ) -> None:
        """With empty TEMPLATES and no router processors, result is empty."""
        with override_settings(
            TEMPLATES=[],
            NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": []},
        ):
            next_framework_settings.reload()
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
            TEMPLATES=templates_config,
            NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": []},
        ):
            next_framework_settings.reload()
            result = _get_context_processors()
            assert result == []

    def test_get_context_processors_with_valid_processors(self, page_instance) -> None:
        """Test _get_context_processors with valid context processors."""

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
                        ],
                    },
                ),
            ]

            with override_settings(
                NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": config},
                TEMPLATES=[],
            ):
                next_framework_settings.reload()
                processors = _get_context_processors()
                assert len(processors) == 2
                assert processors[0] == test_processor
                assert processors[1] == another_processor

    def test_get_context_processors_with_invalid_processor(self, page_instance) -> None:
        """Test _get_context_processors with invalid processor path."""
        config = [
            file_router_config_entry(
                app_dirs=True,
                options={
                    "context_processors": [
                        "invalid.module.path",
                        "django.template.context_processors.request",
                    ],
                },
            ),
        ]

        with (
            override_settings(NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": config}),
            patch("next.pages.processors.import_string") as mock_import,
            patch("next.pages.processors.logger.warning") as mock_warning,
        ):
            next_framework_settings.reload()
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
        """Test _import_context_processor with non-callable import."""
        with patch("next.pages.processors.import_string") as mock_import:
            mock_import.return_value = "not a callable"

            processor = _import_context_processor("some.module.path")
            assert processor is None

    def test_render_with_context_processors(self, page_instance, tmp_path) -> None:
        """Test render method with context_processors."""
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
        """Test render method without request object (should use regular Context)."""
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
        """Test render method without context_processors (should use regular Context)."""
        page_file = tmp_path / "page.py"
        template_str = "<h1>{{ title }}</h1>"
        page_instance.register_template(page_file, template_str)

        mock_request = HttpRequest()
        mock_request.META = {}

        with patch("next.pages.manager._get_context_processors", return_value=[]):
            result = page_instance.render(page_file, mock_request, title="Test Title")

            assert result == "<h1>Test Title</h1>"

    def test_render_with_context_processor_error(self, page_instance, tmp_path) -> None:
        """Test render method with context processor that raises an exception."""
        from typing import Never

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
            result = page_instance.render(
                page_file,
                mock_request,
                title="Test Title",
            )

            assert "Test Title" in result
            assert "good_value" in result
            mock_logger.warning.assert_called_once()

    def test_strict_context_reraises_processor_error(
        self, page_instance, tmp_path
    ) -> None:
        """`STRICT_CONTEXT=True` turns processor errors into hard failures."""
        from typing import Never

        import pytest

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
        self,
        page_instance,
        tmp_path,
    ) -> None:
        """Test render method with context processor that returns non-dict."""
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


class TestBuildRegisteredLoaders:
    """`build_registered_loaders` reads `TEMPLATE_LOADERS` and caches."""

    def _reset_cache(self) -> None:
        import next.pages.loaders as loaders_module

        loaders_module._REGISTERED_LOADERS_CACHE = None

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
            ],
        }
    )
    def test_user_list_replaces_default(self) -> None:
        next_framework_settings.reload()
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
                "next.pages.loaders.LayoutManager",
                "next.pages.loaders.DjxTemplateLoader",
            ],
        }
    )
    def test_invalid_entries_are_skipped(self) -> None:
        next_framework_settings.reload()
        self._reset_cache()
        loaders = build_registered_loaders()
        assert [type(loader) for loader in loaders] == [DjxTemplateLoader]

    def test_settings_reload_resets_cache(self) -> None:
        build_registered_loaders()
        import next.pages.loaders as loaders_module

        assert loaders_module._REGISTERED_LOADERS_CACHE is not None
        next_framework_settings.reload()
        assert loaders_module._REGISTERED_LOADERS_CACHE is None

    @override_settings(
        NEXT_FRAMEWORK={
            "TEMPLATE_LOADERS": [
                "next.pages.loaders.DjxTemplateLoader",
                "next.pages.loaders.DjxTemplateLoader",
            ],
        }
    )
    def test_duplicate_entries_registered_once(self) -> None:
        """A loader class appears at most once even when listed multiple times."""
        next_framework_settings.reload()
        self._reset_cache()
        loaders = build_registered_loaders()
        assert [type(loader) for loader in loaders] == [DjxTemplateLoader]
