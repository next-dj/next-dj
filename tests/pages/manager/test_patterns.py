from unittest.mock import patch

import pytest
from django.http import HttpRequest
from django.urls import path as url_path

from next.caches import PageCache
from next.pages.manager.patterns import fit_page_caches
from next.urls import URLPatternParser
from next.views import page_view, pages as view_pages
from tests.support import URL_PATTERN_CASES, UrlPatternCase


class TestCreateUrlPatternScenarios:
    """``create_url_pattern`` across the body sources a page can have."""

    @pytest.mark.parametrize("case", URL_PATTERN_CASES, ids=lambda case: case.id)
    def test_create_url_pattern_scenarios(
        self, page_instance, tmp_path, url_parser, case: UrlPatternCase
    ) -> None:
        """A page yields a named pattern whenever any body source can serve it."""
        page_file = tmp_path / "page.py"
        if case.page_content is not None:
            page_file.write_text(case.page_content)
        if case.template_djx is not None:
            (tmp_path / "template.djx").write_text(case.template_djx)

        pattern = page_instance.create_url_pattern(
            case.route, page_file, url_parser, page_view
        )

        if case.pattern_name is None:
            assert pattern is None
            return
        assert pattern is not None
        assert pattern.name == case.pattern_name
        if case.composed is not None:
            page_instance.render(page_file)
            assert page_instance._templates.composed[page_file] == case.composed

    def test_create_url_pattern_render_function_fallback(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        """A ``render()`` with no template beside it still produces a route."""
        page_file = tmp_path / "page.py"
        page_file.write_text("""
from django.http import HttpResponse

def render(request, **kwargs):
    return HttpResponse("Fallback render function!")
        """)

        pattern = page_instance.create_url_pattern(
            "test", page_file, url_parser, page_view
        )

        assert pattern is not None
        assert pattern.name == "page_test"

    def test_create_url_pattern_virtual_view_rendering(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        """A virtual page backed only by ``template.djx`` renders its interpolated body."""
        page_file = tmp_path / "page.py"
        djx_file = tmp_path / "template.djx"
        djx_content = "<h1>{{ title }}</h1><p>Hello {{ name }}!</p>"
        djx_file.write_text(djx_content)

        pattern = page_instance.create_url_pattern(
            "test", page_file, url_parser, page_view
        )

        assert pattern is not None

        result = page_instance.render(
            page_file, url_kwargs={"title": "Welcome", "name": "World"}
        )
        assert result == "<h1>Welcome</h1><p>Hello World!</p>"

    def test_create_url_pattern_with_context_functions(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        """A virtual page reads the ``@context`` values registered against its path."""
        page_file = tmp_path / "page.py"
        djx_file = tmp_path / "template.djx"
        djx_content = "<h1>{{ title }}</h1><p>{{ description }}</p>"
        djx_file.write_text(djx_content)

        page_instance.contexts.register_context(
            page_file, "title", lambda: "Context Title"
        )
        page_instance.contexts.register_context(
            page_file, "description", lambda: "Context Description"
        )

        pattern = page_instance.create_url_pattern(
            "test", page_file, url_parser, page_view
        )

        assert pattern is not None

        result = page_instance.render(page_file)
        assert result == "<h1>Context Title</h1><p>Context Description</p>"


class TestPageCreateUrlPattern:
    """``create_url_pattern`` refusing pages it cannot serve."""

    def test_a_broken_import_still_routes(self, page_instance, tmp_path) -> None:
        """A ``page.py`` that fails to import still gets a fail-loud pattern."""
        page_file = tmp_path / "page.py"
        page_file.write_text("invalid python syntax {")

        result = page_instance.create_url_pattern(
            "test", page_file, URLPatternParser(), page_view
        )

        assert result is not None
        assert result.callback.next_page_path == page_file

    def test_a_missing_module_yields_no_pattern(self, page_instance, tmp_path) -> None:
        """A ``page.py`` path with no loadable module and no error yields no pattern."""
        page_file = tmp_path / "page.py"

        result = page_instance.create_url_pattern(
            "test", page_file, URLPatternParser(), page_view
        )

        assert result is None

    def test_a_module_importlib_cannot_build_yields_no_pattern(
        self, page_instance, tmp_path, monkeypatch
    ) -> None:
        """A file that builds no module and records no error routes nothing."""
        page_file = tmp_path / "page.py"
        page_file.write_text("template = 'body'")
        monkeypatch.setattr(view_pages, "load_page_module", lambda _path: (None, None))

        result = page_instance.create_url_pattern(
            "test", page_file, URLPatternParser(), page_view
        )

        assert result is None

    def test_no_body_source_yields_no_pattern(self, page_instance, tmp_path) -> None:
        """A module with neither a body nor ``render()`` yields no pattern."""
        page_file = tmp_path / "page.py"
        page_file.write_text("def other_function(): pass")

        result = page_instance.create_url_pattern(
            "test", page_file, URLPatternParser(), page_view
        )

        assert result is None


class TestFitPageCaches:
    """The page caches are sized by the distinct pages a list of patterns mounts."""

    def test_each_page_is_counted_once(
        self, page_instance, tmp_path, url_parser
    ) -> None:
        patterns: list[object] = [url_path("plain/", HttpRequest), "marker"]
        for name in ("a", "b"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "template.djx").write_text("x")
            file_path = tmp_path / name / "page.py"
            patterns.append(
                page_instance.create_url_pattern(name, file_path, url_parser, page_view)
            )
        patterns.append(patterns[-1])
        with patch.object(PageCache, "fit") as fit:
            fit_page_caches(patterns)
        fit.assert_called_once_with(2)
