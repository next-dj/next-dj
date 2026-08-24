from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import RequestFactory

from next.components import ComponentInfo, FileComponentsBackend, components_manager
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
        previous = components_manager._backends
        previously_loaded = components_manager._loaded
        components_manager._backends = [backend]
        components_manager._loaded = True
        try:
            html = render_component_by_name(
                "greeter", at=tmp_path / "page.djx", context={"name": "World"}
            )
        finally:
            components_manager._backends = previous
            components_manager._loaded = previously_loaded
        assert "<b>World</b>" in html

    def test_accepts_str_anchor(self, tmp_path: Path) -> None:
        with pytest.raises(LookupError):
            render_component_by_name("nope", at=str(tmp_path / "page.djx"))

    @staticmethod
    def _info(tmp_path: Path, module_path: Path | None) -> ComponentInfo:
        (tmp_path / "widget.djx").write_text("[{{ current_component_module_path }}]")
        return ComponentInfo(
            name="widget",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "widget.djx",
            module_path=module_path,
            is_simple=True,
        )

    def test_sets_component_module_path_from_info(self, tmp_path: Path) -> None:
        module_path = tmp_path / "widget" / "component.py"
        info = self._info(tmp_path, module_path)
        with patch.object(components_manager, "get_component", return_value=info):
            html = render_component_by_name("widget", at=tmp_path / "page.djx")
        assert f"[{module_path}]" in html

    def test_module_less_component_sets_none(self, tmp_path: Path) -> None:
        info = self._info(tmp_path, None)
        with patch.object(components_manager, "get_component", return_value=info):
            html = render_component_by_name("widget", at=tmp_path / "page.djx")
        assert "[None]" in html

    def test_renderer_stamp_overrides_caller_value(self, tmp_path: Path) -> None:
        module_path = tmp_path / "widget" / "component.py"
        info = self._info(tmp_path, module_path)
        with patch.object(components_manager, "get_component", return_value=info):
            html = render_component_by_name(
                "widget",
                at=tmp_path / "page.djx",
                context={"current_component_module_path": "caller-anchor"},
            )
        assert f"[{module_path}]" in html
        assert "caller-anchor" not in html


class TestRenderComponentByNamePropGuard:
    """The caller's context keys stand in for props, as a tag call site would."""

    @staticmethod
    def _composite(tmp_path: Path, returned: str) -> ComponentInfo:
        """Build a composite component whose keyless context returns `returned`."""
        (tmp_path / "component.djx").write_text(
            "<div>title={{ title }} hint={{ hint }}</div>"
        )
        (tmp_path / "component.py").write_text(
            "from next.components import component\n\n\n"
            "@component.context\n"
            "def extra():\n"
            f"    return {returned}\n"
        )
        return ComponentInfo(
            name="card",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "component.djx",
            module_path=(tmp_path / "component.py").resolve(),
            is_simple=False,
        )

    def test_caller_context_key_raises(self, tmp_path: Path) -> None:
        info = self._composite(tmp_path, '{"title": "from context"}')
        with (
            patch.object(components_manager, "get_component", return_value=info),
            pytest.raises(ValueError, match="context returns 'title'"),
        ):
            render_component_by_name(
                "card", at=tmp_path / "page.djx", context={"title": "from caller"}
            )

    def test_unrelated_key_merges(self, tmp_path: Path) -> None:
        info = self._composite(tmp_path, '{"hint": "merged"}')
        with patch.object(components_manager, "get_component", return_value=info):
            html = render_component_by_name(
                "card", at=tmp_path / "page.djx", context={"title": "from caller"}
            )
        assert "title=from caller" in html
        assert "hint=merged" in html
