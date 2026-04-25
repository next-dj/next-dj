from pathlib import Path

import pytest
from django.test import RequestFactory

from next.components import FileComponentsBackend, components_manager
from next.pages.manager import page
from next.testing.rendering import render_component_by_name, render_page


class TestRenderPage:
    """`render_page` forwards to `page.render` with a synthetic request."""

    def test_renders_registered_template(self, tmp_path: Path) -> None:
        page_file = tmp_path / "page.py"
        page.register_template(page_file, "<p>hello</p>")
        assert "<p>hello</p>" in render_page(page_file)

    def test_accepts_str_path(self, tmp_path: Path) -> None:
        page_file = tmp_path / "page.py"
        page.register_template(page_file, "<p>plain</p>")
        assert "<p>plain</p>" in render_page(str(page_file))

    def test_forwards_url_kwargs(self, tmp_path: Path) -> None:
        page_file = tmp_path / "page.py"
        page.register_template(page_file, "<p>{{ slug }}</p>")
        assert "<p>abc</p>" in render_page(page_file, slug="abc")

    def test_accepts_custom_request(self, tmp_path: Path) -> None:
        page_file = tmp_path / "page.py"
        page.register_template(page_file, "<i>{{ request.path }}</i>")
        req = RequestFactory().get("/custom/")
        assert "/custom/" in render_page(page_file, req)


class TestRenderComponentByName:
    """`render_component_by_name` resolves scoping and renders the component."""

    def test_raises_when_component_not_visible(self, tmp_path: Path) -> None:
        with pytest.raises(LookupError, match="not visible"):
            render_component_by_name("missing", at=tmp_path / "page.djx")

    def test_renders_visible_component(self, tmp_path: Path) -> None:
        root = tmp_path / "_components"
        root.mkdir()
        (root / "greeter.djx").write_text("<b>{{ name }}</b>")

        config = {"DIRS": [str(root)], "COMPONENTS_DIR": "_components"}
        backend = FileComponentsBackend(config)
        previous = list(components_manager._backends)
        components_manager._backends.clear()
        components_manager._backends.append(backend)
        try:
            html = render_component_by_name(
                "greeter",
                at=tmp_path / "page.djx",
                context={"name": "World"},
            )
        finally:
            components_manager._backends.clear()
            components_manager._backends.extend(previous)
        assert "<b>World</b>" in html

    def test_accepts_str_anchor(self, tmp_path: Path) -> None:
        with pytest.raises(LookupError):
            render_component_by_name("nope", at=str(tmp_path / "page.djx"))
